"use strict";

/* 极简的浏览器环境替身：只够把 static/ 里的小部件脚本在 Node 里跑起来，
   用来测"请求晚回来""登出再登录"这类异步行为（字符串断言测不到的东西）。
   不是 jsdom：只实现这些脚本实际用到的那一小撮 DOM 接口。 */
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC = path.join(__dirname, "..", "static");

class TextNode {
  constructor(text) {
    this.nodeType = 3;
    this.textContent = String(text);
    this.parentNode = null;
  }
}

/* ---- 选择器：tag、#id、.class、[attr]、[attr="v"]、:not(...)、:disabled，逗号并列，空格表示后代 ---- */
function parseCompound(text) {
  const parts = { tag: null, id: null, classes: [], attrs: [], not: [], pseudo: [] };
  let rest = text;
  const tag = rest.match(/^[a-zA-Z][\w-]*/);
  if (tag) {
    parts.tag = tag[0].toUpperCase();
    rest = rest.slice(tag[0].length);
  }
  while (rest) {
    let match;
    if ((match = rest.match(/^#([\w-]+)/))) parts.id = match[1];
    else if ((match = rest.match(/^\.([\w-]+)/))) parts.classes.push(match[1]);
    else if ((match = rest.match(/^\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/))) {
      parts.attrs.push([match[1], match[2] ?? match[3] ?? match[4] ?? null]);
    } else if ((match = rest.match(/^:not\(((?:[^()]|\([^()]*\))*)\)/))) parts.not.push(parseCompound(match[1]));
    else if ((match = rest.match(/^:([\w-]+)/))) parts.pseudo.push(match[1]);
    else throw new Error(`unsupported selector: ${text}`);
    rest = rest.slice(match[0].length);
  }
  return parts;
}
function matchesCompound(node, parts) {
  if (node.nodeType !== 1) return false;
  if (parts.tag && node.tagName !== parts.tag) return false;
  if (parts.id && node.id !== parts.id) return false;
  const classes = String(node.className).split(/\s+/);
  if (!parts.classes.every((name) => classes.includes(name))) return false;
  for (const [name, value] of parts.attrs) {
    const actual = name === "hidden" ? (node.hidden ? "" : null) : node.getAttribute(name);
    if (actual === null || actual === undefined) return false;
    if (value !== null && actual !== value) return false;
  }
  for (const inner of parts.not) if (matchesCompound(node, inner)) return false;
  for (const pseudo of parts.pseudo) {
    if (pseudo === "disabled" && !node.disabled) return false;
  }
  return true;
}
function matches(node, selector) {
  return selector.split(",").some((alternative) => {
    const chain = alternative.trim().split(/\s+/).map(parseCompound);
    if (!matchesCompound(node, chain[chain.length - 1])) return false;
    let current = node.parentNode;
    for (let index = chain.length - 2; index >= 0; index -= 1) {
      while (current && !matchesCompound(current, chain[index])) current = current.parentNode;
      if (!current) return false;
      current = current.parentNode;
    }
    return true;
  });
}
function walk(root, visit) {
  for (const child of root.children) {
    if (child.nodeType !== 1) continue;
    visit(child);
    walk(child, visit);
  }
}

class FakeElement {
  constructor(tag, owner) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = owner;
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.style = { setProperty() {} };
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.tabIndex = 0;
    this.id = "";
    this.title = "";
    this.type = "";
    this.value = "";
    this.offsetParent = {};
    this._text = null;
    const element = this;
    this.classList = {
      add(...names) { element.className = [...new Set([...element.className.split(/\s+/).filter(Boolean), ...names])].join(" "); },
      remove(...names) { element.className = element.className.split(/\s+/).filter((name) => name && !names.includes(name)).join(" "); },
      toggle(name, force) { (force ?? !this.contains(name)) ? this.add(name) : this.remove(name); },
      contains(name) { return element.className.split(/\s+/).includes(name); },
    };
  }

  get textContent() {
    return this._text !== null ? this._text : this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this.children = [];
    this._text = String(value);
  }
  get isConnected() {
    for (let node = this; node; node = node.parentNode) if (node === this.ownerDocument.documentElement) return true;
    return false;
  }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  get childElementCount() { return this.children.filter((child) => child.nodeType === 1).length; }
  get form() { return this.closest("form"); }

  _take(items) {
    const taken = [];
    for (const item of items) {
      const node = typeof item === "string" ? new TextNode(item) : item;
      if (node.tagName === "#FRAGMENT") taken.push(...node.children.splice(0));
      else {
        if (node.parentNode) node.parentNode.children = node.parentNode.children.filter((child) => child !== node);
        taken.push(node);
      }
    }
    for (const node of taken) node.parentNode = this;
    return taken;
  }
  append(...items) {
    this._text = null;
    const taken = this._take(items);
    this.children.push(...taken);
  }
  appendChild(node) { this.append(node); return node; }
  prepend(...items) {
    this._text = null;
    const taken = this._take(items);
    this.children.unshift(...taken);
  }
  replaceChildren(...items) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this._text = null;
    this.append(...items);
  }
  remove() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
  replaceWith(node) {
    const parent = this.parentNode;
    if (!parent) return;
    const index = parent.children.indexOf(this);
    this.remove();
    parent.children.splice(index, 0, ...parent._take([node]));
  }
  get nextElementSibling() {
    const children = this.parentNode?.children.filter((node) => node.nodeType === 1) || [];
    return children[children.indexOf(this) + 1] || null;
  }
  get previousElementSibling() {
    const children = this.parentNode?.children.filter((node) => node.nodeType === 1) || [];
    return children[children.indexOf(this) - 1] || null;
  }
  setSelectionRange(start, end, direction = "none") {
    this.selectionStart = start; this.selectionEnd = end; this.selectionDirection = direction;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = String(value);
    if (name === "id") this.id = String(value);
    if (name === "hidden") this.hidden = true;
  }
  getAttribute(name) {
    if (name.startsWith("data-")) {
      // data-* 属性和 dataset 是同一份数据（脚本里两种写法都用）。
      const key = name.slice(5).replace(/-([a-z])/g, (_all, letter) => letter.toUpperCase());
      if (key in this.dataset) return String(this.dataset[key]);
    }
    if (name === "id") return this.id || null;
    if (name === "class") return this.className || null;
    if (name === "tabindex") return String(this.tabIndex);
    return name in this.attributes ? this.attributes[name] : null;
  }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "hidden") this.hidden = false;
  }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  removeEventListener(type, listener) { this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== listener); }
  dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    for (const listener of [...(this.listeners[event.type] || [])]) listener.call(this, event);
    if (event.bubbles && this.parentNode) this.parentNode.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  click() { this.dispatchEvent(new FakeEvent("click", { bubbles: true })); }
  focus() { this.ownerDocument.activeElement = this; }
  blur() {}
  scrollIntoView() {}
  getBoundingClientRect() { return { left: 0, top: 0, right: 100, bottom: 20, width: 100, height: 20 }; }
  closest(selector) {
    for (let node = this; node && node.nodeType === 1; node = node.parentNode) if (matches(node, selector)) return node;
    return null;
  }
  contains(other) {
    for (let node = other; node; node = node.parentNode) if (node === this) return true;
    return false;
  }
  querySelectorAll(selector) {
    const found = [];
    walk(this, (node) => { if (matches(node, selector)) found.push(node); });
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
    this.bubbles = Boolean(init.bubbles);
    this.defaultPrevented = false;
    Object.assign(this, init.props || {});
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() {}
}

class FakeDialog extends FakeElement {
  constructor(owner) {
    super("dialog", owner);
    this.open = false;
    this.returnValue = "";
  }
  showModal() {
    this.open = true;
    this.setAttribute("open", "");
  }
  close(returnValue = "") {
    if (!this.open) return;
    this.open = false;
    this.returnValue = String(returnValue);
    this.removeAttribute("open");
    this.dispatchEvent(new FakeEvent("close"));
  }
  dispatchEvent(event) {
    const allowed = super.dispatchEvent(event);
    if (event.type === "cancel" && allowed) this.close();
    return allowed;
  }
}

class FakeDocument {
  constructor() {
    this.documentElement = new FakeElement("html", this);
    this.documentElement.dataset.view = "app";
    this.body = new FakeElement("body", this);
    this.documentElement.append(this.body);
    this.listeners = {};
    this.activeElement = null;
    this.hidden = false;
  }
  createElement(tag) { return String(tag).toLowerCase() === "dialog" ? new FakeDialog(this) : new FakeElement(tag, this); }
  createElementNS(_namespace, tag) { return new FakeElement(tag, this); }
  createTextNode(text) { return new TextNode(text); }
  createDocumentFragment() { return new FakeElement("#fragment", this); }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  removeEventListener(type, listener) { this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== listener); }
  dispatchEvent(event) {
    event.target = event.target || this;
    for (const listener of [...(this.listeners[event.type] || [])]) listener.call(this, event);
    return !event.defaultPrevented;
  }
  /** `#id` 找不到时现造一个挂到 body 上：脚本加载时会按 id 取页面上的固定节点。 */
  querySelector(selector) {
    const found = this.documentElement.querySelector(selector);
    if (found || !/^#[\w-]+$/.test(selector)) return found;
    const created = new FakeElement("div", this);
    created.id = selector.slice(1);
    this.body.append(created);
    return created;
  }
  querySelectorAll(selector) { return this.documentElement.querySelectorAll(selector); }
  /** 标准行为：找不到就返回 null（不像 querySelector 那样现造节点）。 */
  getElementById(id) {
    const found = this.documentElement.querySelector(`#${id}`);
    return found || null;
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** 在一个假浏览器里依次执行 static/ 下的脚本；fetch 的每次调用都记下来，由测试决定何时、怎样回应。 */
function load(files, { extra = {} } = {}) {
  const document = new FakeDocument();
  const calls = [];
  const events = [];
  const storage = new Map();
  const fetchStub = (url, init = {}) => {
    const call = { url: String(url), init, ...deferred() };
    calls.push(call);
    return call.promise;
  };
  const window = {
    document,
    fetch: fetchStub,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: (callback) => setTimeout(callback, 0),
    cancelAnimationFrame: clearTimeout,
    addEventListener() {},
    removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    localStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
    innerWidth: 1280,
    innerHeight: 800,
    ...extra,
  };
  window.window = window;
  document.addEventListener("__record__", () => {});
  const record = document.dispatchEvent.bind(document);
  document.dispatchEvent = (event) => {
    events.push({ type: event.type, detail: event.detail });
    return record(event);
  };
  const context = vm.createContext({
    window, document, fetch: fetchStub, console, setTimeout, clearTimeout, Promise,
    CustomEvent: FakeEvent, Event: FakeEvent, Intl, URL, URLSearchParams, encodeURIComponent,
    ResizeObserver: undefined,
    HTMLDialogElement: FakeDialog,
  });
  for (const file of files) vm.runInContext(fs.readFileSync(path.join(STATIC, file), "utf8"), context, { filename: file });
  const respond = (call, status, body) => call.resolve({
    ok: status >= 200 && status < 300, status, json: async () => body,
  });
  return { window, document, calls, events, respond, context };
}

module.exports = { load, tick, deferred, FakeElement, FakeEvent, FakeDialog };
