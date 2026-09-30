/*
 * 鼠标特效改写自 React Bits 的 RippleDistortion 组件
 * https://github.com/DavidHDev/react-bits
 *
 * MIT + Commons Clause License Condition v1.0
 * Copyright (c) 2026 David Haz
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, and distribute the Software as part of
 * an application, website, or product, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * Commons Clause Restriction: You may use this Software, including for any
 * commercial purpose, so long as you do not sell, sublicense, or redistribute
 * the components themselves - whether alone, in a bundle, or as a ported version.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 *
 * 本文件只作为本网站的一部分使用，不得单独抽出来作为组件发布或分发。
 */
(function () {
  "use strict";

  // Ripple tuning: CSS pixels and seconds. Landscape cached for this page session.
  const RIPPLE_BRUSH_SIZE = 150;
  const RIPPLE_STRENGTH = 0.16;
  const RIPPLE_SWIRL = 1;
  const RIPPLE_RINGS = 4;
  const RIPPLE_SPREAD = 5;
  const RIPPLE_FADE = 3;
  // Unitless. A wave below this opacity is dropped: its displacement is under half a pixel, and one 50 ms frame earlier
  // it was already below half an 8-bit step of the displacement field, so nothing visible disappears when it stops.
  const RIPPLE_MIN_OPACITY = 0.039;
  const RIPPLE_SPACING = 15;
  const RIPPLE_DISPERSION = 0;
  const RIPPLE_CLICK_STRENGTH = 2;
  const RIPPLE_GRAYSCALE = false;
  const RIPPLE_TINT = "#bc5b3a";
  const RIPPLE_TINT_AMOUNT = 0.08;
  const RIPPLE_GLINT = 0.25;
  const RIPPLE_HIGHLIGHT = "#ffffff";
  const RIPPLE_QUALITY = "medium";
  const RIPPLE_QUALITY_SCALE = { low: 0.4, medium: 0.7, high: 1 };
  const RIPPLE_MAX_WAVES = 100;
  const RIPPLE_START_SCALE = 1.5;
  const RIPPLE_LIFE_CONSTANT = Math.log(500);
  const RIPPLE_MAX_DPR = 1.5;
  const RIPPLE_AMBIENT_FIRST_DELAY = [700, 1300]; // ms: delay range before the first automatic drop.
  const RIPPLE_AMBIENT_INTERVAL = [4500, 8000]; // ms: random interval between automatic drops.
  const RIPPLE_AMBIENT_POWER = [0.8, 1.1]; // Ring size relative to an ordinary pointer wave (= 1).
  const RIPPLE_AMBIENT_OPACITY = [0.85, 1]; // Unitless initial opacity; displacement scales with opacity squared.
  const RIPPLE_AMBIENT_MARGIN = 0.12; // Fraction of viewport width/height kept clear at each edge.
  const RIPPLE_AMBIENT_PROBES = 24; // Maximum random candidates checked for exposed landscape per automatic drop.
  const RIPPLE_AMBIENT_IDLE_MS = 60000; // ms without input before automatic drops stop.
  const RIPPLE_TOUCH_MAX_DPR = 1.25; // Maximum device pixels per CSS pixel on touchscreens.
  const RIPPLE_TOUCH_MIN_FRAME_MS = 12; // ms between drawn touch frames; caps 120 Hz displays near 60 fps.
  const RIPPLE_TEXTURE_WIDTH = 1600;
  const RIPPLE_TEXTURE_HEIGHT = 1000;
  const RIPPLE_TEXTURE_SEED = 0x729bc53a;
  const RIPPLE_MIN_LUMINANCE = 0.82;
  let rippleLandscape = null;

  const effects = new Map();
  const failures = new Map(); // A failed effect stays off for this page session.
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const noHover = window.matchMedia("(hover: none)");
  const coarsePointer = window.matchMedia("(pointer: coarse)");
  let enabled = true;
  let wanted = "ripple";
  let mode = "off";
  let reason = null;
  let current = null;
  let noWebGL2 = false;
  let ready = false;
  try {
    enabled = window.localStorage.getItem("cursorFx") !== "off";
  } catch (_) { /* Storage blocked: default to enabled, keep changes in memory. */ }

  function publish(actual, why = null) {
    mode = actual;
    reason = why;
    document.documentElement.dataset.cursorFx = actual;
  }

  function disposeCurrent() {
    const old = current;
    current = null;
    if (!old) return;
    old.canvas.hidden = true;
    old.canvas.removeEventListener("webglcontextlost", old.onLost);
    try { old.effect?.destroy(); } catch (_) { /* Still release the context. */ }
    try {
      // Removing a canvas alone does not promptly release its WebGL context.
      if (old.gl && !old.gl.isContextLost()) {
        old.gl.getExtension("WEBGL_lose_context")?.loseContext();
      }
    } catch (_) { /* Context may already have been lost. */ }
    old.canvas.remove();
  }

  function blockedReason() {
    if (!enabled) return "user-off";
    if (wanted === "off") return null;
    if (reducedMotion.matches) return "reduced-motion";
    if (!effects.has(wanted)) return "unregistered";
    if (noWebGL2) return "no-webgl2";
    return failures.get(wanted) || null;
  }

  function reconcile() {
    if (!ready) return;
    const blocked = blockedReason();
    if (blocked || wanted === "off") {
      disposeCurrent();
      publish("off", blocked);
      return;
    }
    if (current && current.name !== wanted) disposeCurrent();
    if (document.hidden) {
      current?.effect.pause();
      if (current) current.canvas.hidden = true;
      publish("off", "document-hidden");
      return;
    }
    if (!current) {
      const canvas = document.createElement("canvas");
      canvas.className = `cursor-fx-canvas cursor-fx-canvas--${wanted}`;
      canvas.setAttribute("aria-hidden", "true");
      canvas.style.setProperty("pointer-events", "none");
      canvas.hidden = true;
      const record = { name: wanted, canvas, gl: null, effect: null, onLost: null };
      const fail = (why) => {
        if (current !== record) return;
        failures.set(record.name, why);
        reconcile();
      };
      record.onLost = (event) => {
        event.preventDefault();
        fail("context-lost");
      };
      current = record;
      canvas.addEventListener("webglcontextlost", record.onLost);
      try {
        record.gl = canvas.getContext("webgl2", {
          alpha: true, premultipliedAlpha: true, antialias: false,
          depth: false, stencil: false, preserveDrawingBuffer: false,
        });
      } catch (_) { /* Disabled/unsupported WebGL2 has the same silent fallback. */ }
      if (!record.gl) {
        noWebGL2 = true;
        disposeCurrent();
        publish("off", "no-webgl2");
        return;
      }
      try {
        record.effect = effects.get(wanted)({ canvas, gl: record.gl, fail });
        // A future factory may synchronously report failure while initializing.
        if (current !== record) {
          record.effect?.destroy();
          return;
        }
        document.body.appendChild(canvas);
      } catch (_) {
        fail("effect-error");
        return;
      }
    }
    const record = current;
    try {
      record.canvas.hidden = false;
      record.effect.resume();
      if (current === record) publish(record.name);
    } catch (_) {
      failures.set(record.name, "effect-error");
      reconcile();
    }
  }

  // Effect factory contract: ({ canvas, gl, fail }) =>
  // { pause(), resume(), destroy(), isActive() }. The manager owns the canvas
  // and context; destroy must cancel all callbacks/listeners and free GL objects.
  // fail(reason) disables this effect for the rest of this page session.
  function registerEffect(name, factory) {
    if (name !== "ripple" || typeof factory !== "function") return;
    if (current?.name === name) disposeCurrent();
    effects.set(name, factory);
    reconcile(); // In particular, activate a previously requested, missing ripple.
  }

  window.CursorFX = {
    registerEffect,
    setMode(name) {
      wanted = ["ripple", "off"].includes(name) ? name : "off";
      reconcile();
    },
    setEnabled(value) {
      enabled = Boolean(value);
      try { window.localStorage.setItem("cursorFx", enabled ? "on" : "off"); } catch (_) { /* Optional persistence. */ }
      reconcile();
    },
    isEnabled() { return enabled; },
    getState() {
      // mode is the available effect; active means it currently has a render loop.
      return { mode, wanted, enabled, active: Boolean(current?.effect.isActive()), reason };
    },
  };

  function getRippleLandscape() {
    if (rippleLandscape) return rippleLandscape;
    const paper = document.createElement("canvas");
    const w = paper.width = RIPPLE_TEXTURE_WIDTH;
    const h = paper.height = RIPPLE_TEXTURE_HEIGHT;
    const ctx = paper.getContext("2d");
    if (!ctx) throw new Error("landscape context unavailable");
    let seed = RIPPLE_TEXTURE_SEED;
    function random() {
      let t = seed += 0x6d2b79f5;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }
    ctx.fillStyle = "#f4efe4";
    ctx.fillRect(0, 0, w, h);
    const ground = ctx.createRadialGradient(w * 0.5, h * 0.42, 0, w * 0.5, h * 0.42, w * 0.65);
    ground.addColorStop(0, "#f8f5ee");
    ground.addColorStop(0.6, "#f4efe4");
    ground.addColorStop(1, "#efe8da");
    ctx.fillStyle = ground;
    ctx.fillRect(0, 0, w, h);
    for (let i = 0; i < 2500; i++) {
      const x = random() * w, y = random() * h;
      ctx.strokeStyle = `rgba(203,191,169,${0.03 + random() * 0.03})`;
      ctx.lineWidth = 0.5;
      ctx.beginPath(); ctx.moveTo(x, y);
      ctx.lineTo(x + 1 + random() * 5, y + random() * 2 - 1); ctx.stroke();
    }
    const sun = ctx.createRadialGradient(w * 0.72, h * 0.27, 0, w * 0.72, h * 0.27, h * 0.08);
    sun.addColorStop(0, "rgba(188,91,58,0.62)");
    sun.addColorStop(0.65, "rgba(188,91,58,0.30)");
    sun.addColorStop(1, "rgba(188,91,58,0)");
    ctx.fillStyle = sun; ctx.fillRect(0, 0, w, h);
    for (let layer = 0; layer < 5; layer++) {
      const t = layer / 4, base = h * (0.46 + 0.28 * t);
      const phases = [random() * 6.28, random() * 6.28, random() * 6.28];
      const ridge = new Path2D();
      ridge.moveTo(0, h);
      for (let x = 0; x <= w; x += 4) {
        const u = x / w;
        const y = base + h * (0.040 * Math.sin(u * 9 + phases[0])
          + 0.024 * Math.sin(u * 23 + phases[1])
          + 0.009 * Math.sin(u * 57 + phases[2])
          - 0.012 * Math.abs(Math.sin(u * 83 + phases[1])));
        ridge.lineTo(x, y);
      }
      ridge.lineTo(w, h); ridge.closePath();
      const rgb = [120 - 82 * t, 132 - 86 * t, 136 - 86 * t].map(Math.round).join(",");
      const ink = ctx.createLinearGradient(0, base - h * 0.08, 0, base + h * 0.38);
      ink.addColorStop(0, `rgba(${rgb},${0.28 + 0.44 * t})`);
      ink.addColorStop(0.32, `rgba(${rgb},${0.06 + 0.05 * t})`);
      ink.addColorStop(1, `rgba(${rgb},0)`);
      ctx.fillStyle = ink; ctx.fill(ridge);
      ctx.save(); ctx.clip(ridge);
      for (let i = 0; i < 300; i++) {
        const x = random() * w, y = base - h * 0.08 + random() * h * 0.38;
        ctx.strokeStyle = `rgba(${i % 2 ? "255,255,255" : rgb},${0.05 + random() * 0.07})`;
        ctx.lineWidth = 0.3 + random() * 0.5;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + 3 + random() * 45, y); ctx.stroke();
      }
      ctx.restore();
      if (layer < 4) {
        const fogY = base + h * 0.06;
        const fog = ctx.createLinearGradient(0, fogY - 45, 0, fogY + 65);
        fog.addColorStop(0, "rgba(248,245,238,0)");
        fog.addColorStop(0.5, "rgba(248,245,238,0.55)");
        fog.addColorStop(1, "rgba(248,245,238,0)");
        ctx.fillStyle = fog; ctx.fillRect(0, fogY - 45, w, 110);
      }
    }
    const water = ctx.createLinearGradient(0, h * 0.76, 0, h);
    water.addColorStop(0, "rgba(248,245,238,0)");
    water.addColorStop(0.3, "rgba(248,245,238,0.88)");
    water.addColorStop(1, "#f8f5ee");
    ctx.fillStyle = water; ctx.fillRect(0, h * 0.76, w, h * 0.24);
    for (let i = 0; i < 60; i++) {
      const x = random() * w, y = h * (0.80 + random() * 0.2);
      ctx.strokeStyle = `rgba(38,46,50,${0.05 + random() * 0.05})`;
      ctx.lineWidth = 0.5;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + 40 + random() * 120, y); ctx.stroke();
    }
    const reflection = ctx.createRadialGradient(w * 0.72, h * 0.88, 0, w * 0.72, h * 0.88, 90);
    reflection.addColorStop(0, "rgba(188,91,58,0.08)");
    reflection.addColorStop(1, "rgba(188,91,58,0)");
    ctx.save(); ctx.translate(w * 0.72, 0); ctx.scale(0.25, 1);
    ctx.translate(-w * 0.72, 0); ctx.fillStyle = reflection;
    ctx.fillRect(0, h * 0.78, w * 4, h * 0.22); ctx.restore();
    // Measure linear-light relative luminance once, and lift only as needed.
    const pixels = ctx.getImageData(0, 0, w, h);
    const linear = Array.from({ length: 256 }, (_, n) => {
      const v = n / 255;
      return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    let luminance = 0;
    for (let i = 0; i < pixels.data.length; i += 4) {
      luminance += linear[pixels.data[i]] * 0.2126 + linear[pixels.data[i + 1]] * 0.7152
        + linear[pixels.data[i + 2]] * 0.0722;
    }
    luminance /= w * h;
    if (luminance < RIPPLE_MIN_LUMINANCE) {
      const lift = (RIPPLE_MIN_LUMINANCE - luminance) / (1 - luminance);
      // Same arithmetic and Uint8Clamped conversion as the per-channel path.
      const lifted = new Uint8ClampedArray(256);
      for (let n = 0; n < lifted.length; n++) {
        const v = linear[n] * (1 - lift) + lift;
        lifted[n] = Math.ceil(255 * (v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055));
      }
      for (let i = 0; i < pixels.data.length; i += 4) {
        for (let c = 0; c < 3; c++) pixels.data[i + c] = lifted[pixels.data[i + c]];
      }
      ctx.putImageData(pixels, 0, 0);
    }
    rippleLandscape = paper;
    return paper;
  }

  const rippleWaveVertex = `#version 300 es
    precision highp float;
    layout(location=0) in vec2 position;
    layout(location=1) in vec2 iOffset;
    layout(location=2) in vec2 iScale;
    layout(location=3) in float iOpacity;
    out vec2 vUv; out float vOpacity;
    void main() {
      vUv = position * 0.5 + 0.5; vOpacity = iOpacity;
      gl_Position = vec4(iOffset + position * iScale, 0.0, 1.0);
    }
  `;
  const rippleWaveFragment = `#version 300 es
    precision highp float;
    in vec2 vUv; in float vOpacity;
    uniform float uRings;
    out vec4 fragColor;
    void main() {
      vec2 p = vUv * 2.0 - 1.0;
      float r = dot(p, p);
      if (r > 1.0) discard;
      float brush = (exp(-r * 5.0) - 0.006737947) / (1.0 - 0.006737947);
      brush *= 0.55 + 0.45 * cos(sqrt(r) * 6.283185307179586 * uRings);
      fragColor = vec4(vec3(brush * vOpacity * vOpacity), 1.0);
    }
  `;
  const rippleScreenVertex = `#version 300 es
    out vec2 vUv;
    void main() {
      vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
      vUv = p; gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
    }
  `;
  const rippleCompositeFragment = `#version 300 es
    precision highp float;
    in vec2 vUv;
    uniform sampler2D uTexture, uDisplacement;
    uniform vec2 uResolution, uTextureSize, uTexel;
    uniform vec3 uTint, uHighlight;
    uniform float uStrength, uSwirl, uDispersion, uGlint, uTintAmount, uGrayscale;
    out vec4 fragColor;
    vec2 coverUV(vec2 uv) {
      vec2 safe = max(uTextureSize, vec2(1.0));
      vec2 s = uResolution / safe;
      vec2 scaledSize = safe * max(s.x, s.y);
      vec2 offset = (uResolution - scaledSize) * 0.5;
      return (uv * uResolution - offset) / scaledSize;
    }
    void main() {
      float amount = texture(uDisplacement, vUv).r;
      vec2 base = coverUV(vUv);
      float theta = amount * uSwirl * 6.283185307179586;
      vec2 push = vec2(sin(theta), cos(theta)) * amount * uStrength;
      vec3 color;
      if (uDispersion > 0.001) {
        float split = uDispersion * 0.25;
        color.r = texture(uTexture, base + push * (1.0 + split)).r;
        color.g = texture(uTexture, base + push).g;
        color.b = texture(uTexture, base + push * (1.0 - split)).b;
      } else { color = texture(uTexture, base + push).rgb; }
      if (uGrayscale > 0.001) color = mix(color, vec3(dot(color, vec3(0.2126,0.7152,0.0722))), uGrayscale);
      if (uTintAmount > 0.001) color = mix(color, color * uTint * 1.9, clamp(amount * 1.6, 0.0, 1.0) * uTintAmount);
      if (uGlint > 0.001) {
        float ex = texture(uDisplacement, vUv + vec2(uTexel.x,0.0)).r - texture(uDisplacement, vUv - vec2(uTexel.x,0.0)).r;
        float ey = texture(uDisplacement, vUv + vec2(0.0,uTexel.y)).r - texture(uDisplacement, vUv - vec2(0.0,uTexel.y)).r;
        vec3 normal = normalize(vec3(-ex * 26.0, -ey * 26.0, 1.0));
        vec3 light = normalize(vec3(-0.35, 0.55, 1.0));
        float raw = pow(max(dot(normal, light), 0.0), 22.0);
        float flatSpec = pow(max(light.z, 0.0), 22.0);
        color += uHighlight * clamp((raw - flatSpec) / max(1.0 - flatSpec, 0.0001), 0.0, 1.0) * uGlint;
      }
      fragColor = vec4(color, 1.0);
    }
  `;

  function createRipple({ canvas, gl, fail }) {
    const programs = [], buffers = [], textures = [], arrays = [], framebuffers = [];
    let paused = true, destroyed = false, raf = 0, lastFrame = 0;
    let width = 1, height = 1, fieldWidth = 1, fieldHeight = 1;
    let sizeDirty = true, currentWave = 0, previousX = null, previousY = null;
    let rect = null, ambientTimer = 0, previousAmbient = null;
    let lastInput = performance.now();
    const isTouch = () => noHover.matches || coarsePointer.matches;
    const randomBetween = ([min, max]) => min + Math.random() * (max - min);
    const activityEvents = ["pointerdown", "touchstart", "keydown", "scroll"];
    const waves = Array.from({ length: RIPPLE_MAX_WAVES }, () => ({ x: 0, y: 0, scale: RIPPLE_START_SCALE,
      target: RIPPLE_START_SCALE, size: 1, opacity: 0 }));
    const instances = new Float32Array(RIPPLE_MAX_WAVES * 5);
    function allocate(list, object) {
      if (!object) throw new Error("ripple allocation failed");
      list.push(object); return object;
    }
    function program(vertex, fragment) {
      const result = allocate(programs, gl.createProgram());
      const shaders = [];
      try {
        for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]]) {
          const shader = gl.createShader(type);
          if (!shader) throw new Error("shader allocation failed");
          shaders.push(shader); gl.shaderSource(shader, source); gl.compileShader(shader);
          if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error("shader compilation failed");
          gl.attachShader(result, shader);
        }
        gl.linkProgram(result);
        if (!gl.getProgramParameter(result, gl.LINK_STATUS)) throw new Error("shader link failed");
        return result;
      } finally {
        for (const shader of shaders) { gl.detachShader(result, shader); gl.deleteShader(shader); }
      }
    }
    function texture() {
      const result = allocate(textures, gl.createTexture());
      gl.bindTexture(gl.TEXTURE_2D, result);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      return result;
    }
    function pause() {
      paused = true; window.cancelAnimationFrame(raf); raf = 0; lastFrame = 0;
      window.clearTimeout(ambientTimer); ambientTimer = 0;
      for (const type of activityEvents) window.removeEventListener(type, onActivity, true);
      window.visualViewport?.removeEventListener("resize", onResize);
      previousX = previousY = null;
      for (const wave of waves) wave.opacity = 0;
    }
    function destroy() {
      if (destroyed) return;
      destroyed = true; pause();
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("resize", onResize);
      for (const item of framebuffers) gl.deleteFramebuffer(item);
      for (const item of arrays) gl.deleteVertexArray(item);
      for (const item of buffers) gl.deleteBuffer(item);
      for (const item of textures) gl.deleteTexture(item);
      for (const item of programs) gl.deleteProgram(item);
    }
    let waveProgram, screenProgram, waveArray, screenArray, instanceBuffer, landscape, displacement, framebuffer;
    const uniforms = {};
    function resize() {
      rect = canvas.getBoundingClientRect();
      width = Math.max(1, rect.width); height = Math.max(1, rect.height);
      const ratio = Math.min(window.devicePixelRatio || 1, isTouch() ? RIPPLE_TOUCH_MAX_DPR : RIPPLE_MAX_DPR);
      canvas.width = Math.max(1, Math.floor(width * ratio));
      canvas.height = Math.max(1, Math.floor(height * ratio));
      fieldWidth = Math.max(1, Math.floor(width * RIPPLE_QUALITY_SCALE[RIPPLE_QUALITY]));
      fieldHeight = Math.max(1, Math.floor(height * RIPPLE_QUALITY_SCALE[RIPPLE_QUALITY]));
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, displacement);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, fieldWidth, fieldHeight, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, displacement, 0);
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
        fail("effect-error"); return false;
      }
      sizeDirty = false; return true;
    }
    function wake() {
      if (paused || destroyed || raf || document.hidden || reducedMotion.matches) return;
      raf = window.requestAnimationFrame(render);
    }
    function pointerPosition(event, bounds = null) {
      // An input can arrive between a resize event and the next render frame.
      if (!bounds) {
        if (sizeDirty || !rect) rect = canvas.getBoundingClientRect();
        bounds = rect;
      }
      return { x: event.clientX - bounds.left, y: bounds.height - (event.clientY - bounds.top) };
    }
    function newWave(event, power, opacity = 1, bounds = null) {
      if (paused || destroyed || document.hidden || reducedMotion.matches) return null;
      const wave = waves[currentWave]; currentWave = (currentWave + 1) % RIPPLE_MAX_WAVES;
      const point = pointerPosition(event, bounds);
      wave.x = point.x; wave.y = point.y;
      wave.scale = RIPPLE_START_SCALE * power;
      wave.target = RIPPLE_START_SCALE * Math.max(1, RIPPLE_SPREAD) * power;
      wave.size = Math.max(1, RIPPLE_BRUSH_SIZE); wave.opacity = opacity;
      wake(); return wave;
    }
    function pointerWave(event, power) {
      const wave = newWave(event, power);
      if (wave) { previousX = wave.x; previousY = wave.y; }
    }
    function onPointerMove(event) {
      if (paused || destroyed) return;
      const point = pointerPosition(event);
      if (previousX === null || Math.abs(point.x - previousX) > RIPPLE_SPACING
        || Math.abs(point.y - previousY) > RIPPLE_SPACING) pointerWave(event, 1);
    }
    function onPointerDown(event) { pointerWave(event, RIPPLE_CLICK_STRENGTH); }
    function canAmbient() {
      return !paused && !destroyed && !document.hidden && !reducedMotion.matches
        && isTouch() && performance.now() - lastInput < RIPPLE_AMBIENT_IDLE_MS;
    }
    function backgroundAlpha(color) {
      color = color.trim().toLowerCase();
      if (color === "transparent") return 0;
      const rgb = color.match(/^rgba?\((.*)\)$/);
      const srgb = color.match(/^color\(srgb\s+(.*)\)$/);
      if (!rgb && !srgb) return 1; // Unknown serialization is conservatively opaque.
      const parts = (rgb || srgb)[1].split("/");
      const commaSyntax = Boolean(rgb && parts[0].includes(","));
      if (parts.length > 2 || (commaSyntax && parts.length > 1)) return 1;
      const channels = parts[0].trim().split(commaSyntax ? /\s*,\s*/ : /\s+/);
      let alpha = parts.length === 2 ? parts[1].trim() : "1";
      if (commaSyntax && channels.length === 4) alpha = channels.pop();
      const number = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?%?$/;
      if (channels.length !== 3 || !channels.every((channel) => number.test(channel))
        || !number.test(alpha)) return 1;
      const value = parseFloat(alpha) / (alpha.endsWith("%") ? 100 : 1);
      return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 1;
    }
    function exposesLandscape(clientX, clientY) {
      let element = document.elementFromPoint(clientX, clientY);
      if (!element) return false;
      for (; element && element !== document.body && element !== document.documentElement;
        element = element.parentElement) {
        const style = window.getComputedStyle(element);
        if (backgroundAlpha(style.backgroundColor) >= 0.5 || style.backgroundImage !== "none") return false;
      }
      return true;
    }
    function scheduleAmbient(first = false) {
      // Re-read media queries every time: attaching a mouse can change them live.
      if (!canAmbient()) {
        window.clearTimeout(ambientTimer); ambientTimer = 0;
        return;
      }
      if (ambientTimer) return; // resume/input/media changes share this one chain.
      const delay = randomBetween(first ? RIPPLE_AMBIENT_FIRST_DELAY : RIPPLE_AMBIENT_INTERVAL);
      ambientTimer = window.setTimeout(() => {
        ambientTimer = 0;
        if (!canAmbient()) return;
        const bounds = canvas.getBoundingClientRect();
        if (bounds.width > 0 && bounds.height > 0) {
          const min = RIPPLE_AMBIENT_MARGIN, max = 1 - min;
          let selected = null, firstExposed = null;
          for (let attempt = 0; attempt < RIPPLE_AMBIENT_PROBES; attempt++) {
            const x = randomBetween([min, max]), y = randomBetween([min, max]);
            const candidate = { x, y, clientX: bounds.left + x * bounds.width,
              clientY: bounds.top + y * bounds.height };
            if (!exposesLandscape(candidate.clientX, candidate.clientY)) continue;
            if (!firstExposed) firstExposed = candidate;
            if (!previousAmbient || Math.abs(x - previousAmbient.x) * bounds.width
              + Math.abs(y - previousAmbient.y) * bounds.height >= bounds.width * 0.25) {
              selected = candidate; break;
            }
          }
          selected = selected || firstExposed; // Relax spacing only after checking all candidates.
          if (selected && newWave(selected, randomBetween(RIPPLE_AMBIENT_POWER),
            randomBetween(RIPPLE_AMBIENT_OPACITY), bounds)) {
            previousAmbient = { x: selected.x, y: selected.y }; // Never alter pointer drag-spacing state.
          }
        }
        scheduleAmbient();
      }, delay);
    }
    function onActivity() {
      if (paused || destroyed || document.hidden) return;
      lastInput = performance.now();
      scheduleAmbient(true);
    }
    function onResize() { sizeDirty = true; wake(); }
    function render(now) {
      raf = 0;
      if (paused || destroyed || document.hidden || reducedMotion.matches) return;
      if (gl.isContextLost()) { fail("context-lost"); return; }
      if (isTouch() && lastFrame && now - lastFrame < RIPPLE_TOUCH_MIN_FRAME_MS) {
        wake(); return; // Keep lastFrame unchanged so evolution follows elapsed time.
      }
      try {
        if (sizeDirty && !resize()) return;
        const delta = lastFrame ? Math.min((now - lastFrame) / 1000, 0.05) : 0;
        const growth = 1 - Math.exp(-delta * 1.09);
        const decay = Math.exp(-delta * RIPPLE_LIFE_CONSTANT / Math.max(0.15, RIPPLE_FADE));
        let count = 0;
        for (const wave of waves) {
          if (wave.opacity <= 0) continue;
          wave.opacity *= decay; wave.scale += (wave.target - wave.scale) * growth;
          if (wave.opacity < RIPPLE_MIN_OPACITY) { wave.opacity = 0; continue; }
          const half = wave.scale * wave.size / 2, i = count++ * 5;
          instances[i] = wave.x / width * 2 - 1; instances[i + 1] = wave.y / height * 2 - 1;
          instances[i + 2] = half / width * 2; instances[i + 3] = half / height * 2;
          instances[i + 4] = wave.opacity;
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
        gl.viewport(0, 0, fieldWidth, fieldHeight);
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.enable(gl.BLEND); gl.blendEquation(gl.FUNC_ADD); gl.blendFunc(gl.ONE, gl.ONE);
        gl.useProgram(waveProgram); gl.bindVertexArray(waveArray);
        if (count) {
          gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
          gl.bufferSubData(gl.ARRAY_BUFFER, 0, instances.subarray(0, count * 5));
          gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, count);
        }
        gl.disable(gl.BLEND); gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.useProgram(screenProgram); gl.bindVertexArray(screenArray);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, landscape);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, displacement);
        gl.uniform2f(uniforms.uResolution, width, height);
        gl.uniform2f(uniforms.uTexel, 1 / fieldWidth, 1 / fieldHeight);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        // The zero-wave pass leaves a complete static landscape; no idle GL work.
        lastFrame = count ? now : 0;
        if (count) wake();
      } catch (_) { fail("effect-error"); }
    }
    try {
      if (reducedMotion.matches) return { pause, resume() {}, destroy, isActive() { return false; } };
      waveProgram = program(rippleWaveVertex, rippleWaveFragment);
      screenProgram = program(rippleScreenVertex, rippleCompositeFragment);
      waveArray = allocate(arrays, gl.createVertexArray()); gl.bindVertexArray(waveArray);
      const quad = allocate(buffers, gl.createBuffer()); gl.bindBuffer(gl.ARRAY_BUFFER, quad);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      instanceBuffer = allocate(buffers, gl.createBuffer()); gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, instances.byteLength, gl.DYNAMIC_DRAW);
      for (const [location, size, offset] of [[1,2,0], [2,2,8], [3,1,16]]) {
        gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, size, gl.FLOAT, false, 20, offset);
        gl.vertexAttribDivisor(location, 1);
      }
      screenArray = allocate(arrays, gl.createVertexArray()); gl.bindVertexArray(screenArray);
      gl.activeTexture(gl.TEXTURE0); landscape = texture();
      // Canvas top becomes texture top: sun upper right, water below (cover crop).
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, getRippleLandscape());
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.activeTexture(gl.TEXTURE1); displacement = texture();
      framebuffer = allocate(framebuffers, gl.createFramebuffer());
      gl.useProgram(waveProgram); gl.uniform1f(gl.getUniformLocation(waveProgram, "uRings"), RIPPLE_RINGS);
      gl.useProgram(screenProgram);
      for (const name of ["uTexture", "uDisplacement", "uResolution", "uTextureSize", "uTexel", "uTint", "uHighlight",
        "uStrength", "uSwirl", "uDispersion", "uGlint", "uTintAmount", "uGrayscale"]) {
        uniforms[name] = gl.getUniformLocation(screenProgram, name);
      }
      gl.uniform1i(uniforms.uTexture, 0); gl.uniform1i(uniforms.uDisplacement, 1);
      gl.uniform2f(uniforms.uTextureSize, RIPPLE_TEXTURE_WIDTH, RIPPLE_TEXTURE_HEIGHT);
      const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
      gl.uniform3fv(uniforms.uTint, rgb(RIPPLE_TINT));
      gl.uniform3fv(uniforms.uHighlight, rgb(RIPPLE_HIGHLIGHT));
      for (const [name, value] of [["uStrength", RIPPLE_STRENGTH], ["uSwirl", RIPPLE_SWIRL],
        ["uDispersion", RIPPLE_DISPERSION], ["uGlint", RIPPLE_GLINT], ["uTintAmount", RIPPLE_TINT_AMOUNT],
        ["uGrayscale", Number(RIPPLE_GRAYSCALE)]]) gl.uniform1f(uniforms[name], value);
      window.addEventListener("pointermove", onPointerMove, { passive: true });
      window.addEventListener("pointerdown", onPointerDown, { passive: true });
      window.addEventListener("resize", onResize, { passive: true });
    } catch (error) { destroy(); throw error; }
    return {
      pause,
      resume() {
        if (destroyed || document.hidden || reducedMotion.matches) return;
        if (paused) {
          paused = false;
          // Capture sees scrolling inside panels too (scroll does not bubble).
          for (const type of activityEvents) window.addEventListener(type, onActivity, { passive: true, capture: true });
          window.visualViewport?.addEventListener("resize", onResize, { passive: true });
        }
        sizeDirty = true; lastFrame = 0; wake();
        scheduleAmbient(true);
      },
      destroy,
      isActive() { return Boolean(raf) && !paused && !destroyed; },
    };
  }

  for (const query of [reducedMotion, noHover, coarsePointer]) {
    if (query.addEventListener) query.addEventListener("change", reconcile);
    else query.addListener(reconcile);
  }
  document.addEventListener("visibilitychange", reconcile);
  registerEffect("ripple", createRipple);
  function init() {
    ready = true;
    reconcile();
  }
  publish("off");
  const afterFirstPaint = () => window.requestAnimationFrame(() => window.setTimeout(init, 0));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", afterFirstPaint, { once: true });
  else afterFirstPaint();
})();
