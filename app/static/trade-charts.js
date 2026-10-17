"use strict";

// Charts only consume the applied API response; changing chart type never queries MT5.
window.TradeCharts = (() => {
  let response = null;
  let mode = "cumulative";
  const node = (id) => document.getElementById(id);
  const signed = (value) => `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(2)}`;
  const ns = "http://www.w3.org/2000/svg";
  function mark(tag, attributes = {}, label) {
    const item = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, value);
    if (label !== undefined) item.textContent = label;
    return item;
  }
  function text(svg, x, y, label, anchor = "start") {
    svg.append(mark("text", { x, y, "text-anchor": anchor }, label));
  }
  function frame(svg, values, unit, counts = false) {
    svg.replaceChildren();
    const width = Math.max(svg.getBoundingClientRect().width, 180), height = 230;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.append(mark("title", {}, svg.getAttribute("aria-label")));
    const left = counts ? 32 : 62, right = 12, top = 25, bottom = 32;
    const min = Math.min(0, ...values), max = Math.max(1, ...values);
    const hi = counts ? Math.ceil(max) : max + (max - min) * .12;
    const lo = counts || min >= 0 ? 0 : min - (max - min) * .08;
    const y = value => top + (hi - value) / (hi - lo) * (height - top - bottom);
    const ticks = counts ? Math.min(hi, 3) : 4;
    for (let i = 0; i <= ticks; i++) {
      const value = counts ? Math.round(hi * i / ticks) : lo + (hi - lo) * i / ticks;
      svg.append(mark("line", { x1:left, x2:width-right, y1:y(value), y2:y(value), class:"chart-grid" }));
      const formatted = Math.abs(value) >= 10000 ? `${(value / 1000).toFixed(1)}k` : value.toFixed(0);
      text(svg, left-8, y(value)+4, formatted, "end");
    }
    if (lo < 0) svg.append(mark("line", { x1:left, x2:width-right, y1:y(0), y2:y(0), class:"chart-zero" }));
    text(svg, left, 13, unit);
    return { width, height, left, right, top, bottom, y, plot:width-left-right };
  }
  function draw() {
    if (!response) return;
    const data = response;
    // Keep long ranges readable, retain all days in totals and tooltips.
    const size = Math.max(1, Math.ceil(data.daily.length / 30));
    const buckets = [];
    for (let i = 0; i < data.daily.length; i += size) {
      const group = data.daily.slice(i, i + size);
      buckets.push({ date:group[0].date, end:group.at(-1).date,
        profit:group.reduce((sum, d) => sum + Number(d.net_profit || 0), 0),
        tp:group.reduce((sum, d) => sum + d.take_profit, 0),
        sl:group.reduce((sum, d) => sum + d.stop_loss, 0) });
    }
    const period = size === 1 ? "每日" : `每 ${size} 天`;
    node("statisticsCountsTitle").textContent = `${period}止盈 / 止损`;
    node("statisticsProfitTitle").textContent = mode === "daily" ? `${period}净收益` : "累计净收益";
    const profitSvg = node("statisticsProfitChart"), countSvg = node("statisticsBars");
    const hasProfit = data.profit_available && (data.closed_deal_count || data.recent_deals?.length);
    profitSvg.toggleAttribute("hidden", !hasProfit);
    node("statisticsProfitEmpty").hidden = !!hasProfit;
    node("statisticsProfitEmpty").textContent = !data.history_available ? "MT5 成交历史暂不可用，请检查终端连接后重试。"
      : !data.profit_available ? "请按客户端查看收益，各账户币种不能直接合并。" : "所选日期范围内暂无平仓成交。";
    node("statisticsChartNote").textContent = `${mode === "daily" ? "按平仓日期汇总" : "区间起点归零"}${data.profit_currency ? ` · ${data.profit_currency}` : ""}${size > 1 ? ` · 每 ${size} 天汇总` : ""}`;
    const label = i => buckets[i].date === buckets[i].end ? buckets[i].date : `${buckets[i].date} — ${buckets[i].end}`;
    const dateTick = i => buckets[i].date.slice(5).replace("-", "/");
    function ticks(svg, f, x) {
      const n = Math.min(buckets.length, f.width < 350 ? 3 : 4);
      for (let j = 0; j < n; j++) {
        const i = Math.round(j * (buckets.length-1) / (n-1 || 1));
        text(svg, x(i), f.height-7, dateTick(i), j === n-1 ? "end" : j === 0 ? "start" : "middle");
      }
    }
    function hit(svg, f, x, width, message) {
      const target = mark("rect", { x, y:f.top, width, height:f.height-f.top-f.bottom, fill:"transparent",
        class:"statistics-chart-hit", tabindex:0, role:"img", "aria-label":message });
      target.append(mark("title", {}, message));
      target.addEventListener("pointermove", () => { svg.nextElementSibling.textContent = message; });
      target.addEventListener("click", () => { svg.nextElementSibling.textContent = message; });
      target.addEventListener("focus", () => { svg.nextElementSibling.textContent = message; });
      svg.append(target);
    }
    // Persistent labels also expose details to touch and keyboard users.
    for (const svg of [profitSvg, countSvg]) {
      if (!svg.nextElementSibling.classList.contains("statistics-chart-detail")) {
        const detail = document.createElement("p"); detail.className = "statistics-hint statistics-chart-detail";
        svg.after(detail);
      }
      svg.nextElementSibling.textContent = "";
      svg.nextElementSibling.hidden = svg.hasAttribute("hidden");
    }
    if (hasProfit && buckets.length) {
      let sum = 0;
      const values = mode === "daily" ? buckets.map(d => d.profit) : [0, ...buckets.map(d => sum += d.profit)];
      const f = frame(profitSvg, values, data.profit_currency);
      profitSvg.setAttribute("aria-label", `${node("statisticsProfitTitle").textContent}，区间合计 ${signed(data.net_profit)} ${data.profit_currency}`);
      const step = f.plot / buckets.length, x = i => f.left + i * step;
      if (mode === "cumulative") {
        const path = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${f.y(v)}`).join(" ");
        profitSvg.append(mark("path", { d:`${path} L${x(values.length-1)},${f.y(0)} L${x(0)},${f.y(0)} Z`, class:"chart-up", opacity:.08 }));
        profitSvg.append(mark("path", { d:path, class:"chart-line" }));
        profitSvg.append(mark("circle", { cx:x(values.length-1), cy:f.y(values.at(-1)), r:3, class:"chart-up" }));
      } else buckets.forEach((d,i) => {
        profitSvg.append(mark("rect", { x:x(i)+step*.18, y:Math.min(f.y(0), f.y(d.profit)), width:step*.64,
          height:Math.abs(f.y(d.profit)-f.y(0)), rx:2, class:d.profit < 0 ? "chart-down" : "chart-up" }));
      });
      ticks(profitSvg, f, i => x(i + (mode === "daily" ? .5 : 1)));
      buckets.forEach((d,i) => hit(profitSvg, f, x(i), step,
        `${label(i)} · ${mode === "daily" ? "净收益" : "累计"} ${signed(mode === "daily" ? d.profit : values[i+1])} ${data.profit_currency}`));
    } else profitSvg.replaceChildren();
    const hasCounts = data.total_signal_count > 0;
    countSvg.toggleAttribute("hidden", !hasCounts);
    countSvg.nextElementSibling.hidden = !hasCounts;
    node("statisticsCountsEmpty").hidden = hasCounts;
    node("statisticsCountsEmpty").textContent = "所选日期范围内暂无平仓信号。";
    if (hasCounts && buckets.length) {
      const f = frame(countSvg, buckets.map(d => d.tp+d.sl), "次", true), step = f.plot / buckets.length;
      countSvg.setAttribute("aria-label", `止盈 ${data.take_profit_count} 次，止损 ${data.stop_loss_count} 次，按日期统计`);
      buckets.forEach((d,i) => {
        const x = f.left + (i+.22)*step;
        if (d.tp) countSvg.append(mark("rect", { x, y:f.y(d.tp), width:step*.56, height:f.y(0)-f.y(d.tp), class:"chart-up" }));
        if (d.sl) countSvg.append(mark("rect", { x, y:f.y(d.tp+d.sl), width:step*.56, height:f.y(d.tp)-f.y(d.tp+d.sl), class:"chart-down" }));
      });
      ticks(countSvg, f, i => f.left+(i+.5)*step);
      buckets.forEach((d,i) => hit(countSvg, f, f.left+i*step, step, `${label(i)} · 止盈 ${d.tp} 次 / 止损 ${d.sl} 次`));
    } else countSvg.replaceChildren();
  }
  document.querySelectorAll("[data-profit-chart]").forEach(button => button.addEventListener("click", () => {
    mode = button.dataset.profitChart;
    document.querySelectorAll("[data-profit-chart]").forEach(b => b.setAttribute("aria-pressed", String(b === button)));
    draw();
  }));
  let observedWidth = 0;
  new ResizeObserver(entries => {
    const width = entries[0].contentRect.width;
    if (width !== observedWidth) { observedWidth = width; if (response) requestAnimationFrame(draw); }
  }).observe(node("statisticsProfitChart").parentElement);
  return { render(data) { response = data; draw(); } };
})();
