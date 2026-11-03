/* The first-visit tour: Charles talks you through Home, then the Leaderboard, where you really copy someone, reset it, share your
   settings and enter the monthly challenge. It waits on its own page and picks up after a reload. A real part of the page stays lit;
   on a "do" step only that part can be pressed. Inside a dialog (the top layer, where nothing outside it can be pressed) Charles
   talks from a strip at the top of the dialog. web/board.js says what happened through the "charles:board" event.
   No browser storage means no tour, rather than one on every visit. It starts by itself only on a new install; Settings can replay it. */
(function () {
  const header = document.querySelector("header.shell");
  if (!header) return;
  const page = header.dataset.page, html = document.documentElement;
  const DONE = "charles-tour-v2", AT = "charles-tour-at", PAGES = {home: "/", leaderboard: "/leaderboard"};
  let done, at;
  try { done = localStorage.getItem(DONE); at = localStorage.getItem(AT); } catch (e) { return; }
  const put = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
  const del = k => { try { localStorage.removeItem(k); } catch (e) {} };
  const $ = id => document.getElementById(id);

  const keysIn = () => !!document.querySelector("#t-setup .setup-row.done");
  const running = () => !!document.querySelector("#t-run .dot.good");
  const anyone = () => !!document.querySelector(".lb-row");
  const challengeName = () => ($("ch-sum") ? $("ch-sum").textContent.split(": ").slice(1).join(": ") : "");

  /* What Charles says, in order. A step goes on to the next one unless it says otherwise.
     sel: what stays lit. need: wait for this, else go to `miss`. do: only `allow` can be pressed, and `on` says what happens next.
     dlg: said inside the dialog; `on` as above, `closed` when the dialog closes, `fail` when board.js reports a refusal. */
  const STEPS = [
    {id: "hi", page: "home", mood: "happy", reply: "Show me around", skip: ["Not now", "end"],
     say: "Woof! I'm Charles, your new trading bot. Got two minutes? I'll show you around."},
    {id: "todo", page: "home", sel: "#t-setup", need: "#t-setup", miss: "score", reply: "Okay",
     say: () => keysIn() ? "Here's your to-do list. Your keys are in, so that's one down. The plan's next, and I'll show you the lazy way in a minute."
       : "Here's your to-do list. Start with your keys. I can't fetch a thing without them."},
    {id: "score", page: "home", sel: "#t-earn", mood: "wink", reply: "Ha, okay",
     say: "This is the scoreboard. It's all practice money, so the worst I can do is embarrass us both."},
    {id: "run", page: "home", sel: "#t-run", mood: "happy", reply: "Good dog",
     say: () => running() ? "I check the plan once a day, at 10 AM Eastern on weekdays. Most days I do nothing, on purpose. Press Pause and I'll sit. Sitting never sells anything."
       : "I start once your keys are in. Then I check the plan once a day, at 10 AM Eastern on weekdays. Most days I do nothing, on purpose."},
    {id: "bots", page: "home", sel: "#t-bots", mood: "think", reply: "Noted",
     say: "Tap me for my chart and what I'm holding. My Plan tab has the sliders, if you like to micromanage."},
    {id: "help", page: "home", sel: "#t-help", reply: "Got it",
     say: "The bell is what I've been up to. The bubble is for questions, which I love. The gear has your keys and updates."},
    {id: "board", page: "home", sel: "#t-board", need: "#t-board", miss: "bye", mood: "wow", reply: "Take me there", skip: ["Skip this part", "bye"],
     say: "Now the fun part. Other people run me too, with their own settings. Let's go sniff around."},

    {id: "lb", page: "leaderboard", sel: "#list", need: "#list > li", mood: "think", reply: "Nice",
     say: () => anyone() ? "Everyone's settings, all run on the same public prices. So nobody can fib about their returns. Not even me."
       : "Nobody's been here 20 days yet, so the board is still warming up. Once it fills, this is where you copy people."},
    {id: "copy", page: "leaderboard", do: true, sel: ".lb-row:nth-child(-n+3)", need: ".lb-row", miss: "share", allow: ".lb-row", skip: ["Skip this part", "share"], on: {"open-copy": "copy-check"},
     say: "Let's borrow someone's homework. Press Copy next to anyone who looks smart."},
    {id: "copy-check", dlg: true, closed: "copy", fail: "share", on: {copied: "copy-done"},
     say: () => $("d-go") ? "I always show you what changes before anything happens. Like it? Press Copy these settings."
       : "Ha, you're already using those. Close this and pick someone else."},
    {id: "copy-done", dlg: true, mood: "proud", closed: "reset", on: {undone: "undo-done"},
     say: "Done! I'll use those at my next check, and I may trade to match. Press Done."},
    {id: "undo-done", dlg: true, mood: "wink", closed: "share",
     say: "Undone, just like that. Your old settings are back. Press Done."},
    {id: "reset", page: "leaderboard", do: true, sel: "#me", need: "#me-reset", miss: "share", allow: "#me-reset", skip: ["Skip this part", "share"], on: {"open-reset": "reset-check"},
     say: "Want the default plan back? Reset does it, any time, and shows you what changes first. Try it."},
    {id: "reset-check", dlg: true, closed: "reset", fail: "share", on: {reset: "reset-done"},
     say: "Same deal: here's what changes back. Press Reset."},
    {id: "reset-done", dlg: true, mood: "wink", closed: "share",
     say: "Back on the default plan. No hard feelings. Press Done."},
    {id: "share", page: "leaderboard", do: true, sel: "#me", need: "#me-share", miss: "ch", allow: "#me-share", skip: ["Skip this part", "ch"], on: {"open-share": "share-check"},
     say: "Think you can beat them? Share puts your settings up on the board. This part's optional."},
    {id: "share-check", dlg: true, closed: "share", fail: "ch", on: {shared: "share-wait"},
     say: "Only these settings go up. Never your money, keys, holdings or trades. Add a name if you want the glory, then press Share publicly."},
    {id: "share-wait", dlg: true, mood: "proud", closed: "shared", say: "Woof! Up it goes."},
    {id: "shared", page: "leaderboard", sel: "#me", need: "#me", mood: "proud", reply: "Can't wait",
     say: "You're on the board! You get ranked after 20 days. Change your mind? Withdraw retires your entry."},
    {id: "ch", page: "leaderboard", do: true, sel: "#ch-box", need: "#ch-box", miss: "bye", allow: "#ch-enter", skip: ["Skip this part", "bye"], on: {"open-enter": "ch-check"},
     before: () => { $("ch-box").open = true; },
     say: () => `One more. Every month has a challenge with its own rules. This one's “${challengeName()}”. Your bot keeps its settings, and only your entry follows the rules. Press Enter.`},
    {id: "ch-check", dlg: true, closed: "ch", fail: "bye", on: {entered: "ch-wait"},
     say: "These are the settings I'd enter. There are no prizes, only bragging rights. Press Enter publicly."},
    {id: "ch-wait", dlg: true, mood: "proud", closed: "ch-done", say: "You're in!"},
    {id: "ch-done", page: "leaderboard", sel: "#ch-box", need: "#ch-box", miss: "bye", mood: "proud", reply: "Let's win",
     say: "All set. You can enter again or withdraw until it starts. Everyone runs the same days, so no head starts."},

    {id: "bye", mood: "happy", reply: "Thanks, Charles", last: true,
     say: "That's the whole tour! I'll take it from here and check the plan at 10 AM Eastern on weekdays. Settings can replay this tour any time."},
  ];
  const byId = id => STEPS.find(s => s.id === id);
  const after = st => st.then || (STEPS[STEPS.indexOf(st) + 1] || {}).id || "end";

  if (done && !at) return;
  const first = byId(at) || STEPS[0];
  if (first.page && first.page !== page) return;            // it waits on its own page
  html.dataset.tour = "on";                                 // web/alerts.js holds its bubbles until the tour is over

  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const dlg = $("dlg");
  const block = document.createElement("div"), spot = document.createElement("div"), card = document.createElement("div");
  block.className = "tour-block"; spot.className = "tour-spot";
  card.tabIndex = -1; card.setAttribute("role", "dialog"); card.setAttribute("aria-labelledby", "tour-t");
  let cur = null, run = 0, talking = null, ended = false, built = false;

  /* His face is the logo in the header, with a tongue and two happy eyes to choose from (shell.css shows them by mood). */
  const FACE = (() => {
    const svg = header.querySelector(".logo svg").cloneNode(true);
    svg.removeAttribute("width"); svg.removeAttribute("height");
    svg.querySelectorAll(".eye, .glint").forEach(c => { if (+c.getAttribute("cx") > 16) c.classList.add("r"); });
    svg.insertAdjacentHTML("beforeend", '<path class="f-tongue" d="M14.6 20.6h2.8v2.2a1.4 1.4 0 0 1-2.8 0z" fill="#ee7f90" stroke="#d65f72" stroke-width=".3"/>' +
      '<path class="f-arc f-arc-l" d="M11.1 16q1.5-2.1 3 0" stroke="#1f2937" stroke-width="1.25" fill="none" stroke-linecap="round"/>' +
      '<path class="f-arc f-arc-r" d="M17.9 16q1.5-2.1 3 0" stroke="#1f2937" stroke-width="1.25" fill="none" stroke-linecap="round"/>');
    return svg.outerHTML;
  })();
  const face = (mood, cls = "") => `<span class="logo tour-face ${cls}" data-mood="${mood || "plain"}">${FACE}</span>`;

  /* He says it a letter at a time. The room for the whole line is there from the start, so nothing moves while he talks. */
  function hush() { if (talking) { const t = talking; talking = null; t(); } }
  function speak(line, text, faceEl) {
    hush();
    line.setAttribute("aria-label", text);
    line.innerHTML = '<span class="said" aria-hidden="true"></span><span class="unsaid" aria-hidden="true"></span>';
    const said = line.firstChild, unsaid = line.lastChild, chars = Array.from(text);
    const finish = () => { said.textContent = text; unsaid.textContent = ""; if (faceEl) faceEl.classList.remove("talking"); };
    if (reduced) return finish();
    unsaid.textContent = text;
    if (faceEl) faceEl.classList.add("talking");
    const t0 = performance.now(), per = Math.min(24, 1800 / chars.length);
    let live = true;
    talking = () => { live = false; finish(); };
    (function tick(now) {
      if (!live) return;
      const n = Math.floor((now - t0) / per);
      if (n >= chars.length) { talking = null; live = false; return finish(); }
      said.textContent = chars.slice(0, n).join(""); unsaid.textContent = chars.slice(n).join("");
      requestAnimationFrame(tick);
    })(t0);
  }
  const words = st => typeof st.say === "function" ? st.say() : st.say;
  const shown = el => !!el && el.offsetParent !== null;
  const lit = st => [...document.querySelectorAll(st.sel)].filter(shown);

  function waitFor(sel, ms, id) {
    return new Promise(ok => {
      const t0 = performance.now();
      (function look() {
        if (id !== run) return ok(false);
        const el = [...document.querySelectorAll(sel)].some(shown);
        if (el) return ok(true);
        if (performance.now() - t0 > ms) return ok(false);
        setTimeout(look, 100);
      })();
    });
  }

  function place() {
    if (!cur || cur.dlg || ended) return;
    const vh = innerHeight, vw = innerWidth, h = card.offsetHeight, gap = 14, floor = vh - h - 12 - (vw < 860 ? 64 : 0);
    const els = cur.sel ? lit(cur) : [];
    if (!els.length) {                                      // nothing lit: he's in the middle of the room
      spot.style.top = (vh / 2) + "px"; spot.style.left = (vw / 2) + "px"; spot.style.width = spot.style.height = "0";
      card.style.top = Math.max(12, Math.min(floor, (vh - h) / 2)) + "px";
      if (vw >= 860) card.style.left = (vw - card.offsetWidth) / 2 + "px";
      return;
    }
    const rs = els.map(e => e.getBoundingClientRect()), pad = 6;
    const top = Math.min(...rs.map(r => r.top)), bottom = Math.max(...rs.map(r => r.bottom)), left = Math.min(...rs.map(r => r.left)), right = Math.max(...rs.map(r => r.right));
    spot.style.top = (top - pad) + "px"; spot.style.left = (left - pad) + "px";
    spot.style.width = (right - left + pad * 2) + "px"; spot.style.height = (bottom - top + pad * 2) + "px";
    const below = bottom + pad + gap, above = top - pad - gap - h;
    // Below if it fits, else above; a part taller than the screen keeps him in the lower corner.
    card.style.top = Math.max(12, below <= floor ? below : above >= 12 ? above : floor) + "px";
    if (vw >= 860) card.style.left = Math.max(16, Math.min(vw - card.offsetWidth - 16, left)) + "px";
  }

  function build() {
    if (built) return;
    built = true;
    document.body.append(block, spot, card);
    addEventListener("resize", place); addEventListener("scroll", place, true); addEventListener("keydown", keys, true);
    document.addEventListener("click", guard, true);
  }

  function showCard(st) {
    build();
    const text = words(st), n = STEPS.indexOf(st) + 1;
    block.hidden = !!st.do; spot.hidden = false; card.hidden = false;
    spot.classList.toggle("do", !!st.do);
    card.className = "tour-card" + (st.sel ? "" : " center");
    card.setAttribute("aria-modal", st.do ? "false" : "true");
    const leave = st.skip ? `<button type="button" data-t="skip">${st.skip[0]}</button>` : st.last ? "" : `<button type="button" data-t="skip">Skip</button>`;
    card.innerHTML = `${face(st.mood, st.sel ? "" : "big")}<div class="tour-talk"><p class="tour-line" id="tour-t"></p>
      <div class="tour-end">${leave}${st.reply ? `<button type="button" class="go" data-t="next">${st.reply}</button>` : ""}</div></div>
      <div class="tour-prog" role="progressbar" aria-label="Tour" aria-valuemin="0" aria-valuemax="${STEPS.length}" aria-valuenow="${n}"><i style="width:${n / STEPS.length * 100}%"></i></div>`;
    speak(card.querySelector(".tour-line"), text, card.querySelector(".tour-face"));
    const next = card.querySelector('[data-t="next"]'), skip = card.querySelector('[data-t="skip"]');
    if (next) next.onclick = () => { if (talking) return hush(); st.last ? end() : go(after(st)); };
    if (skip) skip.onclick = () => { const to = st.skip ? st.skip[1] : "end"; to === "end" ? end() : go(to); };
    card.classList.remove("pop"); void card.offsetWidth; card.classList.add("pop");
    // A part that isn't fully on screen comes into view first, clear of the header and the phone's tab bar.
    const els = st.sel ? lit(st) : [];
    if (els.length) {
      const rs = els.map(e => e.getBoundingClientRect()), top = Math.min(...rs.map(r => r.top)), bottom = Math.max(...rs.map(r => r.bottom));
      if (top < 64 || bottom > innerHeight - 200) els[0].scrollIntoView({block: bottom - top > innerHeight * .5 ? "start" : "center", behavior: reduced ? "auto" : "smooth"});
    }
    place();
    setTimeout(place, 320); setTimeout(place, 620);
    (next || card).focus({preventScroll: true});
  }

  function sayInDialog(st) {
    if (!dlg || !dlg.open) return go(st.closed);
    if (built) { spot.hidden = true; card.hidden = true; block.hidden = true; }
    let strip = dlg.querySelector(".tour-say");
    if (!strip) {
      strip = document.createElement("div"); strip.className = "tour-say";
      strip.innerHTML = `${face(st.mood, "small")}<p class="tour-line"></p>`;
      dlg.prepend(strip);
    }
    const f = strip.querySelector(".tour-face");
    f.dataset.mood = st.mood || "plain";
    speak(strip.querySelector(".tour-line"), words(st), f);
    dlg.scrollTop = 0;
  }

  async function go(id) {
    if (id === "end") return end();
    const st = byId(id), mine = ++run;
    if (!st) return end();
    if (!st.dlg) put(AT, st.id);
    if (st.page && st.page !== page) { hush(); location.href = PAGES[st.page]; return; }
    if (!st.dlg) {
      const wanted = st.need || st.sel;
      if (wanted && ![...document.querySelectorAll(wanted)].some(shown)) {
        if (built) { card.hidden = true; }
        const ok = await waitFor(wanted, 2600, mine);
        if (mine !== run) return;
        if (!ok) return go(st.miss || after(st));
      }
    }
    cur = st;
    if (st.before) st.before();
    if (st.dlg) sayInDialog(st); else { const s = dlg && dlg.querySelector(".tour-say"); if (s) s.remove(); showCard(st); }
  }

  function end() {
    if (ended) return;
    ended = true; cur = null; run++;
    hush();
    put(DONE, "1"); del(AT);
    delete html.dataset.tour;
    block.remove(); spot.remove(); card.remove();
    const s = dlg && dlg.querySelector(".tour-say");
    if (s) s.remove();
    removeEventListener("resize", place); removeEventListener("scroll", place, true); removeEventListener("keydown", keys, true);
    document.removeEventListener("click", guard, true);
    window.scrollTo({top: 0, behavior: reduced ? "auto" : "smooth"});
    dispatchEvent(new Event("charles:tour-done"));
  }

  /* On a "do" step, a press anywhere else doesn't go through: he wiggles and the lit part pulses. */
  function guard(ev) {
    if (!cur || !cur.do) return;
    const t = ev.target;
    if (t.closest && (t.closest(".tour-card") || t.closest(cur.allow) || t.closest("dialog[open]"))) return;
    ev.preventDefault(); ev.stopPropagation();
    card.classList.remove("nudge", "pop"); void card.offsetWidth; card.classList.add("nudge");
  }
  function keys(e) {
    if (!cur || cur.dlg) return;                            // a dialog's own Escape closes the dialog
    if (e.key === "Escape") { e.preventDefault(); end(); }
    if (e.key === "Tab" && !cur.do) {                       // stays inside the card, unless there's something on the page to press
      const f = [...card.querySelectorAll("button")], k = f.indexOf(document.activeElement);
      e.preventDefault(); if (f.length) f[(k + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
    }
  }

  addEventListener("charles:board", ev => {
    if (!cur) return;
    const what = ev.detail;
    if (what.startsWith("failed") && cur.fail) {
      const fail = cur.fail;
      cur = {dlg: true, closed: fail};
      return sayInDialog({dlg: true, mood: "think", closed: fail, say: what === "failed-same"
        ? "Someone's already sharing exactly these settings, so there's no new spot for them. Move a slider on my Plan tab first, and the spot's yours. Close this and let's keep going."
        : "Hmm. That didn't go through, and the message below says why. Close this and we'll move on."});
    }
    const to = cur.on && cur.on[what];
    if (to) go(to);
  });
  if (dlg) dlg.addEventListener("close", () => { if (cur && cur.dlg) go(cur.closed); });

  /* By itself it starts only on a new install, while Home's Get started is there (owner 2026-11-03: it came back on every new
     network, phone or browser). A tour already under way, or one replayed from Settings (which sets AT), goes on. */
  const begin = () => {
    if (!at && !window.homeNew) { put(DONE, "1"); delete html.dataset.tour; dispatchEvent(new Event("charles:tour-done")); return; }
    go(first.id);
  };
  if (page !== "home" || window.homeReady) begin(); else addEventListener("charles:home-ready", begin, {once: true});
})();
