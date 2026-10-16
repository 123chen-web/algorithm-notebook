"use strict";

/* 画板“算法图形”：一键插入常用的算法示意图（数组、双指针、链表、二叉树、图、栈、队列、DP 表、哈希表、调用栈）。
   对外：window.DrawShapes = { SHAPES, build(kind, origin, options), originBeside(elements) }。
   - 纯函数，不碰 DOM：生成 Excalidraw 0.18 的元素 JSON，宿主（draw-host.js）把它们追加到当前场景后用 draw:load 发给画板；
   - 每次生成都是新的 id / seed，同一个图形的所有元素共用一个 groupId，拖动时整体移动；
   - 箭头不做绑定（startBinding/endBinding 为 null），文字不挂在容器上，元素之间没有互相引用，插进任何场景都不会出现悬空引用；
   - index 留 null，由画板在 updateScene 时补上分数索引。 */
(() => {
  const INK = "#1e1e1e";
  const MUTED = "#868e96";
  const FILL = "#a5d8ff"; // 指针/高亮格用的浅蓝，亮色、暗色画板上都看得清

  const SHAPES = Object.freeze([
    { kind: "array", label: "数组（带下标）" },
    { kind: "two-pointers", label: "双指针" },
    { kind: "linked-list", label: "链表" },
    { kind: "binary-tree", label: "二叉树" },
    { kind: "graph", label: "图" },
    { kind: "stack", label: "栈" },
    { kind: "queue", label: "队列" },
    { kind: "dp-grid", label: "DP 表格" },
    { kind: "hash-table", label: "哈希表" },
    { kind: "call-stack", label: "递归调用栈" },
  ]);

  function defaultRandom() {
    const bytes = new Uint32Array(1);
    if (globalThis.crypto?.getRandomValues) {
      globalThis.crypto.getRandomValues(bytes);
      return bytes[0];
    }
    return Math.floor(Math.random() * 2 ** 31);
  }

  /** 一次 build 用的工厂：统一的 groupId、自增 id、随机 seed。 */
  function factory(options = {}) {
    const random = options.random || defaultRandom;
    const now = options.now ?? Date.now();
    const prefix = `oy${random().toString(36)}${random().toString(36)}`.slice(0, 14);
    let counter = 0;
    const groupId = `${prefix}-g`;
    const elements = [];
    const nextId = () => `${prefix}-${(counter += 1).toString(36)}`;
    const nonce = () => random() % 2 ** 31;

    function base(type, x, y, width, height, extra = {}) {
      const element = {
        id: nextId(), type, x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height),
        angle: 0, strokeColor: INK, backgroundColor: "transparent", fillStyle: "solid",
        strokeWidth: 2, strokeStyle: "solid", roughness: 1, opacity: 100,
        groupIds: [groupId], frameId: null, index: null, roundness: null,
        seed: nonce(), version: 1, versionNonce: nonce(), isDeleted: false,
        boundElements: null, updated: now, link: null, locked: false,
        ...extra,
      };
      elements.push(element);
      return element;
    }

    const rect = (x, y, w, h, extra = {}) => base("rectangle", x, y, w, h, { roundness: { type: 3 }, ...extra });
    const ellipse = (x, y, w, h, extra = {}) => base("ellipse", x, y, w, h, extra);

    /** 文字估宽：中文按一个字号宽，其他按 0.55 个字号宽。 */
    function textSize(value, fontSize) {
      const lines = String(value).split("\n");
      const widest = Math.max(...lines.map((line) => [...line].reduce(
        (sum, ch) => sum + (/[⺀-鿿＀-￯]/.test(ch) ? fontSize : fontSize * 0.55), 0)));
      return { width: Math.max(fontSize * 0.55, widest), height: lines.length * fontSize * 1.25 };
    }
    /** 以 (cx, cy) 为中心放一段文字。 */
    function text(value, cx, cy, extra = {}) {
      const fontSize = extra.fontSize || 20;
      const size = textSize(value, fontSize);
      return base("text", cx - size.width / 2, cy - size.height / 2, size.width, size.height, {
        text: String(value), originalText: String(value), fontSize, fontFamily: 5,
        textAlign: "center", verticalAlign: "middle", containerId: null, autoResize: true, lineHeight: 1.25,
        ...extra,
      });
    }
    /** 从 (x1, y1) 指向 (x2, y2) 的箭头；head=false 时是一条线（树和图的边）。 */
    function arrow(x1, y1, x2, y2, head = true, extra = {}) {
      const dx = x2 - x1;
      const dy = y2 - y1;
      return base("arrow", x1, y1, Math.abs(dx), Math.abs(dy), {
        points: [[0, 0], [Math.round(dx), Math.round(dy)]], lastCommittedPoint: null,
        startBinding: null, endBinding: null, startArrowhead: null, endArrowhead: head ? "arrow" : null,
        elbowed: false, roundness: { type: 2 },
        ...extra,
      });
    }
    return { elements, rect, ellipse, text, arrow };
  }

  /* ---------- 各个图形（坐标从 (x, y) 往右下画） ---------- */
  const CELL = 50;

  function cells(f, x, y, values, { indexLabels = true, highlight = [] } = {}) {
    values.forEach((value, i) => {
      const cx = x + i * CELL;
      f.rect(cx, y, CELL, CELL, { roundness: null, ...(highlight.includes(i) ? { backgroundColor: FILL } : {}) });
      if (value !== "") f.text(value, cx + CELL / 2, y + CELL / 2);
      if (indexLabels) f.text(String(i), cx + CELL / 2, y + CELL + 14, { fontSize: 14, strokeColor: MUTED });
    });
  }

  const BUILDERS = {
    array(f, x, y) {
      f.text("nums", x + 24, y - 22, { fontSize: 16, strokeColor: MUTED });
      cells(f, x, y, ["3", "1", "4", "1", "5", "9", "2", "6"]);
    },
    "two-pointers"(f, x, y) {
      cells(f, x, y, ["1", "2", "4", "7", "11", "15"], { highlight: [0, 5] });
      const below = y + CELL + 34;
      for (const [i, label] of [[0, "L"], [5, "R"]]) {
        const cx = x + i * CELL + CELL / 2;
        f.arrow(cx, below + 40, cx, below);
        f.text(label, cx, below + 56, { fontSize: 18 });
      }
    },
    "linked-list"(f, x, y) {
      const values = ["1", "2", "3", "4"];
      values.forEach((value, i) => {
        const nx = x + i * 110;
        f.rect(nx, y, 64, 44);
        f.text(value, nx + 32, y + 22);
        f.arrow(nx + 64, y + 22, nx + 108, y + 22);
      });
      f.text("null", x + values.length * 110 + 24, y + 22, { fontSize: 18, strokeColor: MUTED });
      f.text("head", x + 32, y - 22, { fontSize: 16, strokeColor: MUTED });
    },
    "binary-tree"(f, x, y) {
      const R = 22;
      const levels = [[240], [120, 360], [60, 180, 300, 420]];
      const labels = ["1", "2", "3", "4", "5", "6", "7"];
      const centers = [];
      levels.forEach((row, depth) => row.forEach((cx) => centers.push([x + cx, y + depth * 90 + R])));
      centers.forEach(([cx, cy], i) => {
        const parent = i ? centers[Math.floor((i - 1) / 2)] : null;
        if (parent) f.arrow(parent[0], parent[1] + R, cx, cy - R, false);
      });
      centers.forEach(([cx, cy], i) => {
        f.ellipse(cx - R, cy - R, R * 2, R * 2);
        f.text(labels[i], cx, cy);
      });
    },
    graph(f, x, y) {
      const R = 22;
      const points = [[150, 22], [270, 110], [225, 240], [75, 240], [30, 110]];
      const edges = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 0], [0, 2]];
      for (const [a, b] of edges) {
        const [ax, ay] = points[a];
        const [bx, by] = points[b];
        const len = Math.hypot(bx - ax, by - ay);
        const ux = (bx - ax) / len;
        const uy = (by - ay) / len;
        f.arrow(x + ax + ux * R, y + ay + uy * R, x + bx - ux * R, y + by - uy * R, false);
      }
      points.forEach(([px, py], i) => {
        f.ellipse(x + px - R, y + py - R, R * 2, R * 2);
        f.text(String.fromCharCode(65 + i), x + px, y + py);
      });
    },
    stack(f, x, y) {
      const values = ["", "7", "3", "5"];
      values.forEach((value, i) => {
        f.rect(x, y + i * 40, 120, 40, { roundness: null, strokeStyle: i === 0 ? "dashed" : "solid" });
        if (value) f.text(value, x + 60, y + i * 40 + 20);
      });
      f.arrow(x + 200, y + 60, x + 128, y + 60);
      f.text("栈顶", x + 230, y + 60, { fontSize: 16 });
      f.text("栈底", x + 60, y + values.length * 40 + 18, { fontSize: 14, strokeColor: MUTED });
    },
    queue(f, x, y) {
      cells(f, x, y, ["4", "8", "15", "16", ""], { indexLabels: false });
      f.arrow(x + CELL / 2, y - 50, x + CELL / 2, y - 6);
      f.text("队头（出）", x + CELL / 2, y - 64, { fontSize: 14 });
      f.arrow(x + 4 * CELL + CELL / 2, y - 50, x + 4 * CELL + CELL / 2, y - 6);
      f.text("队尾（进）", x + 4 * CELL + CELL / 2, y - 64, { fontSize: 14 });
    },
    "dp-grid"(f, x, y) {
      const N = 5;
      const S = 44;
      f.text("dp[i][j]", x + 22, y + 22, { fontSize: 14, strokeColor: MUTED });
      for (let j = 0; j < N; j += 1) f.text(String(j), x + S + j * S + S / 2, y + S / 2, { fontSize: 14, strokeColor: MUTED });
      for (let i = 0; i < N; i += 1) {
        f.text(String(i), x + S / 2, y + S + i * S + S / 2, { fontSize: 14, strokeColor: MUTED });
        for (let j = 0; j < N; j += 1) {
          f.rect(x + S + j * S, y + S + i * S, S, S, { roundness: null, ...(i === 0 || j === 0 ? { backgroundColor: FILL } : {}) });
        }
      }
    },
    "hash-table"(f, x, y) {
      for (let i = 0; i < 5; i += 1) {
        f.text(String(i), x + 12, y + i * 44 + 22, { fontSize: 14, strokeColor: MUTED });
        f.rect(x + 30, y + i * 44, 60, 44, { roundness: null });
      }
      // 第 1 个桶挂一条冲突链
      const chain = ["k1:v1", "k6:v6"];
      chain.forEach((value, i) => {
        const nx = x + 140 + i * 130;
        f.arrow(i ? nx - 40 : x + 60, y + 44 + 22, nx, y + 44 + 22);
        f.rect(nx, y + 44, 90, 44);
        f.text(value, nx + 45, y + 44 + 22, { fontSize: 16 });
      });
      f.text("hash(key) % 5", x + 60, y - 22, { fontSize: 14, strokeColor: MUTED });
    },
    "call-stack"(f, x, y) {
      const frames = ["f(1)  ← 正在执行", "f(2)", "f(3)"];
      frames.forEach((value, i) => {
        f.rect(x, y + i * 46, 220, 46, i === 0 ? { backgroundColor: FILL } : {});
        f.text(value, x + 110, y + i * 46 + 23, { fontSize: 16 });
      });
      f.text("调用栈（上面先返回）", x + 110, y - 22, { fontSize: 14, strokeColor: MUTED });
    },
  };

  /** 生成一个图形的全部元素；kind 不认识时返回空数组。 */
  function build(kind, origin = { x: 0, y: 0 }, options = {}) {
    const draw = BUILDERS[kind];
    if (!draw) return [];
    const f = factory(options);
    draw(f, Number(origin.x) || 0, Number(origin.y) || 0);
    return f.elements;
  }

  /** 新图形放在已有内容的右边（顶端对齐），空画板放在原点附近。 */
  function originBeside(elements) {
    const live = (Array.isArray(elements) ? elements : []).filter((el) => el && !el.isDeleted
      && Number.isFinite(el.x) && Number.isFinite(el.y));
    if (!live.length) return { x: 100, y: 100 };
    const right = Math.max(...live.map((el) => el.x + Math.max(0, Number(el.width) || 0)));
    const top = Math.min(...live.map((el) => el.y));
    return { x: Math.round(right + 80), y: Math.round(top + 40) };
  }

  window.DrawShapes = Object.freeze({ SHAPES, build, originBeside });
})();
