/* The Plan tab: set the plan yourself with sliders, or copy someone from the leaderboard into them (owner 2026-10-31). Nothing changes until
   Save. Save and Undo go through /api/settings or /api/leaderboard/copy: the dashboard only leaves a request, and the root job checks it again.
   The stock plan also has (owner 2026-11-03): presets (Charles's move only amounts, never tickers; the user can save and name their own), how
   much of the buying power is invested at all, and "My own": the user's tickers, found with the search (/api/tickers), in long-term, sector
   and momentum blocks with a share each (portfolio.py checks them again). Self-tune's switch on the Self-Tune tab saves the same way.
   The crypto plan has the same (owner 2026-11-03, "match the stock configuration"): its presets, Invested, and "My own" coins from
   Coinbase's list in long-term, trend and momentum blocks (crypto_plan.py checks them again).
   stocks.html and crypto.html keep their own helpers, so this keeps its own too, inside here. */
(function () {
  const box = document.getElementById("tune"), copyBox = document.getElementById("tune-copy"), stBox = document.getElementById("selftune");
  if (!box) return;
  const bot = box.dataset.bot, STOCKS = bot === "stocks";
  const e = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const pc = v => +(v * 100).toFixed(1) + "%";
  const days = v => v + " days";
  // a list of blocks compares by what's in it, whatever order its keys came in
  const canon = v => Array.isArray(v) ? "[" + v.map(canon).join(",") + "]"
    : v && typeof v === "object" ? "{" + Object.keys(v).sort().map(k => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}" : JSON.stringify(v ?? null);
  const same = (a, b) => typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-9
    : (a && typeof a === "object") || (b && typeof b === "object") ? canon(a) === canon(b) : a === b;
  const clone = v => v == null ? null : JSON.parse(JSON.stringify(v));
  const onOff = v => v ? "On" : "Off";
  const pickSize = d => pc(d.PICK_PCT);
  const own = d => !!(d && d.BLOCKS);
  const X = `<svg width="10" height="10" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 3l8 8M11 3l-8 8"/></svg>`;
  const TYPE = STOCKS ? {long: "Long term", sector: "Sector", momentum: "Momentum"} : {long: "Long term", trend: "Trend", momentum: "Momentum"};
  const TYPE_ABOUT = STOCKS ? {long: "Held all the time, split evenly.", sector: "Each one is held only while it's above its trend line. Until then its share waits in T-bills."}
    : {long: "Held all the time, split evenly.", trend: "Each coin is held only while it's above its trend line. Until then its share waits in cash."};
  const TYPE_COLOR = {long: "--core", sector: "--sector", trend: "--sector", momentum: "--pick"};
  // the words that differ: stocks wait in T-bills, coins in cash; Charles picks stocks, but holds bitcoin and ether
  const T = STOCKS ? {what: "Stocks", charles: "Charles's picks", item: "ticker", items: "tickers", rest: "T-bills", money: "buying power", none: "No stock or fund matches",
                      charlesAbout: "The S&P 500 and the strongest stocks of Charles's list, re-picked monthly.", mineAbout: "Your tickers, in blocks. Each block gets its share of what's invested."}
    : {what: "Coins", charles: "Charles's coins", item: "coin", items: "coins", rest: "cash", money: "book", none: "No coin matches",
       charlesAbout: "Bitcoin and ether, each held only while it's above its trend line.", mineAbout: "Your coins, in blocks. Each block gets its share of what's invested."};
  const short = t => STOCKS ? t : String(t).replace(/-USD$/, "");
  const NAME_OK = /^[\p{L}\p{N}_][\p{L}\p{N}_ &'+,./()-]*$/u;       // portfolio.check_name
  const tidy = s => String(s || "").trim().replace(/\s+/g, " ");
  const spyLong = d => own(d) && d.BLOCKS.some(b => b.type === "long" && b.tickers.includes(CORE()));
  const CORE = () => ((V && V.custom) || {}).core || "SPY";

  // Each row: the setting, its words, and how a value reads. `pct` sliders work in whole percents, so the values stay clean numbers.
  const ROWS = {
    stocks: [
      {k: "INVESTED", label: "Invested", pct: true, step: 5, show: pc,
       about: d => d.INVESTED >= .9995 ? "All of your buying power goes into the plan." : `${pc(d.INVESTED)} of your buying power goes into the plan. The other ${pc(1 - d.INVESTED)} waits in T-bills.`},
      {k: "BLOCKS", kind: true},
      {k: "CORE_PCT", label: "S&P 500 core", pct: true, step: 5, show: pc, why: "index-fund", when: d => !own(d),
       about: d => "An index fund of 500 big US companies." + (d.INVESTED < .9995 ? ` That's ${pc(d.CORE_PCT * d.INVESTED)} of all your money.` : "")},
      {k: "PICKS", label: "Momentum picks", step: 1, show: v => v, why: "momentum", when: d => !own(d),
       about: d => d.PICKS ? `The past year's strongest stocks, re-picked monthly, ${pickSize(d)} each.` : "No picks: only the core and T-bills."},
      {k: "TREND_DAYS", label: "Trend line", step: 10, show: days, why: "trend-line", about: d => own(d) ? "The average the safety switch, sector funds and momentum picks are measured against." : "The average the safety switch and the picks are measured against."},
      {k: "SAFETY_SWITCH", label: "Safety switch", toggle: true, why: "safety",
       about: "If SPY ends a month below its trend line, everything moves to T-bills until it recovers."},
      {k: "AGGRESSIVE", label: "Aggressive mode", toggle: true, why: "aggressive",
       about: d => own(d) && !spyLong(d) ? `It swaps ${CORE()} in a long-term block for a leveraged fund, and your blocks have no ${CORE()}: it changes nothing.`
         : "While the market is calm and rising, the core moves to a leveraged S&P fund. Off on any choppy day."},
      {k: "AGG_CORE", label: "How much leverage", when: d => d.AGGRESSIVE, sub: true, choices: [["SSO", "2×"], ["UPRO", "3×"]],
       about: d => d.AGG_CORE === "UPRO" ? "A 3× fund falls three times as fast as the market." : "A 2× fund falls twice as fast as the market."},
      {k: "AGG_VOL_MAX", label: "Calm means swings under", when: d => d.AGGRESSIVE, sub: true, pct: true, step: 1, show: pc, about: "How much the market may swing in a year."},
    ],
    crypto: [
      {k: "INVESTED", label: "Invested", pct: true, step: 5, show: pc,
       about: d => d.INVESTED >= .9995 ? "All of the book goes into the plan." : `${pc(d.INVESTED)} of the book goes into the plan. The other ${pc(1 - d.INVESTED)} waits in cash.`},
      {k: "BLOCKS", kind: true},
      {k: "BTC_PCT", label: "Bitcoin", pct: true, step: 5, show: pc, when: d => !own(d), about: "Its share of the plan while its trend is up."},
      {k: "ETH_PCT", label: "Ether", pct: true, step: 5, show: pc, when: d => !own(d), about: "Its share of the plan while its trend is up."},
      {k: "TREND_DAYS", label: "Trend line", step: 10, show: days, about: d => own(d) ? "The average your trend and momentum coins are measured against. Under it, a coin's share waits in cash." : "Under this average, a coin's share waits in cash."},
      {k: "CHECK_DAYS", label: "Check every", step: 1, show: v => v === 1 ? "day" : days(v), about: "How often the book looks. Longer means fewer trades and fees."},
    ],
  }[bot];
  // Everything a saved change can touch, in plain words: the sliders, and what comes along with a copied config.
  const blockWords = v => !v ? "Charles's picks" : v.map(b => `${b.name} ${Math.round(b.pct * 100)}%`).join(", ");
  const WORDS = {
    stocks: {CORE_PCT: ["S&P 500 core", pc], PICKS: ["Momentum picks", v => v], PICK_PCT: ["Each pick", pc], DRIFT_PCT: ["Fix drift over", pc], TREND_DAYS: ["Trend line", days],
      SAFETY_SWITCH: ["Safety switch", onOff], SWITCH_CHECK: ["Switch checked", v => v], AGGRESSIVE: ["Aggressive mode", onOff], AGG_CORE: ["Aggressive fund", v => v === "UPRO" ? "3×" : "2×"],
      AGG_VOL_MAX: ["Calm means swings under", pc], AGG_VOL_EXIT: ["Aggressive off above", pc], INVESTED: ["Invested", pc], BLOCKS: ["Stocks", blockWords]},
    crypto: {BTC_PCT: ["Bitcoin", pc], ETH_PCT: ["Ether", pc], TREND_DAYS: ["Trend line", days], CHECK_DAYS: ["Check every", v => v === 1 ? "day" : days(v)], MIN_TRADE: ["Skip moves under", pc],
      INVESTED: ["Invested", pc], BLOCKS: ["Coins", v => !v ? "Charles's coins" : blockWords(v)]},
  }[bot];
  const SAVED = {stocks: "Saved. Charles uses them at its next daily check and may trade to match.", crypto: "Saved. The book checks again within the hour and trades to match."}[bot];

  let V = null, draft = null, copying = null, busy = false, note = {text: "", tone: ""}, rows = [], names = {}, naming = false, picking = null;
  let stBusy = false, stNote = {text: "", tone: ""};

  const changes = () => Object.keys(draft).filter(k => !same(draft[k], V.settings[k]));
  const say = (text, tone = "") => { note = {text, tone}; paint(); };
  const C = () => (V && V.custom) || {};
  const used = (d, skip = -1) => (d.BLOCKS || []).reduce((x, b, i) => i === skip ? x : x + b.pct, 0);
  const nameOf = t => names[t] || t;

  /* What would stop a Save, in a sentence (portfolio.check_blocks says the same again on the server). */
  function problem(d) {
    if (!own(d)) return "";
    if (!d.BLOCKS.length) return `Add a ${T.item}, or go back to ${T.charles}.`;
    const seen = new Set();
    for (const b of d.BLOCKS) {
      const n = tidy(b.name);
      if (!n) return "Give every block a name.";
      if (!NAME_OK.test(n)) return "Block names can use letters, numbers, spaces and & ' + , . / ( ) -";
      if (seen.has(n.toLowerCase())) return `Two blocks are called ${n}.`;
      seen.add(n.toLowerCase());
      if (!b.tickers.length) return `${n} has no ${T.items} yet: add one, or remove the block.`;
    }
    return "";
  }

  /* The money, as it would be with the sliders where they are. */
  function money() {
    const k = draft.INVESTED ?? 1;
    if (bot === "crypto" && !own(draft)) {
      const b = draft.BTC_PCT * k, x = draft.ETH_PCT * k;
      return [["Bitcoin", b, "var(--btc)"], ["Ether", x, "var(--eth)"], ["Cash", Math.max(0, 1 - b - x), "var(--cash)"]];
    }
    if (own(draft)) {
      const n = {};
      const parts = draft.BLOCKS.map(b => { const j = n[b.type] = (n[b.type] ?? -1) + 1;
        return [b.name || TYPE[b.type], b.pct * k, j % 2 ? `color-mix(in srgb, var(${TYPE_COLOR[b.type]}) 72%, #000)` : `var(${TYPE_COLOR[b.type]})`]; });
      return [...parts, [STOCKS ? "T-bills" : "Cash", Math.max(0, 1 - used(draft) * k), STOCKS ? "var(--safe)" : "var(--cash)"]];
    }
    const picks = draft.PICKS * draft.PICK_PCT * k, core = draft.CORE_PCT * k, rest = Math.max(0, 1 - core - picks);
    return [["S&P 500", core, "var(--core)"], ["Picks", picks, "var(--pick)"], ["T-bills", rest, "var(--safe)"]];
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
    if (r.kind) return kindHtml();
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

  /* ── Whose stocks: Charles's picks, or the user's own blocks ── */
  function kindHtml() {
    const d = draft, mine = own(d);
    return `<div class="tn-row tn-kind"><div class="tn-top"><b>${T.what}</b><div class="tabs" role="group" aria-label="Whose ${T.what.toLowerCase()}">
        <button type="button" data-kind="charles">${T.charles}</button><button type="button" data-kind="own">My own</button></div></div>
      <p>${mine ? T.mineAbout : T.charlesAbout}</p>
      ${mine ? `<div class="bk-find"><input type="search" id="bk-q" placeholder="Search for a ${T.item} to add" autocomplete="off" autocapitalize="characters" spellcheck="false"
            enterkeyhint="search" aria-label="Search for a ${T.item} to add" aria-autocomplete="list" aria-controls="bk-sug" aria-expanded="false"${d.BLOCKS.reduce((x, b) => x + b.tickers.length, 0) >= C().max_tickers * C().max_blocks ? " disabled" : ""}>
          <ul class="bk-sug" id="bk-sug" role="listbox" aria-label="Tickers found" hidden></ul></div>
        <div class="bk-to" id="bk-to" hidden></div>
        <div class="bk-list">${d.BLOCKS.map(blockHtml).join("")}</div>
        <p class="bk-left" id="bk-left"></p>` : ""}
    </div>`;
  }

  function blockHtml(b, i) {
    const max = Math.min(C().max_picks || 8, b.tickers.length);
    const about = b.type === "momentum"
      ? `Holds the <span class="bk-step"><button type="button" data-picks="${i}" data-d="-1" aria-label="One fewer pick"${b.picks <= 1 ? " disabled" : ""}>−</button><b class="num">${b.picks}</b><button type="button" data-picks="${i}" data-d="1" aria-label="One more pick"${b.picks >= max ? " disabled" : ""}>+</button></span> strongest of these ${STOCKS ? "over the past year, re-picked monthly" : `over the past ${C().mom_days || 90} days, above their trend line, re-picked at every check`}.`
      : e(TYPE_ABOUT[b.type]);
    return `<div class="bk" style="--bk:var(${TYPE_COLOR[b.type]})">
      <div class="bk-head"><input class="bk-name" data-name="${i}" value="${e(b.name)}" maxlength="${C().name_max || 30}" aria-label="Block name" spellcheck="false" autocomplete="off">
        <select class="bk-type" data-type="${i}" aria-label="Kind of block">${Object.entries(TYPE).map(([k, l]) => `<option value="${k}"${k === b.type ? " selected" : ""}>${l}</option>`).join("")}</select>
        <button class="bk-x" type="button" data-del="${i}" aria-label="Remove the block ${e(b.name)}">${X}</button></div>
      <div class="tn-top"><label for="bk-${i}" id="bkl-${i}">Share</label><output id="bko-${i}"></output></div>
      <input class="tn-range" type="range" id="bk-${i}" data-bpct="${i}" min="0" max="100" step="1" value="${Math.round(b.pct * 100)}">
      <div class="bk-tickers">${b.tickers.map(t => `<span class="bk-chip" title="${e(nameOf(t))}"><b>${e(short(t))}</b><button type="button" data-untick="${i}" data-sym="${e(t)}" aria-label="Remove ${e(short(t))}">${X}</button></span>`).join("")
        || `<span class="bk-none">No ${T.items} yet. Search above to add one.</span>`}</div>
      <p class="bk-about">${about}</p></div>`;
  }

  function freeName(base) {
    const taken = new Set((draft.BLOCKS || []).map(b => tidy(b.name).toLowerCase()));
    for (let n = 1; ; n++) { const name = n === 1 ? base : `${base} ${n}`; if (!taken.has(name.toLowerCase())) return name; }
  }

  /* A new block's share: what's left over, up to 20 points, or else 10 points from the biggest block. */
  function newShare() {
    if (!draft.BLOCKS.length) return 1;
    const room = Math.round((1 - used(draft)) * 100);
    if (room >= 1) return Math.min(room, 20) / 100;
    const big = draft.BLOCKS.reduce((a, b) => b.pct > a.pct ? b : a);
    const take = Math.min(10, Math.round(big.pct * 100) - 1);
    big.pct = (Math.round(big.pct * 100) - take) / 100;
    return take / 100;
  }

  function addTicker(r, to) {
    copying = null;
    names[r.symbol] = r.name;
    if (to === "new") {
      draft.BLOCKS.push({name: freeName(TYPE[r.type]), type: r.type, pct: newShare(), tickers: [r.symbol], ...(r.type === "momentum" ? {picks: 1} : {})});
    } else {
      const b = draft.BLOCKS[to];
      b.tickers.push(r.symbol);
      if (b.type === "momentum" && b.picks < 3 && b.picks === b.tickers.length - 1) b.picks += 1;    // a small block keeps holding all of them
    }
    picking = null;
    if (note.text) note = {text: "", tone: ""};
    build();
    const q = document.getElementById("bk-q");
    if (q && matchMedia("(hover: hover)").matches) q.focus();
  }

  /* After a ticker is picked from the search: which block it goes in, the one of its kind first (or a new one). */
  function chooser() {
    const to = document.getElementById("bk-to");
    if (!to) return;
    to.hidden = !picking;
    if (!picking) { to.innerHTML = ""; return; }
    const B = draft.BLOCKS, r = picking, fit = B.filter(b => b.type === r.type && b.tickers.length < C().max_tickers);
    const others = B.filter(b => b.type !== r.type && b.tickers.length < C().max_tickers);
    const btn = (label, val, primary) => `<button class="btn small${primary ? " primary" : ""}" type="button" data-to="${val}">${e(label)}</button>`;
    to.innerHTML = `<span class="bk-to-l">Add <b>${e(short(r.symbol))}</b> <span class="muted">${e(r.name)}</span> to</span><span class="bk-to-b">`
      + fit.map((b, j) => btn(b.name, B.indexOf(b), j === 0)).join("")
      + (B.length < C().max_blocks ? btn(`A new ${TYPE[r.type].toLowerCase()} block`, "new", !fit.length) : "")
      + others.map(b => btn(b.name, B.indexOf(b), false)).join("")
      + `<button class="link-btn" type="button" data-to="cancel">Cancel</button></span>`;
    to.querySelectorAll("[data-to]").forEach(el => el.onclick = () => {
      if (el.dataset.to === "cancel") { picking = null; chooser(); return; }
      addTicker(r, el.dataset.to === "new" ? "new" : +el.dataset.to);
    });
  }

  let sugAt = 0, sugTimer = 0;
  async function search(input) {
    const q = input.value.trim(), ul = document.getElementById("bk-sug"), at = ++sugAt;
    if (!q) { ul.hidden = true; ul.innerHTML = ""; input.setAttribute("aria-expanded", "false"); return; }
    let found = [];
    try { found = (await (await fetch(`/api/tickers?bot=${bot}&q=` + encodeURIComponent(q), {cache: "no-store"})).json()).tickers || []; } catch (x) { found = null; }
    if (at !== sugAt || !document.body.contains(ul)) return;          // a newer search is on its way
    const where = {};
    draft.BLOCKS.forEach(b => b.tickers.forEach(t => { where[t] = b.name; }));
    ul.innerHTML = found === null ? `<li class="bk-nohit">The search didn't answer. Try again.</li>`
      : found.length ? found.map((r, j) => `<li role="none"><button type="button" role="option" id="bks-${j}" data-hit="${j}"${where[r.symbol] ? " disabled" : ""}>
          <b>${e(short(r.symbol))}</b><span>${e(r.name)}</span><small>${where[r.symbol] ? `in ${e(where[r.symbol])}` : e(TYPE[r.type])}</small></button></li>`).join("")
      : `<li class="bk-nohit">${T.none} “${e(q)}”.</li>`;
    ul.hidden = false;
    input.setAttribute("aria-expanded", "true");
    let cur = found && found.findIndex(r => !where[r.symbol]);
    const mark = () => ul.querySelectorAll("[data-hit]").forEach(b => { const on = +b.dataset.hit === cur; b.classList.toggle("on", on); b.setAttribute("aria-selected", on);
      if (on) { input.setAttribute("aria-activedescendant", b.id); b.scrollIntoView({block: "nearest"}); } });
    mark();
    const take = j => { const r = found[j]; if (!r || where[r.symbol]) return; picking = r; input.value = ""; ul.hidden = true; input.setAttribute("aria-expanded", "false"); chooser(); };
    ul.querySelectorAll("[data-hit]").forEach(b => { b.onmousedown = ev => ev.preventDefault(); b.onclick = () => take(+b.dataset.hit); });
    input.onkeydown = ev => {
      if (!found || !found.length) return;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        const step = ev.key === "ArrowDown" ? 1 : -1;
        for (let j = cur + step; j >= 0 && j < found.length; j += step) if (!where[found[j].symbol]) { cur = j; break; }
        mark(); ev.preventDefault();
      } else if (ev.key === "Enter") { take(cur); ev.preventDefault(); }
      else if (ev.key === "Escape") { input.value = ""; ul.hidden = true; input.setAttribute("aria-expanded", "false"); }
    };
  }

  /* The rows are built once per shape, so a slider being dragged is never replaced under the finger. */
  function build() {
    rows = ROWS.filter(r => !r.when || r.when(draft));
    box.querySelector(".tn-presets").innerHTML = V.charles_presets ? presetsHtml() : "";
    box.querySelector(".tn-rows").innerHTML = rows.map(rowHtml).join("");
    box.querySelectorAll("[data-slide]").forEach(el => el.oninput = () => { moved(el.dataset.slide, +el.value / +el.dataset.k); paint(); });
    box.querySelectorAll("[data-set]").forEach(el => el.onclick = () => {
      const shape = rows.length;
      moved(el.dataset.set, JSON.parse(el.dataset.val));
      if (ROWS.filter(r => !r.when || r.when(draft)).length !== shape) build(); else paint();
    });
    if (V.custom) wireCustom();
    paint();
  }

  function wireCustom() {
    box.querySelectorAll("[data-kind]").forEach(el => el.onclick = () => {
      const mine = el.dataset.kind === "own";
      if (mine === own(draft)) return;
      copying = null; picking = null;
      // "My own" starts from what the user has now: the S&P 500 part as a long-term block (stocks), or bitcoin and ether as
      // trend blocks with their shares (crypto: the same plan as Charles's until it's changed); the rest is theirs to fill
      const start = STOCKS ? [{name: "S&P 500", type: "long", pct: Math.round(draft.CORE_PCT * 100) / 100, tickers: [CORE()]}]
        : [["Bitcoin", "BTC-USD", draft.BTC_PCT], ["Ether", "ETH-USD", draft.ETH_PCT]].filter(x => x[2] >= .01)
            .map(([name, t, v]) => ({name, type: "trend", pct: Math.round(v * 100) / 100, tickers: [t]}));
      draft.BLOCKS = mine ? (clone(V.settings.BLOCKS) || start) : null;
      if (note.text) note = {text: "", tone: ""};
      build();
    });
    const q = document.getElementById("bk-q");
    if (q) {
      q.oninput = () => { clearTimeout(sugTimer); sugTimer = setTimeout(() => search(q), 140); };
      q.onblur = () => setTimeout(() => { const ul = document.getElementById("bk-sug"); if (ul && document.activeElement !== q) { ul.hidden = true; q.setAttribute("aria-expanded", "false"); } }, 150);
      q.onfocus = () => { if (q.value.trim()) search(q); };
    }
    chooser();
    box.querySelectorAll("[data-bpct]").forEach(el => el.oninput = () => {
      const i = +el.dataset.bpct, room = Math.round((1 - used(draft, i)) * 100);
      const v = Math.max(1, Math.min(+el.value, room));
      el.value = v;
      draft.BLOCKS[i].pct = v / 100;
      copying = null;
      if (note.text) note = {text: "", tone: ""};
      paint();
    });
    box.querySelectorAll("[data-name]").forEach(el => {
      el.oninput = () => { draft.BLOCKS[+el.dataset.name].name = el.value; copying = null; paint(); };
      el.onchange = () => { const b = draft.BLOCKS[+el.dataset.name]; b.name = tidy(el.value); el.value = b.name; paint(); };
      el.onkeydown = ev => { if (ev.key === "Enter") el.blur(); };
    });
    box.querySelectorAll("[data-type]").forEach(el => el.onchange = () => {
      const b = draft.BLOCKS[+el.dataset.type];
      if (TYPE[b.name] || tidy(b.name).replace(/ \d+$/, "") === TYPE[b.type]) b.name = freeName(TYPE[el.value]);    // a block still named for its kind follows it
      b.type = el.value;
      if (b.type === "momentum") b.picks = Math.max(1, Math.min(3, b.tickers.length)); else delete b.picks;
      copying = null;
      build();
    });
    box.querySelectorAll("[data-del]").forEach(el => el.onclick = () => {
      draft.BLOCKS.splice(+el.dataset.del, 1);
      copying = null; picking = null;
      build();
    });
    box.querySelectorAll("[data-untick]").forEach(el => el.onclick = () => {
      const b = draft.BLOCKS[+el.dataset.untick];
      b.tickers = b.tickers.filter(t => t !== el.dataset.sym);
      if (b.type === "momentum") b.picks = Math.max(1, Math.min(b.picks, b.tickers.length));
      copying = null;
      build();
    });
    box.querySelectorAll("[data-picks]").forEach(el => el.onclick = () => {
      const b = draft.BLOCKS[+el.dataset.picks], max = Math.min(C().max_picks || 8, b.tickers.length);
      b.picks = Math.max(1, Math.min(max, b.picks + +el.dataset.d));
      copying = null;
      build();
    });
    wirePresets();
  }

  /* ── Presets: Charles's move only amounts; the user's own hold a whole plan ── */
  function presetList() {
    const mine = (V.presets || []).map((p, i) => ({id: "u" + i, name: p.name, mine: true, i, settings: p.settings,
      about: "Your own preset: " + (p.settings.BLOCKS ? blockWords(p.settings.BLOCKS) : T.charles) + `, ${pc(p.settings.INVESTED ?? 1)} invested.`}));
    return [...(V.charles_presets || []), ...mine];
  }
  // what a preset does to the sliders: Charles's keep the user's blocks; a saved one is the whole plan it was saved with
  const applied = (p, d) => p.mine ? {...V.defaults, ...clone(p.settings)}
    : {...d, ...(p.settings ? p.settings : Object.fromEntries(Object.entries(V.defaults).filter(([k]) => k !== "BLOCKS")))};
  const matches = (p, d) => Object.entries(applied(p, d)).every(([k, v]) => same(v, d[k]));
  const active = d => { const L = presetList(); return L.find(p => p.mine && matches(p, d)) || L.find(p => !p.mine && matches(p, d)); };

  function presetsHtml() {
    if (!V.charles_presets) return "";
    const L = presetList(), on = active(draft), full = (V.presets || []).length >= (C().max_presets || 8);
    return `<div class="tn-pre-row" role="group" aria-label="Presets">${L.map(p => `<button class="tn-pre${p.mine ? " mine" : ""}" type="button" data-pre="${p.id}">${e(p.name)}</button>`).join("")}
        ${full ? "" : `<button class="tn-pre add" type="button" data-pre-save>+ Save as preset</button>`}</div>
      <div class="tn-pre-form" hidden><input type="text" id="tn-pre-name" maxlength="${C().name_max || 30}" placeholder="Name your preset" aria-label="Preset name" autocomplete="off">
        <button class="btn small primary" type="button" data-pre-ok>Save preset</button><button class="btn small" type="button" data-pre-cancel>Cancel</button></div>
      <p class="tn-pre-about" id="tn-pre-about" data-on="${on ? on.id : ""}">${on ? e(on.about) : "Presets move the sliders. Nothing changes until you save."}
        ${on && on.mine ? `<button class="link-btn" type="button" data-pre-del="${on.i}">Delete this preset</button>` : ""}</p>`;
  }

  function wirePresets() {
    box.querySelectorAll("[data-pre]").forEach(el => el.onclick = () => {
      const p = presetList().find(x => x.id === el.dataset.pre);
      if (!p) return;
      draft = applied(p, draft); copying = null; picking = null; naming = false;
      if (note.text) note = {text: "", tone: ""};
      build();
    });
    const form = box.querySelector(".tn-pre-form"), name = document.getElementById("tn-pre-name");
    if (!form) return;
    const open = on => { naming = on; form.hidden = !on; box.querySelector(".tn-pre-row").hidden = on; document.getElementById("tn-pre-about").hidden = on; if (on) name.focus(); };
    open(naming);
    const add = box.querySelector("[data-pre-save]");
    if (add) add.onclick = () => {
      const why = problem(draft);
      if (why) { say(why, "down"); return; }
      if (!V.can) { say(V.why || "Saving needs the dashboard login.", "down"); return; }
      open(true);
    };
    box.querySelector("[data-pre-cancel]").onclick = () => open(false);
    const ok = async () => {
      const n = tidy(name.value);
      if (!n) { name.focus(); return; }
      if (!NAME_OK.test(n)) { say("A preset's name can use letters, numbers, spaces and & ' + , . / ( ) -", "down"); return; }
      if ((V.presets || []).some(p => p.name.toLowerCase() === n.toLowerCase()) || (V.charles_presets || []).some(p => p.name.toLowerCase() === n.toLowerCase())) {
        say(`There's already a preset called ${n}.`, "down"); return; }
      const plan = Object.fromEntries(Object.keys(V.defaults).map(k => [k, clone(draft[k])]));
      naming = false;
      await send("/api/settings", {bot, settings: {PRESETS: [...(V.presets || []), {name: n, settings: plan}]}}, "apply-settings", `Saved the preset ${n}.`, true);
    };
    box.querySelector("[data-pre-ok]").onclick = ok;
    name.onkeydown = ev => { if (ev.key === "Enter") ok(); if (ev.key === "Escape") open(false); };
    box.querySelectorAll("[data-pre-del]").forEach(el => el.onclick = () => {
      const p = (V.presets || [])[+el.dataset.preDel];
      if (!p || !confirm(`Delete the preset ${p.name}? Your plan stays as it is.`)) return;
      send("/api/settings", {bot, settings: {PRESETS: V.presets.filter((x, i) => i !== +el.dataset.preDel)}}, "apply-settings", `Deleted the preset ${p.name}.`, true);
    });
  }

  function paint() {
    const d = draft, list = changes(), any = !!list.length, why = problem(d);
    if (box.parentElement.classList.contains("tune-row")) box.parentElement.classList.toggle("own", own(d));
    for (const r of rows) {
      if (r.kind) continue;
      const about = box.querySelector(`[data-about="${r.k}"]`);
      if (about) about.textContent = typeof r.about === "function" ? r.about(d) : r.about || "";
      const out = document.getElementById("tno-" + r.k);
      if (out) out.textContent = r.show(d[r.k]);
      const el = document.getElementById("tn-" + r.k);
      if (el) { const k = +el.dataset.k; el.value = Math.round(d[r.k] * k); el.style.setProperty("--p", (el.value - el.min) / (el.max - el.min) * 100 + "%"); }
    }
    box.querySelectorAll("[data-set]").forEach(b => { const on = JSON.parse(b.dataset.val) === d[b.dataset.set]; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    box.querySelectorAll("[data-kind]").forEach(b => { const on = (b.dataset.kind === "own") === own(d); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    if (own(d)) {
      d.BLOCKS.forEach((b, i) => {
        const el = document.getElementById("bk-" + i);
        if (el) { el.value = Math.round(b.pct * 100); el.style.setProperty("--p", el.value + "%"); }
        const out = document.getElementById("bko-" + i);
        if (out) out.textContent = pc(b.pct);
        const l = document.getElementById("bkl-" + i);
        if (l) l.textContent = (d.INVESTED ?? 1) < .9995 ? `Share · ${pc(b.pct * d.INVESTED)} of ${STOCKS ? "all your money" : "the book"}` : "Share";
      });
      const left = document.getElementById("bk-left"), room = Math.round((1 - used(d)) * 100);
      if (left) { left.textContent = why || (room > 0 ? `${room}% isn't in a block: it waits in ${T.rest}.` : ""); left.className = "bk-left" + (why ? " bad" : ""); left.hidden = !left.textContent; }
    }
    const on = V.charles_presets ? active(d) : null;
    box.querySelectorAll("[data-pre]").forEach(b => { const x = !!on && b.dataset.pre === on.id; b.classList.toggle("on", x); b.setAttribute("aria-pressed", x); });
    const preAbout = document.getElementById("tn-pre-about");
    if (preAbout && !preAbout.hidden) {
      const was = preAbout.dataset.on || "", now = on ? on.id : "";
      if (was !== now) { preAbout.dataset.on = now; preAbout.innerHTML = (on ? e(on.about) : "Presets move the sliders. Nothing changes until you save.")
        + (on && on.mine ? ` <button class="link-btn" type="button" data-pre-del="${on.i}">Delete this preset</button>` : ""); wirePresets(); }
    }
    const parts = money().filter(m => m[1] > 0.0005), bar = box.querySelector(".tn-bar"), W = (bar.clientWidth || 320) - 2 * (parts.length - 1);
    bar.innerHTML = parts.map(([l, v, c]) => { const px = v * W, num = pc(v), both = px >= (l.length + num.length + 1) * 7.2 + 14;
      return `<i style="flex-grow:${v * 100};background:${c}" title="${e(l)} ${num}"><span>${both ? `${e(l)} ${num}` : px >= num.length * 7.2 + 8 ? num : ""}</span></i>`; }).join("");
    bar.setAttribute("aria-label", parts.map(([l, v]) => `${l} ${pc(v)}`).join(", "));
    const del = box.querySelector(".tn-diff");
    del.hidden = !any;
    del.innerHTML = !any ? "" : `<div class="sh-h" style="margin-top:0">${copying ? `What changes if you copy ${e(copying.name)}` : "What changes"}</div>` + list.map(k => {
      const [l, f] = WORDS[k] || [k, x => x];
      return `<div class="chg"><span>${e(l)}</span><b class="num">${e(f(V.settings[k]))} → ${e(f(d[k]))}</b></div>`; }).join("");
    const reset = box.querySelector(".tn-reset");
    if (reset) reset.hidden = Object.keys(V.defaults).every(k => same(d[k], V.defaults[k]));
    const save = box.querySelector(".tn-save");
    save.hidden = !any;
    save.disabled = busy || !V.can || !!why;
    save.textContent = busy ? "Saving…" : copying ? `Copy ${copying.name}` : "Save";
    const test = box.querySelector(".tn-test");
    test.hidden = !any;
    test.disabled = busy || !V.can || !!why;
    box.querySelector(".tn-back").hidden = !any;
    box.querySelector(".tn-back").disabled = busy;
    const undo = box.querySelector(".tn-undo");
    undo.hidden = any || !V.can_undo || !V.can;
    undo.disabled = busy;
    const m = box.querySelector(".tn-msg");
    m.className = "lb-msg tn-msg " + (note.tone || "");
    m.textContent = note.text;
    m.hidden = !note.text;
    const whyNot = box.querySelector(".tn-whynot");
    whyNot.hidden = V.can || !any;
    whyNot.textContent = V.why || "";
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
  /* One round: ask, wait for the root job if there is one, then look at the file again. `keepDraft`: a preset or self-tune was saved,
     so the sliders stay where the user had them. */
  async function send(path, body, action, saidDone, keepDraft) {
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
    if (done) { await load(true, keepDraft); say(res.test && !keepDraft ? res.message : saidDone, "up"); } else { paint(); say(text || "That didn't work.", "down"); }
    return done;
  }

  function save() {
    if (copying) return send("/api/leaderboard/copy", {id: copying.id}, "copy-config", SAVED);
    (draft.BLOCKS || []).forEach(b => { b.name = tidy(b.name); });
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
    const line = (label, r) => r ? `<div class="chg"><span>${label}</span><b class="num">${e(f(r))}</b></div>` : "";
    keep.hidden = false;
    keep.innerHTML = `<div class="sh-h" style="margin-top:0">On past prices, ${e(b.history)}</div>
      ${line("Now", b.now.stocks || b.now.plan)}${line("With this change", b.with_change.stocks || b.with_change.plan)}
      ${line("Sector funds, now", b.now.sectors)}${line("Sector funds, with it", b.with_change.sectors)}
      <p class="small muted" style="margin:6px 0 0">Simulated, not a promise.${b.now.sectors ? " The sector line has no hindsight in its stock list."
        : ` A ${T.item} without prices that far back waits in ${T.rest} until its prices start.${STOCKS ? "" : ` The test charges Coinbase's ${((V.fee || .006) * 100).toFixed(1)}% fee on every trade.`}`}</p>`;
    say("");
  }

  function shell() {
    box.innerHTML = `<div class="card-head"><h2>Set it yourself</h2></div>
      <div class="tn-presets"></div>
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
    box.querySelector(".tn-back").onclick = () => { draft = {...V.settings, BLOCKS: clone(V.settings.BLOCKS)}; copying = null; picking = null; box.querySelector(".tn-bt").hidden = true; note = {text: "", tone: ""}; build(); };
    const reset = box.querySelector(".tn-reset");
    if (reset) reset.onclick = () => { draft = {...V.settings, ...V.defaults}; copying = null; note = {text: "", tone: ""}; build(); };
    box.querySelector(".tn-undo").onclick = () => send("/api/settings", {bot, undo: true}, "undo-settings", "Undone. The settings from before are back.");
  }

  /* ── Self-tune on or off (the Self-Tune tab; owner 2026-11-03) ── */
  function paintSelfTune() {
    if (!stBox) return;
    if (!V || !V.ok || V.self_tune == null) { stBox.hidden = true; return; }
    stBox.hidden = false;
    const on = V.self_tune !== false;
    const say_ = on ? (V.checkup && STOCKS ? "Claude may change one number of your plan a quarter, and only a change that won every test of the monthly check."
                                  : `Once a quarter at most, one number of your plan changes by itself, and only a change that won every test of the monthly check on ${STOCKS ? "19 years of prices" : "Coinbase's prices since 2017"}.`)
        + ` It never adds or removes your ${T.items}, and every change can be undone on the Plan tab.`
      : "The monthly check still runs and shows what it found here, but nothing changes by itself.";
    stBox.innerHTML = `<div class="card-head"><h2>Self-tune</h2><div class="tabs" role="group" aria-label="Self-tune" style="margin-left:auto">
        <button type="button" data-st="1" class="${on ? "on" : ""}" aria-pressed="${on}">On</button><button type="button" data-st="0" class="${on ? "" : "on"}" aria-pressed="${!on}">Off</button></div></div>
      <p class="st-say">${e(say_)}</p>
      ${stNote.text ? `<p class="lb-msg ${e(stNote.tone)}" role="status">${e(stNote.text)}</p>` : ""}
      ${!V.can ? `<p class="small muted st-why">${e(V.why || "")}</p>` : V.test ? `<p class="small muted st-why">On this preview, this writes only to a test folder.</p>` : ""}`;
    stBox.querySelectorAll("[data-st]").forEach(b => { b.disabled = stBusy || !V.can; b.onclick = () => setSelfTune(b.dataset.st === "1"); });
  }

  async function setSelfTune(on) {
    if (stBusy || on === (V.self_tune !== false)) return;
    stBusy = true; stNote = {text: "Saving…", tone: ""}; paintSelfTune();
    const before = (await lastJob()).at;
    const res = await call("/api/settings", {bot, settings: {SELF_TUNE: on}}).catch(() => ({ok: false, message: "The dashboard didn't answer. Try again."}));
    let done = res.ok, text = res.message;
    if (res.ok && !res.done) { const j = await job("apply-settings", before); done = j.status === "done"; text = done ? "" : j.result || "That didn't work."; }
    stBusy = false;
    if (done) { await load(true, true); stNote = {text: on ? "Self-tune is on." : "Self-tune is off. Nothing changes by itself.", tone: "up"}; }
    else stNote = {text: text || "That didn't work.", tone: "down"};
    paintSelfTune();
  }

  async function load(keepNote, keepDraft) {
    let got = null;
    try { got = await (await fetch("/api/settings?bot=" + bot, {cache: "no-store"})).json(); } catch (x) { got = null; }
    if (!got || !got.ok) { if (!V) { box.hidden = true; if (stBox) stBox.hidden = true; } return; }
    const before = V;
    V = got;
    Object.assign(names, V.names || {});
    if (!box.querySelector(".tn-rows")) shell();
    if (!(keepDraft && draft && before)) { draft = {...V.settings, BLOCKS: clone(V.settings.BLOCKS)}; copying = null; picking = null; box.querySelector(".tn-bt").hidden = true; }
    if (!keepNote) note = {text: "", tone: ""};
    build();
    paintSelfTune();
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
      <p class="fi-foot">Copy moves the sliders to their settings, on ${T.charles}. Nothing changes until you press the button under them.</p>`;
    copyBox.querySelectorAll("[data-copy-id]").forEach(el => el.onclick = () => {
      const r = top.find(x => x.id === el.dataset.copyId);
      if (!r || !draft) return;
      // a copy is the leaderboard's plan on Charles's picks: the user's own blocks and the share invested go (Undo brings them back)
      draft = {...V.settings, BLOCKS: null, INVESTED: 1, ...r.settings}; copying = r; picking = null; note = {text: "", tone: ""};
      build();
      if (innerWidth < 860) box.scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"});
    });
    if (V) paint();
  }

  let rz = 0;
  addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { if (V && draft) paint(); }, 150); });     // the bar's labels fit its new width
  load().then(loadBoard);
})();
