"use strict";

const $ = (selector) => document.querySelector(selector);

const grades = [
  [0, "忘记 / 答错"],
  [3, "困难，但答对"],
  [4, "记得"],
  [5, "熟练"],
];

const resultLabels = {
  unattempted: "待练习",
  solved: "已解决",
  partial: "部分解决",
  failed: "未解决",
};

let user = null;
let view = "today";
let busy = false;

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function message(text = "", error = false) {
  $("#notice").textContent = text;
  $("#notice").classList.toggle("error", error);
}

function setBusy(value) {
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = value || button.dataset.blocked === "1";
  });
}

function signedOut() {
  user = null;
  $("#auth").hidden = false;
  $("#app").hidden = true;
  $("#logout").hidden = true;
  $("#user-info").textContent = "";
  $("#cards").replaceChildren();
  $("#detail").replaceChildren();
  $("#problem-form").reset();
  $("#mistake-inputs").replaceChildren();
  addMistakeInput();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Protection": "1",
      ...(options.headers || {}),
    },
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    if (response.status === 401) signedOut();

    let detail = data.detail || "请求失败，请稍后重试";
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => `${item.loc.join(".")}: ${item.msg}`)
        .join("；");
    }

    const error = new Error(String(detail));
    error.status = response.status;
    throw error;
  }

  return data;
}

async function run(action) {
  if (busy) return;
  busy = true;
  setBusy(true);

  try {
    await action();
  } catch (error) {
    if (error.status === 409 && user && view !== "new") {
      try {
        await loadList();
      } catch {
        // 保留原始冲突提示。
      }
    }
    message(error.message || "操作失败，请检查网络后重试", true);
  } finally {
    busy = false;
    setBusy(false);
  }
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function field(labelText, control) {
  const label = element("label", labelText);
  label.append(control);
  return label;
}

function textarea(value, maxLength, rows, code = false) {
  const control = document.createElement("textarea");
  control.value = value;
  control.maxLength = maxLength;
  control.rows = rows;
  if (code) {
    control.className = "code";
    control.spellcheck = false;
  }
  return control;
}

function timestamp(value) {
  if (!value) return "尚无";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: user.timezone,
    hour12: false,
  });
}

function updateUserInfo() {
  $("#user-info").textContent =
    `${user.username} · ${user.timezone} · ${user.today}`;
}

async function enterApp() {
  user = await api("/api/me");
  $("#auth").hidden = true;
  $("#app").hidden = false;
  $("#logout").hidden = false;
  updateUserInfo();
  await showView("today");
}

async function showView(nextView) {
  view = nextView;
  $("#new-page").hidden = view !== "new";
  $("#list-page").hidden = view === "new";

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });

  if (view !== "new") await loadList();
}

async function loadList() {
  const data = await api(`/api/mistakes?due_only=${view === "today"}`);
  user.today = data.today;
  updateUserInfo();

  $("#list-title").textContent =
    view === "today" ? "今日复习" : "全部记录";
  $("#list-summary").textContent = view === "today"
    ? `${data.today} · ${data.items.length} 条到期易错点，包含逾期记录`
    : `共 ${data.items.length} 条易错点`;

  $("#cards").replaceChildren();
  $("#detail").replaceChildren(
    element("p", "选择一条易错点查看详情。", "muted")
  );

  if (!data.items.length) {
    $("#cards").append(element(
      "p",
      view === "today" ? "今日复习已完成。" : "还没有记录，先添加一道题。",
      "muted"
    ));
    return;
  }

  for (const item of data.items) {
    const button = element("button", "", "record-button");
    button.type = "button";
    button.dataset.id = String(item.id);
    button.append(
      element("strong", item.title),
      element("span", item.description, "record-description"),
      element("small", `复习日期：${item.due_date}`, "muted")
    );
    button.addEventListener("click", () => run(() => openMistake(item.id)));
    $("#cards").append(button);
  }
}

async function openMistake(id) {
  const item = await api(`/api/mistakes/${id}`);
  user.today = item.today;
  updateUserInfo();

  document.querySelectorAll(".record-button").forEach((button) => {
    button.classList.toggle("selected", Number(button.dataset.id) === id);
  });

  renderDetail(item);
}

function renderVariant(variant) {
  const box = document.createElement("details");
  box.className = "variant";
  box.open = variant.result === "unattempted";

  const summary = element(
    "summary",
    `变体 #${variant.id} · ${resultLabels[variant.result]}`
  );
  const savedAt = element(
    "p",
    variant.result_updated_at
      ? `结果保存于：${timestamp(variant.result_updated_at)}`
      : "尚未记录练习结果",
    "muted"
  );

  const form = document.createElement("form");
  const result = document.createElement("select");

  for (const [value, label] of Object.entries(resultLabels)) {
    const option = element("option", label);
    option.value = value;
    result.append(option);
  }
  result.value = variant.result;

  const code = textarea(variant.answer_code, 40000, 7, true);
  const notes = textarea(variant.notes, 8000, 3);
  const save = element("button", "保存练习结果", "primary");
  save.type = "submit";

  form.append(
    field("练习结果", result),
    field("我的解答代码（仅保存，不运行）", code),
    field("复盘：是否还犯了同样的错误？", notes),
    save
  );

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      const updated = await api(`/api/variants/${variant.id}/result`, {
        method: "PUT",
        body: JSON.stringify({
          result: result.value,
          answer_code: code.value,
          notes: notes.value,
        }),
      });
      summary.textContent =
        `变体 #${updated.id} · ${resultLabels[updated.result]}`;
      savedAt.textContent =
        `结果保存于：${timestamp(updated.result_updated_at)}`;
      message("练习结果已保存。复习日期保持不变。");
    });
  });

  box.append(
    summary,
    element("pre", variant.description, "prose"),
    savedAt,
    form
  );
  return box;
}

function renderDetail(item) {
  const root = $("#detail");
  root.replaceChildren();

  root.append(
    element("h2", item.title),
    element("p", `语言：${item.language}`, "muted"),
    element("h3", "这条易错点"),
    element("p", item.description, "multiline"),
    element(
      "p",
      `下次复习：${item.due_date} · 连续成功：${item.repetitions} 次`,
      "muted"
    ),
    element(
      "p",
      `上次复习：${timestamp(item.last_reviewed_at)}`,
      "muted"
    )
  );

  const original = document.createElement("details");
  original.append(
    element("summary", "查看当时的思路和代码"),
    element("h4", "当时的思路"),
    element("p", item.thinking, "multiline"),
    element("h4", "当时的代码"),
    element("pre", item.code, "code")
  );
  root.append(original);

  const reviewButtons = element("div", "", "actions");
  const due = item.due_date <= item.today;
  root.append(
    element("h3", "标记这条易错点的掌握程度"),
    element(
      "p",
      due
        ? "先尝试回忆如何避免这个错误，再对照记录评分。"
        : "尚未到期。可以查看记录或练习变体题，到期后再评分。",
      "muted"
    )
  );

  for (const [quality, label] of grades) {
    const button = element("button", label);
    button.type = "button";
    button.dataset.blocked = due ? "0" : "1";
    button.disabled = !due;
    button.addEventListener("click", () => run(async () => {
      const state = await api(`/api/mistakes/${item.id}/review`, {
        method: "POST",
        body: JSON.stringify({ quality, version: item.version }),
      });
      await loadList();
      message(`评分已保存，下次复习日期：${state.due_date}`);
    }));
    reviewButtons.append(button);
  }
  root.append(reviewButtons);

  if (item.reviews.length) {
    const history = document.createElement("details");
    history.append(element("summary", `复习历史（${item.reviews.length} 次）`));
    const list = document.createElement("ul");
    for (const review of item.reviews) {
      list.append(element(
        "li",
        `${timestamp(review.reviewed_at)} · 评分 ${review.quality}/5` +
        ` · 下次 ${review.next_due_date}`
      ));
    }
    history.append(list);
    root.append(history);
  }

  root.append(
    element("h3", "同一薄弱点，再练一道"),
    element(
      "p",
      "生成时会将这道题的代码、思路和当前易错点发送给 OpenAI。" +
      `每天最多 ${user.ai_daily_limit} 次尝试，失败也计入次数。`,
      "muted"
    ),
    element(
      "p",
      "生成内容供练习使用，题意可能需要核对；系统不会判题。",
      "muted"
    )
  );

  const variants = element("div");
  const empty = element("p", "还没有生成变体题。", "muted");
  if (!item.variants.length) variants.append(empty);
  for (const variant of item.variants) {
    variants.append(renderVariant(variant));
  }

  const generate = element(
    "button",
    user.ai_enabled ? "生成一道变体题" : "AI 尚未配置",
    "primary"
  );
  generate.type = "button";
  generate.dataset.blocked = user.ai_enabled ? "0" : "1";
  generate.disabled = !user.ai_enabled;

  generate.addEventListener("click", () => run(async () => {
    message("正在生成题目，请稍候……");
    const variant = await api(`/api/mistakes/${item.id}/variants`, {
      method: "POST",
    });
    empty.remove();
    variants.prepend(renderVariant(variant));
    message("变体题已生成并保存。");
  }));

  root.append(generate, variants);
}

function addMistakeInput() {
  const container = $("#mistake-inputs");
  if (container.children.length >= 10) {
    message("每道题最多添加 10 条易错点", true);
    return;
  }

  const row = element("div", "", "mistake-row");
  const input = textarea("", 2000, 3);
  input.name = "mistake";
  input.required = true;
  input.placeholder = "哪里容易错？当时为什么会错？应该如何避免？";

  const remove = element("button", "移除");
  remove.type = "button";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) row.remove();
  });

  row.append(field("易错点 / 为什么错", input), remove);
  container.append(row);
}

$("#login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(formObject(form)),
    });
    form.reset();
    await enterApp();
    message("登录成功。");
  });
});

$("#register-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(formObject(form)),
    });
    form.reset();
    await enterApp();
    message("注册成功，可以开始记录题目了。");
  });
});

$("#logout").addEventListener("click", () => run(async () => {
  await api("/api/auth/logout", { method: "POST" });
  signedOut();
  message("已退出登录。");
}));

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => run(async () => {
    message();
    await showView(button.dataset.view);
  }));
});

$("#refresh").addEventListener("click", () => run(async () => {
  if (view !== "new") await loadList();
  message(view === "new" ? "请先保存当前记录。" : "已刷新。");
}));

$("#add-mistake").addEventListener("click", addMistakeInput);

$("#problem-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;

  run(async () => {
    const data = new FormData(form);
    const created = await api("/api/problems", {
      method: "POST",
      body: JSON.stringify({
        title: data.get("title"),
        language: data.get("language"),
        code: data.get("code"),
        thinking: data.get("thinking"),
        mistakes: data.getAll("mistake"),
      }),
    });

    form.reset();
    $("#mistake-inputs").replaceChildren();
    addMistakeInput();
    await showView("today");
    await openMistake(created.mistake_ids[0]);
    message("记录已保存，新的易错点已加入今日复习。");
  });
});

$("#timezone").value =
  Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";

addMistakeInput();

run(async () => {
  try {
    await enterApp();
  } catch (error) {
    if (error.status !== 401) throw error;
    message("请登录，或使用邀请码注册。");
  }
});
