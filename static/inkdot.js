"use strict";

/* 总览页「连续打卡墨点」：把“连续打卡 N 天”卡片里的一排 7 天小圆圈换成一滴水墨点。
   对外契约：window.InkDot = { render(data, username), reset() }。
   - render(data, username)：data = GET /api/overview 的片段，至少 {streak_days, last7, today}；
     把墨点画进 #streak-ink（role="img"，aria-label 由这里设置）。
   - 墨点大小/浓淡/毛边随连续天数分 5 档；中断后不归零——墨色变淡、缩小一档，
     并显示“墨还在，再点一下就回来了”；从没打过卡显示淡淡的空轮廓。
   - 今天第一次完成复习（last7 的“今天”从未完成变完成）时，播放“墨滴落下”动画
     （≤1 秒，只播一次，记 localStorage；读写失败也能用，内存兜底）。
   - 7/30/100 天里程碑：墨点边上盖一个小印 + 一句文案。
   - 颜色全部走主题变量（见 inkdot.css），JS 里只算几何；不拼 HTML 字符串，不写行内样式。 */
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const MOUNT_ID = "streak-ink";
  const CX = 100;
  const CY = 92;

  /* 档位：0 空/中断 · 1 起步(1-6) · 2 七日(7-29) · 3 一月(30-99) · 4 百日(100+) */
  const CORE_R = [10, 20, 28, 36, 44];

  const MILESTONES = [
    { days: 100, seal: "百日", text: "百日之印 · 墨点成海" },
    { days: 30, seal: "一月", text: "一月之印 · 墨海微澜" },
    { days: 7, seal: "七日", text: "七日之印 · 墨色渐浓" },
  ];
  const TIER_CAPTION = ["", "墨点初生 · 一笔一画", "墨点初具 · 日日不辍", "墨海微澜 · 三十而立", "墨点成海 · 日日不辍"];

  function tierFor(days) {
    if (days >= 100) return 4;
    if (days >= 30) return 3;
    if (days >= 7) return 2;
    if (days >= 1) return 1;
    return 0;
  }

  /* 确定性伪随机：同一天数永远画出同一滴墨，位置不乱跳。 */
  function mulberry(seed) {
    let state = seed >>> 0;
    return () => {
      state = (state + 0x6d2b79f5) >>> 0;
      let value = state;
      value = Math.imul(value ^ (value >>> 15), value | 1);
      value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
  }

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function svgEl(tag, attrs) {
    const item = document.createElementNS(NS, tag);
    for (const key of Object.keys(attrs || {})) item.setAttribute(key, String(attrs[key]));
    return item;
  }

  /* 不规则墨团轮廓：12 个点，半径带一点 seeded 起伏，再用二次贝塞尔连成闭合曲线。 */
  function blobPath(r, seed) {
    const rand = mulberry(seed * 2654435761 + 97);
    const points = [];
    for (let i = 0; i < 12; i++) {
      const angle = (i / 12) * Math.PI * 2;
      const wobble = 1 + (rand() - 0.5) * 0.28;
      points.push([CX + Math.cos(angle) * r * wobble, CY + Math.sin(angle) * r * wobble]);
    }
    let d = "";
    for (let i = 0; i < points.length; i++) {
      const p = points[i];
      const q = points[(i + 1) % points.length];
      const mid = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
      d += (i === 0 ? `M${mid[0].toFixed(1)},${mid[1].toFixed(1)}` : "") + ` Q${p[0].toFixed(1)},${p[1].toFixed(1)} ${mid[0].toFixed(1)},${mid[1].toFixed(1)}`;
    }
    return `${d} Z`;
  }

  const memory = new Map();
  function storeGet(key) {
    try {
      const raw = window.localStorage.getItem(key);
      return raw === null ? undefined : JSON.parse(raw);
    } catch {
      return memory.has(key) ? memory.get(key) : undefined;
    }
  }
  function storeSet(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      memory.set(key, value);
    }
  }

  const snapKey = (username) => `oy-inkdot:snap:${username || "anon"}`;
  const dropKey = (username, date) => `oy-inkdot:drop:${username || "anon"}:${date}`;

  function reducedMotion() {
    return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function milestoneFor(days) {
    for (const item of MILESTONES) if (days >= item.days) return item;
    return null;
  }

  function buildDot(svg, days, tier, state) {
    const r = CORE_R[tier];
    if (state === "never") {
      svg.append(svgEl("circle", { cx: CX, cy: CY, r, class: "inkdot-empty" }));
      return;
    }
    const defs = svgEl("defs", {});
    const filter = svgEl("filter", { id: "inkdot-blur", x: "-60%", y: "-60%", width: "220%", height: "220%" });
    filter.append(svgEl("feGaussianBlur", { stdDeviation: 7 }));
    defs.append(filter);
    svg.append(defs);
    /* 外层晕开的淡墨 */
    svg.append(svgEl("circle", { cx: CX, cy: CY, r: (r * 1.55).toFixed(1), class: "inkdot-halo", filter: "url(#inkdot-blur)" }));
    /* 不规则墨团 */
    const blob = svgEl("path", { d: blobPath(r * 1.12, days), class: "inkdot-blob" });
    svg.append(blob);
    /* 浓墨芯 */
    svg.append(svgEl("circle", { cx: CX, cy: CY, r: (r * 0.66).toFixed(1), class: "inkdot-core" }));
    /* 边缘毛边：档位越高越多 */
    const rand = mulberry(days * 40503 + 11);
    const edgeCount = 4 + tier * 3;
    for (let i = 0; i < edgeCount; i++) {
      const angle = rand() * Math.PI * 2;
      const dist = r * (1.3 + rand() * 0.35);
      svg.append(svgEl("circle", {
        cx: (CX + Math.cos(angle) * dist).toFixed(1),
        cy: (CY + Math.sin(angle) * dist).toFixed(1),
        r: (1.4 + rand() * 2.2).toFixed(1),
        class: "inkdot-edge",
      }));
    }
  }

  function buildDrop(svg, r) {
    const layer = svgEl("g", { class: "inkdot-drop-layer" });
    const drop = svgEl("circle", { cx: CX, cy: 8, r: 9, class: "inkdot-drop-dot" });
    layer.append(drop);
    for (let i = 0; i < 3; i++) {
      const ripple = svgEl("circle", { cx: CX, cy: CY, r: (r * 0.85).toFixed(1), class: "inkdot-ripple" });
      ripple.dataset.delay = String(i);
      layer.append(ripple);
    }
    svg.append(layer);
    window.setTimeout(() => layer.remove(), 1200);
  }

  function render(data, username) {
    const mount = document.getElementById(MOUNT_ID);
    if (!mount) return;
    const days = Math.max(0, Number.parseInt(data.streak_days, 10) || 0);
    const last7 = Array.isArray(data.last7) ? data.last7 : [];
    const todayDone = last7[6] === true;
    const date = typeof data.today === "string" && data.today ? data.today : new Date().toISOString().slice(0, 10);

    const snap = storeGet(snapKey(username)) || {};
    const everHad = snap.everHad === true || days >= 1;
    const broken = days === 0 && everHad;
    const never = days === 0 && !everHad;
    const lastDays = days >= 1 ? days : Number(snap.lastDays) || 0;
    /* 中断：不归零，墨色变淡、缩小一档（取上次档位的下一档）。 */
    const tier = never ? 0 : broken ? Math.max(0, tierFor(lastDays) - 1) : tierFor(days);
    const state = never ? "never" : broken ? "broken" : "ok";

    const dropPlayed = storeGet(dropKey(username, date)) === 1;
    const shouldDrop = todayDone && !dropPlayed && !reducedMotion();
    if (todayDone && !dropPlayed) storeSet(dropKey(username, date), 1);

    mount.replaceChildren();
    const svg = svgEl("svg", { viewBox: "0 0 200 190", class: "inkdot-svg", "aria-hidden": "true", focusable: "false" });
    svg.dataset.tier = String(tier);
    if (broken) svg.classList.add("is-broken");
    buildDot(svg, days, tier, state);
    if (shouldDrop) buildDrop(svg, CORE_R[tier] || CORE_R[0]);
    mount.append(svg);

    /* 里程碑小印 */
    const milestone = milestoneFor(days);
    if (milestone) {
      const seal = node("span", "inkdot-seal", milestone.seal);
      seal.setAttribute("aria-hidden", "true");
      mount.append(seal);
    }

    /* 一句小字：中断安慰 / 空状态邀请 / 档位小注 / 里程碑文案 */
    const caption = node("p", "inkdot-caption");
    if (never) {
      caption.textContent = "今天完成一次复习，这里会落下第一滴墨";
    } else if (broken) {
      caption.textContent = "墨还在，再点一下就回来了";
    } else if (milestone) {
      caption.textContent = milestone.text;
      caption.classList.add("is-milestone");
    } else {
      caption.textContent = TIER_CAPTION[tier];
    }
    mount.append(caption);

    const stateText = never ? "还没有落下第一滴墨" : broken ? "已中断，墨色变淡" : `墨点第${tier + 1}阶`;
    mount.setAttribute("aria-label", `连续打卡墨点：连续 ${days} 天，${stateText}`);

    storeSet(snapKey(username), { date, days, todayDone, everHad, lastDays });
  }

  function reset() {
    const mount = document.getElementById(MOUNT_ID);
    if (mount) mount.replaceChildren();
  }

  window.InkDot = { render, reset };
})();
