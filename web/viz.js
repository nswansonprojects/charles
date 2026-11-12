/* The Plan and Self-Tune tabs' big visuals: a dial and a progress ring, as SVG strings. The stocks and crypto pages load this before their own script. */
"use strict";
const vizEsc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

/* A half-circle dial. zones: [[from, to, css colour]]; the needle shows `value`, the big text sits under it. */
function dial({min, max, value, zones, big, sub, tone = ""}) {
  const R = 82, CX = 100, CY = 96, at = v => Math.PI * (1 - (Math.max(min, Math.min(max, v)) - min) / (max - min));
  const pt = (v, r = R) => [CX + r * Math.cos(at(v)), CY - r * Math.sin(at(v))].map(n => n.toFixed(1));
  const arc = (a, b) => { const [x0, y0] = pt(a), [x1, y1] = pt(b); return `M${x0},${y0}A${R},${R} 0 0 1 ${x1},${y1}`; };
  const [nx, ny] = value == null ? [CX, CY - 60] : pt(value, 62);
  return `<div class="dial"><svg viewBox="0 0 200 106" role="img" aria-label="${vizEsc(big)} ${vizEsc(sub)}">
      ${zones.map(([a, b, c]) => `<path d="${arc(a, b)}" fill="none" stroke="${c}" stroke-width="15" stroke-linecap="butt"/>`).join("")}
      ${value == null ? "" : `<line x1="${CX}" y1="${CY}" x2="${nx}" y2="${ny}" stroke="var(--text)" stroke-width="4" stroke-linecap="round"/><circle cx="${CX}" cy="${CY}" r="7" fill="var(--text)"/>`}
    </svg><div class="dial-big num ${tone}">${vizEsc(big)}</div><div class="dial-sub">${vizEsc(sub)}</div></div>`;
}

/* A progress ring: `p` of 1, with a short word or number in the middle. */
function ring(p, inner, label, sub, color = "var(--series-1)") {
  const R = 34, C = 2 * Math.PI * R, pc = Math.max(0, Math.min(1, p || 0));
  return `<div class="ring"><div class="ring-g"><svg viewBox="0 0 84 84" role="img" aria-label="${vizEsc(label)}: ${Math.round(pc * 100)}% of the way">
      <circle cx="42" cy="42" r="${R}" fill="none" stroke="var(--surface-2)" stroke-width="9"/>
      <circle cx="42" cy="42" r="${R}" fill="none" stroke="${color}" stroke-width="9" stroke-linecap="round" stroke-dasharray="${(C * pc).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 42 42)"/></svg>
      <span class="ring-in num">${vizEsc(inner)}</span></div><b>${vizEsc(label)}</b><span class="ring-sub">${vizEsc(sub)}</span></div>`;
}

/* One green or red bar a year. yearly: {"2007": 12.3, ...}; pct formats a percentage. One year far bigger than the rest (crypto's 2017) would
   flatten all the others, so a bar stops at three times the typical year and prints its real number instead. */
function yearBars(yearly, label, pct) {
  const ys = Object.keys(yearly).sort(), sizes = ys.map(y => Math.abs(yearly[y])).sort((a, b) => a - b);
  if (!ys.length) return "";
  const top = Math.max(Math.min(sizes.at(-1), 3 * sizes[sizes.length >> 1]), 1);
  const bar = v => `<i style="height:${Math.min(100, Math.abs(v) / top * 100).toFixed(0)}%"></i>`;
  return `<div class="label">${vizEsc(label)}</div><div class="yr-row" role="img" aria-label="Simulated yearly returns, ${vizEsc(ys[0])} to ${vizEsc(ys.at(-1))}">${ys.map(y => { const v = yearly[y];
    return `<div class="yr" title="${vizEsc(y)}: ${vizEsc(pct(v, 1))}"><span class="yr-up">${v > 0 ? bar(v) : ""}${v > top ? `<small>${vizEsc(pct(v, 0))}</small>` : ""}</span><span class="yr-down">${v < 0 ? bar(v) : ""}${-v > top ? `<small>${vizEsc(pct(v, 0))}</small>` : ""}</span><em>${vizEsc(y.slice(2))}</em></div>`; }).join("")}</div>`;
}

/* The plan in each kind of market (evaluate.by_kind), beside the market itself: two bars from a middle zero line, per month.
   K: {kinds: {calm|fall|rebound|choppy: {share_pct, plan_pct, market_pct}}, weak, today, provisional_days, from, live}. Returns {html, note}. */
const KIND_NAMES = {calm: "Calm rise", fall: "Fall", rebound: "Rebound", choppy: "Choppy"};
function kindBars(K, market, pct) {
  const order = ["calm", "fall", "rebound", "choppy"].filter(k => K.kinds[k] && K.kinds[k].plan_pct != null);
  const top = Math.max(1, ...order.flatMap(k => [Math.abs(K.kinds[k].plan_pct), Math.abs(K.kinds[k].market_pct || 0)]));
  const row = (who, v, mk) => `<div class="kd-r${mk ? " mk" : ""}"><span>${vizEsc(who)}</span><span class="kd-t"><i class="${v < 0 ? "kd-dn" : "kd-up"}" style="width:${(Math.abs(v || 0) / top * 50).toFixed(1)}%"></i></span><b class="num ${v > 0 ? "up" : v < 0 ? "down" : ""}">${vizEsc(pct(v, 1))}</b></div>`;
  const html = order.map(k => { const r = K.kinds[k];
    return `<div class="kd" role="img" aria-label="${KIND_NAMES[k]}: the plan ${vizEsc(pct(r.plan_pct, 1))} a month, ${vizEsc(market)} ${vizEsc(pct(r.market_pct, 1))}">
      <div class="kd-h"><b>${KIND_NAMES[k]}</b>${K.weak === k ? `<span class="chip">Weakest here</span>` : `<span>${Math.round(r.share_pct)}% of days</span>`}</div>${row("The plan", r.plan_pct)}${row(market, r.market_pct, true)}</div>`; }).join("");
  const live = Object.entries(K.live || {}).map(([k, r]) => `${r.days} ${KIND_NAMES[k].toLowerCase()} day${r.days === 1 ? "" : "s"}: the plan ${pct(r.plan_pct, 1)}, ${market} ${pct(r.market_pct, 1)}`);
  const note = `Simulated since ${(K.from || "").slice(0, 4)}, per month.` + (K.today ? ` Right now the market reads as ${KIND_NAMES[K.today].toLowerCase()}; ${K.provisional_days > 90 ? "the days since its last high" : `the last ${K.provisional_days} days`} can still change, since a fall only counts once its low is known.` : "")
    + (live.length ? ` Your real book so far: ${live.join("; ")}.` : "")
    + (K.weak ? ` The rule check won't propose a change that makes ${KIND_NAMES[K.weak].toLowerCase()}s worse.` : "");
  return {html, note};
}
