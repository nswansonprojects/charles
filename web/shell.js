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
