// Real Chromium Canvas regression: node tests/test_motion_pixels.cjs
// No npm packages. Set CHROME_PATH if Chrome/Edge is installed elsewhere.
// --diagnose prints measurements without assertions; --preview <path> saves a PNG.
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const { existsSync, readFileSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const source = readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const start = source.indexOf("function initMarbleBackground()");
assert.ok(start >= 0);
const effect = source.slice(start);
const css = readFileSync(path.join(__dirname, "../static/style.css"), "utf8");
const opacity = Number(css.match(/\.marble-background\s*\{[^}]*opacity:\s*([\d.]+)/)?.[1]);
assert.ok(opacity > 0 && opacity <= 1);

// Only animation scheduling and environment are controlled; every gradient,
// composite, drawImage and getImageData call runs in Chromium's real Canvas 2D.
function measure(sourceCode, cssOpacity, blend) {
  document.body.replaceChildren();
  const canvases = [];
  let nextFrame;
  const env = {
    innerWidth: 1440, innerHeight: 900,
    matchMedia: (query) => ({
      matches: query === "(pointer: fine)",
      addEventListener() {}, removeEventListener() {},
    }),
    requestAnimationFrame(callback) { nextFrame = callback; return 1; },
    cancelAnimationFrame() {}, addEventListener() {}, removeEventListener() {},
  };
  const doc = {
    body: document.body, hidden: false,
    addEventListener() {}, removeEventListener() {},
    createElement(tag) {
      const node = document.createElement(tag);
      canvases.push(node);
      return node;
    },
  };
  const code = blend === "source-over"
    ? sourceCode.replace('ctx.globalCompositeOperation = "multiply";', 'ctx.globalCompositeOperation = "source-over";')
    : sourceCode;
  new Function("window", "document", code)(env, doc);
  if (typeof nextFrame !== "function") throw new Error("Effect did not schedule a frame");
  nextFrame(100);
  const output = document.querySelector(".marble-background");
  const texture = canvases[1];
  const point = (canvas, x, y) => Array.from(canvas.getContext("2d").getImageData(
    Math.min(canvas.width - 1, Math.floor(canvas.width * x)),
    Math.min(canvas.height - 1, Math.floor(canvas.height * y)), 1, 1,
  ).data);
  const stats = (canvas) => {
    const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    let sum = 0, visible = 0, min = 255, max = 0;
    for (let i = 3; i < pixels.length; i += 4) {
      const alpha = pixels[i];
      sum += alpha;
      min = Math.min(min, alpha);
      max = Math.max(max, alpha);
      if (alpha >= 32) visible += 1;
    }
    const count = pixels.length / 4;
    return { minAlpha: min, maxAlpha: max, meanAlpha: +(sum / count).toFixed(2),
      coverageAlpha32: +(visible / count).toFixed(3) };
  };
  const composite = document.createElement("canvas");
  composite.width = output.width;
  composite.height = output.height;
  const ctx = composite.getContext("2d");
  ctx.fillStyle = "#faf9f5";
  ctx.fillRect(0, 0, composite.width, composite.height);
  ctx.globalAlpha = cssOpacity;
  ctx.drawImage(output, 0, 0);
  const samples = Object.fromEntries([
    ["center", .5, .5], ["topLeft", .2, .2], ["topRight", .8, .2],
    ["bottomLeft", .2, .8], ["bottomRight", .8, .8],
  ].map(([name, x, y]) => [name, point(output, x, y)]));
  return { blend, textureCenter: point(texture, .5, .5), texture: stats(texture),
    output: { width: output.width, height: output.height, ...stats(output) },
    samples, compositedCenter: point(composite, .5, .5),
    preview: composite.toDataURL("image/png") };
}

async function main() {
  const browserPath = [process.env.CHROME_PATH,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/chromium", "/usr/bin/google-chrome",
  ].find((candidate) => candidate && existsSync(candidate));
  assert.ok(browserPath, "Install no packages: set CHROME_PATH to an existing Chromium browser");
  // Use the OS temp directory only. Never fall back to the repository on failure.
  const tempRoot = path.resolve(os.tmpdir());
  const profile = await fs.mkdtemp(path.join(tempRoot, "marble-pixels-"));
  let browser, socket;
  let browserLog = "";
  const pending = new Map();
  let sequence = 0;
  const cdp = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    pending.set(id, {
      resolve: (value) => { clearTimeout(timer); resolve(value); },
      reject: (error) => { clearTimeout(timer); reject(error); },
    });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  try {
    browser = spawn(browserPath, ["--headless", "--disable-gpu", "--in-process-gpu", "--no-first-run",
      // Optional for an already sandboxed runner that cannot launch Chromium's sandbox.
      ...(process.argv.includes("--no-browser-sandbox") ? ["--no-sandbox"] : []),
      "--no-default-browser-check", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
      "--disable-background-networking", "about:blank"], { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
    const endpoint = await new Promise((resolve, reject) => {
      let stderr = "";
      const timer = setTimeout(() => reject(new Error(`Chrome startup timed out: ${stderr.slice(-1200)}`)), 15000);
      browser.once("error", (error) => { clearTimeout(timer); reject(error); });
      browser.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Chrome exited (${code}): ${stderr.slice(-1200)}`)); });
      browser.stderr.on("data", (chunk) => {
        stderr += chunk;
        browserLog = stderr;
        const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) { clearTimeout(timer); resolve(match[1]); }
      });
    });
    socket = new WebSocket(endpoint);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      const request = pending.get(message.id);
      if (!request) return;
      pending.delete(message.id);
      if (message.error) request.reject(new Error(JSON.stringify(message.error)));
      else request.resolve(message.result);
    });
    const { product } = await cdp("Browser.getVersion");
    const { targetId } = await cdp("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp("Target.attachToTarget", { targetId, flatten: true });
    const reports = [];
    for (const blend of ["multiply", "source-over"]) {
      const { result, exceptionDetails } = await cdp("Runtime.evaluate", {
        expression: `(${measure.toString()})(${JSON.stringify(effect)}, ${opacity}, ${JSON.stringify(blend)})`,
        returnByValue: true,
      }, sessionId);
      if (exceptionDetails) throw new Error(JSON.stringify(exceptionDetails));
      const { preview, ...report } = result.value;
      const previewIndex = process.argv.indexOf("--preview");
      if (blend === "multiply" && previewIndex !== -1) {
        assert.ok(process.argv[previewIndex + 1], "--preview requires a path");
        await fs.writeFile(process.argv[previewIndex + 1], Buffer.from(preview.split(",")[1], "base64"));
      }
      reports.push(report);
    }
    console.log(JSON.stringify({ browser: product, cssOpacity: opacity, reports }, null, 2));
    if (!process.argv.includes("--diagnose")) {
      const actual = reports[0];
      assert.ok(actual.samples.center[3] >= 40, "Center must have visible alpha before CSS opacity");
      assert.ok(actual.output.coverageAlpha32 >= .75, "Soft color should cover most of the viewport");
      for (const [name, pixel] of Object.entries(actual.samples)) {
        assert.ok(pixel[3] >= 32, `${name} must not fall into a transparent gap`);
      }
      const paper = [250, 249, 245];
      const difference = Math.max(...paper.map((value, i) => Math.abs(value - actual.compositedCenter[i])));
      assert.ok(difference >= 10, "Color must remain visible after the real CSS opacity");
      assert.ok(Math.min(...actual.compositedCenter.slice(0, 3)) >= 210, "Background stays light");
      // The blend comparison is diagnostic only: Chromium's different compositing
      // paths can accumulate different 8-bit rounding across the 126 soft daubs.
      console.log("Canvas pixel regression passed.");
    }
  } catch (error) {
    if (browserLog) console.error(browserLog.slice(-3000));
    throw error;
  } finally {
    if (socket?.readyState === WebSocket.OPEN) {
      await cdp("Browser.close").catch(() => {});
      socket.close();
    }
    if (browser && browser.exitCode === null) {
      await new Promise((resolve) => {
        const timer = setTimeout(() => { browser.kill(); resolve(); }, 3000);
        browser.once("exit", () => { clearTimeout(timer); resolve(); });
      });
    }
    // Only remove this test's freshly created directory, after checking its boundary.
    assert.equal(path.dirname(path.resolve(profile)), tempRoot);
    assert.ok(path.basename(profile).startsWith("marble-pixels-"));
    await fs.rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
