// Serialized into TradingView's MAIN world. Keep every dependency inside this function.
// TradingView's website API is private: fail closed when its shape changes.
export async function chartAdapter(command) {
  const value = (v) => typeof v?.value === "function" ? v.value() : v;
  const fail = (message) => { throw new Error(message); };
  const collection = window._exposed_chartWidgetCollection;
  const widgets = collection?.getAll?.();
  if (!Array.isArray(widgets)) fail("TradingView 图表接口不可用，请等待图表加载；网站更新后可能需要更新扩展");
  const list = () => widgets.flatMap((widget, index) => {
    const sources = widget.model?.()?.model?.()?.dataSources?.() || [];
    return sources.flatMap((study) => {
      const meta = study.metaInfo?.();
      if (meta?.description !== "黄金价格策略") return [];
      return [{ chart_id: String(widget.id()), study_id: String(study.id()),
        pine_id: String(meta.scriptIdPart || ""), pine_version: String(meta.pine?.version || ""),
        symbol: String(value(widget.symbol())), resolution: String(value(widget.interval())),
        price_capacity: meta.inputs?.filter((i) => i.type === "price").length || 0,
        name: meta.description, index, widget, study, meta }];
    });
  });
  const publicTarget = ({ widget, study, meta, ...target }) => target;
  if (command.kind === "discover") return { ok: true, targets: list().map(publicTarget) };
  if (command.kind !== "apply" || command.snapshot?.schema !== 1) fail("不支持的同步协议");
  const keys = ["chart_id", "study_id", "pine_id", "pine_version", "symbol", "resolution"];
  const resolve = () => {
    const candidates = list().filter((t) => keys.every((k) => t[k] === command.target[k]));
    if (candidates.length !== 1) fail("图表、策略、版本、品种或周期已变化，请重新读取图表列表");
    const target = candidates[0];
    if (target.price_capacity !== 20) fail("策略必须支持 20 个水平价格");
    const api = window.TradingViewApi?.chart?.(target.index)?.getStudyById?.(target.study_id);
    if (typeof api?.getInputValues !== "function" || typeof api?.setInputValues !== "function") {
      fail("当前 TradingView 版本未提供兼容的策略写入接口，未修改参数；请更新扩展适配器");
    }
    return { target, api };
  };
  const snapshot = command.snapshot;
  for (const key of ["pine_id", "pine_version", "symbol", "resolution"]) {
    if (snapshot[key] !== command.target[key]) fail("策略快照与目标不一致，未修改参数");
  }
  const requested = snapshot.inputs;
  if (!requested || !Object.keys(requested).length || Object.entries(requested).some(([id, v]) =>
    !/^in_\d+$/.test(id) || !["string", "boolean", "number"].includes(typeof v) ||
    (typeof v === "number" && !Number.isFinite(v)))) fail("策略输入格式无效");
  const { target, api } = resolve();
  const before = api.getInputValues();
  const known = new Set(before.map((i) => i.id));
  const meta = new Map(target.meta.inputs.map((i) => [i.id, i]));
  if (Object.keys(requested).some((id) => !known.has(id) || !meta.has(id))) fail("策略输入字段已变化，未修改参数");
  for (let i = 0; i < 20; i++) {
    const enabled = `in_${4 + i * 3}`, price = `in_${5 + i * 3}`;
    if (meta.get(enabled)?.type !== "bool" || meta.get(price)?.type !== "price" ||
        typeof requested[enabled] !== "boolean" || typeof requested[price] !== "number" ||
        (!requested[enabled] && requested[price] !== 0)) fail("价格输入映射不兼容，未修改参数");
  }
  if (Date.now() / 1000 > command.expires_at) fail("命令已过期，未修改参数");
  await api.setInputValues(Object.entries(requested).map(([id, value]) => ({ id, value })));
  // Verify the same instance after the API update and require three stable reads.
  let stable = 0;
  for (let attempt = 0; attempt < 35; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 150));
    const current = resolve();
    const actual = Object.fromEntries(current.api.getInputValues().map((i) => [i.id, i.value]));
    const matched = Object.entries(requested).every(([id, expected]) => actual[id] === expected);
    stable = matched ? stable + 1 : 0;
    if (stable >= 3) {
      return { ok: true, message: "策略输入已回读一致；图表可能仍在计算。已有警报保持原来的快照。",
        readback: { ...publicTarget(current.target),
          inputs: Object.fromEntries(Object.keys(requested).map((id) => [id, actual[id]])) } };
    }
  }
  fail("已尝试写入，但回读不一致，无法确认成功；请检查图表后重新读取，不会自动重试");
}
