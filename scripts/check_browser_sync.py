"""Optional real MV3/WS smoke test. Uses a fresh Edge profile and simulated TV page.

Run: python -m pip install playwright; python scripts/check_browser_sync.py
No TradingView credentials or MT5 processes are used.
"""
from __future__ import annotations

import asyncio
import gc
import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
from playwright.sync_api import expect, sync_playwright
import uvicorn

from app.alert_service import AlertService
from app.alert_template import AlertTemplateBuilder
from app.browser_sync import BrowserBridge, router
from app.trade_repository import TradeRepository


class FakeTV:
    def __init__(self):
        self.alerts = []

    async def list_alerts(self):
        return self.alerts

    async def create_alert(self, template):
        self.alerts.append({**template["payload"], "alert_id": 7})
        return 7


def run():
    with TemporaryDirectory(prefix="mt5-sync-smoke-") as temporary:
        directory = Path(temporary)
        repository = TradeRepository(directory / "test.db")
        repository.initialize()
        tv = FakeTV()
        service = AlertService(tv, AlertTemplateBuilder(ROOT / "payload.json"), name_prefix="SMOKE::",
                               webhook_url="http://localhost/webhook", repository=repository)
        asyncio.run(service.create_alert("4700", uuid4()))
        saved = repository.get_alert_configs([7])[7]["chart_snapshot"]
        bridge = BrowserBridge(repository.database_file, service)
        app = FastAPI()
        app.state.browser_bridge = bridge
        app.include_router(router)
        app.mount("/static", StaticFiles(directory=ROOT / "app/static"))

        @app.get("/")
        async def index():
            return FileResponse(ROOT / "app/static/index.html")

        @app.get("/api/alerts")
        async def alerts():
            return await service.list_alerts()

        @app.get("/api/alerts/signal-settings")
        async def signals():
            return service.get_signal_settings()

        @app.get("/api/trading/status")
        async def trading_status():
            return {"enabled": False, "volume": 0.01, "max_volume": 0.1, "demo_only": False,
                    "mt5": {"connected": False, "message": "浏览器测试，不连接 MT5"}}

        @app.get("/api/tradingview/setup")
        async def setup():
            return {"webhook_url": "http://localhost/webhook", "message": "{}"}

        @app.get("/api/trade-signals")
        async def trade_signals():
            return []

        @app.get("/api/trade-statistics")
        async def statistics():
            return {"start_date": "2026-10-12", "end_date": "2026-10-18", "total": {}, "daily": [], "accounts": []}

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        try:
            with sync_playwright() as playwright:
                extension = str(ROOT / "browser-extension")
                context = playwright.chromium.launch_persistent_context(str(directory / "profile"), channel="msedge",
                    headless=True, viewport={"width": 1440, "height": 1100},
                    args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"])
                try:
                    # This check explicitly opts into the currently hidden UI.
                    context.add_init_script("window.BROWSER_SYNC_UI_ENABLED = true")
                    worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
                    popup = context.new_page()
                    popup.goto(worker.url.replace("background.js", "popup.html"))
                    expect(popup.locator("#status")).to_contain_text("尚未配对")
                    token = httpx.get(base + "/api/browser-sync/session").json()["token"]
                    headers = {"Authorization": f"Bearer {token}"}
                    code = httpx.post(base + "/api/browser-sync/pair-code", headers=headers).json()["code"]
                    popup.locator("#backend").fill(base)
                    popup.locator("#code").fill(code)
                    popup.locator("#pair").click()
                    expect(popup.locator("#status")).to_contain_text("已连接", timeout=15000)

                    mock = """<!doctype html><meta charset="utf-8"><title>Simulated TradingView chart</title><script>
                    const snapshot=SNAPSHOT; window.chartInputs={...snapshot.inputs}; window.chartWrites=0;
                    const inputs=Object.entries(snapshot.inputs).map(([id,v])=>({id,type:typeof v==='boolean'?'bool':typeof v==='number'?'float':'text'}));
                    for(let i=0;i<20;i++)inputs.find(x=>x.id===`in_${5+i*3}`).type='price';
                    const study={id:()=> 'study-1',metaInfo:()=>({description:'黄金价格策略',scriptIdPart:snapshot.pine_id,pine:{version:snapshot.pine_version},inputs})};
                    const widget={id:()=> 'chart-1',symbol:()=>({value:()=>snapshot.symbol}),interval:()=>({value:()=>snapshot.resolution}),model:()=>({model:()=>({dataSources:()=>[study]})})};
                    window._exposed_chartWidgetCollection={getAll:()=>[widget]};
                    window.TradingViewApi={chart:()=>({getStudyById:()=>({getInputValues:()=>Object.entries(window.chartInputs).map(([id,value])=>({id,value})),setInputValues:(values)=>{window.chartWrites++;window.chartInputs=Object.fromEntries(values.map(x=>[x.id,x.value]));}})})};
                    </script>""".replace("SNAPSHOT", json.dumps(saved, ensure_ascii=True))
                    context.route("https://www.tradingview.com/chart/sync-smoke/", lambda route: route.fulfill(content_type="text/html", body=mock))
                    chart = context.new_page()
                    chart.goto("https://www.tradingview.com/chart/sync-smoke/")
                    dashboard = context.new_page()
                    errors = []
                    dashboard.on("pageerror", lambda error: errors.append(str(error)))
                    dashboard.goto(base)
                    expect(dashboard.locator("#browserConnection")).to_contain_text("1 个浏览器已连接", timeout=10000)
                    dashboard.locator("#discoverCharts").click()
                    expect(dashboard.locator("#browserSyncStatus")).to_contain_text("找到 1 个", timeout=15000)
                    dashboard.locator("#browserTarget").select_option(index=1)
                    dashboard.locator("#prices").fill("4600, 4620.5")
                    dashboard.locator("#syncDraft").click()
                    dashboard.get_by_role("button", name="同步并校验", exact=True).click()
                    expect(dashboard.locator("#browserSyncStatus")).to_contain_text("回读一致", timeout=15000)
                    assert chart.evaluate("window.chartInputs.in_5") == 4600
                    assert chart.evaluate("window.chartInputs.in_8") == 4620.5
                    assert chart.evaluate("window.chartInputs.in_10") is False
                    assert chart.evaluate("window.chartInputs.in_11") == 0
                    dashboard.get_by_role("button", name="把警报 7 的参数同步到图表").click()
                    dashboard.get_by_role("button", name="同步并校验", exact=True).click()
                    expect(dashboard.locator("#browserSyncStatus")).to_contain_text("回读一致", timeout=15000)
                    assert chart.evaluate("window.chartInputs.in_5") == 4700
                    assert chart.evaluate("window.chartWrites") == 2
                    assert len(tv.alerts) == 1
                    assert not errors, errors
                    output = ROOT / "data/browser-sync-qa"
                    output.mkdir(parents=True, exist_ok=True)
                    dashboard.locator("#browserSync").screenshot(path=str(output / "desktop.png"))
                    dashboard.set_viewport_size({"width": 390, "height": 844})
                    dashboard.locator("#browserSync").scroll_into_view_if_needed()
                    dashboard.locator("#browserSync").screenshot(path=str(output / "mobile.png"))
                    assert dashboard.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows"
                    # A document navigation invalidates the captured instance, even if IDs repeat.
                    chart.reload()
                    dashboard.locator("#syncDraft").click()
                    dashboard.get_by_role("button", name="同步并校验", exact=True).click()
                    expect(dashboard.locator("#browserSyncStatus")).to_have_class("form-help sync-error", timeout=15000)
                    assert chart.evaluate("window.chartWrites") == 0
                    print("PASS: real MV3 pairing, authenticated WebSocket, draft/replay readback, navigation guard, desktop/mobile UI")
                    print("Screenshots:", output)
                finally:
                    context.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            gc.collect()


if __name__ == "__main__":
    run()
