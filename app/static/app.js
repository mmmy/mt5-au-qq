"use strict";

const elements = {
  form: document.querySelector("#createForm"),
  prices: document.querySelector("#prices"),
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
  manualActionButtons: [...document.querySelectorAll("[data-trade-action]")],
  tradingHelp: document.querySelector("#tradingHelp"),
  signalTableBody: document.querySelector("#signalTableBody"),
  signalEmptyState: document.querySelector("#signalEmptyState"),
  signalSummary: document.querySelector("#signalSummary"),
  clearSignalsButton: document.querySelector("#clearSignalsButton"),
};
let pendingCreate = null;

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
    appendCell(row, alert.resolution ? `${alert.resolution} 分钟` : "—");
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
  const prices = elements.prices.value.trim();
  if (!prices) {
    showNotice("请输入至少一个价格", "error");
    elements.prices.focus();
    return;
  }

  elements.createButton.disabled = true;
  elements.createButton.textContent = "创建中……";
  if (!pendingCreate || pendingCreate.prices !== prices) {
    pendingCreate = { prices, requestId: createRequestId() };
  }
  try {
    const result = await apiRequest("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prices, request_id: pendingCreate.requestId }),
    });
    const message = result.created
      ? `警报创建成功，价格：${result.prices.join("、")}`
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
  await Promise.all([loadAlerts({ quiet: true }), loadTradingStatus(), loadSignals()]);
}

elements.form.addEventListener("submit", createAlert);
elements.refreshButton.addEventListener("click", () => {
  hideNotice();
  refreshDashboard();
});
elements.tradingToggle.addEventListener("change", () => toggleTrading(elements.tradingToggle.checked));
elements.copyWebhookButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(elements.webhookUrl.textContent);
    showNotice("Webhook URL 已复制");
  } catch (_error) {
    showNotice("无法自动复制，请手动选择 URL", "error");
  }
});
elements.clearSignalsButton.addEventListener("click", clearSignals);
for (const button of elements.manualActionButtons) {
  button.addEventListener("click", () => submitManualAction(button));
}

refreshDashboard();
setInterval(() => {
  if (document.visibilityState === "visible") {
    refreshDashboard();
  }
}, 30_000);
