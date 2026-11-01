/* Every page: the status in the header, and the first-visit tour on Home. */
(function () {
  const page = (document.querySelector("header.shell") || {dataset: {}}).dataset.page;

  async function status() {
    const box = document.getElementById("st-bot");
    if (!box) return;
    try {
      const s = await (await fetch("/api/status", {cache: "no-store"})).json();
      const [dot, word] = s.waiting ? ["", "Not started"] : s.running == null ? ["", "…"] : !s.running ? ["bad", "Stopped"] : s.paused ? ["warn", "Paused"] : ["good", "Running"];
      box.innerHTML = `<span class="dot ${dot}"></span><span>${word}</span>`;
      box.setAttribute("aria-label", `Charles is ${word.toLowerCase()}`);
    } catch (e) {
      box.innerHTML = `<span class="dot bad"></span><span>Offline</span>`;
    }
  }
  window.refreshStatus = status;
  status();
  setInterval(() => { if (!document.hidden) status(); }, 60000);

  // A small tour of Home, once per browser (Settings can replay it). It waits for the page to fill, then lights up one
  // real part at a time. No browser storage means no tour, rather than one on every visit.
  const KEY = "charles-tour-v2";
  const STOPS = [
    ["#t-setup", "Start here", "Connect your keys, choose a plan, and add a bot if you like."],
    ["#t-earn", "It's practice money", "This is what your bots have earned or lost. Every order is simulated, so no real dollars move."],
    ["#t-run", "Charles runs itself", "It checks its plan once a day. Most days it does nothing. Pause it here any time. Pausing never sells anything."],
    ["#t-bots", "Tap a bot to look closer", "Each card opens that bot's chart and what it holds. Its Plan tab has the sliders."],
    ["#t-board", "Copy what works", "See how other people's settings do, and copy any of them in two taps."],
    ["#t-help", "Questions?", "Ask Charles explains what it's doing. The gear has your keys, updates and bots."],
  ];
  if (page !== "home") return;
  try { if (localStorage.getItem(KEY)) return; } catch (e) { return; }
  if (window.homeReady) start(); else window.addEventListener("charles:home-ready", start, {once: true});

  function start() {
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const showing = ([sel]) => { const el = document.querySelector(sel); return el && el.offsetParent !== null; };
    const stops = STOPS.filter(showing);
    if (!stops.length) return;
    const block = document.createElement("div"), spot = document.createElement("div"), card = document.createElement("div");
    block.className = "tour-block"; spot.className = "tour-spot"; card.className = "tour-card";
    card.tabIndex = -1; card.setAttribute("role", "dialog"); card.setAttribute("aria-modal", "true"); card.setAttribute("aria-labelledby", "tour-t");
    document.body.append(block, spot, card);
    let i = 0;
    const done = () => {
      try { localStorage.setItem(KEY, "1"); } catch (e) {}
      block.remove(); spot.remove(); card.remove();
      removeEventListener("resize", place); removeEventListener("scroll", place, true); removeEventListener("keydown", keys, true);
      window.scrollTo({top: 0, behavior: reduced ? "auto" : "smooth"});
      dispatchEvent(new Event("charles:tour-done"));
    };
    function place() {
      if (!stops[i] || !card.isConnected) return;              // a delayed call after the tour ended
      const el = document.querySelector(stops[i][0]);
      const r = el.getBoundingClientRect(), pad = 6, vh = innerHeight, vw = innerWidth;
      spot.style.top = (r.top - pad) + "px"; spot.style.left = (r.left - pad) + "px";
      spot.style.width = (r.width + pad * 2) + "px"; spot.style.height = (r.height + pad * 2) + "px";
      const h = card.offsetHeight, gap = 14, floor = vh - h - 12 - (vw < 860 ? 64 : 0);
      const below = r.bottom + pad + gap, above = r.top - pad - gap - h;
      // Below if it fits, else above; a part taller than the screen keeps the card in the lower corner.
      card.style.top = Math.max(12, below <= floor ? below : above >= 12 ? above : floor) + "px";
      if (vw >= 860) card.style.left = Math.max(16, Math.min(vw - card.offsetWidth - 16, r.left)) + "px";
    }
    function show() {
      const [sel, title, text] = stops[i], last = i === stops.length - 1;
      card.innerHTML = `<h2 id="tour-t">${title}</h2><p>${text}</p><div class="tour-end">
        <span class="tour-dots" role="img" aria-label="Step ${i + 1} of ${stops.length}">${stops.map((_, k) => `<i class="${k === i ? "on" : ""}"></i>`).join("")}</span>
        ${last ? "" : `<button type="button" data-t="skip">Skip</button>`}<button type="button" class="go" data-t="next">${last ? "Done" : "Next"}</button></div>`;
      card.querySelector('[data-t="next"]').onclick = () => {
        i++;
        while (stops[i] && !showing(stops[i])) stops.splice(i, 1);    // a card can hide after the tour starts (no leaderboard)
        if (i < stops.length) show(); else done();
      };
      const skip = card.querySelector('[data-t="skip"]');
      if (skip) skip.onclick = done;
      const el = document.querySelector(sel), r = el.getBoundingClientRect();
      // A part that isn't fully on screen comes into view first, clear of the header and the phone's tab bar.
      if (r.top < 64 || r.bottom > innerHeight - 200) el.scrollIntoView({block: r.height > innerHeight * .5 ? "start" : "center", behavior: reduced ? "auto" : "smooth"});
      place();
      setTimeout(place, 320); setTimeout(place, 620);
      card.querySelector('[data-t="next"]').focus({preventScroll: true});
    }
    function keys(e) {
      if (e.key === "Escape") { e.preventDefault(); done(); }
      if (e.key === "Tab") {                                   // stays inside the card while it's open
        const f = [...card.querySelectorAll("button")], k = f.indexOf(document.activeElement);
        e.preventDefault(); f[(k + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
      }
    }
    addEventListener("resize", place); addEventListener("scroll", place, true); addEventListener("keydown", keys, true);
    show();
  }
})();
