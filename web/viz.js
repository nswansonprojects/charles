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
