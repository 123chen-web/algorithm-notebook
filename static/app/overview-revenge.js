// static/app/overview-revenge.js — 总览·复仇成功卡：本月复仇成功次数 + 列表 + 分享图。
// 对外契约：window.RevengeCard = { configure({ api, getUser, getEpoch }), mount(container), refresh() }。
// 严格 CSP：用户文本一律 textContent，不用 innerHTML。
"use strict";

(() => {
  let hooks = null;
  let container = null;
  let generation = 0;
  let expanded = false;
  let items = [];

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function monthCount(list) {
    const now = new Date();
    const prefix = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    return list.filter((item) => (item.revenge_at || "").startsWith(prefix)).length;
  }

  function modeLabel(mode) {
    return { rewrite: "默写", redo: "限时重做", recall: "只回忆" }[mode] || mode || "";
  }

  function render() {
    if (!container) return;
    container.replaceChildren();
    const count = monthCount(items);
    const card = el("section", "", "panel revenge-card");
    const head = el("button", "", "revenge-card-head");
    head.type = "button";
    head.setAttribute("aria-expanded", String(expanded));
    head.append(el("span", "本月复仇成功", "revenge-card-title"));
    head.append(el("strong", `${count} 次`, "revenge-card-count"));
    head.append(el("span", expanded ? "收起" : "展开", "revenge-card-toggle"));
    head.addEventListener("click", () => {
      expanded = !expanded;
      render();
    });
    card.append(head);
    if (expanded) {
      const list = el("ul", "", "revenge-list");
      if (!items.length) {
        list.append(el("li", "还没有复仇成功的记录。", "revenge-empty"));
      }
      for (const item of items) {
        const li = el("li", "", "revenge-item");
        const title = el("span", `《${item.problem_title}》`, "revenge-title");
        const meta = el("span", "", "revenge-meta");
        const parts = [];
        if (item.first_fail_date) parts.push(`${item.first_fail_date} 栽过`);
        if (item.verdict_label) parts.push(item.verdict_label);
        if (item.mode) parts.push(modeLabel(item.mode));
        meta.textContent = parts.join(" · ");
        const shareBtn = el("button", "分享", "revenge-share");
        shareBtn.type = "button";
        shareBtn.addEventListener("click", (event) => {
          event.stopPropagation();
          openShareDialog(item);
        });
        li.append(title, meta, shareBtn);
        list.append(li);
      }
      card.append(list);
    }
    container.append(card);
  }

  function openShareDialog(item) {
    // 分享前预览：可隐藏题名；只有点“生成分享图”才请求服务端绘制。
    const dialog = el("div", "", "revenge-dialog-backdrop");
    const panel = el("div", "", "revenge-dialog");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "分享复仇成功");
    panel.append(el("h3", "分享复仇成功", ""));
    const preview = el("p", "", "revenge-preview");
    preview.append(el("strong", "复仇成功", ""));
    preview.append(document.createTextNode(`《${item.problem_title}》`));
    panel.append(preview);
    const label = el("label", "", "revenge-hide-label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    label.append(checkbox);
    label.append(document.createTextNode("隐藏题名"));
    panel.append(label);
    const actions = el("div", "", "revenge-dialog-actions");
    const cancelBtn = el("button", "取消", "");
    cancelBtn.type = "button";
    const goBtn = el("button", "生成分享图", "");
    goBtn.type = "button";
    actions.append(cancelBtn, goBtn);
    panel.append(actions);
    const imgHost = el("div", "", "revenge-img-host");
    panel.append(imgHost);
    dialog.append(panel);
    document.body.append(dialog);
    const close = () => dialog.remove();
    cancelBtn.addEventListener("click", close);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
    checkbox.addEventListener("change", () => {
      preview.replaceChildren();
      preview.append(el("strong", "复仇成功", ""));
      preview.append(document.createTextNode(
        checkbox.checked ? "《···》" : `《${item.problem_title}》`
      ));
    });
    goBtn.addEventListener("click", () => {
      // 沿用成就分享卡的模式：img 直接 GET，session cookie 鉴权。
      const url = `/api/stats/revenge/share-card?mistake_id=${item.mistake_id}`
        + (checkbox.checked ? "&hide_title=true" : "");
      const img = document.createElement("img");
      img.alt = "复仇成功分享图";
      img.src = url;
      img.addEventListener("error", () => {
        imgHost.replaceChildren(el("p", "生成失败，请稍后再试", "revenge-error"));
      });
      imgHost.replaceChildren(img);
      const dl = el("a", "下载图片", "revenge-download");
      dl.href = url;
      dl.download = "revenge.png";
      imgHost.append(dl);
      goBtn.disabled = true;
    });
  }

  async function load() {
    const myGeneration = ++generation;
    try {
      const data = await hooks.api("/api/stats/revenge");
      if (myGeneration !== generation) return;
      items = data.items || [];
    } catch (error) {
      if (myGeneration !== generation) return;
      items = [];
    }
    render();
  }

  window.RevengeCard = {
    configure(options) {
      hooks = options;
    },
    mount(node) {
      container = node;
      load();
    },
    refresh() {
      load();
    },
    helpers: { monthCount, modeLabel },
  };
})();
