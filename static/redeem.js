"use strict";

/* 手动收款只展示站长收款码；兑换与管理写操作统一带 CSRF，所有响应按登录代次隔离。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const card = $("#admin-redeem-card");
  const redeemForm = $("#redeem-form");
  if (!card || !redeemForm) return;
  let hooks = null;
  let generation = 0;
  let planGeneration = 0;
  let adminGeneration = 0;
  let listGeneration = 0;
  let plainGeneration = 0;
  let redeemPending = false;
  let listStatus = "all";
  const pending = new Set();
  const previewUrls = new Map();
  const labels = { alipay: "支付宝", wechat: "微信" };
  const statuses = { unused: "未使用", redeemed: "已兑换", revoked: "已撤销", expired: "已过期" };

  function node(tag, text, className = "") {
    const result = document.createElement(tag);
    result.textContent = text;
    result.className = className;
    return result;
  }

  function status(id, text = "", error = false) {
    const target = $(id);
    target.textContent = text;
    target.classList.toggle("error", error);
  }

  function ticket(area) {
    return { generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id, area };
  }

  function current(request) {
    return hooks && request.generation === generation && request.epoch === hooks.getEpoch()
      && request.userId === hooks.getUser()?.id && Boolean(hooks.getUser())
      && hooks.getView() === request.area
      && (request.area !== "admin" || hooks.getUser().is_admin);
  }

  function date(value) {
    if (!value) return "—";
    return String(value).replace("T", " ").replace(/(?:\.\d+)?(?:Z|\+00:00)?$/, "");
  }

  function normalize(value) {
    return String(value).normalize("NFKC").replace(/[\s-]+/gu, "").toUpperCase();
  }

  function clearPlaintext() {
    plainGeneration += 1;
    $("#redeem-generated-codes").value = "";
    $("#redeem-generated").hidden = true;
    status("#redeem-copy-status");
  }

  function revokePreview(channel) {
    const previous = previewUrls.get(channel);
    if (previous) window.URL.revokeObjectURL(previous);
    previewUrls.delete(channel);
  }

  function reset() {
    generation += 1;
    planGeneration += 1;
    adminGeneration += 1;
    listGeneration += 1;
    redeemPending = false;
    pending.clear();
    clearPlaintext();
    $("#redeem-code").value = "";
    $("#redeem-submit").disabled = false;
    $("#redeem-submit").textContent = "兑换";
    redeemForm.dataset.state = "idle";
    status("#redeem-status");
    $("#redeem-panel").hidden = true;
    $("#redeem-trial-note").hidden = true;
    $("#manual-payment-panel").hidden = true;
    $("#manual-payment-contact").textContent = "";
    status("#manual-payment-status");
    $("#manual-payment-prices").replaceChildren();
    $("#manual-payment-qrs").replaceChildren();
    $("#redeem-code-list").replaceChildren();
    card.hidden = true;
    for (const channel of Object.keys(labels)) {
      revokePreview(channel);
      const image = $(`#manual-${channel}-preview`);
      image.removeAttribute("src");
      image.hidden = true;
      $(`#manual-${channel}-file`).value = "";
    }
    for (const id of ["#manual-contact", "#redeem-note", "#grant-username"]) $(id).value = "";
    $("#manual-enabled").checked = false;
    card.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    card.querySelectorAll('[role="status"]').forEach((target) => { target.textContent = ""; });
    const dialog = $("#manual-qr-dialog");
    if (dialog.open) dialog.close();
    $("#manual-qr-large").removeAttribute("src");
  }

  async function submitRedeem(event) {
    event.preventDefault();
    if (!hooks || redeemPending || !hooks.getUser() || hooks.getUser().is_trial || hooks.getView() !== "plan") return;
    const code = normalize($("#redeem-code").value);
    if (!code) {
      redeemForm.dataset.state = "failure";
      status("#redeem-status", "请输入兑换码", true);
      $("#redeem-code").focus();
      return;
    }
    const request = ticket("plan");
    redeemPending = true;
    redeemForm.dataset.state = "submitting";
    $("#redeem-submit").disabled = true;
    $("#redeem-submit").textContent = "兑换中…";
    status("#redeem-status", "正在兑换…");
    try {
      const result = await hooks.api("/api/redeem", { method: "POST", body: JSON.stringify({ code }) });
      if (!current(request)) return;
      $("#redeem-code").value = "";
      redeemForm.dataset.state = "success";
      const success = `套餐已开通，有效期至 ${String(result.plan_expires_at).slice(0, 10)}`;
      status("#redeem-status", success);
      try {
        await hooks.refreshPlanSubscription(() => current(request));
      } catch {
        if (current(request)) status("#redeem-status", `${success}；套餐展示暂未刷新，请稍后刷新页面。`);
      }
    } catch (error) {
      if (current(request)) {
        redeemForm.dataset.state = "failure";
        status("#redeem-status", error.message, true);
      }
    } finally {
      if (current(request)) {
        redeemPending = false;
        $("#redeem-submit").disabled = false;
        $("#redeem-submit").textContent = "兑换";
      }
    }
  }

  function openQr(channel, source) {
    const image = $("#manual-qr-large");
    image.src = source;
    image.alt = `${labels[channel]}个人收款码，放大查看`;
    $("#manual-qr-title").textContent = `${labels[channel]}收款码`;
    $("#manual-qr-dialog").showModal();
  }

  async function loadPlan(plans) {
    const request = ticket("plan");
    const sequence = ++planGeneration;
    if (!current(request)) return;
    $("#redeem-panel").hidden = Boolean(hooks.getUser().is_trial);
    $("#redeem-trial-note").hidden = !hooks.getUser().is_trial;
    $("#manual-payment-panel").hidden = true;
    try {
      const data = await hooks.api("/api/manual-payment");
      if (!current(request) || sequence !== planGeneration) return;
      if (!data.enabled || !Object.keys(labels).some((channel) => data.qr[channel])) return;
      const images = Object.keys(labels).filter((channel) => data.qr[channel]).map((channel) => {
        const button = node("button", "", "manual-qr-button");
        button.type = "button";
        button.setAttribute("aria-label", `放大查看${labels[channel]}个人收款码`);
        const image = document.createElement("img");
        image.src = `/api/manual-payment/qr/${channel}`;
        image.alt = `${labels[channel]}个人收款码，点击放大`;
        button.append(image, node("span", `${labels[channel]} · 点击放大`));
        button.addEventListener("click", () => openQr(channel, image.src));
        return button;
      });
      $("#manual-payment-qrs").replaceChildren(...images);
      $("#manual-payment-prices").replaceChildren(...plans.map((plan) => {
        const row = node("li", "");
        row.append(node("span", `${plan.name} · ${plan.period_days} 天`), node("strong", `¥${(plan.price_cents / 100).toFixed(2)}`));
        return row;
      }));
      $("#manual-payment-contact").textContent = data.contact;
      $("#manual-payment-panel").hidden = false;
      status("#manual-payment-status");
    } catch (error) {
      if (current(request) && sequence === planGeneration) status("#manual-payment-status", error.message, true);
    }
  }

  function populatePlans(plans) {
    for (const id of ["#redeem-plan", "#grant-plan"]) {
      const select = $(id);
      select.replaceChildren(...plans.map((plan) => {
        const option = node("option", `${plan.name}（${plan.period_days} 天）`);
        option.value = String(plan.id);
        return option;
      }));
      if (plans.length) select.value = String(plans[0].id);
    }
  }

  function applySettings(data) {
    $("#manual-enabled").checked = Boolean(data.enabled);
    $("#manual-contact").value = data.contact;
    for (const channel of Object.keys(labels)) {
      const image = $(`#manual-${channel}-preview`);
      if (!previewUrls.has(channel)) {
        image.hidden = !data.qr[channel];
        if (data.qr[channel]) image.src = `/api/manual-payment/qr/${channel}?v=${Date.now()}`;
        else image.removeAttribute("src");
      }
    }
  }

  async function loadCodes() {
    const request = ticket("admin");
    const sequence = ++listGeneration;
    status("#redeem-list-status", "正在加载兑换码…");
    try {
      const result = await hooks.api(`/api/admin/redeem-codes?status=${listStatus}&limit=50`);
      if (!current(request) || sequence !== listGeneration) return;
      const rows = result.codes.map((code) => {
        const row = node("article", "", "redeem-code-row");
        row.append(node("h5", `末 4 位 ${code.hint} · ${code.plan_name} · ${code.period_days} 天`));
        const facts = node("dl", "");
        for (const [label, value] of [["备注", code.note || "—"], ["状态", statuses[code.status] || code.status],
          ["创建时间", date(code.created_at)], ["过期时间", date(code.expires_at)],
          ["兑换人", code.redeemed_by || "—"], ["兑换时间", date(code.redeemed_at)]]) {
          facts.append(node("dt", label), node("dd", value));
        }
        row.append(facts);
        if (code.status === "unused") {
          const button = node("button", "撤销");
          button.type = "button";
          button.setAttribute("aria-label", `撤销末 4 位为 ${code.hint} 的兑换码`);
          button.addEventListener("click", () => {
            if (!window.confirm(`确认撤销末 4 位为 ${code.hint} 的兑换码？撤销后不能兑换。`)) return;
            adminAction(`revoke:${code.id}`, button, "#redeem-list-status", async () => {
              await hooks.api(`/api/admin/redeem-codes/${code.id}/revoke`, { method: "POST" });
              return "兑换码已撤销";
            }, loadCodes);
          });
          row.append(button);
        }
        return row;
      });
      $("#redeem-code-list").replaceChildren(...rows);
      status("#redeem-list-status", rows.length ? "" : "暂无兑换码。");
    } catch (error) {
      if (current(request) && sequence === listGeneration) status("#redeem-list-status", error.message, true);
    }
  }

  async function loadAdmin() {
    const request = ticket("admin");
    const sequence = ++adminGeneration;
    card.hidden = !current(request);
    if (!current(request)) return;
    clearPlaintext();
    status("#manual-settings-status", "正在加载…");
    const [settings, plans] = await Promise.allSettled([
      hooks.api("/api/manual-payment"), hooks.api("/api/plans"),
    ]);
    if (!current(request) || sequence !== adminGeneration) return;
    if (settings.status === "fulfilled") {
      applySettings(settings.value);
      status("#manual-settings-status");
    } else status("#manual-settings-status", settings.reason.message, true);
    if (plans.status === "fulfilled") populatePlans(plans.value.plans);
    else status("#redeem-generate-status", plans.reason.message, true);
    await loadCodes();
  }

  async function adminAction(key, button, statusId, operation, after) {
    if (!hooks || pending.has(key) || !hooks.getUser()?.is_admin || hooks.getView() !== "admin") return;
    const request = ticket("admin");
    pending.add(key);
    button.disabled = true;
    status(statusId, "正在处理…");
    try {
      const text = await operation(request);
      if (!current(request)) return;
      status(statusId, text || "操作成功");
      if (after) await after();
    } catch (error) {
      if (current(request)) status(statusId, error.message, true);
    } finally {
      if (current(request)) {
        pending.delete(key);
        button.disabled = false;
      }
    }
  }

  function optionalDays(id, field, body) {
    if ($(id).value.trim()) body[field] = Number($(id).value);
  }

  async function upload(channel, file) {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(`/api/admin/manual-payment/qr/${channel}`, {
      method: "PUT", credentials: "same-origin", headers: { "X-CSRF-Protection": "1" }, body,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "上传失败，请稍后再试");
    return data;
  }

  function validateFile(file) {
    if (!file) return "请先选择收款码图片";
    if (!["image/png", "image/jpeg"].includes(file.type)) return "请选择 PNG 或 JPEG 图片";
    if (file.size > 2 * 1024 * 1024) return "图片不能超过 2 MB";
    return "";
  }

  redeemForm.addEventListener("submit", submitRedeem);
  $("#redeem-code").addEventListener("input", (event) => {
    event.target.value = event.target.value.normalize("NFKC").toUpperCase();
  });
  $("#manual-qr-close").addEventListener("click", () => $("#manual-qr-dialog").close());
  $("#manual-qr-dialog").addEventListener("close", () => $("#manual-qr-large").removeAttribute("src"));

  $("#manual-settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    adminAction("settings", $("#manual-settings-save"), "#manual-settings-status", async () => {
      await hooks.api("/api/admin/manual-payment/settings", { method: "PUT", body: JSON.stringify({
        enabled: $("#manual-enabled").checked, contact: $("#manual-contact").value,
      }) });
      return "设置已保存";
    });
  });

  for (const channel of Object.keys(labels)) {
    $(`#manual-${channel}-file`).addEventListener("change", () => {
      const file = $(`#manual-${channel}-file`).files?.[0];
      const error = validateFile(file);
      status(`#manual-${channel}-status`, error, Boolean(error));
      if (error) return;
      revokePreview(channel);
      const url = window.URL.createObjectURL(file);
      previewUrls.set(channel, url);
      const image = $(`#manual-${channel}-preview`);
      image.src = url;
      image.hidden = false;
    });
    $(`#manual-${channel}-upload`).addEventListener("click", () => {
      const file = $(`#manual-${channel}-file`).files?.[0];
      const error = validateFile(file);
      if (error) { status(`#manual-${channel}-status`, error, true); return; }
      adminAction(`qr:${channel}`, $(`#manual-${channel}-upload`), `#manual-${channel}-status`, async (request) => {
        await upload(channel, file);
        if (current(request)) {
          revokePreview(channel);
          $(`#manual-${channel}-file`).value = "";
          $(`#manual-${channel}-preview`).src = `/api/manual-payment/qr/${channel}?v=${Date.now()}`;
          $(`#manual-${channel}-preview`).hidden = false;
        }
        return "收款码已上传";
      });
    });
    $(`#manual-${channel}-delete`).addEventListener("click", () => {
      if (!window.confirm(`确认删除${labels[channel]}收款码？`)) return;
      adminAction(`qr:${channel}`, $(`#manual-${channel}-delete`), `#manual-${channel}-status`, async (request) => {
        await hooks.api(`/api/admin/manual-payment/qr/${channel}`, { method: "DELETE" });
        if (current(request)) {
          revokePreview(channel);
          $(`#manual-${channel}-preview`).removeAttribute("src");
          $(`#manual-${channel}-preview`).hidden = true;
          $(`#manual-${channel}-file`).value = "";
        }
        return "收款码已删除";
      });
    });
  }

  $("#redeem-generate-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (pending.has("generate")) return;
    clearPlaintext();
    const plaintextTicket = plainGeneration;
    adminAction("generate", $("#redeem-generate"), "#redeem-generate-status", async (request) => {
      const body = { plan_id: Number($("#redeem-plan").value), count: Number($("#redeem-count").value), note: $("#redeem-note").value };
      optionalDays("#redeem-days", "days", body);
      optionalDays("#redeem-expiry", "expires_in_days", body);
      const result = await hooks.api("/api/admin/redeem-codes", { method: "POST", body: JSON.stringify(body) });
      if (current(request) && plaintextTicket === plainGeneration && card.open) {
        $("#redeem-generated-codes").value = result.codes.join("\n");
        $("#redeem-generated").hidden = false;
        return `已生成 ${result.codes.length} 个兑换码，请现在复制。`;
      }
      return "兑换码已生成；明文已清空，请勿重复发码。";
    }, loadCodes);
  });

  $("#redeem-copy").addEventListener("click", async () => {
    const request = ticket("admin");
    const sequence = plainGeneration;
    const text = $("#redeem-generated-codes").value;
    if (!text || !current(request)) return;
    try {
      await window.navigator.clipboard.writeText(text);
      if (current(request) && sequence === plainGeneration) status("#redeem-copy-status", "已复制全部兑换码");
    } catch {
      if (current(request) && sequence === plainGeneration) {
        const textarea = $("#redeem-generated-codes");
        textarea.focus();
        textarea.select();
        status("#redeem-copy-status", "自动复制失败，已选中全部兑换码；请按 Ctrl+C 或长按复制。", true);
      }
    }
  });
  $("#redeem-generated-close").addEventListener("click", clearPlaintext);
  $("#redeem-status-filters").querySelectorAll("[data-redeem-status]").forEach((button) => {
    button.addEventListener("click", () => {
      listStatus = button.dataset.redeemStatus;
      $("#redeem-status-filters").querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      loadCodes();
    });
  });
  $("#manual-grant-form").addEventListener("submit", (event) => {
    event.preventDefault();
    adminAction("grant", $("#manual-grant-submit"), "#manual-grant-status", async () => {
      const body = { username: $("#grant-username").value, plan_id: Number($("#grant-plan").value) };
      optionalDays("#grant-days", "days", body);
      const result = await hooks.api("/api/admin/manual-grant", { method: "POST", body: JSON.stringify(body) });
      return `已为 ${result.username} 开通 ${result.plan_name}，有效期至 ${String(result.plan_expires_at).slice(0, 10)}`;
    }, loadCodes);
  });
  card.addEventListener("toggle", () => { if (!card.open) clearPlaintext(); });
  card.addEventListener("focusout", (event) => {
    if (event.relatedTarget && !card.contains(event.relatedTarget)) clearPlaintext();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!card.contains(event.target)) clearPlaintext();
  });
  document.addEventListener("app:view-changed", (event) => {
    generation += 1;
    pending.clear();
    if (event.detail.view !== "admin") clearPlaintext();
    card.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    if (event.detail.view !== "plan") {
      planGeneration += 1;
      redeemPending = false;
      $("#redeem-submit").disabled = false;
      $("#redeem-submit").textContent = "兑换";
      status("#redeem-status");
      redeemForm.dataset.state = "idle";
    }
  });
  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(() => {
      if (document.documentElement.dataset.view !== "app") reset();
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-view"] });
  }
  window.Redeem = { configure: (value) => { hooks = value; }, loadPlan, loadAdmin, reset };
})();
