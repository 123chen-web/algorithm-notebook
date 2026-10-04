/* 讨论区主页效果图的演示脚本：筛选、分区、排序、搜索、J/K 选择、/ 搜索、N 发帖。
   ?state=loading 看加载骨架，?state=empty 看空状态。正式实现时拆成 static/board.js 里的纯函数。 */
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  const theme = params.get("theme");
  if (theme === "ink" || theme === "qixi") document.documentElement.dataset.theme = theme;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  $$(".avatar[data-hue]").forEach((node) => {
    node.style.background = `hsl(${Number(node.dataset.hue) || 0} 52% 34%)`;
  });

  const list = $("#forum-posts");
  const items = $$(".board-item", list);
  const status = $("#forum-list-status");
  const state = { tab: "all", zone: "", sort: "activity", query: "" };
  let selected = -1;

  function matches(item) {
    if (state.tab === "open" && item.dataset.state !== "open") return false;
    if (state.tab === "solved" && item.dataset.state !== "solved") return false;
    if (state.tab === "mine" && !item.dataset.mine) return false;
    if (state.zone && item.dataset.zone !== state.zone) return false;
    if (state.query) {
      const haystack = `${item.textContent}`.toLowerCase();
      if (!haystack.includes(state.query.toLowerCase())) return false;
    }
    return true;
  }

  function apply() {
    const sorted = [...items].sort((a, b) => {
      if (state.sort === "new") return Number(a.dataset.created) - Number(b.dataset.created);
      if (state.sort === "hot") return Number(b.dataset.hot) - Number(a.dataset.hot);
      return Number(a.dataset.activity) - Number(b.dataset.activity);
    });
    let shown = 0;
    sorted.forEach((item, index) => {
      list.append(item);
      const visible = matches(item);
      item.hidden = !visible;
      item.style.setProperty("--i", String(Math.min(index, 8)));
      if (visible) shown += 1;
    });
    $("#board-empty").hidden = shown > 0;
    $("#board-shown").textContent = String(shown);
    $("#board-count-live").textContent = `${shown} 个帖子`;
    status.textContent = shown ? `当前显示 ${shown} 个帖子。` : "没有符合条件的帖子。";
    $$(".board-row.is-selected").forEach((row) => row.classList.remove("is-selected"));
    selected = -1;
  }

  $$("#board-tabs button").forEach((button) => button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    $$("#board-tabs button").forEach((other) => other.setAttribute("aria-pressed", String(other === button)));
    apply();
  }));
  function setZone(zone) {
    state.zone = zone;
    $$("#board-zones .board-zone").forEach((chip) => chip.setAttribute("aria-pressed", String(chip.dataset.zoneFilter === zone)));
    apply();
  }
  $$("[data-zone-filter]").forEach((button) => button.addEventListener("click", () => {
    setZone(state.zone === button.dataset.zoneFilter ? "" : button.dataset.zoneFilter);
  }));
  $("#board-sort").addEventListener("change", (event) => { state.sort = event.target.value; apply(); });
  $("#forum-search").addEventListener("input", (event) => { state.query = event.target.value.trim(); apply(); });
  $("#rail-help").addEventListener("click", () => $("#board-tabs [data-tab=open]").click());
  $("#board-reset").addEventListener("click", () => {
    state.query = ""; $("#forum-search").value = "";
    $("#board-tabs [data-tab=all]").click(); setZone("");
  });

  function visibleRows() { return items.filter((item) => !item.hidden).map((item) => $(".board-row", item)); }
  function select(delta) {
    const rows = visibleRows();
    if (!rows.length) return;
    selected = Math.min(Math.max(selected + delta, 0), rows.length - 1);
    rows.forEach((row, index) => row.classList.toggle("is-selected", index === selected));
    rows[selected].scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
  addEventListener("keydown", (event) => {
    const typing = event.target.closest("input, textarea, select, [contenteditable]");
    if (event.key === "Escape" && typing) { event.target.blur(); return; }
    if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "/") { event.preventDefault(); $("#forum-search").focus(); }
    else if (event.key === "j") select(1);
    else if (event.key === "k") select(-1);
    else if (event.key === "n") $(".board-new").focus();
    else if (event.key === "Enter" && selected >= 0) { const open = $(".board-open", visibleRows()[selected]); if (open) open.click(); }
  });

  /* 状态预览 */
  if (params.get("state") === "loading") {
    items.forEach((item) => { item.hidden = true; });
    for (let i = 0; i < 4; i += 1) {
      const li = document.createElement("li");
      li.innerHTML = '<div class="board-skeleton" aria-hidden="true"><i class="sk-stat"></i><div><i class="sk-line w1"></i><i class="sk-line w2"></i><i class="sk-line w3"></i></div></div>';
      list.append(li);
    }
    status.textContent = "正在加载帖子列表…";
    $("#board-empty").hidden = true;
  } else if (params.get("state") === "empty") {
    state.query = "zzzz"; apply();
  } else {
    apply();
  }
})();
