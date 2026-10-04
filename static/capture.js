/* 收录：粘贴题目链接 / 书签小工具带入的预填。
   纯前端：只解析链接文字本身，不抓取页面、不发任何请求；预填只填空着的字段，永不自动提交。 */
(() => {
  "use strict";

  const ZONE = "算法";
  const MAX_URL = 2000;
  const MAX_TITLE = 200;
  const PENDING_KEY = "oy-capture-pending";
  const PENDING_TTL = 30 * 60 * 1000;
  const LINK_PREFIX = "题目链接：";
  const TRIM_EDGE = /^[\s“”‘’"'`<>《》〈〉「」『』（）()[\]【】，。、；：！？,;!?]+|[\s“”‘’"'`<>《》〈〉「」『』（）()[\]【】，。、；：！？,;!?]+$/g;
  const TRACKING = /^(?:utm_.*|spm|from|fbclid|gclid|ref|referer|source|share_token|scm|share_.*)$/i;
  const SITE_SUFFIX = /\s*[-–—|｜·]\s*(?:力扣（LeetCode）|力扣\(LeetCode\)|力扣|LeetCode(?:\s*China)?|洛谷(?:\s*[|｜]\s*计算机科学教育新生态)?|牛客网|牛客|Codeforces|AtCoder)\s*$/i;

  const HOSTS = {
    "leetcode.cn": "leetcode", "leetcode.com": "leetcode", "leetcode-cn.com": "leetcode",
    "luogu.com.cn": "luogu",
    "nowcoder.com": "nowcoder",
    "codeforces.com": "codeforces",
    "atcoder.jp": "atcoder",
  };

  const urlCtor = () => window.URL;

  function titleCase(slug) {
    return slug.split("-").filter(Boolean).map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
  }

  function identify(source, segments) {
    const [first, second, third, fourth] = segments;
    if (source === "leetcode") {
      if (first !== "problems" || !/^[a-z0-9][a-z0-9-]*$/i.test(second || "")) return null;
      const slug = second.toLowerCase();
      return { id: slug, title: `LeetCode · ${titleCase(slug)}` };
    }
    if (source === "luogu") {
      if (first !== "problem" || !/^[A-Za-z]{1,4}\d+[A-Za-z0-9_]*$/.test(second || "")) return null;
      const id = second.toUpperCase();
      return { id, title: `洛谷 · ${id}` };
    }
    if (source === "nowcoder") {
      if (!["practice", "questionTerminal"].includes(first) || !/^[0-9A-Za-z]+$/.test(second || "")) return null;
      return { id: second, title: "牛客 · 题目" };
    }
    if (source === "codeforces") {
      let contest;
      let letter;
      if (first === "problemset" && second === "problem") [contest, letter] = [third, fourth];
      else if ((first === "contest" || first === "gym") && third === "problem") [contest, letter] = [second, fourth];
      else return null;
      if (!/^\d{1,7}$/.test(contest || "") || !/^[A-Za-z]\d?$/.test(letter || "")) return null;
      const id = `${contest}${letter.toUpperCase()}`;
      return { id, title: `Codeforces · ${id}` };
    }
    if (source === "atcoder") {
      if (first !== "contests" || !/^[\w-]+$/.test(second || "") || third !== "tasks" || !/^[\w-]+$/.test(fourth || "")) return null;
      return { id: fourth, title: `AtCoder · ${fourth}` };
    }
    return null;
  }

  function cleanUrl(url) {
    const kept = [];
    for (const [key, value] of url.searchParams) if (!TRACKING.test(key)) kept.push([key, value]);
    const query = kept.length ? `?${new URLSearchParams(kept).toString()}` : "";
    return `${url.protocol}//${url.host}${url.pathname}${query}`;
  }

  /** 解析题目链接；认不出（或不安全）返回 null。无网络。 */
  function parse(text) {
    if (typeof text !== "string" || text.length > MAX_URL * 2) return null;
    const trimmed = text.replace(TRIM_EDGE, "");
    if (!trimmed || trimmed.length > MAX_URL || /\s/.test(trimmed)) return null;
    if (!/^https?:\/\//i.test(trimmed)) return null;
    let url;
    try {
      url = new (urlCtor())(trimmed);
    } catch {
      return null;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.username || url.password || url.port) return null;
    const host = url.hostname.toLowerCase().replace(/^www\./, "");
    const source = Object.prototype.hasOwnProperty.call(HOSTS, host) ? HOSTS[host] : null;
    if (!source) return null;
    const found = identify(source, url.pathname.split("/").filter(Boolean));
    if (!found) return null;
    return { source, host, id: found.id, title: found.title, zone: ZONE, url: cleanUrl(url) };
  }

  /** 清洗书签带来的网页标题：去站点后缀、折叠空白、截到 200 字符。 */
  function cleanTitle(raw) {
    let title = String(raw ?? "").slice(0, MAX_URL).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
    for (let guard = 0; guard < 5; guard += 1) {
      const next = title.replace(SITE_SUFFIX, "").trim();
      if (next === title) break;
      title = next;
    }
    return Array.from(title).slice(0, MAX_TITLE).join("").trim();
  }

  /* ---------------- 书签小工具 ---------------- */
  function bookmarkletCode(origin) {
    if (typeof origin !== "string" || !/^https?:\/\/[A-Za-z0-9.\-[\]:]+$/.test(origin)) return "";
    const base = origin.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
    return "(function(){window.open('" + base + "/#/app?new=1&u='+encodeURIComponent(location.href)"
      + "+'&t='+encodeURIComponent(document.title),'_blank','noopener')})()";
  }
  function bookmarkletHref(origin) {
    const code = bookmarkletCode(origin);
    if (!code) return "";
    return `javascript:${code.replace(/%/g, "%25").replace(/"/g, "%22").replace(/</g, "%3C").replace(/>/g, "%3E")}`;
  }

  /* ---------------- 待处理的预填（内存 + sessionStorage） ---------------- */
  let pending = null;

  function readStorage() {
    try {
      const raw = window.sessionStorage?.getItem(PENDING_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }
  function writeStorage(record) {
    try {
      if (record) window.sessionStorage?.setItem(PENDING_KEY, JSON.stringify(record));
      else window.sessionStorage?.removeItem(PENDING_KEY);
    } catch {
      // 隐私模式或存储被禁用：只保留内存里的那份。
    }
  }
  function validRecord(record, now) {
    if (!record || typeof record.url !== "string" || typeof record.at !== "number") return null;
    if (now - record.at > PENDING_TTL || record.at > now + 60 * 1000) return null;
    const parsed = parse(record.url);
    if (!parsed) return null;
    return { parsed, title: cleanTitle(typeof record.title === "string" ? record.title : "") };
  }
  function stash(parsed, title, now = Date.now()) {
    pending = { url: parsed.url, title: title || "", at: now };
    writeStorage(pending);
  }
  function hasPending(now = Date.now()) {
    return Boolean(validRecord(pending, now) || validRecord(readStorage(), now));
  }
  /** 取出并清除待处理的预填；非法、过期或没有则返回 null。 */
  function take(now = Date.now()) {
    const record = validRecord(pending, now) || validRecord(readStorage(), now);
    pending = null;
    writeStorage(null);
    return record;
  }
  function reset() {
    pending = null;
    writeStorage(null);
  }

  /** 解析 `#/app?new=1&u=…&t=…`；任何不合规都返回 null。 */
  function parseHash(hash) {
    if (typeof hash !== "string" || !hash.startsWith("#/app?") || hash.length > MAX_URL * 4) return null;
    const params = new URLSearchParams(hash.slice(6));
    if (params.getAll("new").length !== 1 || params.get("new") !== "1") return null;
    if (params.getAll("u").length !== 1 || params.getAll("t").length > 1) return null;
    const parsed = parse(params.get("u"));
    if (!parsed) return null;
    return { parsed, title: cleanTitle(params.get("t") || "") };
  }

  /** 启动或 hashchange 时调用：收下书签带来的参数并立刻从地址栏去掉。返回是否收下了有效预填。 */
  function intake() {
    const location = window.location;
    const hash = location?.hash || "";
    if (!hash.startsWith("#/app?")) return false;
    const record = parseHash(hash);
    try {
      window.history.replaceState(null, "", `${location.pathname}${location.search}#/app`);
    } catch {
      // 地址栏清理失败不影响预填。
    }
    if (!record) return false;
    stash(record.parsed, record.title);
    return true;
  }

  /* ---------------- 预填表单 ---------------- */
  function field(form, name) {
    return form.querySelector(`[name="${name}"]`);
  }
  const isBlank = (control) => !control || !String(control.value || "").trim();

  function fireEvent(control, type) {
    control.dispatchEvent(new Event(type, { bubbles: true }));
  }

  /** 只填空着的字段。返回实际改动了哪些字段。 */
  function fillForm(form, parsed, titleOverride = "") {
    const filled = [];
    const title = field(form, "title");
    const zone = field(form, "zone");
    const thinking = field(form, "thinking");
    const name = titleOverride || parsed.title;
    if (title && isBlank(title) && name) {
      title.value = name;
      fireEvent(title, "input");
      filled.push("title");
    }
    if (zone) {
      const options = Array.from(zone.querySelectorAll("option"));
      const initial = options[0]?.value ?? "";
      const hasZone = options.some((option) => option.value === parsed.zone);
      if (hasZone && (!zone.value || zone.value === initial) && zone.value !== parsed.zone) {
        zone.value = parsed.zone;
        fireEvent(zone, "change");
        filled.push("zone");
      }
    }
    if (thinking && isBlank(thinking)) {
      thinking.value = `${LINK_PREFIX}${parsed.url}\n`;
      fireEvent(thinking, "input");
      filled.push("thinking");
    }
    return filled;
  }

  function revealAndFocus(control) {
    const fold = control.closest(".form-fold");
    const toggle = fold?.querySelector(".form-fold-toggle");
    if (toggle && toggle.getAttribute("aria-expanded") !== "true") toggle.click();
    control.focus();
    if (typeof control.setSelectionRange === "function") {
      const end = String(control.value).length;
      control.setSelectionRange(end, end);
    }
  }

  /* ---------------- 详情页：题目链接行渲染成可点击链接 ---------------- */
  const LINK_PATTERN = /https?:\/\/[^\s<>"'`]+/g;

  function safeHref(candidate) {
    let href = candidate;
    let rest = "";
    const tail = href.match(/[.,;:!?)）。，；、！？]+$/);
    if (tail) {
      rest = tail[0];
      href = href.slice(0, -rest.length);
    }
    try {
      const url = new (urlCtor())(href);
      if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password) return null;
      return { href: url.href, text: href, rest };
    } catch {
      return null;
    }
  }

  /** 思路文字：以“题目链接：”开头的行里的 http(s) 链接变成 <a>，其余一律纯文本。 */
  function renderThinking(text, className = "multiline") {
    const paragraph = document.createElement("p");
    paragraph.className = className;
    const lines = String(text ?? "").split("\n");
    lines.forEach((line, index) => {
      if (index > 0) paragraph.append("\n");
      if (!line.startsWith(LINK_PREFIX)) {
        if (line) paragraph.append(line);
        return;
      }
      let cursor = 0;
      for (const match of line.matchAll(LINK_PATTERN)) {
        const safe = safeHref(match[0]);
        if (!safe) continue;
        if (match.index > cursor) paragraph.append(line.slice(cursor, match.index));
        const link = document.createElement("a");
        link.setAttribute("href", safe.href);
        link.setAttribute("rel", "noopener noreferrer");
        link.setAttribute("target", "_blank");
        link.textContent = safe.text;
        paragraph.append(link);
        cursor = match.index + safe.text.length;
      }
      if (cursor < line.length) paragraph.append(line.slice(cursor));
    });
    return paragraph;
  }

  /* ---------------- 页面接线 ---------------- */
  function mount(root) {
    const box = root?.querySelector("#capture-box");
    const form = root?.querySelector("#problem-form");
    if (!box || !form) return null;
    const input = box.querySelector("#capture-url");
    const go = box.querySelector("#capture-go");
    const status = box.querySelector("#capture-status");
    const banner = box.querySelector("#capture-banner");
    const link = box.querySelector("#capture-bookmarklet");
    const copy = box.querySelector("#capture-copy");
    const copyStatus = box.querySelector("#capture-copy-status");
    const code = box.querySelector("#capture-code");

    function announce(text) {
      status.textContent = text;
    }
    function showBanner(text) {
      banner.textContent = text;
      banner.hidden = !text;
    }

    function apply(parsed, { title = "", fromBookmark = false } = {}) {
      fillForm(form, parsed, title);
      const thinking = field(form, "thinking");
      if (thinking) revealAndFocus(thinking);
      announce("已从链接带入题名和链接，请补充你的思路");
      showBanner(fromBookmark ? "已从书签带入，请确认后保存" : "");
    }
    function recognize() {
      const parsed = parse(input.value);
      if (!parsed) {
        showBanner("");
        announce("没认出这个链接，可以直接手填");
        return null;
      }
      apply(parsed);
      return parsed;
    }

    go.addEventListener("click", recognize);
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      recognize();
    });
    input.addEventListener("paste", () => setTimeout(recognize, 0));
    form.addEventListener("reset", () => showBanner(""));

    const origin = window.location?.origin || "";
    const href = bookmarkletHref(origin);
    const source = bookmarkletCode(origin);
    if (href) link.setAttribute("href", href);
    else box.querySelector("#capture-bookmarklet-box").hidden = true;
    code.value = source ? `javascript:${source}` : "";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      copyStatus.textContent = "请把它拖到书签栏，而不是点击";
    });
    copy.addEventListener("click", async () => {
      const text = code.value;
      if (!text) return;
      try {
        await window.navigator.clipboard.writeText(text);
        copyStatus.textContent = "书签代码已复制";
      } catch {
        code.hidden = false;
        code.focus();
        if (typeof code.select === "function") code.select();
        else if (typeof code.setSelectionRange === "function") code.setSelectionRange(0, text.length);
        copyStatus.textContent = "无法自动复制，已选中代码，请按 Ctrl+C 复制";
      }
    });

    return { apply, recognize };
  }

  let mounted = null;
  window.Capture = {
    parse, cleanTitle, bookmarkletCode, bookmarkletHref, parseHash,
    intake, hasPending, take, reset, fillForm, renderThinking,
    mount(root) {
      mounted = mount(root);
      return mounted;
    },
    /** 书签带来的预填：切到新增记录页之后调用。 */
    applyIntake(record) {
      mounted?.apply(record.parsed, { title: record.title, fromBookmark: true });
    },
  };
  window.Capture.mount(document.querySelector("#new-page"));
})();
