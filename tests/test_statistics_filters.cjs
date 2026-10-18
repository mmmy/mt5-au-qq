const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

// Run the statistics controller with fresh page state and shared browser storage.
const source = fs.readFileSync(path.join(__dirname, "../app/static/app.js"), "utf8");
const controller = source.slice(0, source.indexOf("const signalTypes ="));
const storageKey = "mt5-au-qq.statistics-dates.v1";

function openPage(storage = new Map(), { storageBlocked = false, failRequest = false } = {}) {
  const nodes = new Map();
  function element(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: "", listeners: {}, dataset: {}, options: [],
      addEventListener(event, handler) { this.listeners[event] = handler; },
      setAttribute() {}, replaceChildren(...options) { this.options = options; this.value = options[0]?.value ?? ""; },
    });
    return nodes.get(selector);
  }
  const presets = [7, 30].map(days => Object.assign(element(`preset-${days}`), { dataset: { days } }));
  const requests = [];
  const context = vm.createContext({
    Option: function(text, value) { this.text = text; this.value = value; },
    document: {
      querySelector: element,
      querySelectorAll: selector => selector === ".statistics-preset" ? presets : [],
      getElementById: element,
    },
    localStorage: {
      getItem(key) { if (storageBlocked) throw Error("Storage blocked"); return storage.get(key) ?? null; },
      setItem(key, value) { if (storageBlocked) throw Error("Storage blocked"); storage.set(key, value); },
    },
    window: { TradeCharts: { render() {} } },
    async apiRequest(url) {
      requests.push(url);
      if (failRequest) throw Error("Network failure");
      const params = new URL(url, "http://localhost").searchParams;
      return {
        start_date: params.get("start_date"), end_date: params.get("end_date"),
        account: params.get("account"), history_available: false, total_signal_count: 0,
      };
    },
  });
  vm.runInContext(controller, context);
  vm.runInContext("initializeTradeStatistics()", context);
  vm.runInContext(source.slice(source.indexOf("async function refreshDashboard()"), source.indexOf('elements.form.addEventListener("submit"')), context);
  context.signalDialog = { open: false };
  for (const name of ["loadAlerts", "loadTradingViewSetup", "loadSignals", "loadSignalSettings"]) context[name] = async () => {};
  context.loadTradingStatus = async () => context.updateStatisticsAccounts([{ client_id: "A" }, { client_id: "B" }]);
  return {
    requests, presets, element,
    dates: () => [element("#statisticsStart").value, element("#statisticsEnd").value],
    setDates(start, end) { element("#statisticsStart").value = start; element("#statisticsEnd").value = end; },
    load: options => context.loadTradeStatistics(options),
    refresh: () => context.refreshDashboard(),
    updateAccounts: clients => context.updateStatisticsAccounts(clients),
    setStatusLoader: loader => { context.loadTradingStatus = loader; },
    today: vm.runInContext("isoDate(new Date())", context),
  };
}

test("custom applied dates survive page reload and the first dashboard refresh", async () => {
  const storage = new Map();
  const first = openPage(storage);
  first.setDates("2026-01-01", "2026-01-15");
  await first.load();
  const reloaded = openPage(storage);
  assert.deepEqual(reloaded.dates(), ["2026-01-01", "2026-01-15"]);
  await reloaded.load({ useApplied: true });
  assert.match(reloaded.requests[0], /start_date=2026-01-01&end_date=2026-01-15/);
});

test("client changes persist with applied dates and survive dashboard initialization", async () => {
  for (const account of ["A", "B", ""]) {
    const storage = new Map();
    const page = openPage(storage);
    await page.refresh();
    page.setDates("2026-01-01", "2026-01-15");
    await page.load();
    page.element("#statisticsAccount").value = account;
    await page.element("#statisticsAccount").listeners.change();
    const reloaded = openPage(storage);
    assert.equal(reloaded.element("#statisticsAccount").value, account);
    await reloaded.refresh();
    assert.equal(reloaded.element("#statisticsAccount").value, account);
    const params = new URL(reloaded.requests[0], "http://localhost").searchParams;
    assert.equal(params.get("account") ?? "", account);
    assert.deepEqual(reloaded.dates(), ["2026-01-01", "2026-01-15"]);
  }
});

test("initial statistics wait for clients and fall back if the saved client was removed", async () => {
  const storage = new Map([[storageKey, JSON.stringify({ start: "2026-01-01", end: "2026-01-15", account: "B" })]]);
  const page = openPage(storage);
  let finishStatus;
  page.setStatusLoader(() => new Promise(resolve => { finishStatus = () => { page.updateAccounts([{ client_id: "A" }]); resolve(); }; }));
  const refreshing = page.refresh();
  assert.equal(page.requests.length, 0);
  finishStatus();
  await refreshing;
  assert.equal(page.element("#statisticsAccount").value, "");
  assert.equal(new URL(page.requests[0], "http://localhost").searchParams.has("account"), false);
  assert.equal(JSON.parse(storage.get(storageKey)).account, "");
});

test("client list updates keep the current selection and refresh a removed applied client", async () => {
  const page = openPage();
  await page.refresh();
  page.element("#statisticsAccount").value = "B";
  await page.element("#statisticsAccount").listeners.change();
  page.updateAccounts([{ client_id: "A" }, { client_id: "B" }]);
  assert.equal(page.element("#statisticsAccount").value, "B");
  page.updateAccounts([{ client_id: "A" }]);
  await new Promise(setImmediate);
  assert.equal(page.element("#statisticsAccount").value, "");
  assert.equal(new URL(page.requests.at(-1), "http://localhost").searchParams.has("account"), false);
});

test("date-only saved preferences remain compatible and malformed clients are ignored", async () => {
  for (const account of [undefined, null, 123, {}, "bad&client", "x".repeat(33)]) {
    const storage = new Map([[storageKey, JSON.stringify({ start: "2026-01-01", end: "2026-01-15", account })]]);
    const page = openPage(storage);
    await page.refresh();
    assert.deepEqual(page.dates(), ["2026-01-01", "2026-01-15"]);
    assert.equal(page.element("#statisticsAccount").value, "");
  }
});

test("unapplied drafts and invalid ranges do not replace the saved applied dates", async () => {
  const storage = new Map();
  const page = openPage(storage);
  page.setDates("2026-01-01", "2026-01-15");
  await page.load();
  page.setDates("2026-02-01", "2026-02-10");
  await page.load({ useApplied: true });
  assert.match(page.requests.at(-1), /start_date=2026-01-01&end_date=2026-01-15/);
  page.setDates("2026-02-10", "2026-02-01");
  await page.load();
  assert.equal(page.element("#statisticsError").hidden, false);
  assert.deepEqual(openPage(storage).dates(), ["2026-01-01", "2026-01-15"]);
});

test("quick presets and Enter also persist the applied range", async () => {
  for (const days of [7, 30]) {
    const storage = new Map();
    const page = openPage(storage);
    page.presets.find(button => button.dataset.days === days).listeners.click();
    await new Promise(setImmediate);
    assert.deepEqual(openPage(storage).dates(), page.dates());
    assert.equal(page.dates()[1], page.today);
    assert.equal((Date.parse(page.dates()[1]) - Date.parse(page.dates()[0])) / 86400000, days - 1);
  }
  const storage = new Map();
  const page = openPage(storage);
  page.setDates("2026-01-01", "2026-01-15");
  page.element("#statisticsEnd").listeners.keydown({ key: "Enter", preventDefault() {} });
  await new Promise(setImmediate);
  assert.deepEqual(openPage(storage).dates(), page.dates());
});

test("missing, corrupt and invalid saved dates safely use the initial seven-day range", () => {
  const fallback = openPage().dates();
  for (const value of [
    "not json", "null", "[]", "{}",
    { start: "2026-02-30", end: "2026-03-01" },
    { start: "2026-01-15", end: "2026-01-01" },
    { start: "2024-01-01", end: "2026-01-01" },
    { start: "2099-01-01", end: "2099-01-02" },
    { start: 123, end: true },
  ]) {
    const saved = typeof value === "string" ? value : JSON.stringify(value);
    assert.deepEqual(openPage(new Map([[storageKey, saved]])).dates(), fallback);
  }
});

test("storage access failures do not prevent statistics loading", async () => {
  const page = openPage(new Map(), { storageBlocked: true });
  page.setDates("2026-01-01", "2026-01-15");
  await page.load();
  assert.equal(page.requests.length, 1);
  assert.equal(page.element("#statisticsError").hidden, true);
  assert.match(page.element("#statisticsScope").textContent, /2026-01-01 — 2026-01-15/);
});

test("failed requests preserve the previously saved range", async () => {
  const saved = JSON.stringify({ start: "2026-01-01", end: "2026-01-15" });
  const storage = new Map([[storageKey, saved]]);
  const page = openPage(storage, { failRequest: true });
  page.setDates("2026-02-01", "2026-02-10");
  await page.load();
  assert.equal(storage.get(storageKey), saved);
  assert.equal(page.element("#statisticsError").textContent, "Network failure");
});
