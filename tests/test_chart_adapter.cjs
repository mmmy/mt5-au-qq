const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../browser-extension/adapter.js"), "utf8").replace("export async function", "async function");

function fixture() {
  let writes = 0, values = {}, persisted = false;
  const inputs = [{ id: "in_0", type: "text" }, { id: "in_1", type: "integer" }, { id: "in_2", type: "time" }];
  values = { in_0: "自动", in_1: 720, in_2: 1700000040000 };
  for (let i = 0; i < 20; i++) {
    inputs.push({ id: `in_${4 + i * 3}`, type: "bool" }, { id: `in_${5 + i * 3}`, type: "price" });
    values[`in_${4 + i * 3}`] = i === 0;
    values[`in_${5 + i * 3}`] = i === 0 ? 4600 : 0;
  }
  const meta = { description: "黄金价格策略", scriptIdPart: "USER;test", pine: { version: "17.0" }, inputs };
  const study = { id: () => "study1", metaInfo: () => meta };
  const widget = { id: () => "chart1", symbol: () => ({ value: () => "FX:XAUUSD" }), interval: () => ({ value: () => "2" }),
    model: () => ({ model: () => ({ dataSources: () => [study] }) }) };
  const api = { getInputValues: () => Object.entries(values).map(([id, value]) => ({ id, value })),
    setInputValues: (updates) => { writes++; if (persisted) values = Object.fromEntries(updates.map((i) => [i.id, i.value])); } };
  const context = vm.createContext({ window: { _exposed_chartWidgetCollection: { getAll: () => [widget] },
    TradingViewApi: { chart: () => ({ getStudyById: () => api }) } },
    setTimeout: (fn) => fn(), Date });
  vm.runInContext(source, context);
  const target = { chart_id: "chart1", study_id: "study1", pine_id: "USER;test", pine_version: "17.0", symbol: "FX:XAUUSD", resolution: "2" };
  const command = { kind: "apply", expires_at: Date.now() / 1000 + 30, target, snapshot: { schema: 1, ...target, inputs: { ...values, in_5: 4620 } } };
  return { run: (c = command) => context.chartAdapter(c), command, meta, api, widget, writes: () => writes, persist: () => { persisted = true; } };
}
test("discovers instances and writes all 20 slots with stable readback", async () => {
  const f = fixture();
  const inventory = await f.run({ kind: "discover" });
  assert.equal(inventory.targets[0].price_capacity, 20);
  f.persist();
  const result = await f.run();
  assert.equal(result.ok, true);
  assert.equal(result.readback.inputs.in_5, 4620);
  assert.equal(result.readback.inputs.in_61, false);
  assert.equal(result.readback.inputs.in_62, 0);
  assert.equal(f.writes(), 1);
});
test("an API accepting the call without updating values is not success", async () => {
  const f = fixture();
  await assert.rejects(f.run(), /回读不一致/);
  assert.equal(f.writes(), 1);
});
for (const key of ["pine_id", "pine_version", "symbol", "resolution", "chart_id", "study_id"]) {
  test(`refuses stale ${key} before mutation`, async () => {
    const f = fixture(); f.command.target[key] = "changed";
    await assert.rejects(f.run(), /不一致|已变化/);
    assert.equal(f.writes(), 0);
  });
}
test("refuses changed price mappings, missing API and expired commands", async () => {
  const f = fixture(); f.meta.inputs.find((i) => i.id === "in_5").type = "float";
  await assert.rejects(f.run(), /映射不兼容|20 个水平价格/); assert.equal(f.writes(), 0);
  const g = fixture(); delete g.api.setInputValues;
  await assert.rejects(g.run(), /写入接口/); assert.equal(g.writes(), 0);
  const h = fixture(); h.command.expires_at = 0;
  await assert.rejects(h.run(), /已过期/); assert.equal(h.writes(), 0);
});
