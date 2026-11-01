/* Claude's report as a row of findings: a small chart and one short line each (report_view.py, /api/findings), always open. The numbers
   are the bot's own records. The full text stays one tap away. */
(function () {
  const box = document.getElementById("findings");
  if (!box) return;
  const bot = box.dataset.bot || "stocks";
  const e = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const num = (v, d) => Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
  const sign = v => v > 0 ? "+" : v < 0 ? "−" : "";
  const fmt = (v, f, signed) => f === "pct" || f === "%" ? (signed ? sign(v) : v < 0 ? "−" : "") + num(v, Math.abs(v) < 10 && v % 1 ? 1 : Math.abs(v) >= 100 ? 0 : v % 1 ? 1 : 0) + "%"
    : f === "usd" ? (signed ? sign(v) : v < 0 ? "−" : "") + "$" + num(v, Math.abs(v) >= 1000 ? 0 : 2)
    : f === "$" ? (signed ? sign(v) : v < 0 ? "−" : "") + "$" + num(v, Math.abs(v) >= 100 ? 0 : 2)       // a bar's label stays short
    : f === "price" ? "$" + num(v, 2)
    : (signed ? sign(v) : v < 0 ? "−" : "") + num(v, v % 1 ? 1 : 0) + (f ? " " + f : "");
  const tone = v => v > 0 ? "up" : v < 0 ? "down" : "";
  const W = 300, H = 66;

  function path(pts, lo, hi) {
    const n = pts.length, y = v => 4 + (hi - v) * (H - 8) / (hi - lo || 1);
    return "M" + pts.map((p, i) => (i * W / (n - 1)).toFixed(1) + "," + y(p[1]).toFixed(1)).join("L");
  }
  function line(c) {
    const vals = c.points.map(p => p[1]), last = vals.at(-1), first = c.zero ? 0 : vals[0], d = last - first;
    const lo = Math.min(...vals, c.zero ? 0 : Infinity), hi = Math.max(...vals, c.zero ? 0 : -Infinity), t = d >= 0 ? "--good" : "--critical", p = path(c.points, lo, hi);
    const zero = c.zero ? `<line x1="0" x2="${W}" y1="${(4 + hi * (H - 8) / (hi - lo || 1)).toFixed(1)}" y2="${(4 + hi * (H - 8) / (hi - lo || 1)).toFixed(1)}" stroke="var(--ref)" stroke-width="1" vector-effect="non-scaling-stroke"/>` : "";
    const head = c.zero ? `<b class="fi-big ${tone(last)}">${fmt(last, c.fmt === "price" ? "usd" : c.fmt, true)}</b>`
      : `<b class="fi-big">${fmt(last, c.fmt)}</b><span class="fi-move ${tone(d)}">${fmt(d, c.fmt === "price" ? "usd" : c.fmt, true)}</span><span class="fi-when">${since(c.points[0][0])}</span>`;
    return `<div class="fi-head">${head}</div><svg class="fi-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${zero}
      <path d="${p}L${W},${H}L0,${H}Z" fill="var(${t})" opacity=".13"/><path d="${p}" fill="none" stroke="var(${t})" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
  }
  function since(day) {            // the change beside a big number is over what the chart shows, so it says from when
    const d = new Date(String(day).slice(0, 10) + "T12:00:00");
    return isNaN(d) ? "" : "since " + d.toLocaleDateString("en-US", {month: "short", day: "numeric"});
  }
  function lines(c) {
    const all = c.series.flatMap(s => s.points.map(p => p[1])), lo = Math.min(...all, 0), hi = Math.max(...all, 0), y0 = (4 + hi * (H - 8) / (hi - lo || 1)).toFixed(1);
    return `<div class="fi-head">${c.series.map(s => `<span class="fi-key"><i class="${s.dashed ? "ref" : ""}"></i>${e(s.label)} <b class="${tone(s.points.at(-1)[1])}">${fmt(s.points.at(-1)[1], c.fmt, true)}</b></span>`).join("")}</div>
      <svg class="fi-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="${W}" y1="${y0}" y2="${y0}" stroke="var(--grid)" stroke-width="1" vector-effect="non-scaling-stroke"/>
      ${c.series.slice().reverse().map(s => `<path d="${path(s.points, lo, hi)}" fill="none" stroke="var(${s.dashed ? "--ref" : "--series-1"})" stroke-width="2.2"${s.dashed ? ' stroke-dasharray="4 3"' : ""} stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`).join("")}</svg>`;
  }
  function bars(c) {
    const max = c.max || Math.max(...c.rows.map(r => Math.abs(r[1])), .01), neg = !c.max && c.rows.some(r => r[1] < 0), pos = !c.max && c.rows.some(r => r[1] > 0);
    const mid = neg && pos ? 50 : neg ? 100 : 0, room = neg && pos ? 50 : 100;
    return `<div class="fi-bars">${c.rows.map(([l, v]) => { const w = Math.max(1.5, Math.abs(v) / max * room);
      return `<div class="fi-bar"><span class="l">${e(l)}</span><span class="t"><i class="${c.max ? "info" : v < 0 ? "down" : "up"}" style="${v < 0 ? `right:${100 - mid}%` : `left:${mid}%`};width:${w.toFixed(1)}%"></i></span><b class="${c.max ? "" : tone(v)}">${e(fmt(v, c.fmt, !c.max))}</b></div>`; }).join("")}</div>`;
  }
  function gauge(c) {
    const span = c.max - c.min, at = v => Math.max(0, Math.min(100, (v - c.min) / span * 100)), bad = c.mark != null && c.mark < 0 && c.value <= c.mark;
    return `<div class="fi-head"><b class="fi-big ${bad ? "down" : tone(c.value)}">${fmt(c.value, c.fmt, true)}</b><span class="fi-move muted">${e(c.label || "")}</span></div>
      <div class="fi-gauge"><i class="zero" style="left:${at(0)}%"></i>${c.mark ? `<i class="limit" style="left:${at(c.mark)}%"></i>` : ""}<span class="${c.value < 0 ? "down" : "up"}" style="left:${Math.min(at(0), at(c.value))}%;width:${Math.abs(at(c.value) - at(0))}%"></span><b class="${c.value < 0 ? "down" : "up"}" style="left:${at(c.value)}%"></b></div>`;
  }
  const draw = c => ({line, lines, bars, gauge}[c.type] || (() => ""))(c);

  let last = "";
  function render(d) {
    const sig = JSON.stringify([d.items, d.also, d.date]);
    if (sig === last) return;
    last = sig;
    const when = document.getElementById("cl-when"), fullBtn = document.getElementById("cl-full");
    if (when) when.textContent = d.date ? new Date(d.date + "T12:00:00").toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"}) : "";
    if (fullBtn) {
      fullBtn.hidden = !d.text;
      fullBtn.onclick = () => { if (window.openSheet && window.md) openSheet(`<div class="sh-title" id="sh-title">Claude's full report</div><div class="md" style="margin-top:10px">${md(d.text)}</div>`, fullBtn); };
    }
    if (!d.items.length && !d.also.length) { box.innerHTML = `<div class="empty">The first report comes with the next weekday check-up.</div>`; return; }
    box.innerHTML = `<ul class="findings">${d.items.map(f => `<li class="finding">${draw(f.chart)}<p>${e(f.text)}</p></li>`).join("")}</ul>
      ${d.also.length ? `<ul class="fi-also">${d.also.map(t => `<li>${e(t)}</li>`).join("")}</ul>` : ""}
      <p class="fi-foot">The words are from the report. The charts show the latest numbers.</p>`;
  }
  let tries = 0;
  async function load() {
    let d;
    try { const r = await fetch("/api/findings?bot=" + bot, {cache: "no-store"}); if (!r.ok) return; d = await r.json(); } catch (x) { return; }
    render(d);
    if (d.waiting && tries++ < 5) setTimeout(load, 4000);       // a stock's prices are on their way
  }
  load();
  setInterval(() => { if (!document.hidden) load(); }, 600000);
})();
