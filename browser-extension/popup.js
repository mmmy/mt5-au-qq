const $ = (id) => document.getElementById(id);
async function render() {
  const { status, config } = await chrome.storage.local.get(["status", "config"]);
  $("status").textContent = status || "尚未配对";
  if (config?.backend && document.activeElement !== $("backend")) $("backend").value = config.backend;
}
async function request(message) {
  const result = await chrome.runtime.sendMessage(message);
  if (!result?.ok) throw new Error(result?.message || "扩展未响应");
  await render();
}
$("pairForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("pair").disabled = true;
  try {
    // Request a single backend origin from this explicit user gesture.
    const origin = new URL($("backend").value.trim()).origin;
    if (!await chrome.permissions.request({ origins: [`${origin}/*`] })) throw new Error("未授予后端访问权限");
    await request({ type: "pair", backend: $("backend").value.trim(), label: $("label").value.trim(), code: $("code").value.trim() });
    $("code").value = "";
  } catch (error) { $("status").textContent = error.message; }
  finally { $("pair").disabled = false; }
});
for (const type of ["connect", "forget"]) $(type).addEventListener("click", () => request({ type }).catch((e) => { $("status").textContent = e.message; }));
chrome.storage.onChanged.addListener(render);
render();
