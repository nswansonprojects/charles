/* Every page: the status in the header. */
(function () {
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
})();

/* The moon in the header: light <-> dark in one tap, remembered like Settings' choice (the same key), and Settings' buttons
   follow it. In dark it shows a sun. */
(function () {
  const btn = document.getElementById("theme-tg");
  if (!btn) return;
  const root = document.documentElement, I = d => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
        SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';
  const dark = () => root.dataset.theme === "dark" || (root.dataset.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  const show = () => { const d = dark(); btn.innerHTML = I(d ? SUN : MOON); const w = d ? "Light mode" : "Dark mode"; btn.title = w; btn.setAttribute("aria-label", w); };
  btn.onclick = () => {
    const t = dark() ? "" : "dark";
    try { if (t) localStorage.setItem("charles-theme", t); else localStorage.removeItem("charles-theme"); } catch (e) {}
    if (t) root.dataset.theme = t; else delete root.dataset.theme;
    show(); window.dispatchEvent(new Event("charles-theme"));
  };
  window.addEventListener("charles-theme", show);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", show);
  show();
})();
