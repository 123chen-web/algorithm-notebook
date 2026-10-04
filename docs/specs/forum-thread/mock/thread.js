/* 评论区效果图的演示脚本：头像颜色、排序/筛选、折叠、复制、楼层小地图、J/K 切换。
   正式实现时这些要变成 static/thread.js 里的纯函数 + 控制器（见 Codex 规格）。 */
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  const theme = params.get("theme");
  if (theme === "ink" || theme === "qixi") document.documentElement.dataset.theme = theme;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const list = $("#forum-comments");
  const comments = $$(".forum-comment", list);
  const status = $("#forum-comment-status");

  // 头像底色：和正式站一样由色相决定（这里直接用 data-hue）
  $$(".avatar[data-hue]").forEach((node) => {
    const hue = Number(node.dataset.hue) || 0;
    node.style.background = `hsl(${hue} 52% 34%)`;
  });

  /* ── 排序 / 筛选 ─────────────────────────────────── */
  let order = "earliest";
  const filters = new Set();
  function applyList() {
    const sorted = [...comments].sort((a, b) => {
      const fa = Number(a.dataset.floor);
      const fb = Number(b.dataset.floor);
      if (order === "latest") return fb - fa;
      if (order === "helpful") return Number(b.dataset.helpful) - Number(a.dataset.helpful) || fa - fb;
      return fa - fb;
    });
    sorted.forEach((node, index) => {
      list.append(node);
      node.style.setProperty("--i", String(Math.min(index, 8)));
      const visible = (!filters.has("op") || node.dataset.op) && (!filters.has("code") || node.dataset.code)
        && (!filters.has("me") || node.id === "forum-comment-7");
      node.hidden = !visible;
    });
    $("#thread-empty").hidden = comments.some((node) => !node.hidden);
    buildMap();
  }
  $$(".thread-seg button").forEach((button) => button.addEventListener("click", () => {
    order = button.dataset.order;
    $$(".thread-seg button").forEach((other) => other.setAttribute("aria-pressed", String(other === button)));
    status.textContent = `已按${button.textContent}排序，楼层号保持不变。`;
    applyList();
  }));
  $$(".thread-chip").forEach((chip) => chip.addEventListener("click", () => {
    const key = chip.dataset.filter;
    const on = !filters.has(key);
    if (on) filters.add(key); else filters.delete(key);
    chip.setAttribute("aria-pressed", String(on));
    status.textContent = on ? `已筛选：${chip.textContent.trim()}。` : "已取消筛选。";
    applyList();
  }));

  /* ── 折叠 / 复制 / 有用 ───────────────────────────── */
  $$("[data-more]").forEach((button) => button.addEventListener("click", () => {
    const prose = button.previousElementSibling;
    const open = prose.dataset.collapsed === "true";
    prose.dataset.collapsed = open ? "false" : "true";
    button.setAttribute("aria-expanded", String(open));
    $("span", button).textContent = open ? "收起" : "展开全文";
  }));
  $$(".thread-copy").forEach((button) => button.addEventListener("click", async () => {
    const code = $$(".l", button.closest(".thread-code")).map((line) => line.textContent).join("\n");
    try { await navigator.clipboard.writeText(code); } catch (error) { /* 演示环境可能没有剪贴板权限 */ }
    const label = $("span", button);
    label.textContent = "已复制 ✓";
    status.textContent = "代码已复制到剪贴板。";
    setTimeout(() => { label.textContent = "复制"; }, 1600);
  }));
  $$('.thread-act[aria-pressed]').forEach((button) => button.addEventListener("click", () => {
    const on = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(on));
    const n = $(".n", button);
    if (n) n.textContent = String(Math.max(0, Number(n.textContent) + (on ? 1 : -1)));
  }));

  /* ── 跳转并高亮 ─────────────────────────────────── */
  function jump(floor) {
    const target = $(`#forum-comment-${floor}`);
    if (!target) return;
    target.hidden = false;
    target.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    target.focus({ preventScroll: true });
    target.classList.remove("forum-floor-highlight");
    void target.offsetWidth;
    target.classList.add("forum-floor-highlight");
    setTimeout(() => target.classList.remove("forum-floor-highlight"), 1800);
  }
  $$("[data-jump]").forEach((button) => button.addEventListener("click", () => jump(button.dataset.jump)));

  /* ── 楼层小地图 ─────────────────────────────────── */
  const track = $("#map-track");
  const thumb = $("#map-thumb");
  let ticks = [];
  function buildMap() {
    $$(".thread-map-tick", track).forEach((tick) => tick.remove());
    const visible = comments.filter((node) => !node.hidden);
    const total = visible.length;
    ticks = visible.map((node, index) => {
      const tick = document.createElement("i");
      tick.className = "thread-map-tick";
      if (node.dataset.op) tick.classList.add("is-op");
      if (node.dataset.code) tick.classList.add("is-code");
      if (node.classList.contains("is-accepted")) tick.classList.add("is-accepted");
      tick.style.setProperty("--p", String(total > 1 ? index / (total - 1) : 0.5));
      tick.dataset.floor = node.dataset.floor;
      tick.title = `#${String(node.dataset.floor).padStart(2, "0")} ${$("strong", node).textContent}`;
      track.append(tick);
      return { tick, node };
    });
    $("#map-total").textContent = `共 ${total} 条`;
    $("#hud-total").textContent = String(total);
    update();
  }
  function update() {
    if (!ticks.length) return;
    const viewport = innerHeight;
    let current = ticks[0];
    const rects = ticks.map((entry) => entry.node.getBoundingClientRect());
    ticks.forEach((entry, i) => { if (rects[i].top <= viewport * 0.4) current = entry; });
    ticks.forEach((entry) => entry.tick.removeAttribute("aria-current"));
    current.tick.setAttribute("aria-current", "true");
    const label = `#${String(current.node.dataset.floor).padStart(2, "0")}`;
    $("#map-now").textContent = label;
    $("#hud-now").textContent = label;
    const first = rects.findIndex((rect) => rect.bottom > 0);
    let last = -1;
    rects.forEach((rect, i) => { if (rect.top < viewport) last = i; });
    const count = ticks.length;
    if (first >= 0 && last >= first) {
      thumb.style.setProperty("--t", String(count > 1 ? first / (count - 1) : 0));
      thumb.style.setProperty("--h", String(count > 1 ? Math.max((last - first) / (count - 1), 0.08) : 1));
    }
  }
  addEventListener("scroll", () => requestAnimationFrame(update), { passive: true });
  addEventListener("resize", update);
  track.addEventListener("click", (event) => {
    const rect = track.getBoundingClientRect();
    const ratio = (event.clientY - rect.top) / rect.height;
    const index = Math.round(ratio * (ticks.length - 1));
    if (ticks[index]) jump(ticks[index].node.dataset.floor);
  });
  $$("[data-map]").forEach((button) => button.addEventListener("click", () => {
    if (button.dataset.map === "top") scrollTo({ top: 0, behavior: "smooth" });
    else jump(ticks[ticks.length - 1].node.dataset.floor);
  }));
  function step(delta) {
    const visible = ticks.map((entry) => entry.node);
    const currentIndex = ticks.findIndex((entry) => entry.tick.getAttribute("aria-current") === "true");
    const next = visible[Math.min(Math.max(currentIndex + delta, 0), visible.length - 1)];
    if (next) jump(next.dataset.floor);
  }
  $$("[data-hud]").forEach((button) => button.addEventListener("click", () => step(button.dataset.hud === "next" ? 1 : -1)));
  addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select, [contenteditable]") || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "j") step(1);
    if (event.key === "k") step(-1);
  });

  /* 输入框字数计 */
  const area = $("#forum-comment-body");
  area.addEventListener("input", () => {
    $(".thread-count output").textContent = `${area.value.length} / 2000`;
    $(".thread-meter i").style.width = `${Math.max(2, area.value.length / 20)}%`;
  });

  buildMap();
})();
