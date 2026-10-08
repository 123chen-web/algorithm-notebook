"use strict";

/* 掌握度热力图（GROWTH F3）：标签 × 周的热力图 + Dashboard"下周主攻"卡片。
   对外契约：window.Heatmap = { configure, reset, loadHeatmap(el, weeks), mountFocusCard(el), selectTag(tag) }。
   数据来自 GET /api/mastery/heatmap?weeks=8 与 GET /api/mastery/focus。
   点格子跳该标签的错题筛选：派发已有的 "records:filter" 自定义事件，
   app.js 里现成的监听会切到"全部记录"并把 #tag-filter 设为该标签，
   这里不需要改 app.js。所有用户文本一律 textContent。 */
(() => {
  const WEEKS = 8;
  let generation = 0;
  const requests = new WeakMap();
  let hooks = null;
  const ticket = () => ({ generation, epoch: hooks?.getEpoch?.(), userId: hooks?.getUser?.()?.id });
  const alive = (t) => t.generation === generation && t.epoch === hooks?.getEpoch?.() && t.userId === hooks?.getUser?.()?.id;
  function configure(options) { hooks = options; }


  const $ = (selector, root = document) => root.querySelector(selector);

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function percent(score) {
    return `${Math.round(score * 100)}%`;
  }

  function shortDate(iso) {
    const [, month, day] = iso.split("-").map(Number);
    return `${month}/${day}`;
  }

  function quintile(score) {
    if (score < 0.2) return 0;
    if (score < 0.4) return 1;
    if (score < 0.6) return 2;
    if (score < 0.8) return 3;
    return 4;
  }

  function errorBox(message, retry) {
    const box = node("div", "hm-error");
    box.append(node("p", null, message));
    const button = node("button", "hm-retry", "重试");
    button.type = "button";
    button.addEventListener("click", retry);
    box.append(button);
    return box;
  }

  async function fetchJson(url) { return hooks.api(url); }
  function reset() { generation += 1; }
  function selectTag(tag) {
    // app.js 已有监听：切到"全部记录"视图并按标签筛选。
    document.dispatchEvent(new CustomEvent("records:filter", { detail: { tag } }));
  }

  async function loadHeatmap(el, weeks = WEEKS) {
    const t = ticket();
    const seq = (requests.get(el) || 0) + 1;
    requests.set(el, seq);
    const current = () => alive(t) && requests.get(el) === seq;
    el.replaceChildren();
    el.setAttribute("aria-busy", "true");
    let data;
    try {
      data = await fetchJson(`/api/mastery/heatmap?weeks=${encodeURIComponent(weeks)}`);
    } catch (error) {
      if (!current()) return;
      el.setAttribute("aria-busy", "false");
      el.append(errorBox("热力图加载失败，请检查网络后重试。", () => loadHeatmap(el, weeks)));
      return;
    }
    if (!current()) return;
    el.setAttribute("aria-busy", "false");

    if (!data.tags.length) {
      const empty = node("div", "hm-empty");
      empty.append(node("p", null, "还没有带标签的错题。给错题打上标签并复习几次，这里就会出现掌握度热力图。"));
      el.append(empty);
      return;
    }

    const table = node("table", "hm-grid");
    const caption = node("caption", "hm-caption", "标签 × 周掌握度（越深掌握越好）");
    table.append(caption);
    const head = node("thead");
    const headRow = node("tr");
    headRow.append(node("th", "hm-rowhead", "标签 \\ 周"));
    for (const week of data.weeks) {
      const th = node("th", "hm-colhead", shortDate(week));
      th.title = `从 ${week} 开始的一周`;
      headRow.append(th);
    }
    head.append(headRow);
    table.append(head);

    const body = node("tbody");
    for (const tag of data.tags) {
      const row = node("tr");
      const label = node("th", "hm-rowhead");
      const labelButton = node("button", "hm-taglink", tag);
      labelButton.type = "button";
      labelButton.title = `查看「${tag}」的错题`;
      labelButton.addEventListener("click", () => selectTag(tag));
      label.append(labelButton);
      row.append(label);
      data.cells[tag].forEach((score, index) => {
        const cell = node("td", "hm-cell");
        if (score === null || score === undefined) {
          cell.append(node("span", "hm-nodata", "—"));
          cell.title = `${tag} · ${shortDate(data.weeks[index])}：无复习数据`;
        } else {
          const button = node("button", `hm-cellbtn hm-q${quintile(score)}`, percent(score));
          button.type = "button";
          button.title = `${tag} · ${shortDate(data.weeks[index])}：${percent(score)}，点击查看该标签错题`;
          button.setAttribute("aria-label", button.title);
          button.addEventListener("click", () => selectTag(tag));
          cell.append(button);
        }
        row.append(cell);
      });
      body.append(row);
    }
    table.append(body);
    el.append(table);
  }

  async function mountFocusCard(el) {
    const t = ticket();
    const seq = (requests.get(el) || 0) + 1;
    requests.set(el, seq);
    const current = () => alive(t) && requests.get(el) === seq;
    el.replaceChildren();
    el.setAttribute("aria-busy", "true");
    let data;
    try {
      data = await fetchJson("/api/mastery/focus");
    } catch (error) {
      if (!current()) return;
      el.setAttribute("aria-busy", "false");
      el.append(errorBox("下周主攻加载失败，请检查网络后重试。", () => mountFocusCard(el)));
      return;
    }
    if (!current()) return;
    el.setAttribute("aria-busy", "false");

    const card = node("div", "hm-focus");
    if (!data.tag) {
      card.append(node("h3", "hm-focus-title", "下周主攻"));
      card.append(node("p", "hm-focus-empty", data.reason || "暂无推荐"));
      el.append(card);
      return;
    }
    card.append(node("h3", "hm-focus-title", "下周主攻"));
    const tagRow = node("div", "hm-focus-tagrow");
    tagRow.append(node("span", "hm-focus-tag", data.tag));
    tagRow.append(node("span", "hm-focus-score", percent(data.score)));
    card.append(tagRow);
    card.append(node("p", "hm-focus-reason", data.reason));
    const actions = node("div", "hm-focus-actions");
    const button = node("button", "hm-focus-go", `查看这 ${data.plan.length} 道错题 →`);
    button.type = "button";
    button.addEventListener("click", () => selectTag(data.tag));
    actions.append(button);
    card.append(actions);
    el.append(card);
  }

  window.Heatmap = { configure, reset, loadHeatmap, mountFocusCard, selectTag };
})();
