/* The Plan tab: set the plan yourself with sliders, or copy someone from the leaderboard into them (owner 2026-10-31). Nothing changes until
   Save. Save and Undo go through /api/settings or /api/leaderboard/copy: the dashboard only leaves a request, and the root job checks it again.
   stocks.html and crypto.html keep their own helpers, so this keeps its own too, inside here. */
(function () {
  const box = document.getElementById("tune"), copyBox = document.getElementById("tune-copy");
  if (!box) return;
  const bot = box.dataset.bot;
  const e = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const pc = v => +(v * 100).toFixed(1) + "%";
  const days = v => v + " days";
  const same = (a, b) => typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-9 : a === b;
  const onOff = v => v ? "On" : "Off";
  const pickSize = d => pc(d.PICK_PCT);

  // Each row: the setting, its words, and how a value reads. `pct` sliders work in whole percents, so the values stay clean numbers.
  const ROWS = {
    stocks: [
      {k: "CORE_PCT", label: "S&P 500 core", pct: true, step: 5, show: pc, why: "index-fund", about: "An index fund of 500 big US companies."},
      {k: "PICKS", label: "Momentum picks", step: 1, show: v => v, why: "momentum",
       about: d => d.PICKS ? `The past year's strongest stocks, re-picked monthly, ${pickSize(d)} each.` : "No picks: only the core and T-bills."},
      {k: "TREND_DAYS", label: "Trend line", step: 10, show: days, why: "trend-line", about: "The average the safety switch and the picks are measured against."},
      {k: "SAFETY_SWITCH", label: "Safety switch", toggle: true, why: "safety",
       about: "If SPY ends a month below its trend line, everything moves to T-bills until it recovers."},
      {k: "AGGRESSIVE", label: "Aggressive mode", toggle: true, why: "aggressive",
       about: "While the market is calm and rising, the core moves to a leveraged S&P fund. Off on any choppy day."},
      {k: "AGG_CORE", label: "How much leverage", when: d => d.AGGRESSIVE, sub: true, choices: [["SSO", "2×"], ["UPRO", "3×"]],
       about: d => d.AGG_CORE === "UPRO" ? "A 3× fund falls three times as fast as the market." : "A 2× fund falls twice as fast as the market."},
      {k: "AGG_VOL_MAX", label: "Calm means swings under", when: d => d.AGGRESSIVE, sub: true, pct: true, step: 1, show: pc, about: "How much the market may swing in a year."},
    ],
    crypto: [
      {k: "BTC_PCT", label: "Bitcoin", pct: true, step: 5, show: pc, about: "Its share of the book while its trend is up."},
      {k: "ETH_PCT", label: "Ether", pct: true, step: 5, show: pc, about: "Its share of the book while its trend is up."},
      {k: "TREND_DAYS", label: "Trend line", step: 10, show: days, about: "Under this average, a coin's share waits in cash."},
      {k: "CHECK_DAYS", label: "Check every", step: 1, show: v => v === 1 ? "day" : days(v), about: "How often the book looks. Longer means fewer trades and fees."},
    ],
  }[bot];
  // Everything a saved change can touch, in plain words: the sliders, and what comes along with a copied config.
  const WORDS = {
    stocks: {CORE_PCT: ["S&P 500 core", pc], PICKS: ["Momentum picks", v => v], PICK_PCT: ["Each pick", pc], DRIFT_PCT: ["Fix drift over", pc], TREND_DAYS: ["Trend line", days],
      SAFETY_SWITCH: ["Safety switch", onOff], SWITCH_CHECK: ["Switch checked", v => v], AGGRESSIVE: ["Aggressive mode", onOff], AGG_CORE: ["Aggressive fund", v => v === "UPRO" ? "3×" : "2×"],
      AGG_VOL_MAX: ["Calm means swings under", pc], AGG_VOL_EXIT: ["Aggressive off above", pc]},
    crypto: {BTC_PCT: ["Bitcoin", pc], ETH_PCT: ["Ether", pc], TREND_DAYS: ["Trend line", days], CHECK_DAYS: ["Check every", v => v === 1 ? "day" : days(v)], MIN_TRADE: ["Skip moves under", pc]},
  }[bot];
  const SAVED = {stocks: "Saved. Charles uses them at its next daily check and may trade to match.", crypto: "Saved. The book uses them from its next check."}[bot];

  let V = null, draft = null, copying = null, busy = false, note = {text: "", tone: ""}, rows = [];

  const changes = () => Object.keys(draft).filter(k => !same(draft[k], V.settings[k]));
  const say = (text, tone = "") => { note = {text, tone}; paint(); };

  /* The money, as it would be with the sliders where they are. */
  function money() {
    if (bot === "crypto") {
      const rest = Math.max(0, 1 - draft.BTC_PCT - draft.ETH_PCT);
      return [["Bitcoin", draft.BTC_PCT, "var(--btc)"], ["Ether", draft.ETH_PCT, "var(--eth)"], ["Cash", rest, "var(--cash)"]];
    }
    const picks = draft.PICKS * draft.PICK_PCT, rest = Math.max(0, 1 - draft.CORE_PCT - picks);
    return [["S&P 500", draft.CORE_PCT, "var(--core)"], ["Picks", picks, "var(--pick)"], ["T-bills", rest, "var(--safe)"]];
  }

  /* A move keeps the plan valid, so a slider can't lead to a refusal on Save. */
  function moved(k, v) {
    draft[k] = v;
    copying = null;
    if (bot === "stocks") {
      if (k === "CORE_PCT" || k === "PICKS") {
        // the picks share what the core leaves, up to the 10% a pick may be
        const room = 1 - draft.CORE_PCT;
        if (draft.PICKS) draft.PICK_PCT = Math.min(V.limits.PICK_PCT.max, Math.floor(room / draft.PICKS * 1000 + 1e-6) / 1000);
      }
      if (k === "AGG_VOL_MAX" && draft.AGG_VOL_EXIT < v) draft.AGG_VOL_EXIT = v;
    } else {
      if (k === "BTC_PCT" && draft.BTC_PCT + draft.ETH_PCT > 1) draft.ETH_PCT = +(1 - draft.BTC_PCT).toFixed(2);
      if (k === "ETH_PCT" && draft.BTC_PCT + draft.ETH_PCT > 1) draft.BTC_PCT = +(1 - draft.ETH_PCT).toFixed(2);
    }
    if (note.text) note = {text: "", tone: ""};
  }

  function rowHtml(r) {
    const d = draft, lim = (V.limits || {})[r.k];
    const why = r.why && window.SHEETS_LESSONS ? `<button class="tn-why" type="button" data-open="lesson" data-k="${e(r.why)}" aria-label="More about ${e(r.label)}">?</button>` : "";
    const about = `<p data-about="${e(r.k)}"></p>`;
    if (r.toggle || r.choices) {
      const opts = r.toggle ? [[true, "On"], [false, "Off"]] : r.choices;
      return `<div class="tn-row${r.sub ? " tn-sub" : ""}"><div class="tn-top"><b>${e(r.label)}</b>${why}<div class="tabs" role="group" aria-label="${e(r.label)}">${opts.map(([v, l]) =>
        `<button type="button" data-set="${e(r.k)}" data-val='${JSON.stringify(v)}'>${e(l)}</button>`).join("")}</div></div>${about}</div>`;
    }
    const k = r.pct ? 100 : 1;
    return `<div class="tn-row${r.sub ? " tn-sub" : ""}"><div class="tn-top"><label for="tn-${e(r.k)}"><b>${e(r.label)}</b></label>${why}<output id="tno-${e(r.k)}"></output></div>
      <input class="tn-range" type="range" id="tn-${e(r.k)}" data-slide="${e(r.k)}" data-k="${k}" min="${Math.round(lim.min * k)}" max="${Math.round(lim.max * k)}" step="${r.step}" value="${Math.round(d[r.k] * k)}">
      ${about}</div>`;
  }

  /* The rows are built once per shape, so a slider being dragged is never replaced under the finger. */
  function build() {
    rows = ROWS.filter(r => !r.when || r.when(draft));
    box.querySelector(".tn-rows").innerHTML = rows.map(rowHtml).join("");
    box.querySelectorAll("[data-slide]").forEach(el => el.oninput = () => { moved(el.dataset.slide, +el.value / +el.dataset.k); paint(); });
    box.querySelectorAll("[data-set]").forEach(el => el.onclick = () => {
      const shape = rows.length;
      moved(el.dataset.set, JSON.parse(el.dataset.val));
      if (ROWS.filter(r => !r.when || r.when(draft)).length !== shape) build(); else paint();
    });
    paint();
  }

  function paint() {
    const d = draft, list = changes(), any = !!list.length;
    for (const r of rows) {
      const about = box.querySelector(`[data-about="${r.k}"]`);
      if (about) about.textContent = typeof r.about === "function" ? r.about(d) : r.about || "";
      const out = document.getElementById("tno-" + r.k);
      if (out) out.textContent = r.show(d[r.k]);
      const el = document.getElementById("tn-" + r.k);
      if (el) { const k = +el.dataset.k; el.value = Math.round(d[r.k] * k); el.style.setProperty("--p", (el.value - el.min) / (el.max - el.min) * 100 + "%"); }
    }
    box.querySelectorAll("[data-set]").forEach(b => { const on = JSON.parse(b.dataset.val) === d[b.dataset.set]; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    box.querySelector(".tn-bar").innerHTML = money().filter(m => m[1] > 0.0005).map(([l, v, c]) =>
      `<i style="flex-grow:${v * 100};background:${c}" title="${e(l)} ${pc(v)}"><span>${v >= .1 ? `${e(l)} ` : ""}${pc(v)}</span></i>`).join("");
    box.querySelector(".tn-bar").setAttribute("aria-label", money().filter(m => m[1] > 0.0005).map(([l, v]) => `${l} ${pc(v)}`).join(", "));
    const del = box.querySelector(".tn-diff");
    del.hidden = !any;
    del.innerHTML = !any ? "" : `<div class="sh-h" style="margin-top:0">${copying ? `What changes if you copy ${e(copying.name)}` : "What changes"}</div>` + list.map(k => {
      const [l, f] = WORDS[k] || [k, x => x];
      return `<div class="chg"><span>${e(l)}</span><b class="num">${e(f(V.settings[k]))} → ${e(f(d[k]))}</b></div>`; }).join("");
    box.querySelector(".tn-reset").hidden = Object.keys(V.defaults).every(k => same(d[k], V.defaults[k]));
    const save = box.querySelector(".tn-save");
    save.hidden = !any;
    save.disabled = busy || !V.can;
    save.textContent = busy ? "Saving…" : copying ? `Copy ${copying.name}` : "Save";
    const test = box.querySelector(".tn-test");
    test.hidden = !any || bot !== "stocks";
    test.disabled = busy || !V.can;
    box.querySelector(".tn-back").hidden = !any;
    box.querySelector(".tn-back").disabled = busy;
    const undo = box.querySelector(".tn-undo");
    undo.hidden = any || !V.can_undo || !V.can;
    undo.disabled = busy;
    const m = box.querySelector(".tn-msg");
    m.className = "lb-msg tn-msg " + (note.tone || "");
    m.textContent = note.text;
    m.hidden = !note.text;
    const why = box.querySelector(".tn-whynot");
    why.hidden = V.can || !any;
    why.textContent = V.why || "";
    box.querySelector(".tn-foot").hidden = !any && undo.hidden && !note.text;
    document.querySelectorAll("#tune-copy [data-copy-id]").forEach(b => { const on = !!copying && b.dataset.copyId === copying.id; b.textContent = on ? "Chosen" : "Copy"; b.classList.toggle("primary", on); });
  }

  async function call(path, body) {
    const r = await fetch(path, {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    let j = {};
    try { j = await r.json(); } catch (x) {}
    return {...j, ok: r.ok && j.ok !== false, message: j.message || j.error};
  }
  const lastJob = async () => { try { return (await (await fetch("/api/updates", {cache: "no-store"})).json()).job || {}; } catch (x) { return {}; } };
  /* The root service does the work. `before` is the job on file when we asked: until a new one shows up, an old result says nothing about this request. */
  async function job(action, before) {
    for (let i = 0; i < 60; i++) {
      await new Promise(ok => setTimeout(ok, 1500));
      const j = await lastJob();
      if (j.at !== before && j.action === action && !["queued", "running"].includes(j.status)) return j;
      if (j.at !== before && j.status === "refused") return j;
    }
    return {status: "failed", result: "It's taking longer than expected. Check back in a few minutes."};
  }
  /* One round: ask, wait for the root job if there is one, then look at the file again. */
  async function send(path, body, action, saidDone) {
    busy = true; paint();
    const before = (await lastJob()).at;
    const res = await call(path, body).catch(() => ({ok: false, message: "The dashboard didn't answer. Try again."}));
    let done = res.ok, text = res.message;
    if (res.ok && !res.done) {
      const j = await job(action, before);
      done = j.status === "done";
      text = done ? "" : j.result || "That didn't work.";
    }
    busy = false;
    if (done) { await load(true); say(res.test ? res.message : saidDone, "up"); } else { paint(); say(text || "That didn't work.", "down"); }
    return done;
  }

  function save() {
    if (copying) return send("/api/leaderboard/copy", {id: copying.id}, "copy-config", SAVED);
    const list = changes();
    return send("/api/settings", {bot, settings: Object.fromEntries(list.map(k => [k, draft[k]]))}, "apply-settings", SAVED);
  }

  /* The stock plan on 19 years of past prices, now and with the change. Heavy, so only on request, in the root job. */
  async function historyTest() {
    const list = changes(), keep = box.querySelector(".tn-bt");
    busy = true; say("Testing on past prices. This takes about a minute…");
    const before = (await lastJob()).at;
    const res = await call("/api/settings", {bot, settings: Object.fromEntries(list.map(k => [k, draft[k]])), backtest: true}).catch(() => ({ok: false, message: "The dashboard didn't answer."}));
    let j = null;
    if (res.ok) j = await job("backtest-settings", before);
    busy = false;
    if (!res.ok || !j || j.status !== "done" || !j.backtest) { say((j && j.result) || res.message || "That didn't work.", "down"); return; }
    const f = r => `${r.cagr_pct > 0 ? "+" : ""}${(+r.cagr_pct).toFixed(1)}% a year, worst ${(+r.max_drawdown_pct).toFixed(0)}%`, b = j.backtest;
    keep.hidden = false;
    keep.innerHTML = `<div class="sh-h" style="margin-top:0">On past prices, ${e(b.history)}</div>
      <div class="chg"><span>Now</span><b class="num">${e(f(b.now.stocks))}</b></div><div class="chg"><span>With this change</span><b class="num">${e(f(b.with_change.stocks))}</b></div>
      <div class="chg"><span>Sector funds, now</span><b class="num">${e(f(b.now.sectors))}</b></div><div class="chg"><span>Sector funds, with it</span><b class="num">${e(f(b.with_change.sectors))}</b></div>
      <p class="small muted" style="margin:6px 0 0">Simulated, not a promise. The sector line has no hindsight in its stock list.</p>`;
    say("");
  }

  function shell() {
    box.innerHTML = `<div class="card-head"><h2>Set it yourself</h2><button class="link-btn tn-reset" type="button" style="margin-left:auto">Back to the default plan</button></div>
      <div class="tn-bar" role="img"></div>
      <div class="tn-rows"></div>
      <div class="tn-diff" hidden></div>
      <div class="tn-bt" hidden></div>
      ${V.test ? `<p class="fi-foot">On this preview, Save writes only to a test folder.</p>` : ""}
      <div class="tn-foot" hidden>
        <p class="lb-msg tn-msg" role="status" hidden></p>
        <p class="small muted tn-whynot" hidden></p>
        <div class="tn-acts">
          <button class="btn primary tn-save" type="button" hidden>Save</button>
          <button class="btn tn-test" type="button" hidden>Test it<span> on past prices</span></button>
          <button class="btn tn-back" type="button" hidden>Cancel</button>
          <button class="btn tn-undo" type="button" hidden>Undo my last change</button>
        </div>
      </div>`;
    box.querySelector(".tn-save").onclick = save;
    box.querySelector(".tn-test").onclick = historyTest;
    box.querySelector(".tn-back").onclick = () => { draft = {...V.settings}; copying = null; box.querySelector(".tn-bt").hidden = true; note = {text: "", tone: ""}; build(); };
    box.querySelector(".tn-reset").onclick = () => { draft = {...V.settings, ...V.defaults}; copying = null; note = {text: "", tone: ""}; build(); };
    box.querySelector(".tn-undo").onclick = () => send("/api/settings", {bot, undo: true}, "undo-settings", "Undone. The settings from before are back.");
  }

  async function load(keepNote) {
    try { V = await (await fetch("/api/settings?bot=" + bot, {cache: "no-store"})).json(); } catch (x) { V = null; }
    if (!V || !V.ok) { box.hidden = true; return; }
    if (!box.querySelector(".tn-rows")) shell();
    draft = {...V.settings}; copying = null;
    box.querySelector(".tn-bt").hidden = true;
    if (!keepNote) note = {text: "", tone: ""};
    build();
  }

  /* Or copy someone: their settings move the sliders, and Save copies them through the leaderboard's own checks. */
  function sparkOf(vals, ret) {
    if (!vals || vals.length < 2) return "";
    const W = 120, H = 34, lo = Math.min(...vals), hi = Math.max(...vals), k = (hi - lo) || 1;
    const d = "M" + vals.map((v, i) => (1 + i * (W - 6) / (vals.length - 1)).toFixed(1) + "," + (3 + (hi - v) * (H - 6) / k).toFixed(1)).join("L");
    return `<svg class="lb-spark ${ret == null ? "" : ret >= 0 ? "up" : "down"}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
  }
  const signed = v => v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "%";
  async function loadBoard() {
    if (!copyBox) return;
    let B;
    try { B = await (await fetch(`/api/board?bot=${bot}&range=all`, {cache: "no-store"})).json(); } catch (x) { B = {ok: false}; }
    const top = B.ok ? (B.ranked || []).slice(0, 10) : [];
    copyBox.hidden = !top.length;
    if (!top.length) return;
    copyBox.innerHTML = `<div class="card-head"><h2>Or copy someone</h2>${B.test_mode ? `<span class="chip">Test data</span>` : ""}<a class="aside" href="/leaderboard?bot=${bot}">See all</a></div>
      <ul class="mini tn-mini${top.length >= 6 ? " spread" : ""}">${top.map(r => `<li><span class="rk">${r.rank}</span><a class="nm" href="/leaderboard?bot=${bot}&amp;copy=${e(r.id)}">${e(r.name)}</a>
        <span>${sparkOf(r.spark, r.return_pct)}</span><span class="ret ${r.return_pct >= 0 ? "up" : "down"}">${signed(r.return_pct)}</span>
        <button class="btn small" type="button" data-copy-id="${e(r.id)}" aria-label="Copy ${e(r.name)}">Copy</button></li>`).join("")}</ul>
      <p class="fi-foot">Copy moves the sliders to their settings. Nothing changes until you press the button under them.</p>`;
    copyBox.querySelectorAll("[data-copy-id]").forEach(el => el.onclick = () => {
      const r = top.find(x => x.id === el.dataset.copyId);
      if (!r || !draft) return;
      draft = {...V.settings, ...r.settings}; copying = r; note = {text: "", tone: ""};
      build();
      if (innerWidth < 860) box.scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"});
    });
    if (V) paint();
  }

  load().then(loadBoard);
})();
