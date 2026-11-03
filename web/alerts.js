/* Alerts are always on. On a wide screen the window stays open beside the page, on every page, until the bell hides it (the
   choice is remembered), and it shrinks to sit on top of the chat while the chat is open. While the window is hidden, and on
   a phone, a one-line bubble pops up in the header instead. Everything here comes from /api/alerts (webdash.alert_events),
   so it only ever shows what really happened.
   It's only for while the dashboard is open: nothing is pushed to a closed phone. */
(function () {
  const bell = document.getElementById("bell");
  if (!bell) return;
  const SEEN = "charles-alerts-seen", READ = "charles-alerts-read", WIN = "charles-alerts-win", ANY = "charles-alerts-any", TOUR = "charles-tour-v2";
  const POLL = 60000, STAY = 5200, STAY_ERROR = 11000, FIRST_DAYS = 14, BEAT = 1300, SHOW = 3;    // BEAT: Home's number and chart get their moment first
  const page = (document.querySelector("header.shell") || {dataset: {}}).dataset.page;
  const docked = matchMedia("(min-width: 860px)"), roomy = matchMedia("(min-width: 1240px)");   // beside the page; open without being asked
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const memory = {};                                       // no browser storage: alerts still work for this page
  const get = k => { try { const v = localStorage.getItem(k); return v === null ? memory[k] ?? null : v; } catch (x) { return memory[k] ?? null; } };
  const put = (k, v) => { memory[k] = String(v); try { localStorage.setItem(k, v); } catch (x) {} };
  const e = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const safeHref = h => typeof h === "string" && /^\/(?!\/)/.test(h) ? h : null;      // only this dashboard's own pages
  const money = (v, d = 2) => (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
  const ms = a => +new Date(a.ts) || 0;
  const whenOf = ts => { const t = new Date(ts), now = new Date();
    return t.toDateString() === now.toDateString() ? t.toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit"}) : t.toLocaleDateString("en-US", {month: "short", day: "numeric"}); };
  const svg = d => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICON = {buy: svg('<path d="M12 19V5M6 11l6-6 6 6"/>'), sell: svg('<path d="M12 5v14M6 13l6 6 6-6"/>'), won: svg('<path d="M5 13l4.5 4.5L19 7"/>'), lost: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
                bet: svg('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/>'), note: svg('<path d="M12 11v6M12 7.2v.1"/>'), error: svg('<path d="M12 4l9 16H3zM12 10v4.5M12 17.6v.1"/>')};

  function inner(a) {
    const amt = a.pnl != null ? `<b class="al-amt ${a.pnl > 0 ? "up" : a.pnl < 0 ? "down" : ""}">${a.pnl > 0 ? "+" : ""}${money(a.pnl)}</b>` : a.amount != null ? `<b class="al-amt">${money(a.amount, a.amount >= 100 ? 0 : 2)}</b>` : "";
    return `<span class="al-ic ${e(a.kind)}">${ICON[a.kind] || ICON.note}</span>
      <span class="al-t">${e(a.title)}</span>${amt}
      ${a.sub ? `<span class="al-s">${e(a.sub)}</span>` : ""}
      <span class="al-m">${e(a.bot)} · ${e(whenOf(a.ts))}</span>`;
  }

  // ── The bubble ───────────────────────────────────────────────────────────
  // One at a time, in the free stretch of the header. Where that's too narrow (a phone) it covers the bar for a moment.
  const bar = document.querySelector("header.shell .bar");
  const box = document.createElement("div");
  box.className = "toasts"; box.setAttribute("aria-live", "polite"); box.setAttribute("aria-label", "Alerts");
  bar.append(box);
  const WIDE_ENOUGH = 250;
  function lane() {                                        // from the end of the logo or the menu to the status, whichever fits
    const b = bar.getBoundingClientRect(), end = bar.querySelector(".bar-end").getBoundingClientRect();
    const nav = bar.querySelector(".topnav"), brand = bar.querySelector(".brand").getBoundingClientRect();
    const navEnd = nav && nav.offsetParent ? nav.lastElementChild.getBoundingClientRect().right : 0;
    const roomy = x => end.left - x - 16 >= WIDE_ENOUGH;
    const from = navEnd && roomy(navEnd) ? navEnd : roomy(brand.right) ? brand.right : null;
    box.style.left = from === null ? "8px" : (from - b.left + 8) + "px";
    box.style.right = from === null ? "8px" : (b.right - end.left + 8) + "px";
    box.classList.toggle("cover", from === null);
  }
  addEventListener("resize", lane);

  let queue = [], cur = null, unshown = 0;                 // unshown: new ones past the first few; "+N" opens the list
  function compact(a) {
    const amt = a.pnl != null ? `<b class="al-amt ${a.pnl > 0 ? "up" : a.pnl < 0 ? "down" : ""}">${a.pnl > 0 ? "+" : ""}${money(a.pnl)}</b>` : a.amount != null ? `<b class="al-amt">${money(a.amount, a.amount >= 100 ? 0 : 2)}</b>` : "";
    return `<span class="al-ic ${e(a.kind)}">${ICON[a.kind] || ICON.note}</span><span class="toast-t">${e(a.title)}</span>${amt}`;
  }
  function leave(el, dx = 0) {
    clearTimeout(el._t);
    el.classList.remove("in");
    if (dx) el.style.transform = `translateX(${dx}px)`;
    setTimeout(() => el.remove(), still ? 0 : 240);
    if (cur === el) cur = null;
  }
  function next() { if (cur) leave(cur); const a = queue.shift(); if (a) show(a); else unshown = 0; }
  function arm(el, ms_) { clearTimeout(el._t); el._t = setTimeout(next, ms_); }

  function show(a) {
    lane();
    const el = document.createElement("div"), left = queue.length + unshown;
    el.className = "toast" + (a.kind === "error" ? " err" : "");
    el.innerHTML = `<button type="button" class="toast-main">${compact(a)}</button>
      ${left ? `<button type="button" class="toast-more" aria-label="${left} more alerts">+${left}</button>` : ""}
      <button type="button" class="toast-x" aria-label="Dismiss"><svg viewBox="0 0 14 14" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M3 3l8 8M11 3l-8 8"/></svg></button>`;
    box.append(el);
    cur = el;
    el.querySelector(".toast-x").onclick = next;
    const more = el.querySelector(".toast-more");
    if (more) more.onclick = openList;
    el.querySelector(".toast-main").onclick = () => openList(true, a);     // the list, with this one open
    // Swipe it away; a swipe that goes back is a tap.
    let x0 = null, dx = 0;
    el.addEventListener("pointerdown", ev => { if (ev.pointerType !== "mouse") { x0 = ev.clientX; dx = 0; } });
    el.addEventListener("pointermove", ev => {
      if (x0 === null) return;
      dx = ev.clientX - x0;
      el.style.transition = "none"; el.style.transform = `translateX(${dx}px)`;
    });
    const end = () => { if (x0 === null) return; x0 = null; el.style.transition = ""; el.style.transform = ""; if (Math.abs(dx) > 70) { queue.length ? next() : leave(el, dx > 0 ? 300 : -300); } };
    el.addEventListener("pointerup", end); el.addEventListener("pointercancel", end);
    el.addEventListener("click", ev => { if (Math.abs(dx) > 8) { ev.preventDefault(); ev.stopPropagation(); } }, true);
    el.addEventListener("pointerenter", () => clearTimeout(el._t));     // reading it: it stays
    el.addEventListener("pointerleave", () => arm(el, 2500));
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add("in")));
    arm(el, a.kind === "error" ? STAY_ERROR : STAY);
  }

  // The newest few, one after the other; anything that needs a look goes first.
  function popUp(rows) {
    const sorted = [...rows.filter(a => a.kind === "error"), ...rows.filter(a => a.kind !== "error")];
    queue = sorted.slice(0, SHOW);
    unshown = sorted.length - queue.length;
    if (cur) leave(cur);
    const a = queue.shift();
    if (a) show(a);
  }
  const clearAll = () => { queue = []; unshown = 0; if (cur) leave(cur); };

  // ── When ─────────────────────────────────────────────────────────────────
  let A = [], stopped = false;
  const listOpen = () => list && !list.hidden;
  const touring = () => document.querySelector(".tour-card");
  const tourFirst = () => { if (document.documentElement.dataset.tour) return true; if (page !== "home") return false; try { return !localStorage.getItem(TOUR); } catch (x) { return false; } };
  // Open without being asked once there's something to show, so a new install's page doesn't move over for an empty window.
  const wanted = () => { const w = get(WIN); return docked.matches && (w === "open" || (w === null && roomy.matches && get(ANY) === "1")); };
  const settle = () => { if (!listOpen() && wanted() && !touring() && !tourFirst()) openList(false); };

  function badge() {
    const read = +get(READ) || 0, n = A.filter(a => ms(a) > read).length, el = document.getElementById("bell-n");
    el.hidden = !n;
    el.textContent = n > 9 ? "9+" : n;
    bell.setAttribute("aria-label", n ? `Alerts: ${n} new` : "Alerts");
  }

  function check() {
    if (!A.length || touring() || tourFirst()) return;        // waits, and shows at the next look
    const newest = Math.max(...A.map(ms)), seen = get(SEEN);
    if (seen !== null && newest <= +seen) return;
    put(SEEN, newest);
    if (listOpen()) return;                                   // it's in the window already
    popUp(A.filter(a => seen === null ? newest - ms(a) < FIRST_DAYS * 864e5 : ms(a) > +seen));    // a first visit: what's recent
  }

  async function poll() {
    if (document.hidden || stopped) return;
    try {
      const r = await fetch("/api/alerts", {cache: "no-store"});
      if (r.status === 401 || r.status === 403) { stopped = true; if (listOpen()) setShown(false); return; }
      A = (await r.json()).alerts || [];
    } catch (x) { return; }
    if (A.length) put(ANY, "1");
    settle();
    if (listOpen()) fill();
    badge();
    check();
  }

  // ── The list behind the bell ─────────────────────────────────────────────
  let list = null;
  function makeList() {
    list = document.createElement("div");
    list.className = "alerts-panel"; list.hidden = true; list.setAttribute("role", "dialog"); list.setAttribute("aria-label", "Alerts");
    list.innerHTML = `<div class="chat-head"><h2>Alerts</h2><button class="icon" id="alerts-close" type="button" aria-label="Close"><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M3 3l8 8M11 3l-8 8"/></svg></button></div><div class="alerts-list" id="alerts-list"></div>`;
    document.body.append(list);
    document.getElementById("alerts-close").onclick = closeList;
    document.getElementById("alerts-list").addEventListener("click", ev => {   // a row opens in place to its whole message
      const row = ev.target.closest("button.al-line");
      if (!row) return;
      const item = row.parentElement, on = item.classList.toggle("open");
      row.setAttribute("aria-expanded", String(on));
      if (on) opened.add(item.dataset.k); else opened.delete(item.dataset.k);
    });
  }
  const keyOf = a => a.ts + a.title, opened = new Set();
  const goTo = (a, h) => h === "/" ? "Go to Home" : `Go to ${a.bot}`;
  let filled = null, loaded = false;
  function fill() {                                        // only when something changed, so a scrolled list stays where it is
    const key = A.map(a => a.ts + a.title).join();
    if (key !== filled || !loaded) {
      const read = +get(READ) || 0;
      document.getElementById("alerts-list").innerHTML = A.length ? A.map(a => { const h = safeHref(a.href), on = opened.has(keyOf(a));
        return `<div class="al-item${on ? " open" : ""}" data-k="${e(keyOf(a))}"><button type="button" class="al-row al-line${ms(a) > read ? " new" : ""}" aria-expanded="${on}">${inner(a)}</button>${h ? `<a class="al-go" href="${e(h)}">${e(goTo(a, h))}</a>` : ""}</div>`; }).join("")
        : `<p class="alerts-empty">Nothing yet. Trades, bets and anything that needs a look show up here.</p>`;
      filled = key; loaded = true;
    }
    if (A.length) put(READ, Math.max(...A.map(ms)));
  }
  function setShown(on) {
    list.hidden = !on;
    document.body.classList.toggle("alerts-open", on);
    bell.classList.toggle("on", on); bell.setAttribute("aria-expanded", String(on));
    dispatchEvent(new Event("resize"));                    // the page moved over: the charts redraw at their new width
  }
  function openList(asked = true, one = null) {
    if (!list) makeList();
    if (one) { opened.add(keyOf(one)); filled = null; }
    if (asked) { put(WIN, "open"); fill(); }
    clearAll();
    setShown(true);
    badge();
    if (asked) document.getElementById("alerts-close").focus();
    if (one) { const k = list.querySelector(".al-item.open[data-k=\"" + CSS.escape(keyOf(one)) + "\"]"); if (k) k.scrollIntoView({block: "nearest"}); }
  }
  function closeList() { if (!list) return; put(WIN, "hidden"); setShown(false); bell.focus(); }
  bell.setAttribute("aria-haspopup", "dialog");
  bell.onclick = () => listOpen() ? closeList() : openList();
  // On a phone it's a sheet over the page, so a tap elsewhere puts it away too; beside the page, only the bell does.
  const onSheet = () => listOpen() && !docked.matches;
  document.addEventListener("keydown", ev => { if (ev.key === "Escape" && onSheet()) closeList(); });
  document.addEventListener("pointerdown", ev => { if (onSheet() && !ev.target.closest(".alerts-panel, #bell, #chat-open, .chat-panel, .chat-scrim")) closeList(); });

  addEventListener("charles:tour-done", () => { settle(); if (listOpen()) fill(); check(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  settle();                                                // before the first paint, so the page doesn't jump
  setTimeout(poll, listOpen() ? 0 : BEAT);
  setInterval(poll, POLL);
})();
