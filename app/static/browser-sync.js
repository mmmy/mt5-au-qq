(() => {
  // Temporarily hidden. Change the default to true when this feature is enabled again.
  const enabled = window.BROWSER_SYNC_UI_ENABLED ?? false;
  document.getElementById("browserSync").hidden = !enabled;
  if (!enabled) return;
  const $ = (id) => document.getElementById(id);
  let token = "", clients = [], busy = false, pairTimer;
  const status = (text, error = false) => {
    $("browserSyncStatus").textContent = text;
    $("browserSyncStatus").classList.toggle("sync-error", error);
  };
  const controls = () => {
    const client = clients.find((c) => c.id === $("browserClient").value);
    $("discoverCharts").disabled = busy || !client?.connected;
    $("syncDraft").disabled = busy || !client?.connected || !$("browserTarget").value;
    $("browserClient").disabled = busy;
    $("browserTarget").disabled = busy;
    $("revokeBrowser").disabled = busy || !client;
    $("browserPairCode").disabled = !token;
    const selected = target();
    $("browserTargetInfo").hidden = !selected;
    $("browserTargetInfo").textContent = selected ? `脚本 ${selected.pine_id} · 版本 ${selected.pine_version} · ${selected.price_capacity} 个价格位置` : "";
  };
  async function api(path, options = {}) {
    const response = await fetch(`/api/browser-sync${path}`, { ...options,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...options.headers } });
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail;
      const error = new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("；") : detail?.message || `请求失败 (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function renderTargets() {
    const previous = $("browserTarget").value;
    $("browserTarget").replaceChildren(new Option("请选择目标；多个图表需逐一确认", ""));
    const client = clients.find((c) => c.id === $("browserClient").value);
    for (const target of client?.targets || []) {
      const key = JSON.stringify([target.tab_id, target.document_id, target.chart_id, target.study_id]);
      const label = `${target.symbol} · ${target.resolution} 分钟 · v${target.pine_version} · 标签 ${target.tab_id} / 图表 ${target.chart_id} / 策略 ${target.study_id}`;
      const option = new Option(label, key);
      option.title = `${target.pine_id} (${target.title || "TradingView"})`;
      $("browserTarget").add(option);
    }
    if (Array.from($("browserTarget").options).some((o) => o.value === previous)) $("browserTarget").value = previous;
    controls();
  }
  async function refresh() {
    if (!token || busy) return;
    try {
      clients = await api("/clients");
      const previous = $("browserClient").value;
      $("browserClient").replaceChildren(new Option(clients.length ? "请选择浏览器" : "请先配对扩展", ""));
      clients.forEach((c) => $("browserClient").add(new Option(`${c.label} · ${c.connected ? "已连接" : "离线"}`, c.id)));
      if (clients.some((c) => c.id === previous)) $("browserClient").value = previous;
      else if (clients.length === 1) $("browserClient").value = clients[0].id;
      $("browserConnection").textContent = `${clients.filter((c) => c.connected).length} 个浏览器已连接`;
      renderTargets();
    } catch (error) { status(error.message, true); }
  }
  async function run(body) {
    busy = true; controls();
    status(body.kind === "discover" ? "正在读取图表……" : "已请求同步，等待扩展写入并回读校验……");
    const request = { ...body, request_id: createRequestId(), client_id: $("browserClient").value };
    // Keep this ID visible so a page refresh can recover the result without resending a write.
    let job;
    try {
      sessionStorage.setItem("browser-sync-job", request.request_id);
      job = await api("/jobs", { method: "POST", body: JSON.stringify(request) });
      await waitForJob(job);
    } catch (error) {
      if (error.status >= 400 && error.status < 500) sessionStorage.removeItem("browser-sync-job");
      status(error.status ? error.message : `${error.message}；发送结果未确认，请刷新页面查询原任务，避免重复提交`, true);
    }
    finally { busy = false; await refresh(); controls(); }
  }
  async function waitForJob(job) {
    const deadline = Date.now() + 50000;
    while (job.status === "pending" && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      job = await api(`/jobs/${job.id}`);
    }
    const message = job.result?.message || (job.status === "succeeded" ? "已完成回读校验" : "执行结果未确认，请重新读取图表，不会自动重发");
    status(`${message} · 任务 ${job.id.slice(0, 8)}${job.result?.warnings?.length ? `。${job.result.warnings.join("；")}` : ""}`, job.status !== "succeeded");
    if (job.status !== "pending") sessionStorage.removeItem("browser-sync-job");
  }
  function target() {
    const client = clients.find((c) => c.id === $("browserClient").value);
    return client?.targets.find((t) => JSON.stringify([t.tab_id, t.document_id, t.chart_id, t.study_id]) === $("browserTarget").value);
  }
  function confirmSync(source, summary) {
    const selected = target();
    if (busy) { status("已有任务正在执行，请等待结果", true); return; }
    if (!selected) {
      $("browserSync").scrollIntoView({ behavior: "smooth", block: "center" });
      $("browserTarget").focus();
      status("请先选择已连接的浏览器，读取图表并明确选择目标", true);
      return;
    }
    const dialog = document.createElement("dialog");
    dialog.className = "detail-dialog browser-sync-dialog";
    dialog.setAttribute("aria-labelledby", "browserSyncConfirmTitle");
    const content = document.createElement("div");
    content.className = "browser-sync-confirm";
    const title = document.createElement("h3"); title.id = "browserSyncConfirmTitle"; title.textContent = "同步策略参数到图表";
    const description = document.createElement("p"); description.textContent = summary;
    const destination = document.createElement("p");
    destination.textContent = `目标：${selected.symbol} · ${selected.resolution} 分钟 · v${selected.pine_version} · 标签 ${selected.tab_id} / 图表 ${selected.chart_id} / 策略 ${selected.study_id}`;
    const help = document.createElement("p"); help.className = "form-help";
    help.textContent = "将替换该实例的策略输入（含价格、时间、信号和仓位参数），空余价格关闭并清零。已有警报保持独立快照。脚本 ID、版本、品种和周期必须一致。";
    const buttons = document.createElement("div"); buttons.className = "browser-pair-actions";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "button button-secondary"; cancel.textContent = "取消";
    const confirm = document.createElement("button"); confirm.type = "button"; confirm.className = "button button-primary"; confirm.textContent = "同步并校验";
    cancel.onclick = () => dialog.close();
    confirm.onclick = () => { dialog.close(); run({ kind: "apply", target: selected, ...source }); };
    buttons.append(cancel, confirm); content.append(title, description, destination, help, buttons);
    dialog.append(content); document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove()); dialog.showModal(); cancel.focus();
  }
  $("syncDraft").addEventListener("click", () => {
    if (!updatePriceFeedback() || !elements.prices.value.trim()) { elements.prices.reportValidity(); return; }
    if (!signalSettings) { status("请先读取警报配置", true); return; }
    const draft = { prices: elements.prices.value.trim(), side: alertFormSettings.side,
      valid_hours: alertFormSettings.validHours, resolution: alertResolution,
      signal_settings: JSON.parse(JSON.stringify(signalSettings)) };
    confirmSync({ draft }, `来源：当前输入 ${draft.prices}；方向 ${draft.side}；有效 ${draft.valid_hours} 小时。开始时间按同步时当前 K 线计算。`);
  });
  window.BrowserSync = { replay: (alert) => confirmSync({ alert_id: alert.alert_id },
    `来源：警报 ${alert.alert_id} 创建时的完整策略快照。价格：${alert.prices?.join("、") || "由后端读取"}。保留原开始时间和有效期；历史警报可能已经到期。`) };
  $("discoverCharts").addEventListener("click", () => run({ kind: "discover" }));
  $("browserClient").addEventListener("change", () => { $("browserTarget").value = ""; renderTargets(); });
  $("browserTarget").addEventListener("change", controls);
  $("browserPairCode").addEventListener("click", async () => {
    try {
      const data = await api("/pair-code", { method: "POST" });
      $("browserPairOutput").textContent = `${data.code}（2 分钟内有效）`;
      clearTimeout(pairTimer);
      pairTimer = setTimeout(() => { $("browserPairOutput").textContent = "配对码已过期，请重新生成"; }, data.expires_in * 1000);
    } catch (error) { status(error.message, true); }
  });
  $("revokeBrowser").addEventListener("click", async () => {
    try { await api(`/clients/${$("browserClient").value}`, { method: "DELETE" }); await refresh(); status("已撤销该浏览器，需要重新配对才能连接"); }
    catch (error) { status(error.message, true); }
  });
  $("browserAuthorize").addEventListener("click", async () => {
    token = $("browserAdminToken").value.trim(); $("browserAdminToken").value = "";
    await refresh(); controls();
  });
  (async () => {
    try { const response = await fetch("/api/browser-sync/session"); if (response.ok) token = (await response.json()).token; }
    catch { /* Pairing instructions remain usable while the backend is unavailable. */ }
    controls(); await refresh();
    const pending = sessionStorage.getItem("browser-sync-job");
    if (pending && token) {
      busy = true; controls();
      try { await waitForJob(await api(`/jobs/${pending}`)); } catch (error) { status(error.message, true); }
      finally { busy = false; controls(); }
    }
  })();
  setInterval(() => { if (!document.hidden) refresh(); }, 5000);
})();
