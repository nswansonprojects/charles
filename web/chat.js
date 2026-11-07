/* "Ask Charles": the chat drawer on the dashboard (chat.py). The conversation stays in this tab. A proposed settings
   change only shows here, and changes nothing until Apply is pressed. Keys never come through this page. A chart's
   numbers come from the server's real prices; the model only picks what to show and writes the notes. */
(function () {
  const e = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const bold = s => s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  const para = t => e(t).split(/\n{2,}/).map(p => {
    const lines = p.split("\n");
    return lines.every(l => /^[-•] /.test(l)) ? `<ul>${lines.map(l => `<li>${bold(l.slice(2))}</li>`).join("")}</ul>` : `<p>${bold(p).replace(/\n/g, "<br>")}</p>`;
  }).join("");
  const STARTS = ["Why does Charles own what it owns?", "What did Charles do this month, and why?", "Show me what moved the market this month"];
  const KEEP = 12;                                 // turns sent with each question: enough context, a bounded bill
  let S = null, msgs = [], busy = false;

  const fab = document.getElementById("chat-open");   // the speech bubble in the page header (webdash.shell)
  if (!fab) return;
  fab.setAttribute("aria-haspopup", "dialog");
  const panel = document.createElement("div");
  panel.className = "chat-panel"; panel.hidden = true; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Ask Charles");
  panel.innerHTML = `<div class="chat-head"><h2>Ask Charles</h2><span class="spend" id="chat-spend"></span>
      <button class="icon" id="chat-close" type="button" aria-label="Close"><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M3 3l8 8M11 3l-8 8"/></svg></button></div>
    <div class="chat-log" id="chat-log" aria-live="polite"></div>
    <form class="chat-form" id="chat-form" hidden><textarea id="chat-input" rows="1" maxlength="4000" placeholder="Ask about Charles…" aria-label="Your question"></textarea><button class="chat-btn primary" type="submit" id="chat-send">Send</button></form>`;
  const scrim = document.createElement("div");       // on a phone the page steps back while it's open; a computer makes room instead
  scrim.className = "chat-scrim"; scrim.hidden = true;
  document.body.append(scrim, panel);
  const $ = id => document.getElementById(id), log_ = $("chat-log"), input = $("chat-input");

  const money = v => "$" + (+v).toFixed(2);
  function showSpend() { $("chat-spend").textContent = S && S.enabled && !S.demo ? `about ${money(S.spend_usd)} of ${money(S.cap_usd)} this month` : ""; }
  function scroll() { log_.scrollTop = log_.scrollHeight; }
  function add(html, cls) { const d = document.createElement("div"); d.className = cls; d.innerHTML = html; log_.appendChild(d); scroll(); return d; }

  function intro() {
    $("chat-form").hidden = !S.enabled;
    if (S.why === "login") {
      log_.innerHTML = `<div class="chat-empty">The chat needs the dashboard login, or anyone who finds this page could spend your money. Over SSH, run <code>charles-login</code> and open its link here.</div>`;
    } else if (S.why === "key") {
      log_.innerHTML = `<div class="chat-empty"><p style="margin-top:0">The chat uses your own Anthropic API key, on your own bill. It can explain anything Charles does and propose a settings change, which you then have to apply yourself. It can't place orders.</p>Make a key at <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer">console.anthropic.com</a>, then over SSH run <code>charles-keys --set ANTHROPIC_API_KEY</code> and paste it. The key stays on your server, and the chat spends at most ${money(S.cap_usd)} a month until you change that with <code>charles-chat --cap</code>.</div>`;
    } else {
      const lead = S.demo ? "Saved answers from a real test. This preview has no API key, so your own questions won't reach Claude here." : "Ask anything about what Charles holds and does. It explains; it isn't investment advice, and an AI's answers can be wrong.";
      log_.innerHTML = `<div class="chat-empty">${lead}${(S.starts || STARTS).map(q => `<button type="button" data-q="${e(q)}">${e(q)}</button>`).join("")}
        ${S.undo ? `<button type="button" id="chat-undo">Undo the last settings change</button>` : ""}</div>`;
      log_.querySelectorAll("[data-q]").forEach(b => b.onclick = () => ask(b.dataset.q));
      if ($("chat-undo")) $("chat-undo").onclick = () => control({action: "undo-settings"}, $("chat-undo"), "Undoing…");
    }
    showSpend();
  }

  // On a computer the page moves over to make room (shell.css), so the chat never lies on a card; the charts redraw at their new width.
  function shown(on) {
    panel.hidden = !on; scrim.hidden = !on;
    document.body.classList.toggle("chat-open", on);
    fab.classList.toggle("on", on);
    dispatchEvent(new Event("resize"));
  }
  async function open() {
    shown(true);
    if (!S) {
      log_.innerHTML = `<div class="chat-empty">Loading…</div>`;
      try { S = await (await fetch("/api/chat/status", {cache: "no-store"})).json(); intro(); }
      catch (x) { log_.innerHTML = `<div class="chat-msg err">Couldn't load the chat. Is the dashboard running?</div>`; S = null; }
    }
    if (S && S.enabled) input.focus();
    scroll();
  }
  function close() { shown(false); fab.focus(); }
  fab.onclick = () => panel.hidden ? open() : close(); $("chat-close").onclick = close; scrim.onclick = close;
  document.addEventListener("keydown", ev => { if (ev.key === "Escape" && !panel.hidden) close(); });

  async function ask(text) {
    if (busy || !text.trim()) return;
    busy = true; $("chat-send").disabled = true;
    if (!msgs.length) log_.innerHTML = "";
    msgs.push({role: "user", content: text});
    add(e(text), "chat-msg me");
    input.value = ""; input.style.height = "";
    const wait = add("Looking into it…", "chat-msg wait");
    let answer = null, atTop = null;
    try {
      let send = msgs.slice(-KEEP);
      while (send.length && send[0].role !== "user") send = send.slice(1);
      const r = await fetch("/api/chat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({messages: send})});
      const res = await r.json();
      wait.remove();
      if (!res.ok) { msgs.pop(); add(e(res.error || "That didn't work. Try again."), "chat-msg err"); }
      else {
        const charts = res.charts || [];
        // Plain text is all that goes back with the next question, so it says what was on screen ("zoom in on that drop").
        msgs.push({role: "assistant", content: res.text + charts.map(c => `\n\n[You drew a chart: ${c.title}, ${c.start} to ${c.end}.]`).join("")});
        answer = add(para(res.text), "chat-msg");
        charts.forEach(c => log_.appendChild(chartBlock(c)));
        if (res.proposal) proposal(res.proposal);
        S.spend_usd = res.spend_usd; S.cap_usd = res.cap_usd; showSpend();
        if (charts.length) atTop = answer;
      }
    } catch (x) { wait.remove(); msgs.pop(); add("The dashboard didn't answer. Try again in a moment.", "chat-msg err"); }
    busy = false; $("chat-send").disabled = false;
    if (atTop) log_.scrollTop += atTop.getBoundingClientRect().top - log_.getBoundingClientRect().top - 8;   // start of the answer, not the end of the chart
    else scroll();
  }
  $("chat-form").onsubmit = ev => { ev.preventDefault(); ask(input.value); };
  input.addEventListener("keydown", ev => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); ask(input.value); } });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 120) + "px"; });

  const val = (c, v) => /_PCT$/.test(c.name) && typeof v === "number" ? +(v * 100).toFixed(1) + "%" : v === true ? "On" : v === false ? "Off" : v;
  function proposal(p) {
    const box = add(`<h3>Proposed change</h3>${p.reason ? `<div class="fine" style="margin:0 0 8px">${e(p.reason)}</div>` : ""}
      ${p.changes.map(c => `<div class="row"><span>${e(c.label)}</span><b class="num">${e(val(c, c.old))} → ${e(val(c, c.new))}</b></div>`).join("")}
      <div class="bt"></div>
      <div class="acts"><button class="chat-btn primary" type="button" data-a="apply">Apply</button><button class="chat-btn" type="button" data-a="bt">Run the backtest</button></div>
      <div class="fine">Nothing has changed yet. Applying it changes how Charles trades from its next daily check. You can undo it here afterwards.</div>`, "chat-prop");
    const [ap, bt] = [box.querySelector('[data-a="apply"]'), box.querySelector('[data-a="bt"]')];
    ap.onclick = () => settings(p, false, ap, box);
    bt.onclick = () => settings(p, true, bt, box);
  }

  async function settings(p, backtest, btn, box) {
    const was = btn.textContent;
    btn.disabled = true; btn.textContent = backtest ? "Running it, about a minute…" : "Applying…";
    try {
      const before = await jobOnFile();
      const r = await fetch("/api/chat/apply", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({settings: p.settings, reason: p.reason, backtest})});
      const res = await r.json();
      if (!r.ok) throw new Error(res.error || r.status);
      const j = await job(backtest ? "backtest-settings" : "apply-settings", before);
      if (j.status !== "done") throw new Error(j.result || "It didn't finish. Try again.");
      if (backtest) { showBacktest(j.backtest, box.querySelector(".bt")); btn.textContent = was; btn.disabled = false; }
      else { btn.textContent = "Applied"; box.querySelector(".fine:last-child").textContent = j.result; S.undo = true; }
    } catch (x) { btn.disabled = false; btn.textContent = was; add(e(x.message), "chat-msg err"); }
  }

  const jobOnFile = async () => { try { return ((await (await fetch("/api/updates", {cache: "no-store"})).json()).job || {}).at; } catch (x) { return undefined; } };
  // The root service does the work: wait for it to report back. `before` is the job on file when we asked: an old result says nothing about this request.
  async function job(action, before) {
    for (let i = 0; i < 120; i++) {
      await new Promise(ok => setTimeout(ok, 2500));
      try {
        const j = (await (await fetch("/api/updates", {cache: "no-store"})).json()).job || {};
        if (j.at !== before && j.status === "refused") return j;
        if (j.at !== before && j.action === action && !["queued", "running"].includes(j.status)) return j;
      } catch (x) {}
    }
    return {status: "failed", result: "It's taking longer than expected. Check back in a few minutes."};
  }

  function showBacktest(b, el) {
    const fmt = r => `${(+r.cagr_pct).toFixed(1)}% a year, worst fall ${(+r.max_drawdown_pct).toFixed(1)}%`;
    el.innerHTML = `<div class="row"><span>Now</span><b class="num">${e(fmt(b.now.stocks))}</b></div><div class="row"><span>With this change</span><b class="num">${e(fmt(b.with_change.stocks))}</b></div>
      <div class="row"><span>Sector funds, now</span><b class="num">${e(fmt(b.now.sectors))}</b></div><div class="row"><span>Sector funds, with it</span><b class="num">${e(fmt(b.with_change.sectors))}</b></div>
      <div class="fine">Backtest ${e(b.history)}. Past results, not a promise. The company list is today's, so that test flatters the plan; sector funds don't have that bias.</div>`;
    scroll();
  }

  /* A live chart: the line, numbered pins on the days the notes belong to, and a readout that follows a finger or the
     arrow keys. One ticker shows dollars, in green or red by where it ended; several show percent change from the start. */
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const day = d => new Date(d + "T12:00:00");
  const when = (d, o) => day(d).toLocaleDateString("en-US", o || {month: "short", day: "numeric"});
  const signed = (v, dp = 1) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(dp) + "%";
  const dollars = v => "$" + v.toLocaleString("en-US", {minimumFractionDigits: v < 1 ? 4 : 2, maximumFractionDigits: v < 1 ? 4 : 2});
  const tone = v => v > 0 ? "up" : v < 0 ? "down" : "";
  function ticks(lo, hi, n) {
    const span = hi - lo || 1, mag = Math.pow(10, Math.floor(Math.log10(span / n)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(x => span / x <= n) || 10 * mag, out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  }

  function chartBlock(c) {
    const fig = document.createElement("figure"), main = c.series[0], many = c.series.length > 1;
    const span = (day(c.end) - day(c.start)) / 864e5;
    fig.className = "cc";
    fig.innerHTML = `<figcaption><b class="cc-title">${e(c.title)}</b>
        <span class="cc-moves">${c.series.map((s, i) => `<span class="cc-move">${many ? `<i class="cc-key k${i}"></i>` : ""}${e(s.label)} <b class="${tone(s.change_pct)}">${signed(s.change_pct)}</b></span>`).join("")}
        <span class="cc-span">${when(c.start, {month: "short", day: "numeric", year: span > 300 ? "numeric" : undefined})} to ${when(c.end, {month: "short", day: "numeric", year: span > 300 ? "numeric" : undefined})}</span></span></figcaption>
      <div class="cc-plot"></div>
      ${c.notes.length ? `<ol class="cc-notes">${c.notes.map((n, i) => `<li><button type="button" data-n="${i}"><i>${i + 1}</i><span><time>${when(n.date)}</time>${e(n.text)}</span></button></li>`).join("")}</ol>` : ""}
      <p class="cc-src">Prices: ${e(c.source)}. Looking back, not a forecast.</p>`;
    const plot = fig.querySelector(".cc-plot"), rows = [...fig.querySelectorAll(".cc-notes button")];
    const lines = c.series.map(s => s.points.map(([d, v]) => ({d, t: +day(d), v, pct: (v / s.points[0][1] - 1) * 100})));
    const val = p => many ? p.pct : p.v;
    const pts = lines[0], n = pts.length, up = main.change_pct >= 0;
    const index = d => { let k = 0; pts.forEach((p, i) => { if (Math.abs(p.t - +day(d)) < Math.abs(pts[k].t - +day(d))) k = i; }); return k; };
    let drawn = false, cur = -1, go = () => {};

    function draw() {
      const W = plot.clientWidth, H = plot.clientHeight;
      if (!W || !H) return;
      const m = {l: many ? 44 : 54, r: 10, t: 18, b: 22}, all = lines.flat().map(val);
      let lo = Math.min(...all), hi = Math.max(...all);
      const pad = Math.max((hi - lo) * .12, many ? .4 : hi * .002); lo -= pad; hi += pad;
      const t0 = pts[0].t, t1 = pts[n - 1].t;
      const X = t => m.l + (t - t0) * (W - m.l - m.r) / (t1 - t0 || 1), Y = v => m.t + (hi - v) * (H - m.t - m.b) / (hi - lo);
      const path = l => "M" + l.map(p => X(p.t).toFixed(1) + "," + Y(val(p)).toFixed(1)).join("L");
      let axes = "";
      for (const v of ticks(lo, hi, 4)) axes += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${m.l - 7}" y="${Y(v) + 4}" text-anchor="end">${many ? signed(v, 0) : "$" + (v >= 10000 ? (v / 1000).toFixed(0) + "k" : v >= 100 ? v.toFixed(0) : v.toFixed(2))}</text>`;
      const nt = Math.max(1, Math.min(4, Math.floor((W - m.l - m.r) / 88)));
      for (let k = 0; k <= nt; k++) {
        const t = t0 + k * (t1 - t0) / nt, o = span > 900 ? {year: "numeric"} : span > 200 ? {month: "short", year: "2-digit"} : {month: "short", day: "numeric"};
        axes += `<text x="${m.l + k * (W - m.l - m.r) / nt}" y="${H - 5}" text-anchor="${k === 0 ? "start" : k === nt ? "end" : "middle"}">${new Date(t).toLocaleDateString("en-US", o)}</text>`;
      }
      const pin = (nt_, i) => { const p = pts[index(nt_.date)], x = X(p.t), y = Y(val(p));
        return `<g class="cc-pin" data-n="${i}" style="--i:${i}" transform="translate(${x.toFixed(1)},${y.toFixed(1)})"><circle class="hit" r="16"/><circle class="o" r="10"/><text y="4" text-anchor="middle">${i + 1}</text></g>`; };
      const mark = tr => { const p = pts[index(tr.date)], x = X(p.t), y = Y(val(p)), b = tr.side === "buy";
        return `<path class="cc-tr ${tr.side}" d="M${x.toFixed(1)},${(y + (b ? 7 : -7)).toFixed(1)}l5,${b ? 8 : -8}h-10z"/>`; };
      plot.innerHTML = `<svg viewBox="0 0 ${W} ${H}" tabindex="0" role="img" aria-label="${e(c.title)}: ${c.series.map(s => `${e(s.label)} ${signed(s.change_pct)}`).join(", ")}. Arrow keys read values.">
        <g class="cc-axis">${axes}</g>
        ${many ? "" : `<path class="cc-fill ${up ? "up" : "down"}${drawn || still ? " in" : ""}" d="${path(pts)}L${X(t1).toFixed(1)},${H - m.b}L${X(t0).toFixed(1)},${H - m.b}Z"/>`}
        <g class="cc-lines${drawn || still ? " in" : ""}">${lines.map((l, i) => `<path class="cc-line ${many ? "k" + i : up ? "up" : "down"}" d="${path(l)}"/>`).slice().reverse().join("")}</g>
        <line class="cc-cross" y1="${m.t}" y2="${H - m.b}"/>
        <circle class="cc-dot" r="4.5"/>
        ${many ? "" : c.trades.map(mark).join("")}
        <g class="cc-pins${drawn || still ? " in" : ""}">${c.notes.map(pin).join("")}</g>
      </svg><div class="cc-tip" hidden></div>`;
      const svg = plot.querySelector("svg"), tip = plot.querySelector(".cc-tip"), cross = plot.querySelector(".cc-cross"), dot = plot.querySelector(".cc-dot");
      const pins = [...plot.querySelectorAll(".cc-pin")];

      const show = i => {
        cur = Math.max(0, Math.min(n - 1, i));
        const p = pts[cur], x = X(p.t);
        cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.classList.add("on");
        dot.setAttribute("cx", x); dot.setAttribute("cy", Y(val(p))); dot.classList.add("on");
        const note = c.notes.findIndex(z => index(z.date) === cur);
        const row = l => { let q = l[0]; for (const r of l) if (r.t <= p.t) q = r; return q; };
        const moved = many ? c.series.map((s, k) => `<div class="r"><span>${e(s.label)}</span><b class="${tone(row(lines[k]).pct)}">${signed(row(lines[k]).pct)}</b></div>`).join("")
          : `<div class="r"><span>Close</span><b>${dollars(p.v)}</b></div><div class="r"><span>Since ${when(c.start)}</span><b class="${tone(p.pct)}">${signed(p.pct)}</b></div>`;
        const did = many ? "" : c.trades.filter(tr => index(tr.date) === cur).map(tr => `<div class="w">Charles ${tr.side === "buy" ? "bought" : "sold"} ${dollars(tr.value)}</div>`).join("");
        tip.innerHTML = `<div class="t">${when(p.d, {month: "short", day: "numeric", year: "numeric"})}</div>${moved}${did}${note >= 0 ? `<div class="w">${e(c.notes[note].text)}</div>` : ""}`;
        tip.hidden = false;
        const w = tip.offsetWidth;
        tip.style.left = Math.max(0, x + 12 + w > W ? x - w - 12 : x + 12) + "px";
        rows.forEach((b, k) => b.classList.toggle("on", k === note));
        pins.forEach((g, k) => g.classList.toggle("on", k === note));
      };
      const hide = () => { tip.hidden = true; cross.classList.remove("on"); dot.classList.remove("on"); rows.forEach(b => b.classList.remove("on")); pins.forEach(g => g.classList.remove("on")); cur = -1; };
      const at = ev => { const r = svg.getBoundingClientRect(), t = t0 + (ev.clientX - r.left - m.l) * (t1 - t0) / (W - m.l - m.r);
        let k = 0; pts.forEach((p, i) => { if (Math.abs(p.t - t) < Math.abs(pts[k].t - t)) k = i; }); show(k); };
      svg.onpointermove = at; svg.onpointerdown = at;
      svg.onpointerleave = ev => { if (ev.pointerType === "mouse") hide(); };
      svg.onblur = hide;
      svg.onkeydown = ev => {
        if (ev.key === "ArrowLeft") { show(cur < 0 ? n - 1 : cur - 1); ev.preventDefault(); }
        if (ev.key === "ArrowRight") { show(cur < 0 ? 0 : cur + 1); ev.preventDefault(); }
      };
      go = show;
      if (!drawn && !still) requestAnimationFrame(() => requestAnimationFrame(() => plot.querySelectorAll(".cc-lines, .cc-pins, .cc-fill").forEach(x => x.classList.add("in"))));
      drawn = true;
    }
    rows.forEach((b, i) => { b.onclick = () => { go(index(c.notes[i].date)); }; });
    // Drawn when it has a size: at once, or when the drawer is opened again if it was closed while Charles was answering.
    if (window.ResizeObserver) { let w0 = 0; new ResizeObserver(([x]) => { const w = Math.round(x.contentRect.width); if (w && w !== w0) { w0 = w; draw(); } }).observe(plot); }
    else requestAnimationFrame(draw);
    return fig;
  }

  async function control(body, btn, label) {
    btn.disabled = true; btn.textContent = label;
    try {
      const r = await fetch("/api/control", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      const res = await r.json();
      if (!r.ok) throw new Error(res.error || r.status);
      const j = await job(body.action);
      btn.textContent = j.result || "Done"; S.undo = false;
    } catch (x) { btn.disabled = false; btn.textContent = "Undo the last settings change"; add(e(x.message), "chat-msg err"); }
  }
})();
