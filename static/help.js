"use strict";

/* 帮助页纯前端搜索：只在本机对章节文本做筛选，不发起任何网络请求，不拼接 HTML。 */

(() => {
  const input = document.getElementById("help-search");
  const emptyNote = document.getElementById("help-search-empty");
  const sections = Array.from(document.querySelectorAll(".help-section"));
  if (!input || sections.length === 0) return;

  const haystacks = sections.map((section) => {
    const keywords = section.dataset.keywords || "";
    return `${section.textContent || ""} ${keywords}`.toLowerCase();
  });

  function applyFilter() {
    const query = input.value.trim().toLowerCase();
    let visibleCount = 0;
    sections.forEach((section, index) => {
      const matched = query === "" || haystacks[index].includes(query);
      section.hidden = !matched;
      if (matched) visibleCount += 1;
    });
    emptyNote.hidden = query === "" || visibleCount > 0;
  }

  input.addEventListener("input", applyFilter);
  document.addEventListener("DOMContentLoaded", applyFilter, { once: true });
  applyFilter();
})();
