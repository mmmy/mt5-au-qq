"""Authenticated, single-process Chrome bridge. Commands are never replayed on reconnect."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from uuid import UUID, uuid4
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.alert_template import parse_prices
from app.chart_snapshot import chart_snapshot
from app.models import CreateAlertRequest

router = APIRouter(prefix="/api/browser-sync", tags=["browser-sync"])
EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}\Z")
IDENTITY = ("tab_id", "document_id", "chart_id", "study_id", "pine_id", "pine_version", "symbol", "resolution")
SESSION_COOKIE = "browser_sync_session"
SESSION_SECONDS = 30 * 24 * 60 * 60


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def inputs_match(actual, expected):
    if not isinstance(actual, dict) or actual.keys() != expected.keys():
        return False
    return all((type(actual[key]) is bool and actual[key] is value) if type(value) is bool else
               (type(actual[key]) in (int, float) and actual[key] == value) if type(value) in (int, float) else
               (type(actual[key]) is str and actual[key] == value) for key, value in expected.items())


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    client_id: UUID
    kind: str = Field(pattern="^(discover|apply)$")
    target: dict | None = None
    draft: CreateAlertRequest | None = None
    alert_id: int | None = Field(default=None, gt=0)


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=8, max_length=80)
    label: str = Field(default="Chrome", min_length=1, max_length=60)


class BrowserBridge:
    def __init__(self, database: Path, alert_service):
        self.database = database
        self.alert_service = alert_service
        self.sockets: dict[str, WebSocket] = {}
        self.inventory: dict[str, list] = {}
        self.last_seen: dict[str, float] = {}
        self.codes: dict[str, float] = {}
        self.pair_attempts: list[float] = []
        self.lock = asyncio.Lock()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS browser_sync_secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS browser_sync_clients (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, token_hash TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS browser_sync_jobs (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    command TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created REAL NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO browser_sync_secrets VALUES ('admin', ?)", (secrets.token_urlsafe(32),))
            self.admin_token = os.getenv("BROWSER_SYNC_ADMIN_TOKEN") or db.execute(
                "SELECT value FROM browser_sync_secrets WHERE name='admin'").fetchone()[0]
            db.execute("UPDATE browser_sync_jobs SET status='uncertain', result=? WHERE status='pending'",
                       (encode({"message": "后端重启，无法确认执行结果；请重新读取图表后再操作"}),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def issue_pair_code(self):
        self.codes.clear()
        code = secrets.token_urlsafe(18)
        self.codes[digest(code)] = time.time() + 120
        return {"code": code, "expires_in": 120}

    def pair(self, data: PairRequest):
        now = time.time()
        self.pair_attempts = [t for t in self.pair_attempts if t > now - 60]
        if len(self.pair_attempts) >= 20:
            raise HTTPException(429, "配对尝试过多，请稍后重试")
        self.pair_attempts.append(now)
        expires = self.codes.pop(digest(data.code), 0)
        if expires < now:
            raise HTTPException(403, "配对码无效或已过期，请在管理页重新生成")
        client_id, token = str(uuid4()), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO browser_sync_clients (id,label,token_hash) VALUES (?,?,?)",
                       (client_id, data.label, digest(token)))
        return {"client_id": client_id, "token": token}

    def authenticate_client(self, client_id, token):
        if not isinstance(token, str) or not isinstance(client_id, str):
            return False
        with self.connect() as db:
            row = db.execute("SELECT token_hash FROM browser_sync_clients WHERE id=? AND revoked=0", (client_id,)).fetchone()
        return bool(row and secrets.compare_digest(row[0], digest(token)))

    def clients(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,label FROM browser_sync_clients WHERE revoked=0").fetchall()
        return [{**dict(row), "connected": row["id"] in self.sockets and time.time() - self.last_seen.get(row["id"], 0) < 50,
                 "targets": self.inventory.get(row["id"], [])} for row in rows]

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM browser_sync_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "同步任务不存在")
            if row["status"] == "pending" and row["created"] < time.time() - 45:
                db.execute("UPDATE browser_sync_jobs SET status='uncertain',result=? WHERE id=?",
                           (encode({"message": "等待回读超时；结果未确认，请刷新图表列表后检查，命令不会自动重发"}), job_id))
                row = db.execute("SELECT * FROM browser_sync_jobs WHERE id=?", (job_id,)).fetchone()
        command = json.loads(row["command"])
        return {"id": row["id"], "status": row["status"], "kind": command["kind"],
                "result": json.loads(row["result"]) if row["result"] else None,
                "snapshot": command.get("snapshot")}

    async def submit(self, data: SyncRequest):
        async with self.lock:
            return await self._submit(data)

    async def _submit(self, data: SyncRequest):
        job_id, client_id = str(data.request_id), str(data.client_id)
        request_hash = digest(encode(data.model_dump(mode="json", exclude_none=True, exclude={"draft": {"request_id"}})))
        with self.connect() as db:
            existing = db.execute("SELECT request_hash FROM browser_sync_jobs WHERE id=?", (job_id,)).fetchone()
        if existing:
            if existing[0] != request_hash:
                raise HTTPException(409, "请求 ID 已被其他参数使用")
            return self.job(job_id)
        if client_id not in self.sockets or time.time() - self.last_seen.get(client_id, 0) > 50:
            raise HTTPException(409, "扩展未连接，请先在 Chrome 中配对并连接")
        with self.connect() as db:
            busy = db.execute("SELECT id FROM browser_sync_jobs WHERE client_id=? AND status='pending' AND created>?",
                              (client_id, time.time() - 45)).fetchone()
        if busy:
            raise HTTPException(409, "该浏览器仍有任务在执行，请等待回读结果")
        command = {"type": "command", "id": job_id, "kind": data.kind, "expires_at": time.time() + 30}
        if data.kind == "apply":
            if (data.draft is None) == (data.alert_id is None):
                raise HTTPException(422, "请选择当前输入或已有警报其中一种来源")
            targets = self.inventory.get(client_id, [])
            target = next((t for t in targets if all(t.get(k) == (data.target or {}).get(k) for k in IDENTITY)), None)
            if target is None:
                raise HTTPException(409, "所选图表已失效，请重新读取图表列表")
            if data.draft:
                draft = data.draft
                service = self.alert_service
                signals = service._complete_signal_settings(draft.signal_settings, service.get_signal_settings()) if draft.signal_settings else service.get_signal_settings()
                template = service.template_builder.build(parse_prices(draft.prices), name="图表预览", side=draft.side,
                    valid_bars=draft.valid_bars, valid_hours=draft.valid_hours, start_time_ms=draft.start_time_ms,
                    resolution=draft.resolution, signal_settings=signals)
                snapshot = chart_snapshot(template)
            else:
                snapshot = await self.alert_service.get_chart_snapshot(data.alert_id)
            for key in ("pine_id", "pine_version", "symbol", "resolution"):
                if target.get(key) != snapshot[key]:
                    raise HTTPException(409, f"图表与警报的 {key} 不一致：图表 {target.get(key)} / 警报 {snapshot[key]}。请切换到匹配的脚本、品种和周期后重新读取")
            command.update(target=target, snapshot=snapshot)
        with self.connect() as db:
            db.execute("INSERT INTO browser_sync_jobs VALUES (?,?,?,?,?,?,?)",
                       (job_id, client_id, request_hash, encode(command), "pending", None, time.time()))
        try:
            await self.sockets[client_id].send_json(command)
        except (RuntimeError, OSError):
            self.uncertain(client_id)
        return self.job(job_id)

    def uncertain(self, client_id):
        with self.connect() as db:
            db.execute("UPDATE browser_sync_jobs SET status='uncertain',result=? WHERE client_id=? AND status='pending'",
                       (encode({"message": "扩展连接断开，执行结果未确认；不会自动重发，请重新读取图表"}), client_id))

    def accept_result(self, client_id, message):
        job_id = message.get("id")
        if not isinstance(job_id, str):
            return
        with self.connect() as db:
            row = db.execute("SELECT * FROM browser_sync_jobs WHERE id=? AND client_id=?", (job_id, client_id)).fetchone()
            if not row or row["status"] not in ("pending", "uncertain") or row["created"] < time.time() - 120:
                return
            command = json.loads(row["command"])
            result = message.get("result")
            if not isinstance(result, dict):
                return
            ok = result.get("ok") is True
            if ok and command["kind"] == "discover":
                targets = result.get("targets")
                ok = isinstance(targets, list) and len(targets) <= 100 and all(
                    isinstance(t, dict) and all(k in t for k in IDENTITY) for t in targets)
                if ok:
                    self.inventory[client_id] = targets
            elif ok:
                readback = result.get("readback", {})
                ok = (isinstance(readback, dict) and all(readback.get(k) == command["target"].get(k) for k in IDENTITY)
                      and inputs_match(readback.get("inputs"), command["snapshot"]["inputs"]))
                if not ok:
                    result = {"ok": False, "message": "扩展回读与请求参数不一致，未确认同步成功"}
            if not ok and not result.get("message"):
                result = {"ok": False, "message": "扩展返回的结果格式无效"}
            db.execute("UPDATE browser_sync_jobs SET status=?,result=? WHERE id=?",
                       ("succeeded" if ok else "failed", encode(result), job_id))

    async def shutdown(self):
        for socket in list(self.sockets.values()):
            await socket.close(code=1001)


def bridge(request: Request) -> BrowserBridge:
    value = getattr(request.app.state, "browser_bridge", None)
    if value is None:
        raise HTTPException(503, "浏览器同步服务尚未启动")
    return value


def same_origin(request: Request):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "不允许跨站管理浏览器")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站管理浏览器")


def session_signature(service: BrowserBridge, request: Request, payload: str) -> str:
    audience = f"{request.url.scheme}://{request.url.netloc}"
    return hmac.new(service.admin_token.encode(), f"browser-sync:{audience}:{payload}".encode(), hashlib.sha256).hexdigest()


def valid_session(service: BrowserBridge, request: Request) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if not re.fullmatch(r"\d{10,12}\.[0-9a-f]{32}\.[0-9a-f]{64}", cookie):
        return False
    expires, nonce, signature = cookie.split(".")
    if not time.time() < int(expires) <= time.time() + SESSION_SECONDS + 60:
        return False
    return secrets.compare_digest(signature, session_signature(service, request, f"{expires}.{nonce}"))


def set_session(response: Response, request: Request, service: BrowserBridge):
    payload = f"{int(time.time()) + SESSION_SECONDS}.{secrets.token_hex(16)}"
    response.set_cookie(SESSION_COOKIE, f"{payload}.{session_signature(service, request, payload)}",
                        max_age=SESSION_SECONDS, path="/api/browser-sync", httponly=True,
                        secure=request.url.scheme == "https", samesite="strict")


def admin(request: Request, service: BrowserBridge = Depends(bridge)):
    same_origin(request)
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secrets.compare_digest(token.encode(), service.admin_token.encode()) and not valid_session(service, request):
        raise HTTPException(401, "当前网页尚未授权或授权已过期，请填写管理密钥；无需重新配对扩展")
    return service


@router.get("/session")
async def local_session(request: Request, response: Response, service: BrowserBridge = Depends(bridge)):
    same_origin(request)
    local = request.client.host in ("127.0.0.1", "::1") and request.url.hostname in ("localhost", "127.0.0.1", "::1")
    if not local and not valid_session(service, request):
        raise HTTPException(403, "首次访问请填写管理密钥，之后此浏览器会自动恢复授权；无需重新配对扩展")
    set_session(response, request, service)
    # Preserve the local CLI bootstrap; remote browsers never receive the admin key.
    return {"authenticated": True, **({"token": service.admin_token} if local else {})}


@router.post("/session")
async def authorize_session(request: Request, response: Response, service: BrowserBridge = Depends(admin)):
    set_session(response, request, service)
    return {"authenticated": True}


@router.delete("/session")
async def logout_session(request: Request, response: Response):
    same_origin(request)
    response.delete_cookie(SESSION_COOKIE, path="/api/browser-sync", httponly=True,
                           secure=request.url.scheme == "https", samesite="strict")
    return {"authenticated": False}


@router.get("/clients")
async def clients(service: BrowserBridge = Depends(admin)):
    return service.clients()


@router.delete("/clients/{client_id}")
async def revoke(client_id: UUID, service: BrowserBridge = Depends(admin)):
    key = str(client_id)
    with service.connect() as db:
        db.execute("UPDATE browser_sync_clients SET revoked=1 WHERE id=?", (key,))
    socket = service.sockets.get(key)
    if socket:
        await socket.close(code=4001)
    service.inventory.pop(key, None)
    return {"revoked": True}


@router.post("/pair-code")
async def pair_code(service: BrowserBridge = Depends(admin)):
    return service.issue_pair_code()


@router.options("/pair")
async def pair_options(request: Request):
    origin = request.headers.get("origin", "")
    if not EXTENSION_ORIGIN.fullmatch(origin):
        raise HTTPException(403, "只允许 Chrome 扩展配对")
    return Response(headers={"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST",
                             "Access-Control-Allow-Headers": "Content-Type", "Vary": "Origin"})


@router.post("/pair")
async def pair(data: PairRequest, request: Request, service: BrowserBridge = Depends(bridge)):
    origin = request.headers.get("origin", "")
    if not EXTENSION_ORIGIN.fullmatch(origin):
        raise HTTPException(403, "只允许 Chrome 扩展配对")
    from fastapi.responses import JSONResponse
    return JSONResponse(service.pair(data), headers={"Access-Control-Allow-Origin": origin, "Vary": "Origin"})


@router.post("/jobs")
async def submit(data: SyncRequest, service: BrowserBridge = Depends(admin)):
    return await service.submit(data)


@router.get("/jobs/{job_id}")
async def job(job_id: UUID, service: BrowserBridge = Depends(admin)):
    return service.job(str(job_id))


@router.get("/extension.zip")
async def extension_zip():
    directory = Path(__file__).resolve().parent.parent / "browser-extension"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(directory.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(directory).as_posix())
    return Response(output.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="mt5-au-chart-sync.zip"'})


@router.websocket("/ws")
async def websocket(socket: WebSocket):
    service = getattr(socket.app.state, "browser_bridge", None)
    if service is None or not EXTENSION_ORIGIN.fullmatch(socket.headers.get("origin", "")):
        await socket.close(code=1008)
        return
    await socket.accept()
    client_id = None
    try:
        raw = await asyncio.wait_for(socket.receive_text(), timeout=5)
        if len(raw) > 2048:
            await socket.close(code=1008)
            return
        auth = json.loads(raw)
        if not isinstance(auth, dict) or auth.get("type") != "auth" or not service.authenticate_client(auth.get("client_id"), auth.get("token")):
            await socket.close(code=4001)
            return
        client_id = auth["client_id"]
        if client_id in service.sockets:
            await socket.close(code=4009)
            client_id = None
            return
        service.sockets[client_id] = socket
        service.last_seen[client_id] = time.time()
        await socket.send_json({"type": "ready", "protocol": 1})
        while True:
            raw = await asyncio.wait_for(socket.receive_text(), timeout=50)
            if len(raw) > 131072:
                await socket.close(code=1009)
                break
            message = json.loads(raw)
            if not isinstance(message, dict):
                break
            service.last_seen[client_id] = time.time()
            if message.get("type") == "ping":
                await socket.send_json({"type": "pong"})
            elif message.get("type") == "result":
                service.accept_result(client_id, message)
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, TypeError):
        pass
    finally:
        if client_id and service.sockets.get(client_id) is socket:
            service.sockets.pop(client_id, None)
            service.inventory.pop(client_id, None)
            service.uncertain(client_id)
