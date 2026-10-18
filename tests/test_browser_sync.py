from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from app.alert_service import AlertService
from app.alert_template import AlertTemplateBuilder, PRICE_INPUTS
from app.browser_sync import BrowserBridge, router
from app.chart_snapshot import chart_snapshot
from app.errors import AppError
from app.trade_repository import TradeRepository

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = {"origin": "chrome-extension://" + "a" * 32}


class TV:
    def __init__(self):
        self.alerts = []
        self.creates = []

    async def list_alerts(self):
        return self.alerts

    async def create_alert(self, template):
        self.creates.append(deepcopy(template))
        self.alerts.append({**template["payload"], "alert_id": 7})
        return 7


@pytest.fixture
def setup(tmp_path):
    repository = TradeRepository(tmp_path / "app.db")
    repository.initialize()
    tv = TV()
    service = AlertService(tv, AlertTemplateBuilder(ROOT / "payload.json"), name_prefix="TEST::",
                           webhook_url="http://localhost/webhook", repository=repository)
    bridge = BrowserBridge(repository.database_file, service)
    app = FastAPI()
    app.include_router(router)
    app.state.browser_bridge = bridge

    @app.exception_handler(AppError)
    async def handle(_request, error):
        return JSONResponse({"detail": error.message}, status_code=error.status_code)

    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        yield client, bridge, service, tv


def pair(client):
    token = client.get("/api/browser-sync/session").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    code = client.post("/api/browser-sync/pair-code", headers=headers).json()["code"]
    auth = client.post("/api/browser-sync/pair", json={"code": code}, headers=ORIGIN).json()
    return headers, auth, code


def connect(client, auth):
    ws = client.websocket_connect("/api/browser-sync/ws", headers=ORIGIN)
    return ws


def authenticate(ws, auth):
    ws.send_json({"type": "auth", **auth})
    assert ws.receive_json()["type"] == "ready"


def complete(ws, command, result):
    ws.send_json({"type": "result", "id": command["id"], "result": result})
    ws.send_json({"type": "ping"})
    assert ws.receive_json()["type"] == "pong"


def discover(client, ws, headers, auth, snapshot):
    target = {k: snapshot[k] for k in ("pine_id", "pine_version", "symbol", "resolution")}
    target.update(tab_id=10, document_id="document-1", chart_id="chart-1", study_id="study-1", price_capacity=20)
    response = client.post("/api/browser-sync/jobs", headers=headers, json={
        "request_id": str(uuid4()), "client_id": auth["client_id"], "kind": "discover"})
    assert response.status_code == 200
    command = ws.receive_json()
    complete(ws, command, {"ok": True, "targets": [target]})
    assert client.get(f'/api/browser-sync/jobs/{command["id"]}', headers=headers).json()["status"] == "succeeded"
    return target


def template(service):
    return service.template_builder.build([Decimal("4600")], name="TEST::snapshot", resolution="2")


def test_local_bootstrap_auth_origin_and_pairing(setup):
    client, bridge, _, _ = setup
    assert client.get("/api/browser-sync/clients").status_code == 401
    assert client.get("/api/browser-sync/session", headers={"Host": "evil.test"}).status_code == 403
    assert client.get("/api/browser-sync/session", headers={"Origin": "https://evil.test"}).status_code == 403
    headers, auth, code = pair(client)
    assert client.post("/api/browser-sync/pair", json={"code": code}, headers=ORIGIN).status_code == 403
    assert client.post("/api/browser-sync/pair", json={"code": "invalid-code"}).status_code == 403
    assert bridge.authenticate_client(auth["client_id"], auth["token"])
    with bridge.connect() as db:
        assert auth["token"] not in db.execute("SELECT token_hash FROM browser_sync_clients").fetchone()[0]
    assert client.post("/api/browser-sync/pair-code", headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    with connect(client, auth) as ws:
        authenticate(ws, auth)
        assert client.get("/api/browser-sync/clients", headers=headers).json()[0]["connected"]
    client.delete(f'/api/browser-sync/clients/{auth["client_id"]}', headers=headers)
    assert not bridge.authenticate_client(auth["client_id"], auth["token"])


def test_websocket_rejects_unknown_origin_and_invalid_credentials(setup):
    client, _, _, _ = setup
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/browser-sync/ws", headers={"origin": "https://evil.test"}):
            pass
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/api/browser-sync/ws", headers=ORIGIN) as ws:
            ws.send_json({"type": "auth", "client_id": str(uuid4()), "token": "wrong"})
            ws.receive_json()
    assert rejected.value.code == 4001


def test_remote_page_cookie_survives_reload_new_page_and_backend_restart(setup):
    local, bridge, service, _ = setup
    headers, auth, _ = pair(local)
    with TestClient(local.app, base_url="http://public.test", client=("203.0.113.9", 50000)) as remote:
        assert remote.get("/api/browser-sync/session").status_code == 403
        response = remote.post("/api/browser-sync/session", headers=headers)
        assert response.json() == {"authenticated": True}
        cookie_header = response.headers["set-cookie"]
        assert "HttpOnly" in cookie_header and "SameSite=strict" in cookie_header and "Max-Age=2592000" in cookie_header
        assert bridge.admin_token not in cookie_header
        for _ in range(3):
            assert remote.get("/api/browser-sync/session").json() == {"authenticated": True}
            clients = remote.get("/api/browser-sync/clients").json()
            assert clients[0]["id"] == auth["client_id"]
        # A new page/context with the browser's saved cookie needs no bearer key.
        with TestClient(local.app, base_url="http://public.test", client=("203.0.113.9", 50001)) as reopened:
            reopened.cookies.update(remote.cookies)
            assert reopened.get("/api/browser-sync/session").json() == {"authenticated": True}
            local.app.state.browser_bridge = BrowserBridge(bridge.database, service)
            assert reopened.get("/api/browser-sync/clients").json()[0]["id"] == auth["client_id"]
            assert reopened.post("/api/browser-sync/pair-code").status_code == 200
        assert remote.delete("/api/browser-sync/session").json() == {"authenticated": False}
        assert remote.get("/api/browser-sync/clients").status_code == 401
        assert bridge.authenticate_client(auth["client_id"], auth["token"])


def test_page_cookie_is_bound_to_origin_and_rejects_forgery_and_expiry(setup):
    from app.browser_sync import SESSION_COOKIE, SESSION_SECONDS
    local, bridge, _, _ = setup
    headers = {"Authorization": f"Bearer {bridge.admin_token}"}
    with TestClient(local.app, base_url="https://public.test", client=("203.0.113.9", 50000)) as remote:
        response = remote.post("/api/browser-sync/session", headers=headers)
        assert "Secure" in response.headers["set-cookie"]
        cookie = remote.cookies.get(SESSION_COOKIE)
        assert remote.get("/api/browser-sync/clients", headers={"Origin": "https://evil.test"}).status_code == 403
        assert remote.delete("/api/browser-sync/session", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        assert remote.get("/api/browser-sync/clients", headers={"Host": "evil.test"}).status_code == 401
        expires, nonce, signature = cookie.split(".")
        for forged in (cookie[:-1] + ("0" if cookie[-1] != "0" else "1"), f"1.{nonce}.{signature}",
                       f"{int(time.time()) + SESSION_SECONDS + 1000}.{nonce}.{signature}"):
            assert remote.get("/api/browser-sync/clients", headers={"Cookie": f"{SESSION_COOKIE}={forged}"}).status_code == 401
        bridge.admin_token = "rotated-admin-secret"
        assert remote.get("/api/browser-sync/clients").status_code == 401


def test_readback_numbers_accept_json_numeric_equivalence_but_not_boolean_coercion():
    from app.browser_sync import inputs_match
    assert inputs_match({"in_1": 1000, "in_4": False}, {"in_1": 1000.0, "in_4": False})
    assert not inputs_match({"in_1": True, "in_4": False}, {"in_1": 1, "in_4": False})
    assert not inputs_match({"in_1": 1, "in_4": 0}, {"in_1": 1, "in_4": False})


def test_draft_round_trip_idempotency_and_cleared_slots(setup):
    client, _, service, tv = setup
    headers, auth, _ = pair(client)
    with connect(client, auth) as ws:
        authenticate(ws, auth)
        target = discover(client, ws, headers, auth, chart_snapshot(template(service)))
        body = {"request_id": str(uuid4()), "client_id": auth["client_id"], "kind": "apply", "target": target,
                "draft": {"prices": "4600, 4620.5", "valid_hours": 24, "resolution": "2"}}
        response = client.post("/api/browser-sync/jobs", headers=headers, json=body)
        assert response.status_code == 200, response.text
        command = ws.receive_json()
        inputs = command["snapshot"]["inputs"]
        for enabled, price in PRICE_INPUTS[2:]:
            assert inputs[enabled] is False and inputs[price] == 0
        assert "ILScript" not in inputs and "pineFeatures" not in inputs
        complete(ws, command, {"ok": True, "readback": {**target, "inputs": inputs}})
        response = client.post("/api/browser-sync/jobs", headers=headers, json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "succeeded"
        assert tv.creates == []  # Chart preview must not create a server alert.
        body["draft"]["prices"] = "4700"
        assert client.post("/api/browser-sync/jobs", headers=headers, json=body).status_code == 409


@pytest.mark.parametrize("key,value", [("pine_id", "other"), ("pine_version", "999.0"), ("symbol", "FOREXCOM:XAUUSD"), ("resolution", "5")])
def test_mismatched_target_never_queues_a_write(setup, key, value):
    client, bridge, service, _ = setup
    headers, auth, _ = pair(client)
    with connect(client, auth) as ws:
        authenticate(ws, auth)
        snapshot = chart_snapshot(template(service))
        snapshot[key] = value
        target = discover(client, ws, headers, auth, snapshot)
        body = {"request_id": str(uuid4()), "client_id": auth["client_id"], "kind": "apply", "target": target,
                "draft": {"prices": "4600"}}
        assert client.post("/api/browser-sync/jobs", headers=headers, json=body).status_code == 409
        with bridge.connect() as db:
            assert not db.execute("SELECT id FROM browser_sync_jobs WHERE id=?", (body["request_id"],)).fetchone()


def test_false_success_disconnect_and_restart_are_not_replayed(setup):
    client, bridge, service, _ = setup
    headers, auth, _ = pair(client)
    with connect(client, auth) as ws:
        authenticate(ws, auth)
        target = discover(client, ws, headers, auth, chart_snapshot(template(service)))
        body = {"request_id": str(uuid4()), "client_id": auth["client_id"], "kind": "apply", "target": target, "draft": {"prices": "4600"}}
        client.post("/api/browser-sync/jobs", headers=headers, json=body)
        command = ws.receive_json()
        complete(ws, command, {"ok": True, "readback": {**target, "inputs": {"in_5": 4700}}})
        assert bridge.job(command["id"])["status"] == "failed"
        body["request_id"] = str(uuid4())
        client.post("/api/browser-sync/jobs", headers=headers, json=body)
        interrupted = ws.receive_json()
    assert bridge.job(interrupted["id"])["status"] == "uncertain"
    restarted = BrowserBridge(bridge.database, service)
    assert restarted.job(interrupted["id"])["status"] == "uncertain"
    with connect(client, auth) as ws:
        authenticate(ws, auth)
        assert client.post("/api/browser-sync/jobs", headers=headers, json=body).json()["status"] == "uncertain"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"  # No command is resent on reconnect.


def test_existing_alert_replays_original_snapshot_and_legacy_fails_explicitly(setup):
    client, _, service, tv = setup
    asyncio.run(service.create_alert("4600,4620", uuid4()))
    saved = service.repository.get_alert_configs([7])[7]["chart_snapshot"]
    # Today's form/template must never replace the saved alert parameters.
    tv.alerts[0].pop("conditions")
    assert asyncio.run(service.get_chart_snapshot(7)) == saved
    headers, auth, _ = pair(client)
    with connect(client, auth) as ws:
        authenticate(ws, auth)
        target = discover(client, ws, headers, auth, saved)
        body = {"request_id": str(uuid4()), "client_id": auth["client_id"], "kind": "apply", "target": target, "alert_id": 7}
        assert client.post("/api/browser-sync/jobs", headers=headers, json=body).status_code == 200
        command = ws.receive_json()
        assert command["snapshot"] == saved
        complete(ws, command, {"ok": True, "readback": {**target, "inputs": saved["inputs"]}})
        assert len(tv.creates) == 1
    tv.alerts.append({"alert_id": 8, "name": "TEST::legacy"})
    with pytest.raises(AppError, match="快照"):
        asyncio.run(service.get_chart_snapshot(8))
    tv.alerts.append({**tv.creates[0]["payload"], "alert_id": 9})
    assert asyncio.run(service.get_chart_snapshot(9)) == saved


def test_expired_pair_code_and_job_timeout(setup):
    _, bridge, _, _ = setup
    from app.browser_sync import PairRequest, digest, encode
    code = bridge.issue_pair_code()["code"]
    bridge.codes[digest(code)] = time.time() - 1
    with pytest.raises(Exception) as error:
        bridge.pair(PairRequest(code=code))
    assert error.value.status_code == 403
    key = str(uuid4())
    with bridge.connect() as db:
        db.execute("INSERT INTO browser_sync_jobs VALUES (?,?,?,?,?,?,?)", (key, "x", "hash", encode({"kind": "apply"}), "pending", None, time.time() - 50))
    assert bridge.job(key)["status"] == "uncertain"


def test_extension_archive_contains_manifest_and_no_runtime_credentials(setup):
    client, _, _, _ = setup
    import io
    import zipfile
    response = client.get("/api/browser-sync/extension.zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {"manifest.json", "background.js", "adapter.js", "popup.html"} <= set(archive.namelist())
        assert all(not name.endswith((".env", ".db")) for name in archive.namelist())
