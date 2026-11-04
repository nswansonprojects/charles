/* What Home and the Leaderboard share: formatting and two small charts. */
"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const usd = (v, d = 2) => v == null ? "—" : (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
const sgnUsd = (v, d = 2) => v == null ? "—" : (v > 0 ? "+" : "") + usd(v, d);
const pct = (v, d = 2) => v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d) + "%";
const cls = v => v == null ? "" : v > 0.004 ? "up" : v < -0.004 ? "down" : "flat";
const ago = iso => { if (!iso) return ""; const s = (Date.now() - new Date(iso)) / 1000;
  return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : s < 864000 ? Math.round(s / 86400) + " d ago"
    : new Date(iso).toLocaleDateString("en-US", {month: "short", day: "numeric"}); };
const dayFmt = (iso, o = {month: "short", day: "numeric"}) => new Date(iso.length === 10 ? iso + "T12:00:00" : iso).toLocaleDateString("en-US", o);
const getJSON = async u => (await fetch(u, {cache: "no-store"})).json();
function niceTicks(lo, hi, n) {
  const span = hi - lo || 1, mag = Math.pow(10, Math.floor(Math.log10(span / n)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

/* A big number shrinks to fit its box instead of overflowing it: long totals and small phones. */
function fitText(el) {
  if (!el) return;
  el.style.fontSize = "";
  const k = el.clientWidth / el.scrollWidth;
  if (el.clientWidth && k < 1) el.style.fontSize = (parseFloat(getComputedStyle(el).fontSize) * k * .99).toFixed(1) + "px";
}

/* A big number's first look counts up to it from zero, once per element. */
function countUp(el, to, fmt) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches || el.dataset.counted || document.hidden) { el.textContent = fmt(to); return; }
  el.dataset.counted = 1;
  const t0 = performance.now();
  const step = t => { const k = Math.min(1, (t - t0) / 800);
    el.textContent = fmt(to * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

/* Money earned over time, measured from $0. Above the line is green and below it red, so position says what the colour
   says. `rows` is [[iso, dollars], ...]; o.axis draws the axes and takes the keyboard; without it the chart is a small one;
   o.reveal draws the line in from the left, once. */
let chartN = 0;
// The record's once-a-day rows are stamped midnight UTC. They belong to that date wherever you are, not the evening before.
const dayRow = iso => /T00:00:00(\.\d+)?\+00:00$/.test(iso) ? iso.slice(0, 10) + "T12:00:00" : iso;
function zeroChart(box, rows, o = {}) {
  if (!box.clientWidth || !box.clientHeight) return;
  if (rows.length < 2) { box.innerHTML = `<div class="empty">${esc(o.empty || "Not enough history yet.")}</div>`; return; }
  const W = box.clientWidth, H = box.clientHeight, n = rows.length, m = o.axis ? {l: 52, r: 10, t: 10, b: 22} : {l: 1, r: 6, t: 6, b: 6};
  // Only a once-a-day row moves to midday: an hourly row at midnight UTC stays put, or the line would jump ahead and run
  // back over itself (owner 2026-11-04: "going sideways and overlapping"). Time never goes backwards along the line.
  const raw = rows.map(r => +new Date(r[0])), far = (i, j) => j < 0 || j >= n || Math.abs(raw[j] - raw[i]) >= 20 * 36e5;
  const ts = rows.map((r, i) => far(i, i - 1) && far(i, i + 1) ? +new Date(dayRow(r[0])) : raw[i]);
  for (let i = 1; i < n; i++) ts[i] = Math.max(ts[i], ts[i - 1]);
  const vals = rows.map(r => r[1]), t0 = ts[0], span = Math.max(1, ts[n - 1] - t0);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const pad = Math.max((hi - lo) * .1, .5); lo -= pad; hi += pad;
  const X = i => m.l + (ts[i] - t0) * (W - m.l - m.r) / span, Y = v => m.t + (hi - v) * (H - m.t - m.b) / (hi - lo);
  const y0 = Y(0), id = "z" + (++chartN), cut = (y0 / H * 100).toFixed(2) + "%";
  const line = "M" + vals.map((v, i) => X(i).toFixed(1) + "," + Y(v).toFixed(1)).join("L");
  const last = vals[n - 1], tone = last >= 0 ? "--good" : "--critical";
  let axes = "";
  if (o.axis) {
    for (const v of niceTicks(lo, hi, 4)) if (v) axes += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--grid)"/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${sgnUsd(v, 0)}</text>`;
    axes += `<text x="${m.l - 8}" y="${y0 + 4}" text-anchor="end">$0</text>`;
    const nt = Math.max(1, Math.min(4, Math.floor((W - m.l - m.r) / 110)));
    for (let k = 0; k <= nt; k++) axes += `<text x="${m.l + k * (W - m.l - m.r) / nt}" y="${H - 5}" text-anchor="${k === 0 ? "start" : k === nt ? "end" : "middle"}">${new Date(t0 + k * span / nt).toLocaleDateString("en-US", {month: "short", day: "numeric"})}</text>`;
  }
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.label || "Earnings over time")}, ending at ${sgnUsd(last)}.${o.axis ? " Arrow keys read values." : ""}"${o.axis ? ' tabindex="0"' : ""}>
    <defs><linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1="0" y2="${H}"><stop offset="${cut}" stop-color="var(--good)"/><stop offset="${cut}" stop-color="var(--critical)"/></linearGradient></defs>
    <g class="axis">${axes}</g>
    <line x1="${m.l}" x2="${W - m.r}" y1="${y0}" y2="${y0}" stroke="var(--ref)" stroke-width="1"/>
    <g${o.reveal ? ' class="plot-in"' : ""}><path d="${line}L${X(n - 1).toFixed(1)},${y0.toFixed(1)}L${X(0).toFixed(1)},${y0.toFixed(1)}Z" fill="url(#${id})" style="opacity:var(--chart-fill, .17)"/>
    <path d="${line}" fill="none" stroke="url(#${id})" stroke-width="${o.axis ? 2.6 : 2.4}" stroke-linejoin="round" stroke-linecap="round"/></g>
    <line class="cross" y1="${m.t}" y2="${H - m.b}" stroke="var(--text-2)" opacity="0"/>
    <circle class="end" cx="${X(n - 1)}" cy="${Y(last)}" r="4" fill="var(${tone})" stroke="var(--surface)" stroke-width="2"/>
  </svg><div class="tip"></div>`;
  const svg = box.querySelector("svg"), tip = box.querySelector(".tip"), cross = box.querySelector(".cross"), dot = box.querySelector(".end");
  let cur = -1;
  const show = i => {
    cur = Math.max(0, Math.min(n - 1, i)); const x = X(cur), v = vals[cur];
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("opacity", .5);
    dot.setAttribute("cx", x); dot.setAttribute("cy", Y(v)); dot.setAttribute("fill", `var(${v >= 0 ? "--good" : "--critical"})`);
    tip.innerHTML = `<div class="t">${new Date(ts[cur]).toLocaleDateString("en-US", {month: "short", day: "numeric"})}</div><div class="r"><span>Earned</span><b class="num">${sgnUsd(v)}</b></div>`;
    tip.style.display = "block";
    const w = tip.offsetWidth; tip.style.left = Math.max(0, x + 12 + w > W ? x - w - 12 : x + 12) + "px";
  };
  const hide = () => { tip.style.display = "none"; cross.setAttribute("opacity", 0); dot.setAttribute("cx", X(n - 1)); dot.setAttribute("cy", Y(last)); dot.setAttribute("fill", `var(${tone})`); };
  const at = ev => { const r = svg.getBoundingClientRect(), t = t0 + (ev.clientX - r.left - m.l) / (W - m.l - m.r) * span;
    let k = 0; for (let i = 0; i < n; i++) if (Math.abs(ts[i] - t) < Math.abs(ts[k] - t)) k = i; show(k); };
  svg.onpointermove = ev => { if (o.axis || ev.pointerType === "mouse") at(ev); };
  svg.onpointerleave = hide; svg.onblur = hide;
  if (o.axis) {
    svg.onpointerdown = at;
    svg.onpointerup = ev => { if (ev.pointerType !== "mouse") setTimeout(hide, 1500); };
    svg.onkeydown = ev => { if (ev.key === "ArrowLeft") { show(cur < 0 ? n - 1 : cur - 1); ev.preventDefault(); }
      if (ev.key === "ArrowRight") { show(cur < 0 ? 0 : cur + 1); ev.preventDefault(); } };
  }
}

/* A row's small line: red or green by where it ended, with the return's sign printed beside it. */
function spark(vals, ret) {
  if (!vals || vals.length < 2) return "";
  const W = 120, H = 34, lo = Math.min(...vals), hi = Math.max(...vals), k = (hi - lo) || 1;
  const d = "M" + vals.map((v, i) => (1 + i * (W - 6) / (vals.length - 1)).toFixed(1) + "," + (3 + (hi - v) * (H - 6) / k).toFixed(1)).join("L");
  return `<svg class="lb-spark ${ret == null ? "" : ret >= 0 ? "up" : "down"}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
}
