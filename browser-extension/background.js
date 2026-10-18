import { chartAdapter } from "./adapter.js";

let socket, heartbeat, connecting = false, queue = Promise.resolve();
const TV_PATTERNS = ["https://*.tradingview.com/chart/*", "https://tradingview.com/chart/*"];
const setStatus = (status) => chrome.storage.local.set({ status });
const send = (value) => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value)); };
function backendURL(raw) {
  const url = new URL(raw);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("后端地址必须是网站根地址，例如 http://127.0.0.1");
  }
  if (url.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) {
    throw new Error("远程后端请使用 HTTPS；服务器本机可用 http://127.0.0.1");
  }
  return url.origin;
}
async function inject(tabId, command, documentId) {
  const results = await chrome.scripting.executeScript({
    target: documentId ? { tabId, documentIds: [documentId] } : { tabId },
    world: "MAIN", func: chartAdapter, args: [command],
  });
  const frame = results.find((r) => r.frameId === 0);
  if (!frame?.result) throw new Error("图表已关闭或页面接口不可用，请重新读取图表");
  const result = frame.result;
  if (result.targets) result.targets = result.targets.map((t) => ({ ...t, tab_id: tabId, document_id: frame.documentId }));
  if (result.readback) result.readback = { ...result.readback, tab_id: tabId, document_id: frame.documentId };
  return result;
}
async function execute(command) {
  if (!/^[0-9a-f-]{36}$/i.test(command.id) || !["discover", "apply"].includes(command.kind)) return;
  const stored = await chrome.storage.local.get("journal");
  const journal = stored.journal || {};
  const previous = journal[command.id];
  if (previous) {
    send({ type: "result", id: command.id, result: previous.result || {
      ok: false, message: "扩展曾开始处理此命令但未留下确认结果，禁止重复写入；请重新读取图表" } });
    return;
  }
  const entries = Object.entries(journal).slice(-199);
  const next = Object.fromEntries(entries);
  next[command.id] = { started: Date.now() };
  await chrome.storage.local.set({ journal: next });
  let result;
  try {
    if (!Number.isFinite(command.expires_at) || command.expires_at * 1000 < Date.now() || command.expires_at * 1000 > Date.now() + 60000) {
      throw new Error("同步命令已过期，请重新读取图表后重试");
    }
    if (command.kind === "discover") {
      const tabs = await chrome.tabs.query({ url: TV_PATTERNS });
      const targets = [], errors = [];
      for (const tab of tabs) {
        try {
          const response = await inject(tab.id, command);
          targets.push(...response.targets.map((t) => ({ ...t, title: tab.title || "TradingView", url: tab.url })));
        } catch (error) { errors.push(`标签页 ${tab.id}：${error.message}`); }
      }
      result = { ok: true, targets, message: targets.length ? `找到 ${targets.length} 个黄金价格策略实例` :
        "未找到黄金价格策略，请打开 TradingView 图表并等待加载", warnings: errors };
    } else {
      const tab = await chrome.tabs.get(command.target.tab_id);
      const url = new URL(tab.url);
      if (url.protocol !== "https:" || !(url.hostname === "tradingview.com" || url.hostname.endsWith(".tradingview.com")) || !url.pathname.startsWith("/chart/")) {
        throw new Error("目标标签页已跳转，未修改参数");
      }
      result = await inject(tab.id, command, command.target.document_id);
    }
  } catch (error) { result = { ok: false, message: String(error.message).slice(0, 500) }; }
  next[command.id].result = result;
  await chrome.storage.local.set({ journal: next });
  send({ type: "result", id: command.id, result });
}
async function connect() {
  if (connecting || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
  connecting = true;
  try {
    const { config } = await chrome.storage.local.get("config");
    if (!config?.token) { await setStatus("尚未配对"); return; }
    const origin = backendURL(config.backend);
    if (!await chrome.permissions.contains({ origins: [`${origin}/*`] })) {
      await setStatus("后端访问权限已撤销，请重新配对"); return;
    }
    const url = new URL(`${origin}/api/browser-sync/ws`);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const current = new WebSocket(url.href);
    const authTimeout = setTimeout(() => current.close(), 15000);
    socket = current;
    await setStatus("正在连接……");
    current.onopen = () => current.send(JSON.stringify({ type: "auth", client_id: config.client_id, token: config.token }));
    current.onmessage = (event) => {
      let message; try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === "ready") {
        clearTimeout(authTimeout);
        setStatus("已连接，等待同步指令");
        clearInterval(heartbeat);
        heartbeat = setInterval(() => send({ type: "ping" }), 20000);
      } else if (message.type === "command") {
        queue = queue.then(() => execute(message)).catch((e) => setStatus(`处理失败：${e.message}`));
      }
    };
    current.onclose = async (event) => {
      clearTimeout(authTimeout);
      if (socket !== current) return;
      socket = undefined;
      clearInterval(heartbeat);
      if (event.code === 4001) {
        await chrome.storage.local.remove("config");
        await setStatus("配对已失效，请重新配对");
      } else {
        await setStatus(event.code === 4009 ? "此配对已有连接，请勿复制扩展凭据" : "连接断开，自动重连中；未确认的写入不会重发");
        setTimeout(connect, 5000);
      }
    };
    current.onerror = () => setStatus("连接失败，请检查后端地址与服务状态");
  } catch (error) { await setStatus(error.message); }
  finally { connecting = false; }
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;
  (async () => {
    if (message.type === "pair") {
      const backend = backendURL(message.backend);
      const response = await fetch(`${backend}/api/browser-sync/pair`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: message.code, label: message.label || "Chrome" }),
        signal: AbortSignal.timeout(10000),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "配对失败");
      if (socket) { socket.onclose = null; socket.close(); socket = undefined; clearInterval(heartbeat); }
      await chrome.storage.local.set({ config: { backend, ...data }, journal: {} });
      await connect();
    } else if (message.type === "connect") await connect();
    else if (message.type === "forget") {
      await chrome.storage.local.remove(["config", "journal"]);
      if (socket) { socket.onclose = null; socket.close(); socket = undefined; clearInterval(heartbeat); }
      await setStatus("已断开并删除本机凭据；可在管理页撤销此浏览器");
    }
    reply({ ok: true });
  })().catch((error) => reply({ ok: false, message: error.message }));
  return true;
});
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "reconnect") connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
chrome.alarms.create("reconnect", { periodInMinutes: 0.5 });
connect();
