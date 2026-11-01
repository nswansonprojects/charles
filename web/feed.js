/* The feed beside a bot's page, always open (feed.py, /api/feed): what Charles did and noted, and news about what it holds, newest first. */
(function () {
  const box = document.getElementById("feed");
  if (!box) return;
  const bot = box.dataset.bot, title = box.dataset.title || "Feed";
  const e = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const money = v => (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const logo = (document.querySelector("header.shell .logo") || {}).innerHTML || "";
  const when = iso => { if (!iso) return ""; const t = new Date(iso), s = (Date.now() - t) / 1000;
    return s < 3600 ? Math.max(1, Math.round(s / 60)) + "m" : s < 86400 ? Math.round(s / 3600) + "h" : s < 604800 ? Math.round(s / 86400) + "d" : t.toLocaleDateString("en-US", {month: "short", day: "numeric"}); };
  const full = iso => iso ? new Date(iso).toLocaleString("en-US", {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "";
  const keyOf = p => p.ts + "|" + p.text;
  const known = new Set();
  const FOLD = 8;                                               // on a phone the feed is in the page's flow, so a long one folds
  let first = true, tries = 0, shown = "", unfolded = false;

  function post(p) {
    const avatar = p.who === "news" ? `<span class="av av-news" aria-hidden="true">${e(String(p.name || "N")[0].toUpperCase())}</span>`
      : p.who === "claude" ? `<span class="av av-claude" aria-hidden="true">C</span>` : `<span class="av av-charles" aria-hidden="true">${logo}</span>`;
    const tag = p.who === "news" ? p.tag : "";
    const amt = p.pnl != null ? ` <b class="${p.pnl > 0 ? "up" : p.pnl < 0 ? "down" : ""}">${p.pnl > 0 ? "+" : ""}${money(p.pnl)}</b>` : p.amount != null ? ` <b>${money(p.amount)}</b>` : "";
    const inner = `${avatar}<div class="post-b"><div class="post-top"><b>${e(p.name)}</b>${tag ? `<span class="post-tag">${e(tag)}</span>` : ""}<time title="${e(full(p.ts))}">${e(when(p.ts))}</time></div><p>${e(p.text)}${amt}</p></div>`;
    const fresh = !first && !known.has(keyOf(p));
    known.add(keyOf(p));
    const cls = `post${p.tone === "error" ? " err" : ""}${fresh ? " fresh" : ""}`;
    return p.link ? `<li><a class="${cls}" href="${e(p.link)}" target="_blank" rel="noopener noreferrer">${inner}</a></li>` : `<li><div class="${cls}">${inner}</div></li>`;
  }

  function render(d) {
    const posts = d.posts || [], sig = JSON.stringify(posts);
    if (sig === shown) return;
    shown = sig;
    const old = box.querySelector(".fd-list"), top = old ? old.scrollTop : 0;
    box.hidden = false;
    box.innerHTML = `<div class="fd-fill"><div class="card-head fd-head"><h2>${e(title)}</h2></div>
      ${posts.length ? `<ol class="fd-list${posts.length > FOLD && !unfolded ? " clip" : ""}">${posts.map(post).join("")}</ol>` : `<p class="fd-empty">Nothing yet. What Charles does and news about what it holds show up here.</p>`}
      ${posts.length > FOLD && !unfolded ? `<button class="more fd-more" type="button">Show more</button>` : ""}
      ${d.note && posts.some(p => p.who === "news") ? `<p class="fd-note">${e(d.note)}</p>` : ""}</div>`;
    if (top) box.querySelector(".fd-list").scrollTop = top;
    const more = box.querySelector(".fd-more");
    if (more) more.onclick = () => { unfolded = true; box.querySelector(".fd-list").classList.remove("clip"); more.remove(); };
    first = false;
  }

  async function load() {
    let d;
    try {
      const r = await fetch("/api/feed?bot=" + bot, {cache: "no-store"});
      if (!r.ok) return;
      d = await r.json();
    } catch (x) { return; }
    render(d);
    if (d.waiting && tries++ < 5) setTimeout(load, 4000);       // the news is on its way
  }
  load();
  setInterval(() => { if (!document.hidden) { tries = 0; load(); } }, 300000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
})();
