"use strict";

/* 付款登记（半自动手动收款）：用户在套餐页登记"我已付款"，管理员确认收款后由后端开通套餐。
   对外契约：
     window.ManualClaims      = { configure({ api, getUser, getEpoch }), mount(container), refresh(), reset() }
     window.ManualClaimsAdmin = { configure({ api, getUser, getEpoch }), mount(container), refresh(), reset() }
   迟到响应守卫：每个请求记下当时的 generation / getEpoch() / getUser().id / 请求序号；
   登出再登录、换账号、重新挂载、容器被移除（面板关闭）、管理员切换状态或页码之后才返回的响应一律丢弃。
   一切来自服务器的文字都用 textContent；状态用文字加符号表示，不只靠颜色。 */
(() => {
  const NOTE_LIMIT = 60;
  const CONTACT_LIMIT = 60;
  const REASON_LIMIT = 80;
  const FLASH_MS = 12000;

  /* 状态 → 符号 + 文字（颜色只是辅助，语义由文字和符号承担）。 */
  const STATUS_META = {
    pending: { symbol: "⏳", text: "待确认", className: "is-pending" },
    confirmed: { symbol: "✓", text: "已开通", className: "is-confirmed" },
    rejected: { symbol: "✕", text: "已驳回", className: "is-rejected" },
  };

  const FILTERS = [
    ["pending", "待确认"],
    ["confirmed", "已开通"],
    ["rejected", "已驳回"],
    ["all", "全部"],
  ];

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function button(label, className, onClick) {
    const item = node("button", className, label);
    item.type = "button";
    item.addEventListener("click", onClick);
    return item;
  }

  function failure(error) {
    return error?.message || "请求失败，请检查网络后重试。";
  }

  function date(value) {
    if (!value) return "—";
    return String(value).replace("T", " ").replace(/(?:\.\d+)?(?:Z|\+00:00)?$/, "");
  }

  function statusMeta(status) {
    return STATUS_META[status] || { symbol: "•", text: String(status ?? "未知状态"), className: "is-pending" };
  }

  /** 状态胶囊：符号（装饰）+ 文字（语义）。 */
  function statePill(status, prefix) {
    const meta = statusMeta(status);
    const pill = node("span", `${prefix}-state ${meta.className}`);
    const symbol = node("span", `${prefix}-state-symbol`, meta.symbol);
    symbol.setAttribute("aria-hidden", "true");
    pill.append(symbol, node("span", "", meta.text));
    return pill;
  }

  function facts(prefix, pairs) {
    const list = node("dl", `${prefix}-facts`);
    for (const [label, value] of pairs) {
      list.append(node("dt", "", label), node("dd", "", value));
    }
    return list;
  }

  function snapshot(claim) {
    const values = [claim.amount_cents, claim.period_days, claim.plan_name_snapshot];
    if (values.every((value) => value == null)) return "legacy";
    return Number.isSafeInteger(values[0]) && values[0] > 0
      && Number.isSafeInteger(values[1]) && values[1] > 0
      && typeof values[2] === "string" && values[2].trim() ? "fixed" : "invalid";
  }

  function money(cents) {
    return `¥${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
  }

  function snapshotPairs(claim) {
    if (snapshot(claim) === "fixed") return [
      ["登记套餐", claim.plan_name_snapshot], ["登记金额", money(claim.amount_cents)],
      ["登记周期", `${claim.period_days} 天`],
    ];
    return [["历史登记", snapshot(claim) === "legacy"
      ? "原套餐名称、金额与周期未知，需站长人工核账；当前套餐价格不能代替历史金额。"
      : "登记信息不完整，请站长检查，暂不能确认。"]];
  }

  function centsFromInput(value) {
    const text = value.trim();
    if (!/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$/.test(text)) return null;
    const [whole, fraction = ""] = text.split(".");
    const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
    return Number.isSafeInteger(cents) && cents > 0 ? cents : null;
  }

  /* ==================== 用户端 ==================== */
  const ManualClaims = (() => {
    let hooks = null;
    let generation = 0; // reset / 重新挂载时加一，旧请求的响应全部作废
    let listSeq = 0;
    let container = null;
    let els = null;
    let plans = []; // 缓存：重挂载时先画旧数据，再等新响应
    let claims = null; // null 表示还没有取到过
    let submitting = false;
    let flashTimer = 0;

    function usable() {
      return Boolean(hooks?.api && hooks.getUser());
    }

    function ticket(name) {
      if (name === "list") listSeq += 1;
      return {
        name, generation, sequence: listSeq,
        epoch: hooks.getEpoch(), userId: hooks.getUser()?.id,
      };
    }

    function current(request) {
      return Boolean(hooks && hooks.getUser() && container?.isConnected
        && request.generation === generation
        && request.epoch === hooks.getEpoch()
        && request.userId === hooks.getUser()?.id
        && (request.name !== "list" || request.sequence === listSeq));
    }

    function setBusy(busy) {
      els?.listSection?.setAttribute("aria-busy", busy ? "true" : "false");
    }

    function clearFlash() {
      if (flashTimer) {
        window.clearTimeout(flashTimer);
        flashTimer = 0;
      }
    }

    function flash(text) {
      clearFlash();
      els.status.textContent = text;
      els.status.classList.remove("error");
      const request = { generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
      flashTimer = window.setTimeout(() => {
        flashTimer = 0;
        if (!hooks || !container?.isConnected) return;
        if (request.generation !== generation || request.epoch !== hooks.getEpoch()) return;
        if (request.userId !== hooks.getUser()?.id) return;
        els.status.textContent = "";
      }, FLASH_MS);
    }

    function renderPlans() {
      const select = els.plan;
      const wanted = select.value;
      select.replaceChildren(...plans.map((plan) => {
        const option = node("option", "", `${plan.name}（${plan.price_text}）`);
        option.value = String(plan.id);
        return option;
      }));
      if (plans.some((plan) => String(plan.id) === wanted)) select.value = wanted;
      else if (plans.length) select.value = String(plans[0].id);
      els.submit.disabled = submitting || !plans.length;
      els.noPlans.hidden = plans.length > 0;
    }

    function renderClaims() {
      const list = els.list;
      list.replaceChildren(...(claims || []).map((claim) => {
        const row = node("li", `mc-claim ${statusMeta(claim.status).className}`);
        const head = node("div", "mc-claim-head");
        head.append(node("span", "mc-claim-plan", snapshot(claim) === "fixed" ? claim.plan_name_snapshot : "历史登记（套餐信息未知）"), statePill(claim.status, "mc"));
        row.append(head);
        const pairs = [...snapshotPairs(claim),
          ["付款备注", String(claim.payer_note ?? "")],
          ["联系方式", claim.contact ? String(claim.contact) : "—"],
          ["提交时间", date(claim.created_at)],
        ];
        if (claim.status === "rejected" && claim.reject_reason) pairs.push(["驳回原因", String(claim.reject_reason)]);
        if (claim.decided_at) pairs.push(["处理时间", date(claim.decided_at)]);
        row.append(facts("mc", pairs));
        return row;
      }));
      els.listStatus.textContent = claims?.length ? "" : "还没有付款登记。";
    }

    function buildDom(target) {
      const panel = node("section", "mc-panel", "");
      panel.setAttribute("aria-label", "登记付款");
      const form = node("form", "mc-form");
      const planLabel = node("label", "", "选择套餐");
      const plan = node("select", "mc-plan");
      planLabel.append(plan);
      const noteLabel = node("label", "", "付款备注");
      const note = node("input", "mc-note");
      note.type = "text";
      note.setAttribute("maxlength", String(NOTE_LIMIT));
      note.setAttribute("autocomplete", "off");
      note.setAttribute("placeholder", "微信 / 支付宝昵称，或转账单号后 4 位");
      noteLabel.append(note);
      const hint = node("p", "mc-hint", "填你付款用的微信 / 支付宝昵称，或转账单号后 4 位，方便站长在账单里核对你这笔款。");
      const contactLabel = node("label", "", "联系方式（可选）");
      const contact = node("input", "mc-contact");
      contact.type = "text";
      contact.setAttribute("maxlength", String(CONTACT_LIMIT));
      contact.setAttribute("autocomplete", "off");
      contact.setAttribute("placeholder", "微信号或手机号，核对不上时站长好找你");
      contactLabel.append(contact);
      const noPlans = node("p", "mc-hint", "暂时没有可登记的套餐，请稍后再试。");
      noPlans.hidden = true;
      const submit = node("button", "mc-submit primary", "我已付款，登记");
      submit.type = "submit";
      const status = node("p", "mc-status");
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      form.append(planLabel, noteLabel, hint, contactLabel, noPlans, submit, status);
      panel.append(form);
      form.addEventListener("submit", submitClaim);

      const listSection = node("section", "mc-list-section", "");
      listSection.setAttribute("aria-label", "我的付款登记");
      listSection.append(node("h4", "mc-list-title", "我的付款登记"));
      const list = node("ul", "mc-list");
      const listStatus = node("p", "mc-list-status");
      listStatus.setAttribute("role", "status");
      listSection.append(list, listStatus);

      target.replaceChildren(panel, listSection);
      els = { form, plan, note, contact, noPlans, submit, status, listSection, list, listStatus };
    }

    async function load() {
      if (!usable() || !els) {
        if (els) els.listStatus.textContent = "登录后可以登记付款。";
        return false;
      }
      const request = ticket("list");
      setBusy(true);
      els.listStatus.textContent = "正在加载付款登记…";
      try {
        const data = await hooks.api("/api/manual-claims");
        if (!current(request)) return false;
        plans = Array.isArray(data?.plans) ? data.plans : [];
        claims = Array.isArray(data?.claims) ? data.claims : [];
        renderPlans();
        renderClaims();
        return true;
      } catch (error) {
        if (!current(request)) return false;
        els.listStatus.textContent = failure(error);
        return false;
      } finally {
        if (current(request)) setBusy(false);
      }
    }

    async function submitClaim(event) {
      event.preventDefault();
      if (!usable() || submitting || !els || !container?.isConnected) return;
      const note = els.note.value.trim();
      const contact = els.contact.value.trim();
      const invalid = (message, field) => {
        els.status.textContent = message;
        els.status.classList.add("error");
        field.focus();
      };
      if (!plans.length) return invalid("暂时没有可登记的套餐，请稍后再试。", els.submit);
      if (!note) return invalid("请填付款备注：微信 / 支付宝昵称，或转账单号后 4 位。", els.note);
      if (note.length > NOTE_LIMIT) return invalid(`付款备注不能超过 ${NOTE_LIMIT} 个字。`, els.note);
      if (contact.length > CONTACT_LIMIT) return invalid(`联系方式不能超过 ${CONTACT_LIMIT} 个字。`, els.contact);
      const request = { generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
      const stillCurrent = () => Boolean(hooks && hooks.getUser() && container?.isConnected
        && request.generation === generation && request.epoch === hooks.getEpoch()
        && request.userId === hooks.getUser()?.id);
      submitting = true;
      els.submit.disabled = true;
      els.submit.textContent = "正在登记…";
      els.status.classList.remove("error");
      els.status.textContent = "正在提交…";
      try {
        await hooks.api("/api/manual-claims", {
          method: "POST",
          body: JSON.stringify({ plan_id: /^[0-9]+$/.test(els.plan.value) ? Number(els.plan.value) : els.plan.value, payer_note: note, contact }),
        });
        if (!stillCurrent()) return;
        els.note.value = "";
        els.contact.value = "";
        flash("已收到，站长确认后会自动开通。");
        await load();
      } catch (error) {
        if (!stillCurrent()) return;
        els.status.textContent = failure(error);
        els.status.classList.add("error");
      } finally {
        if (stillCurrent()) {
          submitting = false;
          els.submit.disabled = !plans.length;
          els.submit.textContent = "我已付款，登记";
        }
      }
    }

    function mount(target) {
      generation += 1;
      listSeq += 1;
      submitting = false;
      clearFlash();
      container = target || null;
      if (!container) return Promise.resolve(false);
      buildDom(container);
      renderPlans();
      if (claims) renderClaims();
      return load();
    }

    function refresh() {
      if (!els) return Promise.resolve(false);
      return load();
    }

    function reset() {
      generation += 1;
      listSeq += 1;
      submitting = false;
      clearFlash();
      plans = [];
      claims = null;
      if (container) container.replaceChildren();
      container = null;
      els = null;
    }

    return { configure: (value) => { hooks = value; }, mount, refresh, reset };
  })();

  /* ==================== 管理员端 ==================== */
  const ManualClaimsAdmin = (() => {
    let hooks = null;
    let generation = 0;
    let listSeq = 0;
    let container = null;
    let els = null;
    let filter = "pending";
    let page = 1;
    let pages = 1;
    let claims = null;
    const pending = new Set(); // 正在处理中的登记 id：防重复点击

    function usable() {
      return Boolean(hooks?.api && hooks.getUser());
    }

    function ticket(name) {
      if (name === "list") listSeq += 1;
      return {
        name, generation, sequence: listSeq,
        epoch: hooks.getEpoch(), userId: hooks.getUser()?.id,
      };
    }

    /* 列表响应必须是最新一次（换状态 / 翻页后旧响应作废）；确认、驳回动作只要求
       同一代次、同一账号（pending 集合已经挡住了同一登记的重复提交）。 */
    function current(request) {
      return Boolean(hooks && hooks.getUser() && container?.isConnected
        && request.generation === generation
        && request.epoch === hooks.getEpoch()
        && request.userId === hooks.getUser()?.id
        && (request.name !== "list" || request.sequence === listSeq));
    }

    function setStatus(text, error = false) {
      if (!els) return;
      els.status.textContent = text;
      els.status.classList.toggle("error", Boolean(error));
    }

    function claimPairs(claim) {
      const pairs = [
        ...snapshotPairs(claim),
        ["付款备注", String(claim.payer_note ?? "")],
        ["联系方式", claim.contact ? String(claim.contact) : "—"],
        ["提交时间", date(claim.created_at)],
      ];
      if (claim.status === "rejected" && claim.reject_reason) pairs.push(["驳回原因", String(claim.reject_reason)]);
      if (claim.decided_at) pairs.push(["处理时间", date(claim.decided_at)]);
      if (Number.isSafeInteger(claim.verified_amount_cents)) pairs.push(["核对实收", money(claim.verified_amount_cents)]);
      if (claim.receipt_reference) pairs.push(["核账流水", String(claim.receipt_reference)]);
      return pairs;
    }

    function rowOf(id) {
      return els?.list.querySelector(`[data-claim-id="${id}"]`) || null;
    }

    function setRowBusy(id, busy) {
      rowOf(id)?.querySelectorAll("button, input").forEach((item) => { item.disabled = busy; });
    }

    function closePanels(except) {
      els?.list.querySelectorAll(".mca-confirm, .mca-reject-form").forEach((panel) => {
        if (panel !== except) panel.hidden = true;
      });
    }

    async function decide(claim, action, body) {
      const id = claim.id;
      if (!usable() || pending.has(id)) return;
      const request = ticket("action");
      pending.add(id);
      setRowBusy(id, true);
      setStatus("正在处理…");
      const path = `/api/admin/manual-claims/${id}/${action}`;
      const init = { method: "POST", body: JSON.stringify(action === "reject" ? { reason: body } : body) };
      try {
        await hooks.api(path, init);
        if (!current(request)) return;
        await load(); // 先刷新列表，再写结果提示，避免被加载中的状态文本覆盖
        if (!current(request)) return;
        pending.delete(id);
        setStatus(action === "confirm" ? "已确认收款，套餐会自动开通。" : "已驳回。");
      } catch (error) {
        if (!current(request)) return;
        if (error?.status === 409 && error.message === "这条登记已经处理过了") {
          await load();
          if (!current(request)) return;
          pending.delete(id);
          setStatus("这条登记已被处理，列表已刷新。");
          return;
        }
        setStatus(failure(error), true);
        pending.delete(id);
        setRowBusy(id, false);
      }
    }

    function adminRow(claim) {
      const id = claim.id;
      const row = node("li", `mca-claim ${statusMeta(claim.status).className}`);
      row.dataset.claimId = String(id);
      const head = node("div", "mca-claim-head");
      head.append(node("span", "mca-claim-user", String(claim.username ?? "")), statePill(claim.status, "mca"));
      row.append(head, facts("mca", claimPairs(claim)));
      if (claim.status !== "pending") return row;

      const name = String(claim.username ?? "");
      const actions = node("div", "mca-actions");
      const confirmPanel = node("div", "mca-confirm");
      confirmPanel.hidden = true;
      confirmPanel.setAttribute("role", "group");
      confirmPanel.setAttribute("aria-label", `确认 ${name} 的收款`);
      const mode = snapshot(claim);
      const amountLabel = node("label", "", "账单实际到账金额（元，必须手工填写）");
      const amount = node("input", "mca-amount");
      amount.type = "text";
      amount.setAttribute("inputmode", "decimal");
      amount.setAttribute("autocomplete", "off");
      amountLabel.append(amount);
      const receiptLabel = node("label", "", "完整到账流水（alipay: 或 wechat: 开头）");
      const receipt = node("input", "mca-receipt");
      receipt.type = "text";
      receipt.setAttribute("maxlength", "127");
      receipt.setAttribute("autocomplete", "off");
      receiptLabel.append(receipt);
      const confirmationError = node("p", "mca-confirm-error");
      confirmationError.setAttribute("role", "alert");
      const legacyReview = node("input", "mca-legacy-reviewed");
      legacyReview.type = "checkbox";
      const legacyLabel = node("label", "mca-legacy-check");
      legacyLabel.append(legacyReview, node("span", "", "我已人工核查历史账单；原登记金额和周期未知。"));
      const daysLabel = node("label", "", "人工核定开通天数（1–3650，不能代作原登记周期）");
      const days = node("input", "mca-legacy-days");
      days.type = "text";
      days.setAttribute("inputmode", "numeric");
      daysLabel.append(days);
      const confirmYes = button("我已核对，确认收款", "primary", () => {
        const invalid = (text, field) => { confirmationError.textContent = text; field.focus(); };
        if (mode === "invalid") return invalid("登记信息不完整，暂不能确认。", amount);
        const cents = centsFromInput(amount.value);
        if (cents === null) return invalid("请输入大于 0 的到账金额，最多两位小数，不接受科学计数法。", amount);
        if (mode === "fixed" && cents !== claim.amount_cents) return invalid("实际到账金额必须与登记金额一致。", amount);
        const reference = receipt.value.replace(/^ +| +$/g, "");
        if (/[^\x20-\x7E]/.test(receipt.value) || !/^(?:alipay|wechat):[A-Za-z0-9_-]{1,120}$/.test(reference)) return invalid("请填写 alipay: 或 wechat: 开头的完整到账流水号，不能填昵称。", receipt);
        const payload = { verified_amount_cents: cents, receipt_reference: reference };
        if (mode === "legacy") {
          if (!legacyReview.checked) return invalid("请先确认已经人工核查历史账单。", legacyReview);
          const text = days.value.trim();
          const period = /^[1-9][0-9]*$/.test(text) ? Number(text) : 0;
          if (!Number.isSafeInteger(period) || period < 1 || period > 3650) return invalid("请手工填写 1–3650 的开通天数。", days);
          payload.legacy_reviewed = true;
          payload.legacy_period_days = period;
        }
        confirmationError.textContent = "";
        decide(claim, "confirm", payload);
      });
      const confirmNo = button("取消", "", () => { confirmPanel.hidden = true; openConfirm.focus(); });
      const confirmButtons = node("div", "mca-confirm-buttons", "");
      confirmButtons.append(confirmYes, confirmNo);
      confirmPanel.append(
        node("p", "mca-confirm-text", "确认已在微信/支付宝账单里核对到这笔款项？"),
        amountLabel, receiptLabel,
      );
      if (mode === "legacy") confirmPanel.append(legacyLabel, daysLabel);
      confirmPanel.append(confirmationError, confirmButtons);

      const rejectPanel = node("div", "mca-reject-form");
      rejectPanel.hidden = true;
      const reasonLabel = node("label", "", "驳回原因（会展示给用户）");
      const reasonInput = node("input", "mca-reason");
      reasonInput.type = "text";
      reasonInput.setAttribute("maxlength", String(REASON_LIMIT));
      reasonLabel.append(reasonInput);
      const reasonError = node("p", "mca-reject-error");
      reasonError.setAttribute("role", "alert");
      const rejectYes = button("确认驳回", "danger", () => {
        const reason = reasonInput.value.trim();
        if (!reason) {
          reasonError.textContent = "请填驳回原因，用户需要知道为什么。";
          reasonInput.focus();
          return;
        }
        if (reason.length > REASON_LIMIT) {
          reasonError.textContent = `驳回原因不能超过 ${REASON_LIMIT} 个字。`;
          reasonInput.focus();
          return;
        }
        reasonError.textContent = "";
        decide(claim, "reject", reason);
      });
      const rejectNo = button("取消", "", () => { rejectPanel.hidden = true; openReject.focus(); });
      const rejectButtons = node("div", "mca-reject-buttons", "");
      rejectButtons.append(rejectYes, rejectNo);
      rejectPanel.append(reasonLabel, reasonError, rejectButtons);

      const openConfirm = button("确认收款", "", () => {
        closePanels(confirmPanel);
        confirmPanel.hidden = false;
        confirmYes.focus();
      });
      openConfirm.setAttribute("aria-label", `确认收款：${name} 的 ${mode === "fixed" ? claim.plan_name_snapshot : "历史登记（套餐信息未知）"}`);
      const openReject = button("驳回", "", () => {
        closePanels(rejectPanel);
        rejectPanel.hidden = false;
        reasonInput.focus();
      });
      openReject.setAttribute("aria-label", `驳回：${name} 的 ${mode === "fixed" ? claim.plan_name_snapshot : "历史登记（套餐信息未知）"}`);
      actions.append(openConfirm, openReject);
      row.append(actions, confirmPanel, rejectPanel);
      return row;
    }

    function renderFilters() {
      els.filters.querySelectorAll("button").forEach((item) => {
        item.setAttribute("aria-pressed", String(item.dataset.status === filter));
      });
    }

    function renderList() {
      els.list.replaceChildren(...(claims || []).map(adminRow));
      if (!claims?.length) {
        setStatus(filter === "pending" ? "没有待确认的付款登记。" : "这个状态下没有付款登记。");
      } else {
        setStatus("");
      }
      els.pageInfo.textContent = `第 ${page} / ${pages} 页`;
      els.prev.disabled = page <= 1;
      els.next.disabled = page >= pages;
    }

    function buildDom(target) {
      const root = node("section", "mca", "");
      root.setAttribute("aria-label", "付款登记处理队列");
      const filters = node("div", "mca-filters", "");
      filters.setAttribute("role", "group");
      filters.setAttribute("aria-label", "按状态筛选");
      for (const [value, label] of FILTERS) {
        const item = button(label, "", () => {
          if (filter === value || !usable()) return;
          filter = value;
          page = 1;
          renderFilters();
          load();
        });
        item.dataset.status = value;
        filters.append(item);
      }
      const status = node("p", "mca-status");
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      const list = node("ul", "mca-list");
      const pager = node("div", "mca-pages", "");
      const prev = button("上一页", "", () => { if (page > 1) { page -= 1; load(); } });
      const next = button("下一页", "", () => { if (page < pages) { page += 1; load(); } });
      const pageInfo = node("span", "mca-page-info", "第 1 / 1 页");
      pager.append(prev, pageInfo, next);
      root.append(filters, status, list, pager);
      target.replaceChildren(root);
      els = { root, filters, status, list, prev, next, pageInfo };
      renderFilters();
    }

    async function load() {
      if (!usable() || !els) {
        if (els) setStatus("请使用管理员账号登录。", true);
        return false;
      }
      const request = ticket("list");
      els.root.setAttribute("aria-busy", "true");
      setStatus("正在加载付款登记…");
      try {
        const data = await hooks.api(`/api/admin/manual-claims?status=${encodeURIComponent(filter)}&page=${page}`);
        if (!current(request)) return false;
        claims = Array.isArray(data?.claims) ? data.claims : [];
        page = Number.isInteger(data?.page) && data.page > 0 ? data.page : 1;
        pages = Number.isInteger(data?.pages) && data.pages > 0 ? data.pages : 1;
        renderList();
        return true;
      } catch (error) {
        if (!current(request)) return false;
        setStatus(failure(error), true);
        return false;
      } finally {
        if (current(request)) els.root.setAttribute("aria-busy", "false");
      }
    }

    function mount(target) {
      generation += 1;
      listSeq += 1;
      pending.clear();
      filter = "pending";
      page = 1;
      pages = 1;
      container = target || null;
      if (!container) return Promise.resolve(false);
      buildDom(container);
      if (claims) renderList();
      return load();
    }

    function refresh() {
      if (!els) return Promise.resolve(false);
      return load();
    }

    function reset() {
      generation += 1;
      listSeq += 1;
      pending.clear();
      filter = "pending";
      page = 1;
      pages = 1;
      claims = null;
      if (container) container.replaceChildren();
      container = null;
      els = null;
    }

    return { configure: (value) => { hooks = value; }, mount, refresh, reset };
  })();

  window.ManualClaims = ManualClaims;
  window.ManualClaimsAdmin = ManualClaimsAdmin;
})();
