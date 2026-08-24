"use strict";

const elements = {
  form: document.querySelector("#createForm"),
  prices: document.querySelector("#prices"),
  alertSide: document.querySelector("#alertSide"),
  alertResolution: document.querySelector("#alertResolution"),
  validBars: document.querySelector("#validBars"),
  alertStartTime: document.querySelector("#alertStartTime"),
  alertEndTime: document.querySelector("#alertEndTime"),
  createButton: document.querySelector("#createButton"),
  refreshButton: document.querySelector("#refreshButton"),
  tableBody: document.querySelector("#alertTableBody"),
  emptyState: document.querySelector("#emptyState"),
  listSummary: document.querySelector("#listSummary"),
  loadingIndicator: document.querySelector("#loadingIndicator"),
  notice: document.querySelector("#notice"),
  tradingToggle: document.querySelector("#tradingToggle"),
  tradingEnabledStatus: document.querySelector("#tradingEnabledStatus"),
  mt5ConnectionStatus: document.querySelector("#mt5ConnectionStatus"),
  algoTradingStatus: document.querySelector("#algoTradingStatus"),
  accountStatus: document.querySelector("#accountStatus"),
  quoteStatus: document.querySelector("#quoteStatus"),
  positionStatus: document.querySelector("#positionStatus"),
  webhookUrl: document.querySelector("#webhookUrl"),
  copyWebhookButton: document.querySelector("#copyWebhookButton"),
  webhookMessage: document.querySelector("#webhookMessage"),
  copyWebhookMessageButton: document.querySelector("#copyWebhookMessageButton"),
  manualActionButtons: [...document.querySelectorAll("[data-trade-action]")],
  tradingHelp: document.querySelector("#tradingHelp"),
  signalTableBody: document.querySelector("#signalTableBody"),
  signalEmptyState: document.querySelector("#signalEmptyState"),
  signalSummary: document.querySelector("#signalSummary"),
  clearSignalsButton: document.querySelector("#clearSignalsButton"),
};
let pendingCreate = null;
let validBarsEdited = false;
let startTimeEdited = false;
const resolutionMinutes = {
  "1": 1,
  "2": 2,
  "3": 3,
  "5": 5,
  "15": 15,
  "30": 30,
  "60": 60,
  "120": 120,
  "240": 240,
};

async function apiRequest(path, options = {}) {
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch (_error) {
    // The status below provides a useful fallback for non-JSON server errors.
  }

  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "object" ? detail.message : detail;
    throw new Error(message || `请求失败（HTTP ${response.status}）`);
  }
  return data;
}

function createRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    const result = char === "x" ? value : (value & 0x3) | 0x8;
    return result.toString(16);
  });
}

async function copyText(text) {
  if (navigator.clipboard && globalThis.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) {
    throw new Error("复制失败");
  }
}

function showNotice(message, type = "success") {
  elements.notice.textContent = message;
  elements.notice.className = `notice ${type}`;
  elements.notice.hidden = false;
}

function hideNotice() {
  elements.notice.hidden = true;
}

function formatDate(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function toDateTimeLocalValue(date) {
  const offsetMs = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

function updateAlertEndTime() {
  const startMs = new Date(elements.alertStartTime.value).getTime();
  const bars = Number(elements.validBars.value);
  const minutes = resolutionMinutes[elements.alertResolution.value];
  if (!Number.isFinite(startMs) || !Number.isInteger(bars) || bars < 1 || !minutes) {
    elements.alertEndTime.textContent = "—";
    return;
  }
  elements.alertEndTime.textContent = formatDate(startMs + bars * minutes * 60_000);
}

function applyOneDayBarDefault() {
  const minutes = resolutionMinutes[elements.alertResolution.value];
  elements.validBars.value = String(Math.ceil((24 * 60) / minutes));
  updateAlertEndTime();
}

function initializeAlertForm() {
  elements.alertStartTime.value = toDateTimeLocalValue(new Date());
  applyOneDayBarDefault();
}

function appendCell(row, value, className = "") {
  const cell = document.createElement("td");
  cell.textContent = value;
  if (className) {
    cell.className = className;
  }
  row.append(cell);
  return cell;
}

function setStatus(element, text, isGood) {
  element.textContent = text;
  element.className = isGood ? "good" : "bad";
}

function actionLabel(action) {
  return {
    open_long: "开多",
    open_short: "开空",
    close_long: "平多",
    close_short: "平空",
    reverse_to_long: "反转为多",
    reverse_to_short: "反转为空",
  }[action] || action;
}

function signalStatusLabel(status) {
  return {
    queued: "等待执行",
    running: "执行中",
    success: "成功",
    failed: "失败",
    blocked: "已阻止",
    expired: "已过期",
    ignored: "已忽略",
  }[status] || status;
}

async function loadTradingViewSetup() {
  try {
    const data = await apiRequest("/api/tradingview/setup");
    elements.webhookUrl.textContent = data.webhook_url || `${window.location.origin}/api/webhooks/tradingview`;
    elements.webhookMessage.value = data.message;
  } catch (error) {
    elements.webhookMessage.value = "读取 TradingView 配置失败";
    showNotice(error.message, "error");
  }
}

async function loadTradingStatus() {
  try {
    const data = await apiRequest("/api/trading/status");
    const mt5 = data.mt5;
    setStatus(elements.tradingEnabledStatus, data.enabled ? "已启用" : "已停止", data.enabled);
    setStatus(elements.mt5ConnectionStatus, mt5.connected ? "已连接" : "未连接", mt5.connected);
    setStatus(elements.algoTradingStatus, mt5.terminal_trade_allowed ? "已允许" : "未开启", mt5.terminal_trade_allowed);
    setStatus(
      elements.accountStatus,
      mt5.demo_account ? `模拟 · ${mt5.login_masked || ""}` : "非模拟账户",
      mt5.demo_account,
    );
    elements.quoteStatus.textContent = mt5.bid && mt5.ask ? `${mt5.bid} / ${mt5.ask}` : "无报价";
    elements.quoteStatus.className = mt5.bid && mt5.ask ? "good" : "bad";
    elements.positionStatus.textContent = `多 ${mt5.owned_long_positions} / 空 ${mt5.owned_short_positions}`;
    elements.positionStatus.className = "";
    elements.webhookUrl.textContent = data.webhook_url || `${window.location.origin}/api/webhooks/tradingview`;
    elements.tradingToggle.checked = data.enabled;
    elements.tradingToggle.disabled = false;
    for (const button of elements.manualActionButtons) {
      button.disabled = !data.enabled;
    }
    elements.tradingHelp.textContent = mt5.error
      ? mt5.error
      : `固定手数 ${data.volume}，灾难保护止损距离 ${data.emergency_sl_distance}，程序重启后默认停止交易。`;
  } catch (error) {
    elements.tradingToggle.disabled = false;
    setStatus(elements.mt5ConnectionStatus, "读取失败", false);
    showNotice(error.message, "error");
  }
}

async function loadSignals() {
  try {
    const signals = await apiRequest("/api/trade-signals?limit=50");
    elements.signalTableBody.replaceChildren();
    elements.signalEmptyState.hidden = signals.length !== 0;
    elements.signalSummary.textContent = `最近 ${signals.length} 条记录`;
    elements.clearSignalsButton.disabled = !signals.some((signal) =>
      ["success", "failed", "blocked", "expired", "ignored"].includes(signal.status),
    );
    for (const signal of signals) {
      const row = document.createElement("tr");
      appendCell(row, formatDate(signal.received_at));
      appendCell(row, signal.source === "tradingview" ? "TradingView" : "手动");
      appendCell(row, actionLabel(signal.action));
      appendCell(row, signalStatusLabel(signal.status));
      appendCell(row, signal.symbol);
      const resultCell = appendCell(row, signal.error || "—", "name-cell");
      resultCell.title = signal.error || "";
      elements.signalTableBody.append(row);
    }
  } catch (error) {
    elements.clearSignalsButton.disabled = true;
    elements.signalSummary.textContent = "读取失败";
    showNotice(error.message, "error");
  }
}

async function clearSignals() {
  const confirmed = globalThis.confirm("确定清除所有已结束的交易信号记录吗？等待中和执行中的信号不会被清除。");
  if (!confirmed) {
    return;
  }
  elements.clearSignalsButton.disabled = true;
  try {
    const result = await apiRequest("/api/trade-signals/clear", { method: "POST" });
    showNotice(`已清除 ${result.cleared} 条交易信号记录`);
    await loadSignals();
  } catch (error) {
    showNotice(error.message, "error");
    await loadSignals();
  }
}

function renderAlerts(alerts) {
  elements.tableBody.replaceChildren();
  elements.emptyState.hidden = alerts.length !== 0;
  elements.listSummary.textContent = `共 ${alerts.length} 个本项目警报`;

  for (const alert of alerts) {
    const row = document.createElement("tr");

    const statusCell = document.createElement("td");
    const status = document.createElement("span");
    status.className = alert.active ? "status active" : "status";
    status.textContent = alert.active ? "运行中" : "已停用";
    statusCell.append(status);
    row.append(statusCell);

    const nameCell = appendCell(row, alert.name, "name-cell");
    nameCell.title = alert.name;
    appendCell(row, alert.symbol || "—");
    appendCell(row, alert.prices?.length ? alert.prices.join("、") : "—");
    appendCell(row, alert.side || "—");
    appendCell(
      row,
      alert.resolution ? `${alert.resolution} 分钟${alert.valid_bars ? ` / ${alert.valid_bars} 根` : ""}` : "—",
    );
    appendCell(row, formatDate(alert.start_time_ms));
    appendCell(row, formatDate(alert.end_time_ms));
    appendCell(row, formatDate(alert.create_time));
    appendCell(row, formatDate(alert.last_fire_time));

    const actionCell = document.createElement("td");
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "button button-danger";
    deleteButton.textContent = "删除";
    deleteButton.addEventListener("click", () => deleteAlert(alert, deleteButton));
    actionCell.append(deleteButton);
    row.append(actionCell);

    elements.tableBody.append(row);
  }
}

async function loadAlerts({ quiet = false } = {}) {
  elements.refreshButton.disabled = true;
  elements.loadingIndicator.hidden = false;
  if (!quiet) {
    elements.listSummary.textContent = "正在从 TradingView 同步……";
  }
  try {
    const alerts = await apiRequest("/api/alerts");
    renderAlerts(alerts);
  } catch (error) {
    elements.listSummary.textContent = "同步失败";
    showNotice(error.message, "error");
  } finally {
    elements.refreshButton.disabled = false;
    elements.loadingIndicator.hidden = true;
  }
}

async function createAlert(event) {
  event.preventDefault();
  hideNotice();
  if (!startTimeEdited) {
    elements.alertStartTime.value = toDateTimeLocalValue(new Date());
    updateAlertEndTime();
  }
  const prices = elements.prices.value.trim();
  if (!prices) {
    showNotice("请输入至少一个价格", "error");
    elements.prices.focus();
    return;
  }
  const startTimeMs = new Date(elements.alertStartTime.value).getTime();
  const validBars = Number(elements.validBars.value);
  if (!Number.isFinite(startTimeMs)) {
    showNotice("请选择有效的开始时间", "error");
    return;
  }
  if (!Number.isInteger(validBars) || validBars < 1 || validBars > 10000) {
    showNotice("有效 K 线数必须是 1～10000 的整数", "error");
    return;
  }

  const alertConfig = {
    prices,
    side: elements.alertSide.value,
    valid_bars: validBars,
    start_time_ms: startTimeMs,
    resolution: elements.alertResolution.value,
  };
  const requestKey = JSON.stringify(alertConfig);

  elements.createButton.disabled = true;
  elements.createButton.textContent = "创建中……";
  if (!pendingCreate || pendingCreate.key !== requestKey) {
    pendingCreate = { key: requestKey, requestId: createRequestId() };
  }
  try {
    const result = await apiRequest("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...alertConfig, request_id: pendingCreate.requestId }),
    });
    const message = result.created
      ? `警报创建成功：${result.alert.side}，${result.alert.resolution} 分钟，${result.alert.valid_bars} 根 K 线`
      : "该请求对应的警报已经存在，未重复创建";
    showNotice(message);
    pendingCreate = null;
    elements.prices.value = "";
    await loadAlerts({ quiet: true });
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    elements.createButton.disabled = false;
    elements.createButton.textContent = "创建警报";
  }
}

async function deleteAlert(alert, button) {
  const confirmed = globalThis.confirm(`确定删除警报 ${alert.name} 吗？`);
  if (!confirmed) {
    return;
  }
  hideNotice();
  button.disabled = true;
  button.textContent = "删除中……";
  try {
    await apiRequest(`/api/alerts/${encodeURIComponent(alert.alert_id)}`, { method: "DELETE" });
    showNotice("警报已删除");
    await loadAlerts({ quiet: true });
  } catch (error) {
    showNotice(error.message, "error");
    button.disabled = false;
    button.textContent = "删除";
  }
}

async function toggleTrading(enabled) {
  hideNotice();
  elements.tradingToggle.disabled = true;
  try {
    const result = await apiRequest(`/api/trading/${enabled ? "enable" : "disable"}`, { method: "POST" });
    showNotice(result.message);
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    await loadTradingStatus();
  }
}

async function submitManualAction(button) {
  const action = button.dataset.tradeAction;
  const confirmed = globalThis.confirm(`确定执行“${actionLabel(action)}”吗？将使用程序配置的固定手数操作模拟账户。`);
  if (!confirmed) {
    return;
  }
  button.disabled = true;
  try {
    const result = await apiRequest(`/api/mt5/actions/${action}`, { method: "POST" });
    showNotice(`交易任务已提交：${result.signal_id}`);
    await loadSignals();
    setTimeout(() => Promise.all([loadTradingStatus(), loadSignals()]), 1200);
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    await loadTradingStatus();
  }
}

async function refreshDashboard() {
  await Promise.all([loadAlerts({ quiet: true }), loadTradingStatus(), loadTradingViewSetup(), loadSignals()]);
}

elements.form.addEventListener("submit", createAlert);
elements.alertResolution.addEventListener("change", () => {
  if (!validBarsEdited) {
    applyOneDayBarDefault();
  } else {
    updateAlertEndTime();
  }
});
elements.validBars.addEventListener("input", () => {
  validBarsEdited = true;
  updateAlertEndTime();
});
elements.alertStartTime.addEventListener("input", () => {
  startTimeEdited = true;
  updateAlertEndTime();
});
elements.refreshButton.addEventListener("click", () => {
  hideNotice();
  refreshDashboard();
});
elements.tradingToggle.addEventListener("change", () => toggleTrading(elements.tradingToggle.checked));
elements.copyWebhookButton.addEventListener("click", async () => {
  try {
    await copyText(elements.webhookUrl.textContent);
    showNotice("Webhook URL 已复制");
  } catch (_error) {
    showNotice("无法自动复制，请手动选择 URL", "error");
  }
});
elements.copyWebhookMessageButton.addEventListener("click", async () => {
  try {
    await copyText(elements.webhookMessage.value);
    showNotice("警报消息 JSON 已复制");
  } catch (_error) {
    showNotice("无法自动复制，请手动选择消息 JSON", "error");
  }
});
elements.clearSignalsButton.addEventListener("click", clearSignals);
for (const button of elements.manualActionButtons) {
  button.addEventListener("click", () => submitManualAction(button));
}

initializeAlertForm();
refreshDashboard();
setInterval(() => {
  if (document.visibilityState === "visible") {
    refreshDashboard();
  }
}, 30_000);
