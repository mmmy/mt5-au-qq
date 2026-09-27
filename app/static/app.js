"use strict";

const elements = {
  form: document.querySelector("#createForm"),
  prices: document.querySelector("#prices"),
  alertSide: document.querySelector("#alertSide"),
  validHours: document.querySelector("#validHours"),
  alertEndTime: document.querySelector("#alertEndTime"),
  createButton: document.querySelector("#createButton"),
  refreshButton: document.querySelector("#refreshButton"),
  tableBody: document.querySelector("#alertTableBody"),
  emptyState: document.querySelector("#emptyState"),
  listSummary: document.querySelector("#listSummary"),
  loadingIndicator: document.querySelector("#loadingIndicator"),
  notice: document.querySelector("#notice"),
  webhookUrl: document.querySelector("#webhookUrl"),
  copyWebhookButton: document.querySelector("#copyWebhookButton"),
  openWebhookMessageButton: document.querySelector("#openWebhookMessageButton"),
  webhookMessageDialog: document.querySelector("#webhookMessageDialog"),
  webhookMessage: document.querySelector("#webhookMessage"),
  copyWebhookMessageButton: document.querySelector("#copyWebhookMessageButton"),
  closeWebhookDialogButtons: [...document.querySelectorAll("[data-close-webhook-dialog]")],
  signalTableBody: document.querySelector("#signalTableBody"),
  signalEmptyState: document.querySelector("#signalEmptyState"),
  signalSummary: document.querySelector("#signalSummary"),
  clearSignalsButton: document.querySelector("#clearSignalsButton"),
};
let pendingCreate = null;
let createDraft = null;
let creatingAlert = false;
const createDialog = document.querySelector("#createAlertDialog");
const confirmCreateButton = document.querySelector("#confirmCreateButton");
const createError = document.querySelector("#createAlertError");
let signalSettings = null;
let savingSignalSettings = false;
let refreshingDashboard = false;
let monitoring = false;
let tradingClients = [];
let alertFormSettings = { side: "自动", validHours: 24 };
const signalTypes = [
  ["fractal", "分型"],
  ["pinbar", "Pinbar"],
  ["pinbar_more", "Pinbar More"],
  ["insidebar", "Inside Bar"],
  ["engulfing", "吞没"],
];
const signalDistances = [
  ["price_delta", "信号价格区间偏离"],
  ["entry_delta", "开仓距离"],
  ["stop_delta", "止损价格距离"],
];
const signalDialog = document.querySelector("#signalSettingsDialog");
const signalFields = document.querySelector("#signalSettingsFields");
const signalSummary = document.querySelector("#signalSettingsSummary");
const signalError = document.querySelector("#signalSettingsError");
const signalSaveButton = document.querySelector("#saveSignalSettingsButton");

function renderSignalSummary() {
  signalSummary.replaceChildren();
  for (const [key, name] of signalTypes) {
    const item = document.createElement("span");
    const label = document.createElement("strong");
    label.textContent = name;
    item.append(label);
    for (const [field, title] of [["minute_5", "5 分钟"], ["minute_2", "2 分钟"]]) {
      const state = document.createElement("span");
      const enabled = signalSettings[key][field];
      state.textContent = `${title} ${enabled ? "开" : "关"}`;
      state.className = enabled ? "signal-enabled" : "signal-disabled";
      item.append(state);
    }
    signalSummary.append(item);
  }
  const distances = document.createElement("div");
  distances.className = "signal-distance-summary";
  for (const [key, name] of signalDistances) {
    const item = document.createElement("span");
    const value = document.createElement("strong");
    value.textContent = String(signalSettings[key]);
    item.append(document.createTextNode(`${name} `), value);
    distances.append(item);
  }
  signalSummary.append(distances);
}

async function loadSignalSettings() {
  try {
    signalSettings = await apiRequest("/api/alerts/signal-settings");
    renderSignalSummary();
  } catch (error) {
    signalSettings = null;
    signalSummary.textContent = "信号配置读取失败，请点击“警报配置”重试。";
    showNotice(error.message, "error");
  }
}

async function openSignalSettings() {
  if (!signalSettings) await loadSignalSettings();
  if (!signalSettings) return;
  elements.alertSide.value = alertFormSettings.side;
  elements.validHours.value = alertFormSettings.validHours;
  signalFields.replaceChildren();
  signalError.hidden = true;
  const distances = document.createElement("div");
  distances.className = "signal-distance-fields";
  for (const [key, name] of signalDistances) {
    const label = document.createElement("label");
    label.htmlFor = `signal-${key}`;
    label.textContent = name;
    const input = document.createElement("input");
    input.id = `signal-${key}`;
    input.name = key;
    input.type = "number";
    input.min = "0";
    input.step = "any";
    input.required = true;
    input.value = signalSettings[key];
    distances.append(label, input);
  }
  signalFields.append(distances);
  for (const [key, name] of signalTypes) {
    const group = document.createElement("fieldset");
    group.className = "signal-settings-row";
    const legend = document.createElement("legend");
    legend.textContent = name;
    group.append(legend);
    for (const [field, title] of [["minute_5", "5 分钟"], ["minute_2", "2 分钟"]]) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = `${key}.${field}`;
      input.checked = signalSettings[key][field];
      label.append(input, document.createTextNode(title));
      group.append(label);
    }
    signalFields.append(group);
  }
  signalDialog.showModal();
}

async function saveSignalSettings(event) {
  event.preventDefault();
  if (savingSignalSettings) return;
  const validHours = elements.validHours.valueAsNumber;
  if (!Number.isFinite(validHours) || validHours <= 0 || Math.ceil(validHours * 60 / Number(alertResolution)) > 10000) {
    signalError.textContent = "有效时长必须大于 0，且不能超过 333.33 小时。";
    signalError.hidden = false;
    elements.validHours.focus();
    return;
  }
  const alertDraft = { side: elements.alertSide.value, validHours };
  const draft = Object.fromEntries(signalTypes.map(([key]) => [key, {
    minute_5: signalFields.querySelector(`[name="${key}.minute_5"]`).checked,
    minute_2: signalFields.querySelector(`[name="${key}.minute_2"]`).checked,
  }]));
  for (const [key, name] of signalDistances) {
    const input = signalFields.querySelector(`[name="${key}"]`);
    const value = input.valueAsNumber;
    if (!Number.isFinite(value) || value < 0) {
      signalError.textContent = `${name}必须是大于或等于 0 的数值。`;
      signalError.hidden = false;
      input.focus();
      return;
    }
    draft[key] = value;
  }
  savingSignalSettings = true;
  signalError.hidden = true;
  signalSaveButton.disabled = true;
  signalSaveButton.textContent = "保存中……";
  for (const control of signalDialog.querySelectorAll("input, select")) control.disabled = true;
  try {
    signalSettings = await apiRequest("/api/alerts/signal-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    alertFormSettings = alertDraft;
    renderAlertFormSummary();
    updateAlertEndTime();
    renderSignalSummary();
    signalDialog.close();
    showNotice("警报配置已保存，下次创建警报时使用。已有警报不变。");
  } catch (error) {
    signalError.textContent = `${error.message}。修改尚未保存，请重试。`;
    signalError.hidden = false;
  } finally {
    savingSignalSettings = false;
    signalSaveButton.disabled = false;
    signalSaveButton.textContent = "保存配置";
    for (const control of signalDialog.querySelectorAll("input, select")) control.disabled = false;
  }
}
const alertResolution = "2";

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
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join("；")
      : typeof detail === "object" ? detail?.message : detail;
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

function openWebhookMessageDialog() {
  document.getElementById("webhookCopyError").hidden = true;
  elements.webhookMessageDialog.showModal();
}

function closeWebhookMessageDialog() {
  elements.webhookMessageDialog.close();
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

function formatHours(value) {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
}

function updateAlertEndTime() {
  const hours = alertFormSettings.validHours;
  const minutes = Number(alertResolution);
  if (!Number.isFinite(hours) || hours <= 0 || !minutes) {
    elements.alertEndTime.textContent = "—";
    return;
  }
  const bars = Math.ceil((hours * 60) / minutes);
  if (bars > 10000) {
    elements.alertEndTime.textContent = "—";
    return;
  }
  const intervalMs = minutes * 60_000;
  const alignedStartMs = Math.floor(Date.now() / intervalMs) * intervalMs;
  elements.alertEndTime.textContent = formatDate(alignedStartMs + bars * intervalMs);
}

function initializeAlertForm() {
  renderAlertFormSummary();
  updateAlertEndTime();
  updatePriceFeedback();
}

function renderAlertFormSummary() {
  document.getElementById("alertConfigSummary").textContent =
    `开仓方向：${alertFormSettings.side} · 有效时长：${formatHours(alertFormSettings.validHours)} 小时`;
}

function updatePriceFeedback() {
  const parts = elements.prices.value.trim().split(/[\s,，、;；]+/).filter(Boolean);
  const seen = new Set();
  let error = parts.length > 20 ? "最多输入 20 个价格，请减少价格数量。" : "";
  for (const part of parts) {
    if (!/^\d+(?:\.\d+)?$/.test(part) || !Number.isFinite(Number(part)) || Number(part) <= 0) {
      error = `“${part}”不是有效价格，请输入大于 0 的数字。`;
      break;
    }
    const [integer, fraction = ""] = part.split(".");
    const normalized = `${integer.replace(/^0+(?=\d)/, "")}.${fraction.replace(/0+$/, "")}`;
    if (seen.has(normalized)) {
      error = `价格 ${part} 重复，请删除重复项。`;
      break;
    }
    seen.add(normalized);
  }
  const feedback = document.getElementById("priceFeedback");
  feedback.textContent = error || `已输入 ${parts.length} / 20 个价格${parts.length ? " · 格式检查通过" : ""}`;
  feedback.classList.toggle("bad", Boolean(error));
  elements.prices.setCustomValidity(error);
  elements.prices.setAttribute("aria-invalid", String(Boolean(error)));
  return !error;
}

function clientReady(client, demoOnly) {
  const mt5 = client.mt5;
  return client.enabled && !mt5.error && mt5.connected && mt5.terminal_trade_allowed &&
    mt5.account_trade_allowed && mt5.account_trade_expert && mt5.symbol_available &&
    (!demoOnly || mt5.demo_account);
}

function editingClientVolume() {
  return [...document.querySelectorAll(".client-volume-form")].some((form) =>
    form.contains(document.activeElement) || form.dataset.dirty === "true" || form.querySelector("input").disabled);
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

function labelTableRow(row, table) {
  const headings = table.querySelectorAll("thead th");
  [...row.cells].forEach((cell, index) => { cell.dataset.label = headings[index].textContent; });
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
    partial: "部分成功",
    failed: "失败",
    blocked: "已阻止",
    expired: "已过期",
    ignored: "已忽略",
  }[status] || status;
}

function accountTradeModeLabel(mode) {
  return {
    demo: "Demo 模式",
    contest: "Contest / 考核模式",
    real: "Real 技术模式",
    unknown: "账户模式未知",
  }[mode] || "账户模式未知";
}

async function loadTradingViewSetup() {
  try {
    const data = await apiRequest("/api/tradingview/setup");
    elements.webhookUrl.textContent = data.webhook_url || `${window.location.origin}/api/webhooks/tradingview`;
    elements.webhookMessage.value = data.message;
    elements.copyWebhookButton.disabled = false;
    elements.copyWebhookMessageButton.disabled = false;
  } catch (error) {
    elements.webhookMessage.value = "读取 TradingView 配置失败";
    elements.copyWebhookMessageButton.disabled = true;
    showNotice(error.message, "error");
  }
}

function renderMt5Clients(clients) {
  const container = document.getElementById("mt5Clients");
  container.replaceChildren();
  for (const client of clients) {
    const row = document.createElement("tr");
    const mt5 = client.mt5;
    appendCell(row, `客户端 ${client.client_id}`, "client-name");
    const connection = appendCell(row, mt5.connected ? "已连接" : "未连接", mt5.connected ? "good" : "bad");
    connection.title = mt5.error || (mt5.connected ? "MT5 已连接" : "MT5 未连接");
    if (mt5.error) {
      const error = document.createElement("details");
      error.className = "client-error";
      const summary = document.createElement("summary");
      summary.textContent = "查看原因";
      const detail = document.createElement("p");
      detail.textContent = `${mt5.error}。请检查本机 MT5 终端是否已启动并登录，再刷新状态。`;
      error.append(summary, detail);
      connection.append(error);
    }
    const mode = accountTradeModeLabel(mt5.account_trade_mode);
    const account = `${mode} · ${mt5.server || "未知服务器"} · ${mt5.login_masked || "未知账号"}`;
    const accountCell = appendCell(row, account, "client-account");
    accountCell.title = `${account}。账户模式由 MT5 返回，资金性质以服务商说明为准。`;
    appendCell(row, mt5.symbol);
    const volumeCell = document.createElement("td");
    const volumeForm = document.createElement("form");
    volumeForm.className = "client-volume-form";
    const volumeInput = document.createElement("input");
    volumeInput.type = "number";
    volumeInput.value = client.volume;
    volumeInput.min = mt5.volume_min || "0.00000001";
    volumeInput.step = mt5.volume_step || "any";
    if (mt5.volume_max != null) volumeInput.max = mt5.volume_max;
    volumeInput.required = true;
    volumeInput.addEventListener("input", () => {
      volumeForm.dataset.dirty = String(volumeInput.value !== String(client.volume));
      volumeFeedback.textContent = volumeForm.dataset.dirty === "true" ? "尚未保存 · 状态更新暂停" : "";
      volumeFeedback.classList.remove("bad", "good");
    });
    volumeInput.setAttribute("aria-label", `客户端 ${client.client_id} 开仓手数`);
    const saveVolume = document.createElement("button");
    saveVolume.className = "button button-secondary";
    saveVolume.type = "submit";
    saveVolume.textContent = "保存";
    saveVolume.setAttribute("aria-label", `保存客户端 ${client.client_id} 开仓手数`);
    const volumeFeedback = document.createElement("span");
    volumeFeedback.className = "client-volume-feedback";
    volumeFeedback.setAttribute("aria-live", "polite");
    volumeForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const volume = volumeInput.valueAsNumber;
      if (!Number.isFinite(volume) || volume <= 0 || (volumeInput.max && volume > Number(volumeInput.max))) {
        volumeFeedback.textContent = "请输入范围内的正数";
        volumeFeedback.classList.add("bad");
        volumeInput.focus();
        return;
      }
      volumeInput.disabled = true;
      saveVolume.disabled = true;
      saveVolume.textContent = "保存中";
      volumeFeedback.classList.remove("bad", "good");
      try {
        const result = await apiRequest(`/api/trading/clients/${encodeURIComponent(client.client_id)}/volume`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ volume }),
        });
        volumeInput.value = result.volume;
        client.volume = result.volume;
        volumeForm.dataset.dirty = "false";
        volumeFeedback.textContent = `已保存 ${result.volume} 手`;
        volumeFeedback.classList.add("good");
        showNotice(result.message);
      } catch (error) {
        volumeFeedback.textContent = `未保存：${error.message}`;
        volumeFeedback.classList.add("bad");
      } finally {
        volumeInput.disabled = false;
        saveVolume.disabled = false;
        saveVolume.textContent = "保存";
      }
    });
    volumeForm.append(volumeInput, saveVolume, volumeFeedback);
    volumeCell.append(volumeForm);
    row.append(volumeCell);
    appendCell(row, `多 ${mt5.owned_long_positions} / 空 ${mt5.owned_short_positions}`);
    appendCell(row, mt5.terminal_trade_allowed ? "已开启" : "未开启", mt5.terminal_trade_allowed ? "good" : "bad");
    const control = document.createElement("td");
    const label = document.createElement("label");
    label.className = "switch";
    label.title = "独立控制该客户端的程序交易，停止不会自动平仓";
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.setAttribute("role", "switch");
    toggle.dataset.clientId = client.client_id;
    toggle.checked = client.enabled;
    toggle.setAttribute("aria-label", `客户端 ${client.client_id} 交易开关`);
    toggle.addEventListener("change", async () => {
      toggle.disabled = true;
      try {
        const operation = toggle.checked ? "enable" : "disable";
        const result = await apiRequest(`/api/trading/clients/${encodeURIComponent(client.client_id)}/${operation}`, { method: "POST" });
        showNotice(result.message);
      } catch (error) {
        showNotice(error.message, "error");
      }
      await loadTradingStatus();
    });
    const slider = document.createElement("span");
    slider.className = "switch-slider";
    slider.setAttribute("aria-hidden", "true");
    label.append(toggle, slider);
    control.append(label);
    row.append(control);
    const actions = document.createElement("td");
    const more = document.createElement("button");
    more.type = "button";
    more.className = "button button-secondary client-more-button";
    more.textContent = "更多";
    more.setAttribute("aria-label", `客户端 ${client.client_id} 更多`);
    more.setAttribute("aria-expanded", "false");
    const menu = document.createElement("div");
    menu.id = `client-actions-${client.client_id}`;
    menu.className = "client-actions-popover";
    menu.setAttribute("popover", "auto");
    menu.setAttribute("role", "region");
    menu.setAttribute("aria-label", `客户端 ${client.client_id} 手动平仓`);
    more.setAttribute("aria-controls", menu.id);
    const title = document.createElement("h3");
    title.textContent = `客户端 ${client.client_id} · 手动平仓`;
    menu.append(title);
    const buttons = document.createElement("div");
    buttons.className = "client-manual-actions";
    for (const action of ["close_long", "close_short"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button action-button";
      button.dataset.tradeAction = action;
      button.dataset.actionClientId = client.client_id;
      button.textContent = actionLabel(action);
      button.disabled = !client.enabled;
      button.addEventListener("click", () => submitManualAction(button));
      buttons.append(button);
    }
    menu.append(buttons);
    menu.addEventListener("toggle", () => {
      more.setAttribute("aria-expanded", String(menu.matches(":popover-open")));
    });
    more.addEventListener("click", () => {
      if (menu.matches(":popover-open")) { menu.hidePopover(); return; }
      menu.showPopover();
      const rect = more.getBoundingClientRect();
      menu.style.left = `${Math.max(12, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 12))}px`;
      menu.style.top = `${Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - menu.offsetHeight - 12))}px`;
    });
    actions.append(more, menu);
    row.append(actions);
    labelTableRow(row, document.querySelector(".client-table"));
    container.append(row);
  }
}

async function loadTradingStatus() {
  try {
    const data = await apiRequest("/api/trading/status");
    // Older single-client responses still use the same compact row.
    const clients = data.clients?.length ? data.clients : [
      { client_id: "A", enabled: data.enabled, volume: data.volume, max_volume: data.max_volume, mt5: data.mt5 },
    ];
    if (!editingClientVolume() && !document.querySelector(".client-actions-popover:popover-open")) renderMt5Clients(clients);
    else {
      for (const toggle of document.querySelectorAll("[data-client-id]")) {
        const client = clients.find((item) => item.client_id === toggle.dataset.clientId);
        if (client) toggle.checked = client.enabled;
        toggle.disabled = false;
      }
    }
    tradingClients = clients;
    const ready = clients.filter((client) => clientReady(client, data.demo_only)).length;
    const enabled = clients.filter((client) => client.enabled).length;
    const execution = document.getElementById("executionStatus");
    execution.className = `execution-status ${enabled > 0 && ready === 0 ? "warning" : ""}`;
    execution.textContent = enabled > 0
      ? ready === 0 ? "已启用的客户端尚未就绪。请检查连接、算法交易权限和品种。"
        : `${ready} / ${enabled} 个已启用账户就绪${ready < enabled ? " · 部分账户未就绪，请检查下方状态" : " · 等待交易信号"}`
      : "所有客户端均已停止 · 在下方单独启用所需客户端 · 已有持仓不会自动平仓";
    elements.webhookUrl.textContent = data.webhook_url || `${window.location.origin}/api/webhooks/tradingview`;
    document.getElementById("lastUpdated").textContent = `交易状态 ${new Date().toLocaleTimeString("zh-CN", {hour12: false})} · 每 15 秒更新`;
    updateManualActionState();
  } catch (error) {
    tradingClients = [];
    for (const button of document.querySelectorAll("[data-trade-action]")) button.disabled = true;
    document.getElementById("executionStatus").textContent = "交易状态读取失败，请刷新重试。";
    document.getElementById("executionStatus").className = "execution-status warning";
    document.getElementById("lastUpdated").textContent = "交易状态更新失败";
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
      ["success", "partial", "failed", "blocked", "expired", "ignored"].includes(signal.status),
    );
    for (const signal of signals) {
      const row = document.createElement("tr");
      appendCell(row, formatDate(signal.received_at));
      appendCell(row, signal.source === "tradingview" ? "TradingView" : "手动");
      appendCell(row, actionLabel(signal.action));
      appendCell(row, signalStatusLabel(signal.status), `signal-status signal-status-${signal.status}`);
      appendCell(row, signal.symbol);
      appendCell(row, signal.trigger_price || "—");
      const result = signal.executions?.length
        ? signal.executions.map((item) => `${item.client_id} (${item.symbol}): ${signalStatusLabel(item.status)}${item.error ? ` · ${item.error}` : ""}`).join("；")
        : signal.error || "—";
      const resultCell = appendCell(row, result, "name-cell");
      resultCell.title = result;
      labelTableRow(row, elements.signalTableBody.closest("table"));
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

function openAlertParameters(alert) {
  const dialog = document.getElementById("alertParametersDialog");
  const content = document.getElementById("alertParametersContent");
  renderAlertParameters(content, alert);
  dialog.showModal();
}

function renderAlertParameters(content, alert, { preview = false } = {}) {
  content.replaceChildren();
  if (!preview) {
    const summary = document.createElement("p");
    summary.className = "form-help";
    summary.textContent = "该警报创建时保存的参数，后续配置修改不会改变此记录。";
    content.append(summary);
  }
  const fields = document.createElement("dl");
  fields.className = "parameter-grid";
  const effectiveHours = alert.valid_bars && Number(alert.resolution)
    ? formatHours(alert.valid_bars * Number(alert.resolution) / 60) : null;
  for (const [name, value] of [
    ["警报 ID", alert.alert_id],
    ["品种", alert.symbol],
    ["价格", alert.prices?.join("、")],
    ["开仓方向", alert.side],
    ["有效时长", alert.valid_hours ? `${alert.valid_hours} 小时` : effectiveHours ? `${effectiveHours} 小时` : null],
    ["周期", alert.resolution ? `${alert.resolution} 分钟` : null],
    [preview ? "预计开始时间" : "开始时间", alert.start_time_ms ? formatDate(alert.start_time_ms) : null],
    [preview ? "预计结束时间" : "结束时间", alert.end_time_ms ? formatDate(alert.end_time_ms) : null],
  ]) {
    if (preview && (name === "警报 ID" || name === "品种")) continue;
    const label = document.createElement("dt");
    label.textContent = name;
    const text = document.createElement("dd");
    text.textContent = value ?? "未记录";
    fields.append(label, text);
  }
  content.append(fields);
  if (alert.valid_hours && effectiveHours && Number(alert.valid_hours) !== Number(effectiveHours)) {
    const actual = document.createElement("p");
    actual.className = "form-help";
    actual.textContent = `按周期对齐后，实际有效 ${effectiveHours} 小时。`;
    content.append(actual);
  }
  const snapshot = alert.signal_settings;
  if (snapshot) {
    const distances = document.createElement("dl");
    distances.className = "parameter-grid";
    for (const [key, name] of signalDistances) {
      const label = document.createElement("dt");
      label.textContent = name;
      const value = document.createElement("dd");
      value.textContent = snapshot[key] ?? "未记录";
      distances.append(label, value);
    }
    content.append(distances);
    const table = document.createElement("table");
    table.className = "parameter-signals";
    table.setAttribute("aria-label", preview ? "待创建警报的信号开关" : "创建时的信号开关");
    const head = table.createTHead().insertRow();
    for (const text of ["信号", "5 分钟", "2 分钟"]) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = text;
      head.append(cell);
    }
    const body = table.createTBody();
    for (const [key, name] of signalTypes) {
      const row = body.insertRow();
      appendCell(row, name);
      for (const field of ["minute_5", "minute_2"]) {
        const enabled = snapshot[key][field];
        appendCell(row, enabled ? "开启" : "关闭", enabled ? "good" : "signal-disabled");
      }
    }
    content.append(table);
  } else {
    const missing = document.createElement("p");
    missing.className = "form-help snapshot-missing";
    missing.textContent = "此警报未记录创建时的信号开关和价格距离，无法还原这些历史参数。";
    content.append(missing);
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
      alert.resolution
        ? `${alert.resolution} 分钟${alert.valid_bars ? ` / ${formatHours((alert.valid_bars * Number(alert.resolution)) / 60)} 小时` : ""}`
        : "—",
    );
    appendCell(row, formatDate(alert.end_time_ms));
    appendCell(row, formatDate(alert.create_time));
    appendCell(row, formatDate(alert.last_fire_time));

    const actionCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "alert-row-actions";
    const parameterButton = document.createElement("button");
    parameterButton.type = "button";
    parameterButton.className = "button button-secondary";
    parameterButton.textContent = "查看参数";
    parameterButton.setAttribute("aria-label", `查看警报 ${alert.alert_id} 创建时参数`);
    parameterButton.addEventListener("click", () => openAlertParameters(alert));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "button button-danger";
    deleteButton.textContent = "删除";
    deleteButton.addEventListener("click", () => deleteAlert(alert, deleteButton));
    actions.append(parameterButton, deleteButton);
    actionCell.append(actions);
    row.append(actionCell);
    labelTableRow(row, elements.tableBody.closest("table"));

    elements.tableBody.append(row);
  }
}

async function loadAlerts({ quiet = false } = {}) {
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
    elements.loadingIndicator.hidden = true;
  }
}

async function createAlert(event) {
  event.preventDefault();
  if (creatingAlert || createDialog.open) return;
  hideNotice();
  if (!signalSettings) {
    showNotice("请先读取信号配置：点击“警报配置”重试。", "error");
    return;
  }
  const prices = elements.prices.value.trim();
  if (!updatePriceFeedback()) {
    elements.prices.reportValidity();
    return;
  }
  if (!prices) {
    showNotice("请输入至少一个价格", "error");
    elements.prices.focus();
    return;
  }
  const validHours = alertFormSettings.validHours;
  const minutes = Number(alertResolution);
  const convertedBars = Math.ceil((validHours * 60) / minutes);
  if (!Number.isFinite(validHours) || validHours <= 0 || validHours > 40000) {
    showNotice("有效时长必须是大于 0 且不超过 40000 的小时数", "error");
    return;
  }
  if (convertedBars > 10000) {
    showNotice("有效时长过长，换算后 K 线数不能超过 10000", "error");
    return;
  }

  const alertConfig = {
    prices,
    side: alertFormSettings.side,
    valid_hours: validHours,
    resolution: alertResolution,
    signal_settings: JSON.parse(JSON.stringify(signalSettings)),
  };
  createDraft = alertConfig;
  const intervalMs = minutes * 60_000;
  const startTimeMs = Math.floor(Date.now() / intervalMs) * intervalMs;
  renderAlertParameters(document.getElementById("createAlertParameters"), {
    ...alertConfig,
    prices: prices.split(/[\s,，、;；]+/).filter(Boolean),
    valid_bars: convertedBars,
    start_time_ms: startTimeMs,
    end_time_ms: startTimeMs + convertedBars * intervalMs,
  }, { preview: true });
  createError.hidden = true;
  createDialog.showModal();
}

async function confirmCreateAlert(event) {
  event.preventDefault();
  if (creatingAlert || !createDraft || !createDialog.open) return;
  const alertConfig = createDraft;
  const requestKey = JSON.stringify(alertConfig);

  creatingAlert = true;
  createError.hidden = true;
  confirmCreateButton.disabled = true;
  confirmCreateButton.textContent = "创建中……";
  for (const button of createDialog.querySelectorAll("[data-close-create-dialog]")) button.disabled = true;
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
    const actualHours = (result.alert.valid_bars * Number(result.alert.resolution)) / 60;
    const message = result.created
      ? `警报创建成功：${result.alert.side}，${result.alert.resolution} 分钟，实际有效 ${formatHours(actualHours)} 小时（${result.alert.valid_bars} 根 K 线）`
      : "该请求对应的警报已经存在，未重复创建";
    showNotice(message);
    createDialog.close();
    pendingCreate = null;
    elements.prices.value = "";
    updatePriceFeedback();
    await loadAlerts({ quiet: true });
  } catch (error) {
    createError.textContent = `${error.message}。请重试，或返回修改参数。`;
    createError.hidden = false;
  } finally {
    creatingAlert = false;
    confirmCreateButton.disabled = false;
    confirmCreateButton.textContent = "确认创建";
    for (const button of createDialog.querySelectorAll("[data-close-create-dialog]")) button.disabled = false;
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

function updateManualActionState() {
  for (const button of document.querySelectorAll("[data-trade-action]")) {
    const enabled = tradingClients.some((client) => client.enabled && client.client_id === button.dataset.actionClientId);
    button.disabled = !enabled;
    button.title = enabled ? "" : "请先启用该客户端";
  }
}

async function submitManualAction(button) {
  const action = button.dataset.tradeAction;
  const client = button.dataset.actionClientId;
  if (!client) return;
  const target = `客户端 ${client}`;
  const confirmed = globalThis.confirm(`确定对“${target}”执行“${actionLabel(action)}”吗？仅操作本程序对应方向的持仓，按持仓实际手数平仓。`);
  if (!confirmed) {
    return;
  }
  button.disabled = true;
  try {
    const suffix = `?client_id=${encodeURIComponent(client)}`;
    const result = await apiRequest(`/api/mt5/actions/${action}${suffix}`, { method: "POST" });
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
  if (refreshingDashboard) return;
  refreshingDashboard = true;
  elements.refreshButton.disabled = true;
  elements.refreshButton.textContent = "刷新中……";
  try {
    await Promise.all([loadAlerts({ quiet: true }), loadTradingStatus(), loadTradingViewSetup(), loadSignals(),
      signalDialog.open ? Promise.resolve() : loadSignalSettings()]);
  } finally {
    refreshingDashboard = false;
    elements.refreshButton.disabled = false;
    elements.refreshButton.textContent = "刷新全部";
  }
}

elements.form.addEventListener("submit", createAlert);
document.querySelector("#confirmCreateForm").addEventListener("submit", confirmCreateAlert);
for (const button of document.querySelectorAll("[data-close-create-dialog]")) {
  button.addEventListener("click", () => { if (!creatingAlert) createDialog.close(); });
}
createDialog.addEventListener("cancel", (event) => { if (creatingAlert) event.preventDefault(); });
createDialog.addEventListener("close", () => { createDraft = null; });
createDialog.addEventListener("click", (event) => {
  if (event.target === createDialog && !creatingAlert) createDialog.close();
});
elements.prices.addEventListener("input", updatePriceFeedback);

const priceHelpButton = document.getElementById("priceHelpButton");
const priceInputTip = document.getElementById("priceInputTip");
function showPriceInputTip() {
  if (!priceInputTip.matches(":popover-open")) priceInputTip.showPopover();
  const rect = priceHelpButton.getBoundingClientRect();
  priceInputTip.style.left = `${Math.max(12, Math.min(rect.left, document.documentElement.clientWidth - priceInputTip.offsetWidth - 12))}px`;
  priceInputTip.style.top = `${Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - priceInputTip.offsetHeight - 12))}px`;
}
priceHelpButton.addEventListener("focus", showPriceInputTip);
priceHelpButton.addEventListener("click", showPriceInputTip);
priceHelpButton.addEventListener("blur", () => {
  if (priceInputTip.matches(":popover-open")) priceInputTip.hidePopover();
});
priceInputTip.addEventListener("toggle", () => {
  priceHelpButton.setAttribute("aria-expanded", String(priceInputTip.matches(":popover-open")));
});
elements.refreshButton.addEventListener("click", () => {
  hideNotice();
  refreshDashboard();
});
elements.copyWebhookButton.addEventListener("click", async () => {
  try {
    await copyText(elements.webhookUrl.textContent);
    showNotice("Webhook URL 已复制");
  } catch (_error) {
    showNotice("无法自动复制，请手动选择 URL", "error");
  }
});
elements.openWebhookMessageButton.addEventListener("click", openWebhookMessageDialog);
for (const button of elements.closeWebhookDialogButtons) {
  button.addEventListener("click", closeWebhookMessageDialog);
}
elements.webhookMessageDialog.addEventListener("click", (event) => {
  if (event.target === elements.webhookMessageDialog) {
    closeWebhookMessageDialog();
  }
});
elements.copyWebhookMessageButton.addEventListener("click", async () => {
  const errorMessage = document.getElementById("webhookCopyError");
  errorMessage.hidden = true;
  elements.copyWebhookMessageButton.disabled = true;
  elements.copyWebhookMessageButton.textContent = "复制中……";
  try {
    await copyText(elements.webhookMessage.value);
    closeWebhookMessageDialog();
    showNotice("警报消息 JSON 已复制，可粘贴到 TradingView 的「消息」栏。");
  } catch (_error) {
    errorMessage.textContent = "复制失败，请重试或手动选择并复制消息 JSON。";
    errorMessage.hidden = false;
  } finally {
    elements.copyWebhookMessageButton.disabled = false;
    elements.copyWebhookMessageButton.textContent = "复制 JSON";
  }
});
elements.clearSignalsButton.addEventListener("click", clearSignals);
const alertParametersDialog = document.getElementById("alertParametersDialog");
for (const button of document.querySelectorAll("[data-close-alert-parameters]")) {
  button.addEventListener("click", () => alertParametersDialog.close());
}
alertParametersDialog.addEventListener("click", (event) => {
  if (event.target === alertParametersDialog) alertParametersDialog.close();
});
initializeAlertForm();
document.querySelector("#openSignalSettingsButton").addEventListener("click", openSignalSettings);
document.querySelector("#signalSettingsForm").addEventListener("submit", saveSignalSettings);
for (const button of document.querySelectorAll("[data-close-signal-dialog]")) {
  button.addEventListener("click", () => { if (!savingSignalSettings) signalDialog.close(); });
}
signalDialog.addEventListener("cancel", (event) => { if (savingSignalSettings) event.preventDefault(); });
signalDialog.addEventListener("click", (event) => {
  if (event.target === signalDialog && !savingSignalSettings) signalDialog.close();
});
refreshDashboard();
setInterval(async () => {
  updateAlertEndTime();
  if (document.hidden || refreshingDashboard || monitoring || editingClientVolume() || signalDialog.open ||
    document.getElementById("mt5Clients").contains(document.activeElement)) return;
  monitoring = true;
  try { await Promise.all([loadTradingStatus(), loadSignals()]); }
  finally { monitoring = false; }
}, 15000);
