"use strict";

/* 一道题的“变强轨迹”：把复习记录画成一条小时间线（看见自己变强）。
   对外：window.GrowthTrack = { steps(reviews, today), summary(steps, currentInterval), render(item, today) → 元素或 null }。
   - 只用已有的复习记录（GET /api/mistakes/{id} 里的 reviews），不新增接口、不新增任务；
   - 每次复习一个点：颜色按那次的评分（再练 / 吃力 / 记得），旁边写“隔 N 天”（这次定的下次间隔）；
   - 顶上一句总结：从第一次的评分到最近一次、间隔从多少天拉长到多少天；
   - 用户文本一律 textContent。 */
(() => {
  const LABELS = { 0: "完全忘了", 1: "完全忘了", 2: "答错了", 3: "很吃力", 4: "记得", 5: "很熟" };
  const tone = (quality) => (quality >= 4 ? "good" : quality === 3 ? "mid" : "low");

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  /** 本地日期 YYYY-MM-DD → 当天 0 点的毫秒数（按 UTC 算，只用来求天数差）。 */
  function dayValue(text) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(text || ""));
    return match ? Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : NaN;
  }
  /** 复习时间（ISO，带时区）→ 用户本地的日期。 */
  function localDay(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  /** 复习记录按时间从早到晚排成步骤：{ quality, label, tone, day, gap }；gap 是这次定下的“隔几天再复习”。 */
  function steps(reviews) {
    return (Array.isArray(reviews) ? reviews : [])
      .filter((review) => review && review.reviewed_at && Number.isInteger(review.quality))
      .slice()
      .sort((a, b) => String(a.reviewed_at).localeCompare(String(b.reviewed_at)) || (a.id || 0) - (b.id || 0))
      .map((review) => {
        const day = localDay(review.reviewed_at);
        const gap = Math.round((dayValue(review.next_due_date) - dayValue(day)) / 86400000);
        return {
          quality: review.quality,
          label: LABELS[review.quality] || `${review.quality} 分`,
          tone: tone(review.quality),
          day,
          gap: Number.isFinite(gap) && gap > 0 ? gap : null,
        };
      });
  }

  /** 一句话总结；少于 2 次复习时不说“变强”，只说复习了几次。 */
  function summary(list) {
    if (!list.length) return "";
    if (list.length === 1) return `复习过 1 次：${list[0].label}${list[0].gap ? `，定在 ${list[0].gap} 天后再来` : ""}。`;
    const first = list[0];
    const last = list[list.length - 1];
    const gaps = list.map((step) => step.gap).filter(Boolean);
    const parts = [`复习了 ${list.length} 次`];
    if (first.label !== last.label) parts.push(`从「${first.label}」到「${last.label}」`);
    if (gaps.length >= 2 && gaps[gaps.length - 1] > gaps[0]) parts.push(`间隔从 ${gaps[0]} 天拉长到 ${gaps[gaps.length - 1]} 天`);
    else if (gaps.length) parts.push(`现在隔 ${gaps[gaps.length - 1]} 天复习一次`);
    return `${parts.join("，")}。`;
  }

  function render(item) {
    const list = steps(item?.reviews);
    if (!list.length) return null;
    const box = node("section", "growth-track");
    box.setAttribute("aria-label", "这道题的变强轨迹");
    box.append(node("h3", "growth-track-title", "变强轨迹"), node("p", "growth-track-summary", summary(list)));
    const track = node("ol", "growth-track-list");
    list.forEach((step, index) => {
      const item = node("li", `growth-step growth-${step.tone}`);
      item.append(
        node("span", "growth-dot"),
        node("span", "growth-step-label", `第 ${index + 1} 次 · ${step.label}`),
        node("span", "growth-step-meta", [step.day.slice(5).replace("-", "/"), step.gap ? `隔 ${step.gap} 天` : ""].filter(Boolean).join(" · ")),
      );
      item.setAttribute("aria-label", `第 ${index + 1} 次复习，${step.day}，${step.label}${step.gap ? `，之后隔 ${step.gap} 天` : ""}`);
      track.append(item);
    });
    box.append(track);
    // 复习次数多时一排放不下：显示出来后滚到最新那一头（最近的进步最值得看）。
    const toEnd = () => { if (track.isConnected && track.offsetParent) track.scrollLeft = track.scrollWidth; };
    if (typeof window.ResizeObserver === "function") {
      const observer = new window.ResizeObserver(() => { if (track.offsetParent) { toEnd(); observer.disconnect(); } });
      observer.observe(track);
    }
    return box;
  }

  window.GrowthTrack = Object.freeze({ steps, summary, render });
})();
