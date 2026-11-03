/* The Leaderboard page. Look, then copy: a copy shows exactly what changes and needs a second press; the settings are
   checked against the leaderboard's signature on this server (webdash.leaderboard_copy) and again by the root job.
   Sharing sends only the settings and an optional name, and only after the dialog is confirmed. */
(function () {
  const LABELS = {
    stocks: {
      CORE_PCT: ["S&P 500 core", v => Math.round(v * 100) + "%"], PICKS: ["Momentum picks", v => v],
      PICK_PCT: ["Each pick", v => (v * 100).toFixed(1).replace(/\.0$/, "") + "%"], DRIFT_PCT: ["Fix drift over", v => Math.round(v * 100) + "%"],
      TREND_DAYS: ["Trend line", v => v + " days"], SAFETY_SWITCH: ["Safety switch", v => v ? "On" : "Off"],
      SWITCH_CHECK: ["Switch checked", v => v], AGGRESSIVE: ["Aggressive mode", v => v ? "On" : "Off"],
      AGG_CORE: ["Aggressive fund", v => v === "UPRO" ? "UPRO (3×)" : "SSO (2×)"],
      AGG_VOL_MAX: ["Aggressive below", v => Math.round(v * 100) + "% swings"], AGG_VOL_EXIT: ["Aggressive off above", v => Math.round(v * 100) + "% swings"],
      INVESTED: ["Invested", v => Math.round(v * 100) + "%"],
      BLOCKS: ["Stocks", v => !v ? "Charles's picks" : `your ${v.length} block${v.length === 1 ? "" : "s"}`],
    },
    crypto: {
      BTC_PCT: ["Bitcoin", v => Math.round(v * 100) + "%"], ETH_PCT: ["Ether", v => Math.round(v * 100) + "%"],
      TREND_DAYS: ["Trend line", v => v + " days"], CHECK_DAYS: ["Checked every", v => v + (v == 1 ? " day" : " days")],
      MIN_TRADE: ["Skip moves under", v => Math.round(v * 100) + "%"],
    },
  };
  const RANGE_WORD = {week: "this week", month: "this month", all: "since it started"};
  const BENCH = {SPY: "S&P 500", "BTC-USD": "Bitcoin"};
  const q = new URLSearchParams(location.search);
  let bot = q.get("bot") === "crypto" ? "crypto" : "stocks", range = "all", B = null, M = null, C = null, P = null, wantCopy = q.get("copy");
  try { range = localStorage.getItem("board-range") || range; } catch (e) {}
  const dlg = $("dlg");
  dlg.addEventListener("click", ev => { if (ev.target === dlg) dlg.close(); });
  const tell = what => dispatchEvent(new CustomEvent("charles:board", {detail: what}));     // the tour follows along (web/tour.js)

  const label = (k, v) => { const [l, f] = LABELS[bot][k] || [k, x => x]; return [l, f(v)]; };
  const gist = s => !s ? "" : bot === "stocks"
    ? `${Math.round(s.CORE_PCT * 100)}% S&P · ${s.PICKS} pick${s.PICKS === 1 ? "" : "s"} · ${s.AGGRESSIVE ? (s.AGG_CORE === "UPRO" ? "3×" : "2×") + " when calm" : "no leverage"}`
    : `${Math.round(s.BTC_PCT * 100)}% bitcoin · ${Math.round(s.ETH_PCT * 100)}% ether · ${s.CHECK_DAYS}-day check`;
  async function call(method, path, body) {
    const r = await fetch(path, {method, cache: "no-store", headers: body ? {"Content-Type": "application/json"} : {}, body: body ? JSON.stringify(body) : undefined});
    let j = {};
    try { j = await r.json(); } catch (x) {}
    return {...j, ok: r.ok && j.ok !== false, status: r.status, message: j.message || j.error};
  }

  function renderControls() {
    if (!document.querySelector('.topnav a[href="/crypto/"]')) { bot = "stocks"; $("bots").hidden = true; }
    document.querySelectorAll("[data-bot]").forEach(b => b.classList.toggle("on", b.dataset.bot === bot));
    document.querySelectorAll("[data-range]").forEach(b => b.classList.toggle("on", b.dataset.range === range));
  }
  document.querySelectorAll("[data-bot]").forEach(b => b.onclick = () => { bot = b.dataset.bot; history.replaceState(null, "", "/leaderboard?bot=" + bot); load(); });
  document.querySelectorAll("[data-range]").forEach(b => b.onclick = () => { range = b.dataset.range; try { localStorage.setItem("board-range", range); } catch (e) {} load(); });

  function renderList() {
    $("test-chip").hidden = !B.test_mode;
    const bench = BENCH[B.bench] || B.bench;
    $("sum").hidden = B.bench_pct == null;
    $("sum").innerHTML = B.bench_pct == null ? "" : `${esc(bench)} <b class="${cls(B.bench_pct)}">${pct(B.bench_pct, 1)}</b> ${RANGE_WORD[range]}`;
    const R = B.ranked || [];
    $("list").innerHTML = R.length ? R.map(r => `<li class="lb-row" data-id="${esc(r.id)}">
        <span class="lb-rank r${r.rank}">${r.rank}</span>
        <div class="lb-who"><span class="lb-name${r.anonymous ? " anon" : ""}">${esc(r.name)}</span>
          <span class="lb-gist">${esc(gist(r.settings))}${r.copies ? ` · ${r.copies} cop${r.copies === 1 ? "y" : "ies"}` : ""}</span></div>
        ${spark(r.spark, r.return_pct) || "<span></span>"}
        <div class="lb-ret"><b class="${cls(r.return_pct)}">${pct(r.return_pct, 1)}</b><small>worst ${pct(r.max_drop_pct, 1)}</small></div>
        <button class="btn small${r.rank <= 3 ? " primary" : ""}" type="button" data-copy-id="${esc(r.id)}" aria-label="Copy ${esc(r.name)}">Copy</button></li>`).join("")
      : `<li class="lb-empty">Nobody has enough days to rank ${RANGE_WORD[range]} yet.</li>`;
    document.querySelectorAll(".lb-row").forEach(el => el.onclick = () => openCopy(el.dataset.id));
    const W = B.warming || [];
    $("warm-box").hidden = !W.length;
    $("warm-sum").textContent = `${W.length} warming up`;
    $("warm").innerHTML = W.map(w => `<div class="lb-warm-row"><span class="lb-name${w.anonymous ? " anon" : ""}" style="font-size:14.5px">${esc(w.name)}</span>
        <div class="meter" role="img" aria-label="${w.days} of ${w.need || B.min_days} days"><i style="width:${Math.min(100, w.days / (w.need || B.min_days) * 100)}%"></i></div>
        <span class="lb-days">${w.days} of ${w.need || B.min_days} days</span></div>`).join("");
    $("foot").innerHTML = (B.test_mode ? "These are made-up test entries while the leaderboard is being tried out. " : "") +
      `Ranked after ${B.min_days} days. Past results, not a promise.` + (B.board_url ? ` <a href="${esc(B.board_url)}" target="_blank" rel="noopener">Public page ↗</a>` : "");
  }

  function renderMe() {
    const box = $("me");
    if (M && M.custom) {            // the user's own tickers (or part kept aside) stay off the board: Reset is the way back
      box.innerHTML = `<div style="min-width:0"><b>Your own plan isn't shared</b><div class="small muted">${esc(M.message)}</div></div>
        ${offPlan().length ? `<div class="acts"><button class="btn small" type="button" id="me-reset" aria-label="Reset to the default plan">Reset</button></div>` : ""}`;
      if ($("me-reset")) $("me-reset").onclick = openReset;
      return;
    }
    if (!M || !M.ok) { box.innerHTML = `<div class="muted">${esc((M && M.message) || "The leaderboard isn't answering right now.")}</div>`; return; }
    const mine = (M.entries || []).find(x => x.status === "active");
    const reset = offPlan().length ? `<button class="btn small" type="button" id="me-reset" aria-label="Reset to the default plan">Reset</button>` : "";
    if (!mine) {
      box.innerHTML = `<div><b>You're not on it yet</b><div class="small muted">${esc(gist((M.preview || {}).settings))}</div></div>
        <div class="acts">${reset}<button class="btn" type="button" id="me-share">Share mine</button></div>`;
    } else {
      const rk = (B && B.ranked || []).find(r => r.id === mine.id), w = (B && B.warming || []).find(r => r.id === mine.id);
      const need = (w && w.need) || (B && B.min_days) || M.min_days || 20, days = w ? w.days : null;
      box.innerHTML = `<div style="min-width:0"><b>${rk ? `You're #${rk.rank} of ${B.ranked.length}` : "You're on it"}</b> <span class="muted">as ${esc(mine.name || "Anonymous")}</span>
          ${rk ? `<div class="small num ${cls(rk.return_pct)}">${pct(rk.return_pct, 1)} ${RANGE_WORD[range]}</div>` : days != null
            ? `<div class="prog"><div class="meter"><i style="width:${Math.min(100, days / need * 100)}%"></i></div><span>${days} of ${need} days</span></div>`
            : `<div class="small muted">Ranked after ${need} days</div>`}</div>
        <div class="acts">${reset}<button class="btn small" type="button" id="me-share">Share current</button><button class="btn small" type="button" id="me-out">Withdraw</button></div>`;
      $("me-out").onclick = () => openWithdraw(mine);
    }
    $("me-share").onclick = openShare;
    if ($("me-reset")) $("me-reset").onclick = openReset;
  }

  /* Copy: what changes, then a second press */
  function openCopy(id) {
    const r = (B.ranked || []).find(x => x.id === id);
    if (!r) return;
    const cur = (M && M.preview && M.preview.settings) || null;
    const diffs = cur ? Object.keys(r.settings).filter(k => r.settings[k] !== cur[k]) : Object.keys(r.settings);
    const rows = diffs.map(k => { const [l, nw] = label(k, r.settings[k]); return `<div class="chg"><span>${esc(l)}</span><b class="num">${cur ? esc(label(k, cur[k])[1]) + " → " : ""}${esc(nw)}</b></div>`; }).join("");
    dlg.innerHTML = `<div class="lb-dlg-top"><h3>Copy ${esc(r.name)}</h3><button class="icon" type="button" id="d-x" aria-label="Close"><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M3 3l8 8M11 3l-8 8"/></svg></button></div>
      <div class="small muted num">#${r.rank} ${RANGE_WORD[range]} · <span class="${cls(r.return_pct)}">${pct(r.return_pct, 1)}</span> · worst ${pct(r.max_drop_pct, 1)} · ${r.days} days</div>
      <div class="lb-dlg-chart">${spark(r.spark, r.return_pct)}</div>
      ${diffs.length ? `<div class="sh-h">What changes</div>${rows}` : `<p class="sh-p" style="margin-top:10px">You already use these settings.</p>`}
      ${diffs.length ? `<p class="small muted" style="margin:10px 0 0">Charles uses them at its next daily check and may trade to match. Past results, not a promise.</p>` : ""}
      <p class="lb-msg" id="d-msg" role="status"></p>
      <div class="lb-btns" id="d-btns"><button class="btn" type="button" id="d-no">Cancel</button>${diffs.length ? `<button class="btn primary" type="button" id="d-go">Copy these settings</button>` : ""}</div>`;
    $("d-x").onclick = $("d-no").onclick = () => dlg.close();
    if ($("d-go")) $("d-go").onclick = () => doCopy(r, $("d-go"));
    if (!dlg.open) dlg.showModal();
    tell("open-copy");
  }
  async function doCopy(r, btn) {
    const msg = $("d-msg");
    btn.disabled = true; btn.textContent = "Copying…"; msg.className = "lb-msg"; msg.textContent = "";
    const before = await jobOnFile();
    const res = await call("POST", "/api/leaderboard/copy", {id: r.id}).catch(() => ({ok: false, message: "The dashboard didn't answer. Try again."}));
    const stop = text => { btn.disabled = false; btn.textContent = "Copy these settings"; msg.className = "lb-msg down"; msg.textContent = text; tell("failed"); };
    if (!res.ok) return stop(res.message || "That didn't work.");
    let said = res.message;
    if (!res.done) {                                     // the root service does the work: wait for it to report back
      const j = await job("copy-config", before);
      if (j.status !== "done") return stop(j.result || "It didn't finish. Try again.");
      said = bot === "stocks" ? "Copied. Charles uses them at its next daily check." : "Copied. The crypto book uses them from its next check.";
    }
    changed(msg, said);
    tell("copied");
  }
  /* A change went through: say so and offer the way back. */
  function changed(msg, said) {
    msg.className = "lb-msg up"; msg.textContent = said;
    $("d-btns").innerHTML = `<button class="btn" type="button" id="d-done">Done</button><button class="btn" type="button" id="d-undo">Undo</button>`;
    $("d-done").onclick = () => dlg.close();
    $("d-undo").onclick = async e => {
      e.currentTarget.disabled = true; e.currentTarget.textContent = "Undoing…";
      const was = await jobOnFile();
      const u = await call("POST", "/api/settings", {bot, undo: true});
      const j = u.done ? {status: "done"} : u.ok ? await job("undo-settings", was) : {status: "failed", result: u.message};
      msg.className = "lb-msg " + (j.status === "done" ? "muted" : "down"); msg.textContent = j.status === "done" ? "Undone. Your old settings are back." : j.result || "That didn't work.";
      if ($("d-undo")) $("d-undo").remove();
      await loadMine();
      if (j.status === "done") tell("undone");
    };
    loadMine();
  }

  /* Reset: back to the default plan. The same request as the Plan tab's sliders, so it's checked the same way and can be undone. */
  const same = (a, b) => typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-9
    : (a && typeof a === "object") || (b && typeof b === "object") ? JSON.stringify(a ?? null) === JSON.stringify(b ?? null) : a === b;
  const offPlan = () => P && P.ok && P.defaults ? Object.keys(P.defaults).filter(k => !same(P.settings[k], P.defaults[k])) : [];
  function openReset() {
    const diffs = offPlan();
    dlg.innerHTML = `<h3>Back to the default plan?</h3>
      <div class="sh-h">What changes</div>${diffs.map(k => `<div class="chg"><span>${esc(label(k, P.settings[k])[0])}</span><b class="num">${esc(label(k, P.settings[k])[1])} → ${esc(label(k, P.defaults[k])[1])}</b></div>`).join("")}
      <p class="small muted" style="margin:10px 0 0">${bot === "stocks" ? "Charles uses it at its next daily check and may trade to match." : "The crypto book uses it from its next check."}</p>
      <p class="lb-msg" id="d-msg" role="status"></p>
      <div class="lb-btns" id="d-btns"><button class="btn" type="button" id="d-no">Keep mine</button><button class="btn primary" type="button" id="d-go">Reset</button></div>`;
    $("d-no").onclick = () => dlg.close();
    $("d-go").onclick = () => doReset($("d-go"));
    if (!P.can) { $("d-go").disabled = true; $("d-msg").textContent = P.why || ""; }
    dlg.showModal();
    tell("open-reset");
  }
  async function doReset(btn) {
    const msg = $("d-msg"), stop = text => { btn.disabled = false; btn.textContent = "Reset"; msg.className = "lb-msg down"; msg.textContent = text; tell("failed"); };
    btn.disabled = true; btn.textContent = "Resetting…"; msg.className = "lb-msg"; msg.textContent = "";
    const before = await jobOnFile();
    const res = await call("POST", "/api/settings", {bot, settings: Object.fromEntries(offPlan().map(k => [k, P.defaults[k]]))}).catch(() => ({ok: false, message: "The dashboard didn't answer. Try again."}));
    if (!res.ok) return stop(res.message || "That didn't work.");
    if (!res.done) {
      const j = await job("apply-settings", before);
      if (j.status !== "done") return stop(j.result || "It didn't finish. Try again.");
    }
    changed(msg, "Reset. You're back on the default plan.");
    tell("reset");
  }
  const jobOnFile = async () => { try { return ((await getJSON("/api/updates")).job || {}).at; } catch (x) { return undefined; } };
  /* `before` is the job on file when we asked: until a new one shows up, an old result says nothing about this request. */
  async function job(action, before) {
    for (let i = 0; i < 40; i++) {
      await new Promise(ok => setTimeout(ok, 1500));
      try { const j = (await getJSON("/api/updates")).job || {};
        if (j.at !== before && j.status === "refused") return j;
        if (j.at !== before && j.action === action && !["queued", "running"].includes(j.status)) return j; } catch (x) {}
    }
    return {status: "failed", result: "It's taking longer than expected. Check back in a few minutes."};
  }

  /* Share and withdraw */
  const facts = s => `<div class="facts">${Object.entries(s).map(([k, v]) => { const [l, f] = label(k, v); return `<div><span class="l">${esc(l)}</span><b class="num">${esc(f)}</b></div>`; }).join("")}</div>`;
  const nameOk = n => !n || /^[A-Za-z0-9][A-Za-z0-9_-]{2,19}$/.test(n);
  function openShare() {
    const had = (M.entries || []).some(x => x.status === "active");
    dlg.innerHTML = `<h3>Share your settings</h3>
      <p class="sh-p">Only these are sent. No balances, keys, holdings or trades leave your server.</p>
      ${facts(M.preview.settings)}
      <label class="lb-label">Name <span class="muted">(optional)</span>
        <input id="d-name" maxlength="20" autocomplete="off" placeholder="Leave empty to stay anonymous"></label>
      ${had ? `<p class="small muted" style="margin:10px 0 0">Your current entry retires and stays visible with its record.</p>` : ""}
      <p class="lb-msg" id="d-msg" role="status"></p>
      <div class="lb-btns"><button class="btn" type="button" id="d-no">Cancel</button><button class="btn primary" type="button" id="d-go">Share publicly</button></div>`;
    const prior = (M.entries || []).map(x => x.name).find(Boolean);
    if (prior) $("d-name").value = prior;
    $("d-no").onclick = () => dlg.close();
    $("d-go").onclick = async ev => {
      const name = $("d-name").value.trim(), msg = $("d-msg"), go = ev.currentTarget;
      if (!nameOk(name)) { msg.className = "lb-msg down"; msg.textContent = "Names are 3–20 letters, numbers, _ or -."; return; }
      go.disabled = true; msg.className = "lb-msg"; msg.textContent = "Sharing…";
      const r = await call("POST", "/api/leaderboard/submit", {bot, name});
      go.disabled = false;
      msg.className = "lb-msg " + (r.ok ? "up" : "down"); msg.textContent = r.message || (r.ok ? "Shared." : "Couldn't share.");
      tell(r.ok ? "shared" : r.id ? "failed-same" : "failed");
      if (r.ok) { await load(); setTimeout(() => dlg.open && dlg.close(), 2000); }
    };
    dlg.showModal();
    tell("open-share");
  }
  function openWithdraw(mine) {
    dlg.innerHTML = `<h3>Withdraw it?</h3><p class="sh-p">It stops being ranked and shows as retired, with its record. You can share again any time.</p>
      <p class="lb-msg" id="d-msg" role="status"></p>
      <div class="lb-btns"><button class="btn" type="button" id="d-no">Keep it</button><button class="btn danger" type="button" id="d-go">Withdraw</button></div>`;
    $("d-no").onclick = () => dlg.close();
    $("d-go").onclick = async () => {
      const r = await call("POST", "/api/leaderboard/withdraw", {id: mine.id}), msg = $("d-msg");
      msg.className = "lb-msg " + (r.ok ? "up" : "down"); msg.textContent = r.message || "";
      if (r.ok) { await load(); setTimeout(() => dlg.open && dlg.close(), 1400); }
    };
    dlg.showModal();
  }

  /* This month's challenge: entering sends a config; this bot's own settings stay as they are */
  const chDay = d => dayFmt(d);
  function renderChallenge() {
    const c = C && C.open;
    $("ch-box").hidden = !c;
    if (!c) return;
    $("ch-sum").textContent = `This month's challenge: ${c.title}`;
    const changes = Object.entries(C.changes || {}).map(([k, v]) => { const [l] = label(k, v.old); return `<div class="chg"><span>${esc(l)}</span><b class="num">${esc(label(k, v.old)[1])} → ${esc(label(k, v.new)[1])}</b></div>`; }).join("");
    $("ch").innerHTML = `<p class="small muted" style="margin:0">${esc(c.about)}</p>
      <p class="small muted" style="margin:4px 0 0">Entries close ${chDay(c.starts)}. Runs to ${chDay(c.ends)}.${C.board_url ? ` <a href="${esc(C.board_url)}" target="_blank" rel="noopener">See it ↗</a>` : ""}</p>
      ${C.mine ? `<div class="lb-on"><span class="dot good"></span><b>You're in, as ${esc(C.mine.name || "Anonymous")}</b></div>` : ""}
      ${changes ? `<div class="sh-h">Your entry changes these to fit the rules. Your bot keeps its settings.</div>${changes}` : ""}
      <div class="lb-btns"><button class="btn${C.mine ? "" : " primary"}" type="button" id="ch-enter">${C.mine ? "Enter again" : "Enter"}</button>${C.mine ? `<button class="btn" type="button" id="ch-out">Withdraw</button>` : ""}</div>
      <p class="lb-msg" id="ch-msg" role="status"></p>`;
    $("ch-enter").onclick = openEnter;
    if ($("ch-out")) $("ch-out").onclick = async ev => {
      ev.currentTarget.disabled = true;
      const r = await call("POST", "/api/challenge/withdraw", {bot});
      if (r.ok) return loadChallenge();
      if ($("ch-out")) $("ch-out").disabled = false;
      $("ch-msg").className = "lb-msg down"; $("ch-msg").textContent = r.message || "Couldn't withdraw.";
    };
  }
  function openEnter() {
    const c = C.open;
    dlg.innerHTML = `<h3>Enter “${esc(c.title)}”</h3>
      <p class="sh-p">Only these settings are sent, and they show publicly. There are no prizes.</p>
      ${facts(C.settings)}
      <label class="lb-label">Name <span class="muted">(optional)</span><input id="d-name" maxlength="20" autocomplete="off" placeholder="Leave empty to stay anonymous"></label>
      <p class="lb-msg" id="d-msg" role="status"></p>
      <div class="lb-btns"><button class="btn" type="button" id="d-no">Cancel</button><button class="btn primary" type="button" id="d-go">Enter publicly</button></div>`;
    if (C.mine && C.mine.name) $("d-name").value = C.mine.name;
    $("d-no").onclick = () => dlg.close();
    $("d-go").onclick = async ev => {
      const name = $("d-name").value.trim(), msg = $("d-msg"), go = ev.currentTarget;
      if (!nameOk(name)) { msg.className = "lb-msg down"; msg.textContent = "Names are 3–20 letters, numbers, _ or -."; return; }
      go.disabled = true; msg.className = "lb-msg"; msg.textContent = "Entering…";
      const r = await call("POST", "/api/challenge/enter", {bot, name});
      go.disabled = false;
      msg.className = "lb-msg " + (r.ok ? "up" : "down"); msg.textContent = r.message || (r.ok ? "You're in." : "Couldn't enter.");
      tell(r.ok ? "entered" : "failed");
      if (r.ok) { await loadChallenge(); setTimeout(() => dlg.open && dlg.close(), 2000); }
    };
    dlg.showModal();
    tell("open-enter");
  }

  async function loadMine() {
    try { [M, P] = await Promise.all([call("GET", "/api/leaderboard/mine?bot=" + bot), call("GET", "/api/settings?bot=" + bot)]); } catch (x) { M = P = null; }
    renderMe();
  }
  async function loadChallenge() {
    try { C = await call("GET", "/api/challenge?bot=" + bot); } catch (x) { C = null; }
    renderChallenge();
  }
  async function load() {
    renderControls();
    const asked = bot + range;
    let got;
    try { got = await getJSON(`/api/board?bot=${bot}&range=${range}`); } catch (x) { got = {ok: false}; }
    if (asked !== bot + range) return;                    // another button was pressed meanwhile
    B = got;
    if (!B.ok) { $("list").innerHTML = `<li class="lb-empty">The leaderboard isn't answering right now.</li>`; $("sum").innerHTML = "&nbsp;"; $("warm-box").hidden = true; }
    else {
      renderList();
      if (wantCopy) { const id = wantCopy; wantCopy = null; await loadMine(); openCopy(id); history.replaceState(null, "", "/leaderboard?bot=" + bot); }
    }
    loadMine(); loadChallenge();
  }
  load();
})();
