"use strict";

/* 笔记思维导图（static/notes-map.js）行为测试：Node 内置测试运行器 + 假浏览器。
   覆盖：大纲解析（标题层级、列表缩进、提示框、代码块/表格跳过、没有标题时取每段第一句、节点上限）、
   文字宽度与截断、布局几何（不重叠、父在子正中、收起后不再占位）、关联枝、默认收起阈值、
   以及 build 的交互（点击收起展开、↗ 跳转、关联点击、键盘、文字不被当成 HTML）。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

// 假浏览器是另一个 JS 运行环境，里面造出来的数组和这里的数组“不是同一种”，按值比较。
const plain = (value) => JSON.parse(JSON.stringify(value));
const deepEqual = (actual, expected, message) => assert.deepEqual(plain(actual), plain(expected), message);

function env() {
  const loaded = load(["notes-map.js"]);
  assert.ok(loaded.window.NotesMap, "window.NotesMap 应当导出");
  return loaded;
}

const flat = (node, out = []) => {
  out.push(node);
  for (const child of node.children) flat(child, out);
  return out;
};

const NOTE = [
  "# 二分查找",
  "一句话总述，不进导图。",
  "## 触发信号",
  "- 有序数组",
  "- 要求 O(log n)",
  "  - 看到“最小的满足条件的”",
  "## 常见坑",
  "1. 死循环：`mid` 取法",
  "2. **边界**：左闭右闭",
  "> [!易错] left == right 时漏判",
  "```python",
  "# 这行不能进导图",
  "- 代码里的列表也不能",
  "```",
  "| 表 | 头 |",
  "| -- | -- |",
].join("\n");

test("parseOutline: 标题层级、列表缩进、提示框；代码块和表格被跳过", () => {
  const { window } = env();
  const root = window.NotesMap.parseOutline(NOTE, "二分查找笔记");
  assert.equal(root.text, "二分查找笔记");
  const [h1] = root.children;
  assert.equal(h1.text, "二分查找");
  assert.equal(h1.kind, "heading");
  const [signals, pitfalls] = h1.children;
  assert.equal(signals.text, "触发信号");
  deepEqual(signals.children.map((c) => c.text), ["有序数组", "要求 O(log n)"]);
  assert.equal(signals.children[1].children[0].text, "看到“最小的满足条件的”");
  deepEqual(pitfalls.children.map((c) => c.text), ["死循环：mid 取法", "边界：左闭右闭", "易错：left == right 时漏判"]);
  assert.equal(pitfalls.children[2].kind, "callout");
  const texts = flat(root).map((n) => n.text).join("|");
  assert.doesNotMatch(texts, /这行不能进导图|代码里的列表|表/);
});

test("parseOutline: 标题记下它在正文里是第几个标题，方便跳转", () => {
  const { window } = env();
  const root = window.NotesMap.parseOutline("# A\n## B\n```\n# 代码里的井号\n```\n## C", "t");
  const headings = flat(root).filter((n) => n.kind === "heading");
  deepEqual(headings.map((n) => [n.text, n.headingIndex]), [["A", 0], ["B", 1], ["C", 2]]);
});

test("parseOutline: 跳级的标题仍挂在最近的上一级下面", () => {
  const { window } = env();
  const root = window.NotesMap.parseOutline("# A\n### 深\n## B", "t");
  const [a] = root.children;
  deepEqual(a.children.map((n) => n.text), ["深", "B"]);
});

test("parseOutline: 没有标题也没有列表时，取每段的第一句", () => {
  const { window } = env();
  const root = window.NotesMap.parseOutline("先画图再想。后面还有很多话。\n\n第二段只有一句", "t");
  deepEqual(root.children.map((n) => n.text), ["先画图再想", "第二段只有一句"]);
  assert.ok(root.children.every((n) => n.kind === "para"));
});

test("parseOutline: 空笔记没有子节点；Markdown 记号被清掉", () => {
  const { window } = env();
  assert.equal(window.NotesMap.parseOutline("", "空").children.length, 0);
  assert.equal(window.NotesMap.cleanInline("**粗** `码` [链接](http://x) [[别的笔记|别名]] ==重点== $x^2$"), "粗 码 链接 别名 重点 x^2");
});

test("parseOutline: 节点数有上限，超出时标记 truncated", () => {
  const { window } = env();
  const many = Array.from({ length: 400 }, (_, i) => `- 条目 ${i}`).join("\n");
  const root = window.NotesMap.parseOutline(many, "多");
  assert.equal(flat(root).length - 1, window.NotesMap.MAX_NODES);
  assert.equal(root.truncated, true);
});

test("parseOutline: 列表嵌套太深时不会再往下长，也不会出现环", () => {
  const { window } = env();
  const deep = Array.from({ length: 14 }, (_, i) => `${"  ".repeat(i)}- 层${i}`).join("\n");
  const root = window.NotesMap.parseOutline(deep, "深");
  assert.doesNotThrow(() => JSON.stringify(root));
  const maxDepth = Math.max(...flat(root).map((n) => n.depth));
  assert.ok(maxDepth <= 8, `depth ${maxDepth}`);
});

test("fitLabel / textWidth: 中文按一个字宽，英文窄一些，太长加省略号", () => {
  const { window } = env();
  const M = window.NotesMap;
  assert.equal(M.textWidth("中文", 10), 20);
  assert.ok(M.textWidth("ab", 10) < 20);
  assert.equal(M.fitLabel("短", 100), "短");
  const cut = M.fitLabel("很长很长很长很长很长很长很长很长很长很长", 100, 13);
  assert.ok(cut.endsWith("…"));
  assert.ok(M.textWidth(cut, 13) <= 100 + 13);
});

test("layout: 节点不重叠；同一列对齐；父节点在子节点正中", () => {
  const { window } = env();
  const root = window.NotesMap.parseOutline(NOTE, "二分查找笔记");
  const { nodes, edges, width, height } = window.NotesMap.layout(root);
  assert.equal(nodes.length, flat(root).length);
  assert.equal(edges.length, nodes.length - 1);
  for (const a of nodes) {
    assert.ok(a.x >= 0 && a.y >= 0 && a.x + a.w <= width && a.y + a.h <= height, `${a.text} 在画布内`);
    for (const b of nodes) {
      if (a === b) continue;
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
      assert.ok(apart, `${a.text} 与 ${b.text} 不重叠`);
    }
  }
  for (const node of nodes) {
    if (!node.visible.length) continue;
    const first = node.visible[0];
    const last = node.visible[node.visible.length - 1];
    assert.equal(node.cy, (first.cy + last.cy) / 2);
  }
  assert.ok(edges.every((e) => e.x2 > e.x1), "连线都是从左指向右");
});

test("layout: 收起的节点不画子孙，画布随之变小", () => {
  const { window } = env();
  const root = window.NotesMap.parseOutline(NOTE, "t");
  const open = window.NotesMap.layout(root);
  const folded = window.NotesMap.layout(root, new Set(["0.0"]));
  assert.ok(folded.nodes.length < open.nodes.length);
  deepEqual(folded.nodes.map((n) => n.id), ["0", "0.0"]);
  assert.ok(folded.height < open.height);
});

test("layout: 一级分支轮流用不同颜色，子孙跟着所在分支", () => {
  const { window } = env();
  const root = window.NotesMap.parseOutline("# A\n- a1\n# B\n- b1", "t");
  const { nodes } = window.NotesMap.layout(root);
  const byText = Object.fromEntries(nodes.map((n) => [n.text, n.branch]));
  assert.equal(byText.A, 0);
  assert.equal(byText.a1, 0);
  assert.equal(byText.B, 1);
  assert.equal(byText.b1, 1);
  assert.equal(byText["t"], -1);
});

test("withConnections: 加一枝“关联”，每组最多 8 条，空组不出现；没有数据就原样返回", () => {
  const { window } = env();
  const root = window.NotesMap.parseOutline("# A", "t");
  assert.equal(window.NotesMap.withConnections(root, null), root);
  assert.equal(window.NotesMap.withConnections(root, { mistakes: [], patterns: [] }), root);
  const many = Array.from({ length: 12 }, (_, i) => ({ id: i + 1, title: `题 ${i}` }));
  const next = window.NotesMap.withConnections(root, {
    mistakes: many, patterns: [{ id: "p", title: "二分" }], note_mentions: [{ id: 9, title: "另一篇" }],
  });
  assert.equal(root.children.length, 1, "原来的树不被改动");
  const branch = next.children[next.children.length - 1];
  assert.equal(branch.text, "关联");
  deepEqual(branch.children.map((g) => g.text), ["相关错题", "相关套路", "这篇提到的笔记"]);
  assert.equal(branch.children[0].children.length, 8);
  deepEqual(branch.children[0].children[0].action, { type: "mistake", id: 1 });
  assert.equal(branch.children[1].children[0].action.type, "pattern");
});

test("defaultCollapsed: 节点不多全部展开，很多时逐层收起到画面里不超过 60 个", () => {
  const { window } = env();
  const small = window.NotesMap.parseOutline("# A\n- a\n  - b", "t");
  assert.equal(window.NotesMap.defaultCollapsed(small).size, 0);
  const lines = [];
  for (let i = 0; i < 12; i += 1) {
    lines.push(`# 章${i}`, ...Array.from({ length: 6 }, (_, j) => `- 点${j}`));
  }
  const big = window.NotesMap.parseOutline(lines.join("\n"), "t");
  const collapsed = window.NotesMap.defaultCollapsed(big);
  assert.ok(collapsed.size > 0);
  const visible = window.NotesMap.layout(big, collapsed).nodes.length;
  assert.ok(visible <= 60, `画面里 ${visible} 个节点`);
  assert.ok(visible > 12, "仍能看到各章标题");
});

// ───────────── 画出来之后 ─────────────

function mount(markdown, options = {}) {
  const { window, document } = env();
  const host = document.createElement("div");
  const jumps = [];
  const controller = window.NotesMap.build(host, { markdown, title: "测试笔记", onJump: (i) => jumps.push(i), ...options });
  return { window, document, host, controller, jumps };
}

const groups = (host) => host.querySelectorAll(".nm-node");

test("build: 每个节点一个 g，文字用 textContent，尖括号不会变成标签", () => {
  const { host } = mount("# <img src=x onerror=alert(1)>\n- <b>粗</b>");
  const labels = host.querySelectorAll(".nm-text").map((n) => n.textContent);
  assert.ok(labels.includes("测试笔记"));
  assert.ok(labels.every((text) => !text.includes("<")), JSON.stringify(labels));
  assert.equal(host.querySelectorAll("img").length, 0);
});

test("build: 点有子节点的分支会收起，再点展开", () => {
  const { host } = mount("# A\n- a1\n- a2\n# B\n- b1");
  const before = groups(host).length;
  host.querySelector('.nm-node[data-id="0.0"]').click();
  const folded = groups(host).length;
  assert.equal(folded, before - 2);
  assert.match(host.querySelector('.nm-node[data-id="0.0"] .nm-fold').textContent, /\+2/);
  host.querySelector('.nm-node[data-id="0.0"]').click();
  assert.equal(groups(host).length, before);
});

test("build: 标题旁的 ↗ 把标题序号交给 onJump，不收起分支", () => {
  const { host, jumps } = mount("# A\n- a1\n## B");
  const before = groups(host).length;
  host.querySelector('.nm-node[data-id="0.0.1"] .nm-jump').click();
  deepEqual(jumps, [1]);
  assert.equal(groups(host).length, before);
});

test("build: 没有子节点的标题，直接点它也能跳到正文", () => {
  const { host, jumps } = mount("# A\n## 空标题\n# B");
  host.querySelector('.nm-node[data-id="0.0.0"]').click();
  deepEqual(jumps, [1]);
});

test("build: 回车和空格和点击一样；焦点留在被操作的节点上", () => {
  const { host, document } = mount("# A\n- a1");
  const node = host.querySelector('.nm-node[data-id="0.0"]');
  const { FakeEvent } = require("./js_harness.cjs");
  const press = (key) => {
    const event = new FakeEvent("keydown", { bubbles: true, props: { key } });
    event.target = node;
    host.querySelector(".nm-scroll").dispatchEvent(event);
    return event;
  };
  assert.equal(press("Enter").defaultPrevented, true);
  assert.match(host.querySelector('.nm-node[data-id="0.0"]').getAttribute("class"), /is-collapsed/);
  assert.equal(press("a").defaultPrevented, false);
  assert.equal(document.activeElement.getAttribute("data-id"), "0.0");
});

test("build: 工具栏的“全部展开”“只看第一层”", () => {
  const { host } = mount("# A\n- a1\n  - a2\n# B\n- b1");
  const tools = host.querySelectorAll(".nm-tool");
  const total = groups(host).length;
  tools[1].click(); // 只看第一层
  deepEqual(groups(host).map((n) => n.getAttribute("data-id")), ["0", "0.0", "0.1"]);
  tools[0].click(); // 全部展开
  assert.equal(groups(host).length, total);
});

test("build: “适合宽度”在缩放和原大小之间切换", () => {
  const { host } = mount("# A\n- a1");
  const fit = host.querySelectorAll(".nm-tool")[2];
  assert.equal(fit.textContent, "适合宽度");
  fit.click();
  assert.match(host.querySelector(".nm-scroll").getAttribute("class"), /is-fit/);
  assert.equal(fit.textContent, "原大小");
  assert.equal(fit.getAttribute("aria-pressed"), "true");
  fit.click();
  assert.doesNotMatch(host.querySelector(".nm-scroll").getAttribute("class"), /is-fit/);
});

test("build: 关联数据到了多出一枝，点里面的条目会调用对应的跳转", () => {
  const { window, document, host, controller } = mount("# A");
  const opened = [];
  window.NotesMap.configure({
    openNote: (id) => opened.push(["note", id]),
    openMistake: (id) => opened.push(["mistake", id]),
    openPattern: (pattern) => opened.push(["pattern", pattern.id]),
  });
  controller.setConnections({
    mistakes: [{ id: 5, title: "两数之和" }], patterns: [{ id: "bs", title: "二分" }], note_mentions: [{ id: 7, title: "别的笔记" }],
  });
  const labels = host.querySelectorAll(".nm-text").map((n) => n.textContent);
  assert.ok(labels.includes("关联") && labels.includes("两数之和") && labels.includes("别的笔记"));
  const clickLabel = (text) => {
    const group = host.querySelectorAll(".nm-node").find((g) => g.querySelector(".nm-text").textContent === text);
    group.click();
  };
  clickLabel("两数之和");
  clickLabel("二分");
  clickLabel("别的笔记");
  deepEqual(opened, [["mistake", 5], ["pattern", "bs"], ["note", 7]]);
  assert.ok(document, "document 在");
});

test("build: 没有标题也没有列表的笔记显示提示，不画空图，按钮不可点", () => {
  const { host } = mount("");
  assert.match(host.textContent, /加几个小标题/);
  assert.equal(host.querySelectorAll("svg").length, 0);
  assert.ok(host.querySelectorAll(".nm-tool").every((button) => button.disabled));
});
