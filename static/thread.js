"use strict";

/* 帖子详情的正文与数据模型。DOM 只由节点 API 创建；异步控制器把当前
   帖子、会话和用户一起捕获，避免迟到的回复进入下一位用户的页面。 */
(() => {
  const normalize = (value) => String(value ?? "").replace(/\r\n?/g, "\n");
  const normalizedName = (value) => String(value ?? "").normalize("NFKC").toLocaleLowerCase();
  const floorNumber = (comment) => Number(comment?.floor) || 0;
  const countHelpful = (comment) => Math.max(0, Number(comment?.helpful_count) || 0);
  const node = (tag, className, text) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = String(text);
    return item;
  };

  function actionIcon(name) {
    const paths = {
      reply: ["M9 10 4 6l5-4", "M4 6h7a5 5 0 0 1 5 5v3"],
      up: ["m4 9 5-5 5 5", "M9 4v11"],
      edit: ["m11 3 4 4", "m3 12 9-9 3 3-9 9-4 1z"],
      delete: ["M3 5h12", "M7 2h4l1 3", "m5 5 1 11h6l1-11", "M8 8v5M10 8v5"],
      flag: ["M4 16V2", "M4 3h10l-3 4 3 4H4"],
      check: ["m3 9 4 4 8-9"],
      down: ["m4 6 5 5 5-5"],
      copy: ["M6 5V2h9v10h-3", "M3 5h9v11H3z"],
    };
    if (!paths[name]) return null;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 18 18");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    for (const data of paths[name]) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", data);
      svg.append(path);
    }
    return svg;
  }

  // 围栏的识别在渲染、统计和节选中共用，避免三个位置得出不同结果。
  function blocks(text) {
    const lines = normalize(text).split("\n");
    const result = [];
    let plain = [];
    function flush() {
      if (plain.length) result.push({ type: "text", lines: plain });
      plain = [];
    }
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (!line.startsWith("```")) {
        plain.push(line);
        continue;
      }
      flush();
      const label = line.slice(3).trim();
      const language = /^[a-zA-Z0-9+#.\-_]{1,12}$/.test(label) ? label.toUpperCase() : "CODE";
      const code = [];
      index += 1;
      while (index < lines.length && !/^```[ \t]*$/.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      result.push({ type: "code", language, text: code.join("\n") });
    }
    flush();
    return result;
  }

  function appendMentions(parent, text, mentionMe) {
    const pattern = /@([\w\u4e00-\u9fff-]{1,32})/g;
    let cursor = 0;
    for (const match of text.matchAll(pattern)) {
      if (match.index > cursor) parent.append(text.slice(cursor, match.index));
      const mention = node("span", "thread-mention", match[0]);
      if (mentionMe && normalizedName(match[1]) === normalizedName(mentionMe)) mention.classList.add("is-me");
      parent.append(mention);
      cursor = match.index + match[0].length;
    }
    if (cursor < text.length) parent.append(text.slice(cursor));
  }

  function appendInline(parent, text, mentionMe) {
    // 一次收集单个反引号，再成对处理；未配对和连续反引号都作为文字。
    const ticks = [];
    for (const match of text.matchAll(/`+/g)) if (match[0].length === 1) ticks.push(match.index);
    let cursor = 0;
    for (let index = 0; index + 1 < ticks.length; index += 2) {
      const start = ticks[index];
      const end = ticks[index + 1];
      appendMentions(parent, text.slice(cursor, start), mentionMe);
      parent.append(node("code", "", text.slice(start + 1, end)));
      cursor = end + 1;
    }
    appendMentions(parent, text.slice(cursor), mentionMe);
  }

  function listLine(line) {
    let match = line.match(/^(\d+)(?:\.[ \t]+|\)[ \t]+|、[ \t]*)(.*)$/);
    if (match) return { type: "ol", start: Number(match[1]), text: match[2] };
    match = line.match(/^(?:[-*•][ \t]+)(.*)$/);
    return match ? { type: "ul", text: match[1] } : null;
  }

  function renderBody(text, { mentionMe = "", onCopyStatus = () => {} } = {}) {
    const fragment = document.createDocumentFragment();
    if (!String(text ?? "").length) return fragment;
    for (const block of blocks(text)) {
      if (block.type === "code") {
        const figure = node("figure", "thread-code");
        const caption = node("figcaption");
        const copy = node("button", "thread-copy");
        copy.append(actionIcon("copy"), node("span", "thread-action-label", "复制"));
        copy.type = "button";
        copy.setAttribute("aria-label", "复制代码");
        copy.addEventListener("click", () => { void copyCode(block.text, copy, onCopyStatus); });
        caption.append(node("span", "thread-code-language", block.language), copy);
        const pre = node("pre");
        const code = node("code");
        const lines = block.text.split("\n");
        lines.forEach((line) => {
          code.append(node("span", "l", line));
        });
        pre.append(code);
        figure.append(caption, pre);
        fragment.append(figure);
        continue;
      }
      let paragraph = null;
      let list = null;
      let listType = "";
      for (const line of block.lines) {
        const item = listLine(line);
        if (item) {
          paragraph = null;
          if (!list || listType !== item.type) {
            list = node(item.type);
            listType = item.type;
            if (item.type === "ol" && item.start !== 1) list.setAttribute("start", String(item.start));
            fragment.append(list);
          }
          const li = node("li");
          appendInline(li, item.text, mentionMe);
          list.append(li);
        } else {
          list = null;
          listType = "";
          if (!paragraph) {
            paragraph = node("p");
            fragment.append(paragraph);
          } else paragraph.append("\n");
          appendInline(paragraph, line, mentionMe);
        }
      }
    }
    return fragment;
  }

  function countCode(text) { return blocks(text).filter((block) => block.type === "code").length; }
  function hasCode(text) { return countCode(text) > 0; }

  function mentionsMe(comment, comments, me) {
    if (!me) return false;
    const reply = (comments || []).find((item) => item.id === comment.reply_to_id);
    if (reply && me.id !== undefined && me.id !== null && reply.user_id === me.id) return true;
    const mine = normalizedName(me.username);
    if (!mine) return false;
    const body = normalizedName(comment.body);
    for (const match of body.matchAll(/@([\w\u4e00-\u9fff-]{1,32})/g)) if (match[1] === mine) return true;
    return false;
  }

  function arrange(comments, { order = "earliest", onlyOp = false, codeOnly = false, mentionOnly = false, me = null } = {}) {
    const source = Array.isArray(comments) ? comments : [];
    return source.filter((comment) => (!onlyOp || comment.is_op)
      && (!codeOnly || hasCode(comment.body)) && (!mentionOnly || mentionsMe(comment, source, me)))
      .slice().sort((a, b) => {
        if (order === "latest") return floorNumber(b) - floorNumber(a);
        if (order === "helpful") return countHelpful(b) - countHelpful(a) || floorNumber(a) - floorNumber(b);
        return floorNumber(a) - floorNumber(b);
      });
  }

  function stats(post) {
    const comments = Array.isArray(post?.comments) ? post.comments : [];
    const authors = new Set();
    if (post?.user_id !== undefined && post.user_id !== null) authors.add(post.user_id);
    for (const comment of comments) if (comment.user_id !== undefined && comment.user_id !== null) authors.add(comment.user_id);
    let lastActivity = post?.created_at || "";
    let latest = -Infinity;
    for (const comment of comments) {
      const date = Date.parse(comment.created_at);
      if (Number.isFinite(date) && date > latest) {
        latest = date;
        lastActivity = comment.created_at;
      }
    }
    return { replies: comments.length, participants: authors.size,
      codeBlocks: countCode(post?.body) + comments.reduce((count, comment) => count + countCode(comment.body), 0), lastActivity };
  }

  function graphemes(text) {
    if (typeof Intl.Segmenter === "function") {
      return Array.from(new Intl.Segmenter("zh", { granularity: "grapheme" }).segment(text), (item) => item.segment);
    }
    // 老浏览器保留组合符、肤色、国旗与 ZWJ 表情序列。
    const result = [];
    let joinNext = false;
    let regional = false;
    for (const character of text) {
      const point = character.codePointAt(0);
      const isRegional = point >= 0x1f1e6 && point <= 0x1f1ff;
      const attached = /\p{Mark}/u.test(character) || point === 0xfe0f || point === 0xfe0e
        || (point >= 0x1f3fb && point <= 0x1f3ff) || point === 0x200d || joinNext || (isRegional && regional);
      if (attached && result.length) result[result.length - 1] += character;
      else result.push(character);
      joinNext = point === 0x200d;
      regional = isRegional && !regional;
    }
    return result;
  }

  function excerpt(text, max = 160) {
    const value = blocks(text).map((block) => block.type === "code" ? "〈代码〉" : block.lines.join("\n"))
      .join(" ").replace(/\s+/g, " ").trim();
    const limit = Math.max(0, Math.floor(Number(max) || 0));
    const parts = graphemes(value);
    if (parts.length <= limit) return value;
    if (!limit) return "";
    let result = parts.slice(0, Math.max(0, limit - 1)).join("");
    // 摘要截在行内代码里时补齐结束符，预览仍然保持等宽。
    const ticks = Array.from(result.matchAll(/`+/g)).filter((match) => match[0].length === 1).length;
    if (ticks % 2) result += "`";
    return `${result}…`;
  }

  function mapModel(items, rects = [], viewportHeight = 0) {
    const source = Array.isArray(items) ? items : [];
    const total = source.length;
    const viewport = Math.max(0, Number(viewportHeight) || 0);
    let currentIndex = total ? 0 : -1;
    let firstIndex = -1;
    let lastIndex = -1;
    for (let index = 0; index < total; index += 1) {
      const rect = rects[index];
      if (!rect) continue;
      if (rect.top <= viewport * .4) currentIndex = index;
      if (rect.bottom > 0 && rect.top < viewport) {
        if (firstIndex < 0) firstIndex = index;
        lastIndex = index;
      }
    }
    let top = 0;
    let height = 1;
    if (total > 1 && firstIndex >= 0 && lastIndex >= firstIndex) {
      height = Math.min(1, Math.max((lastIndex - firstIndex) / (total - 1), .08));
      top = Math.min(firstIndex / (total - 1), 1 - height);
    } else if (total > 1) {
      height = .08;
      top = Math.min(Math.max(currentIndex, 0) / (total - 1), 1 - height);
    }
    return { hidden: total < 3, total,
      ticks: source.map((item, index) => ({ id: item.id, floor: floorNumber(item),
        position: total > 1 ? index / (total - 1) : .5, isOp: Boolean(item.is_op),
        hasCode: hasCode(item.body), isAccepted: Boolean(item.is_accepted || item.isAccepted) })),
      currentIndex, currentFloor: source[currentIndex]?.floor ?? null, firstIndex, lastIndex, thumb: { top, height } };
  }

  function keyboardAllowed(event, { detailVisible = false, dialogOpen = false } = {}) {
    if (!detailVisible || dialogOpen || event?.ctrlKey || event?.metaKey || event?.altKey || event?.defaultPrevented || event?.isComposing) return false;
    const target = event?.target;
    return !(target?.isContentEditable || target?.closest?.("input, textarea, select, [contenteditable]"));
  }

  async function copyCode(text, button, announce = () => {}) {
    if (button?.threadCopyPending) return false;
    if (button) button.threadCopyPending = true;
    let copied = false;
    try {
      const clipboard = window.navigator?.clipboard;
      if (!clipboard?.writeText) throw new Error("clipboard unavailable");
      await clipboard.writeText(String(text ?? ""));
      copied = true;
    } catch (_error) {
      let temporary = null;
      const selection = window.getSelection?.();
      const previousRanges = [];
      if (selection) for (let index = 0; index < selection.rangeCount; index += 1) previousRanges.push(selection.getRangeAt(index));
      try {
        const code = button?.closest?.(".thread-code")?.querySelector("pre code");
        if (code && selection && document.createRange) {
          const range = document.createRange();
          range.selectNodeContents(code);
          selection.removeAllRanges();
          selection.addRange(range);
        } else {
          temporary = node("textarea", "forum-sr-only");
          temporary.value = String(text ?? "");
          temporary.setAttribute("readonly", "");
          temporary.setAttribute("aria-hidden", "true");
          temporary.tabIndex = -1;
          document.body.append(temporary);
          temporary.select();
        }
        copied = Boolean(document.execCommand?.("copy"));
      } catch (_fallbackError) {
        copied = false;
      } finally {
        temporary?.remove();
        if (selection && previousRanges.length) {
          selection.removeAllRanges();
          previousRanges.forEach((range) => selection.addRange(range));
        }
      }
    }
    if (button) button.threadCopyPending = false;
    if (!copied) {
      announce("复制失败，请手动选择");
      return false;
    }
    announce("代码已复制到剪贴板。");
    if (button) {
      window.clearTimeout(button.threadCopyTimer);
      const label = button.querySelector?.(".thread-action-label") || button;
      label.textContent = "已复制 ✓";
      button.threadCopyTimer = window.setTimeout(() => { label.textContent = "复制"; }, 1600);
    }
    return true;
  }

  function errorDetail(error) {
    if (typeof error?.detail === "string") return error.detail;
    if (typeof error?.message === "string") return error.message;
    return String(error ?? "操作失败，请重试。");
  }

  function sanitizeSummary(summary, post) {
    if (!summary || typeof summary !== "object") return null;
    const floors = new Set((post.comments || []).map((comment) => comment.floor));
    return {
      tldr: typeof summary.tldr === "string" ? summary.tldr : "",
      points: (Array.isArray(summary.points) ? summary.points : []).filter((point) => point && typeof point.text === "string")
        .map((point) => ({ text: point.text, floors: [...new Set((Array.isArray(point.floors) ? point.floors : [])
          .filter((floor) => Number.isInteger(floor) && floors.has(floor)))] })),
      open_questions: (Array.isArray(summary.open_questions) ? summary.open_questions : []).filter((question) => typeof question === "string"),
      generated_at: typeof summary.generated_at === "string" ? summary.generated_at : "",
      comment_count: Number.isInteger(summary.comment_count) && summary.comment_count >= 0 ? summary.comment_count : (post.comments || []).length,
      stale: Boolean(summary.stale),
    };
  }

  function createSummaryController({ api, getIdentity, onState = () => {} }) {
    let sequence = 0;
    let pending = null;
    let owner = null;
    let state = { phase: "idle", summary: null, error: "", count: 0, cached: false };
    const identity = () => ({ ...(getIdentity() || {}) });
    const sameIdentity = (a, b) => a.postId === b.postId && a.sessionEpoch === b.sessionEpoch && a.userId === b.userId
      && a.visible === true && b.visible === true;
    const publish = (next) => { state = next; onState({ ...state }); };
    async function request(post, generating) {
      const captured = identity();
      if (!post || captured.postId !== post.id || !captured.visible) return false;
      if (generating && pending?.generating && sameIdentity(pending.identity, captured)) return false;
      const token = ++sequence;
      pending = { token, identity: captured, generating };
      const isCurrent = () => token === sequence && sameIdentity(captured, identity());
      const summary = owner && sameIdentity(owner, captured) ? state.summary : null;
      owner = captured;
      publish({ phase: generating ? "generating" : "loading", summary, error: "", count: (post.comments || []).length, cached: false });
      try {
        const data = await api(`/api/posts/${encodeURIComponent(post.id)}/summary`, { method: generating ? "POST" : "GET" });
        if (!isCurrent()) return false;
        const summary = sanitizeSummary(data?.summary, post);
        publish({ phase: summary ? "result" : "idle", summary, error: "", count: (post.comments || []).length, cached: Boolean(data?.cached) });
        return true;
      } catch (error) {
        if (!isCurrent()) return false;
        publish({ phase: "error", summary: state.summary, error: errorDetail(error), count: (post.comments || []).length, cached: false });
        return false;
      } finally {
        if (pending?.token === token) pending = null;
      }
    }
    return {
      load: (post) => request(post, false), generate: (post) => request(post, true),
      reset() { sequence += 1; pending = null; owner = null; publish({ phase: "idle", summary: null, error: "", count: 0, cached: false }); },
      getState: () => ({ ...state }),
    };
  }

  async function performMutation({ button, request, onSuccess = () => {}, onError = () => {}, isCurrent = () => true }) {
    if (button?.threadMutationPending) return false;
    const wasDisabled = Boolean(button?.disabled);
    const wasBlocked = button?.dataset?.blocked;
    if (button) {
      button.threadMutationPending = true;
      button.disabled = true;
      if (button.dataset) button.dataset.blocked = "1";
      button.setAttribute("aria-busy", "true");
    }
    try {
      const result = await request();
      if (!isCurrent()) return false;
      onSuccess(result);
      return true;
    } catch (error) {
      if (isCurrent()) onError(errorDetail(error));
      return false;
    } finally {
      if (button) {
        button.threadMutationPending = false;
        button.disabled = wasDisabled;
        if (button.dataset) {
          if (wasBlocked === undefined) delete button.dataset.blocked;
          else button.dataset.blocked = wasBlocked;
        }
        button.removeAttribute("aria-busy");
      }
    }
  }

  window.Thread = Object.freeze({ renderBody, actionIcon, arrange, hasCode, countCode, stats, excerpt,
    mentionsMe, mapModel, keyboardAllowed, copyCode, createSummaryController, performMutation });
})();
