"use strict";
/* 学习小组新功能：每周小目标 / 今日动态 / 共享题单 / 留言板。
 * 依赖 app.js 的全局函数：$、element、api、message、avatarElement、profileAuthor。
 * 所有用户文本一律经 textContent 写入，不用 innerHTML；不用行内 style，
 * 进度条用 data-progress 属性（5% 一档）配合 CSS。 */
(function () {
  var generation = 0;
  var zonesCache = null;

  /* ---------- 纯函数（挂在 window.GroupExtras.helpers 供 node 测试） ---------- */

  // 进度条按 5% 取档，对应 CSS 的 [data-progress] 规则。
  function progressBucket(progress) {
    var p = Math.max(0, Math.min(100, Math.round(progress)));
    return Math.round(p / 5) * 5;
  }

  function goalTypeLabel(goalType) {
    return goalType === "review" ? "复习" : "新记录错题";
  }

  function goalSummary(goal) {
    return "全组本周合计" + goalTypeLabel(goal.goal_type) + " " + goal.target + " 道";
  }

  // 留言列表按 id 倒序（新的在前）；"加载更早"用 before_id 翻页。
  function nextBeforeId(messages) {
    if (!messages.length) return null;
    return messages[messages.length - 1].id;
  }

  function isDuplicateSubmit(lastSentAt, now) {
    return now - lastSentAt < 3000;
  }

  /* ---------- 通用 ---------- */

  function currentGroup() {
    // app.js 维护的当前小组对象（renderStudyGroup 里赋值）。
    return typeof studyGroup !== "undefined" ? studyGroup : null;
  }

  function isCurrent(groupId, gen) {
    var group = currentGroup();
    return group && group.id === groupId && gen === generation;
  }

  async function loadZones() {
    if (zonesCache) return zonesCache;
    var data = await api("/api/zones");
    zonesCache = data.zones || [];
    return zonesCache;
  }

  function setStatus(id, text, isError) {
    var node = $(id);
    if (!node) return;
    node.textContent = text || "";
    node.classList.toggle("error", !!isError);
  }

  /* ---------- 功能 1：每周小目标 ---------- */

  function renderWeeklyGoal(group) {
    var section = $("#groups-goal-section");
    if (!section) return;
    section.hidden = false;
    var goal = group.weekly_goal;
    var body = $("#groups-goal-body");
    body.replaceChildren();

    if (!goal) {
      body.append(element("p", "组长还没设本周目标", "muted"));
    } else {
      var bucket = progressBucket(goal.progress);
      var bar = element("div", "", "groups-goal-bar");
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-label", "本周小组目标进度");
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", "100");
      bar.setAttribute("aria-valuenow", String(Math.round(goal.progress)));
      bar.setAttribute("data-progress", String(bucket));
      bar.append(element("span"));
      var caption = element("p", "", "groups-goal-caption");
      if (goal.progress >= 100) {
        caption.append(element("strong", "已达成", "groups-goal-done"));
        caption.append(document.createTextNode(" · " + goal.total + " / " + goal.target + " 道"));
      } else {
        caption.textContent = goal.total + " / " + goal.target + " 道 · " + Math.round(goal.progress) + "%";
      }
      var desc = element("p", goalSummary(goal), "muted");
      body.append(desc, bar, caption);
    }

    var formWrap = $("#groups-goal-form-wrap");
    formWrap.replaceChildren();
    if (group.is_creator) {
      var form = element("form", "", "groups-goal-form");
      form.noValidate = true;
      var typeLabel = element("label", "目标类型", "");
      var typeSelect = element("select");
      typeSelect.name = "goal_type";
      var optReview = element("option", "全组合计复习 N 道");
      optReview.value = "review";
      var optRecord = element("option", "全组合计新记录 N 道错题");
      optRecord.value = "record";
      typeSelect.append(optReview, optRecord);
      if (goal) typeSelect.value = goal.goal_type;
      typeLabel.append(typeSelect);
      var targetLabel = element("label", "目标数量（5–500）", "");
      var targetInput = element("input");
      targetInput.name = "target";
      targetInput.type = "number";
      targetInput.min = "5";
      targetInput.max = "500";
      targetInput.required = true;
      targetInput.value = goal ? String(goal.target) : "50";
      targetInput.setAttribute("aria-label", "目标数量，5 到 500");
      targetLabel.append(targetInput);
      var submit = element("button", goal ? "更新本周目标" : "设定本周目标", "primary");
      submit.type = "submit";
      form.append(typeLabel, targetLabel, submit);
      var status = element("p", "", "muted");
      status.setAttribute("role", "status");
      form.append(status);
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        saveWeeklyGoal(group.id, typeSelect.value, targetInput.value, submit, status);
      });
      formWrap.append(form);
    }
  }

  async function saveWeeklyGoal(groupId, goalType, targetValue, submit, status) {
    var target = Number(targetValue);
    if (!Number.isInteger(target) || target < 5 || target > 500) {
      status.textContent = "目标数量必须是 5 到 500 之间的整数";
      status.classList.add("error");
      return;
    }
    submit.disabled = true;
    status.classList.remove("error");
    status.textContent = "保存中…";
    try {
      var data = await api("/api/groups/" + groupId + "/weekly-goal", {
        method: "PUT",
        body: JSON.stringify({ goal_type: goalType, target: target }),
      });
      var group = currentGroup();
      if (group && group.id === groupId) {
        group.weekly_goal = data.weekly_goal;
        renderWeeklyGoal(group);
      }
      message("本周目标已保存");
    } catch (error) {
      status.textContent = error.message || "保存失败，请稍后重试";
      status.classList.add("error");
    } finally {
      submit.disabled = false;
    }
  }

  /* ---------- 功能 2：组内今日动态 ---------- */

  async function renderTodayFeed(group) {
    var gen = ++generation;
    var list = $("#groups-today-list");
    if (!list) return;
    setStatus("#groups-today-status", "正在加载今日动态…", false);
    list.replaceChildren();
    try {
      var data = await api("/api/groups/" + group.id + "/today");
      if (!isCurrent(group.id, gen)) return;
      setStatus("#groups-today-status", "", false);
      if (!data.today.length) {
        list.append(element("li", "今天还没有成员动态", "muted"));
        return;
      }
      for (var i = 0; i < data.today.length; i++) {
        (function (entry) {
          var item = element("li", "", "groups-today-item");
          var who = element("div", "", "groups-today-who");
          who.append(
            avatarElement(entry.id, entry.username, entry.avatar_version, { hasAvatar: entry.has_avatar }),
            profileAuthor(entry.id, entry.username)
          );
          item.append(who);
          if (!entry.visible) {
            item.append(element("span", "未公开", "muted"));
          } else {
            var text = "今天复习 " + entry.reviews_today + " 道";
            item.append(element("span", text));
            var badge = element("span", entry.goal_met ? "已完成每日目标" : "未完成今日目标",
              entry.goal_met ? "groups-today-met" : "muted");
            item.append(badge);
          }
          list.append(item);
        })(data.today[i]);
      }
    } catch (error) {
      if (!isCurrent(group.id, gen)) return;
      setStatus("#groups-today-status", error.message || "今日动态加载失败", true);
    }
  }

  /* ---------- 功能 3：小组共享题单 ---------- */

  var sharedState = { offset: 0, total: 0, loading: false, groupId: null };

  async function renderSharedProblems(group, reset) {
    var gen = ++generation;
    if (reset || sharedState.groupId !== group.id) {
      sharedState = { offset: 0, total: 0, loading: false, groupId: group.id };
      $("#groups-shared-list").replaceChildren();
    }
    if (sharedState.loading) return;
    sharedState.loading = true;
    setStatus("#groups-shared-status", "正在加载共享题单…", false);
    $("#groups-shared-more").hidden = true;
    try {
      var data = await api(
        "/api/groups/" + group.id + "/shared-problems?limit=20&offset=" + sharedState.offset
      );
      if (!isCurrent(group.id, gen)) return;
      sharedState.total = data.total;
      sharedState.offset += data.items.length;
      setStatus("#groups-shared-status", "", false);
      var list = $("#groups-shared-list");
      if (!data.items.length && sharedState.offset === 0) {
        list.append(element("li", "还没有人推荐题目，来说一道吧", "muted"));
      }
      for (var i = 0; i < data.items.length; i++) {
        list.append(renderSharedItem(group, data.items[i]));
      }
      var more = $("#groups-shared-more");
      more.hidden = !(sharedState.offset < sharedState.total);
      more.textContent = "加载更多（" + sharedState.offset + " / " + sharedState.total + "）";
    } catch (error) {
      if (!isCurrent(group.id, gen)) return;
      setStatus("#groups-shared-status", error.message || "共享题单加载失败", true);
    } finally {
      sharedState.loading = false;
    }
  }

  function renderSharedItem(group, item) {
    var li = element("li", "", "groups-shared-item");
    var head = element("div", "", "groups-shared-head");
    head.append(element("strong", item.title));
    head.append(element("span", item.zone, "groups-shared-zone"));
    li.append(head);
    var meta = element("div", "", "groups-shared-meta");
    meta.append(element("span", "推荐人：" + item.recommender.username, "muted"));
    if (item.source_url) {
      var link = element("a", "来源链接");
      link.href = item.source_url;
      link.target = "_blank";
      link.rel = "noopener";
      meta.append(link);
    }
    li.append(meta);
    if (item.note) li.append(element("p", "推荐语：" + item.note, "groups-shared-note"));
    var actions = element("div", "", "groups-shared-actions");
    var collect = element("button", item.collected ? "已收录" : "收进我的错题本", item.collected ? "" : "primary");
    collect.type = "button";
    collect.disabled = !!item.collected;
    if (!item.collected) {
      collect.addEventListener("click", function () {
        collectSharedProblem(group.id, item, collect);
      });
    }
    actions.append(collect);
    if (item.can_delete) {
      var del = element("button", item.is_mine ? "撤回" : "删除", "danger");
      del.type = "button";
      del.setAttribute("aria-label", "删除推荐 " + item.title);
      del.addEventListener("click", function () {
        deleteSharedProblem(group.id, item, li, del);
      });
      actions.append(del);
    }
    li.append(actions);
    return li;
  }

  async function collectSharedProblem(groupId, item, button) {
    button.disabled = true;
    try {
      var data = await api(
        "/api/groups/" + groupId + "/shared-problems/" + item.id + "/collect",
        { method: "POST", body: "{}" }
      );
      item.collected = true;
      item.collected_problem_id = data.problem_id;
      button.textContent = "已收录";
      message("已收进你的错题本");
    } catch (error) {
      button.disabled = false;
      message(error.message || "收录失败，请稍后重试", true);
    }
  }

  async function deleteSharedProblem(groupId, item, li, button) {
    button.disabled = true;
    try {
      await api("/api/groups/" + groupId + "/shared-problems/" + item.id, { method: "DELETE" });
      li.remove();
      message(item.is_mine ? "已撤回推荐" : "已删除该推荐");
    } catch (error) {
      button.disabled = false;
      message(error.message || "删除失败，请稍后重试", true);
    }
  }

  function renderRecommendForm(group) {
    var wrap = $("#groups-recommend-form-wrap");
    wrap.replaceChildren();
    var form = element("form", "", "groups-recommend-form");
    form.noValidate = true;
    var titleLabel = element("label", "题名", "");
    var titleInput = element("input");
    titleInput.name = "title";
    titleInput.maxLength = 200;
    titleInput.required = true;
    titleInput.setAttribute("aria-label", "题名");
    titleLabel.append(titleInput);
    var zoneLabel = element("label", "分区", "");
    var zoneSelect = element("select");
    zoneSelect.name = "zone";
    zoneSelect.setAttribute("aria-label", "分区");
    zoneLabel.append(zoneSelect);
    var urlLabel = element("label", "来源链接（可选）", "");
    var urlInput = element("input");
    urlInput.name = "source_url";
    urlInput.type = "url";
    urlInput.placeholder = "https://…";
    urlInput.setAttribute("aria-label", "来源链接，可选");
    urlLabel.append(urlInput);
    var noteLabel = element("label", "一句推荐语（可选，最多 60 字）", "");
    var noteInput = element("input");
    noteInput.name = "note";
    noteInput.maxLength = 60;
    noteInput.setAttribute("aria-label", "推荐语，可选，最多 60 字");
    noteLabel.append(noteInput);
    var submit = element("button", "推荐到小组", "primary");
    submit.type = "submit";
    var status = element("p", "", "muted");
    status.setAttribute("role", "status");
    form.append(titleLabel, zoneLabel, urlLabel, noteLabel, submit, status);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      submitRecommendation(group.id, {
        title: titleInput.value,
        zone: zoneSelect.value,
        source_url: urlInput.value,
        note: noteInput.value,
      }, form, submit, status);
    });
    wrap.append(form);
    loadZones().then(function (zones) {
      if (!document.contains(zoneSelect)) return;
      for (var i = 0; i < zones.length; i++) {
        var opt = element("option", zones[i]);
        opt.value = zones[i];
        zoneSelect.append(opt);
      }
    }).catch(function () {
      setStatus("#groups-shared-status", "分区列表加载失败，请刷新页面重试", true);
    });
  }

  async function submitRecommendation(groupId, values, form, submit, status) {
    if (!values.title.trim()) {
      status.textContent = "请填写题名";
      status.classList.add("error");
      return;
    }
    submit.disabled = true;
    status.classList.remove("error");
    status.textContent = "推荐中…";
    try {
      var data = await api("/api/groups/" + groupId + "/shared-problems", {
        method: "POST",
        body: JSON.stringify(values),
      });
      var group = currentGroup();
      if (group && group.id === groupId) {
        var list = $("#groups-shared-list");
        var empty = list.querySelector(".muted");
        if (empty && list.children.length === 1) list.replaceChildren();
        list.prepend(renderSharedItem(group, data));
        sharedState.offset += 1;
        sharedState.total += 1;
      }
      form.reset();
      status.textContent = "推荐成功";
      message("已推荐到小组题单");
    } catch (error) {
      status.textContent = error.message || "推荐失败，请稍后重试";
      status.classList.add("error");
    } finally {
      submit.disabled = false;
    }
  }

  /* ---------- 功能 4：小组留言板 ---------- */

  var messageState = { loading: false, sending: false, lastSentAt: 0, groupId: null };

  async function renderMessages(group, opts) {
    opts = opts || {};
    var gen = ++generation;
    if (messageState.groupId !== group.id || opts.reset) {
      messageState = { loading: false, sending: false, lastSentAt: 0, groupId: group.id };
      $("#groups-messages-list").replaceChildren();
      $("#groups-messages-more").hidden = true;
    }
    if (messageState.loading) return;
    messageState.loading = true;
    var beforeId = opts.beforeId || null;
    setStatus("#groups-messages-status", beforeId ? "正在加载更早的留言…" : "正在加载留言…", false);
    try {
      var path = "/api/groups/" + group.id + "/messages?limit=30"
        + (beforeId ? "&before_id=" + beforeId : "");
      var data = await api(path);
      if (!isCurrent(group.id, gen)) return;
      setStatus("#groups-messages-status", "", false);
      var list = $("#groups-messages-list");
      if (!data.messages.length && !beforeId) {
        list.append(element("li", "还没有留言，来抢沙发", "muted"));
        return;
      }
      for (var i = 0; i < data.messages.length; i++) {
        list.append(renderMessageItem(group, data.messages[i]));
      }
      var more = $("#groups-messages-more");
      var oldest = nextBeforeId(data.messages);
      more.hidden = !(oldest && data.messages.length >= 30);
      more.dataset.beforeId = oldest || "";
    } catch (error) {
      if (!isCurrent(group.id, gen)) return;
      setStatus("#groups-messages-status", error.message || "留言加载失败", true);
    } finally {
      messageState.loading = false;
    }
  }

  function renderMessageItem(group, msg) {
    var li = element("li", "", "groups-message");
    li.dataset.messageId = String(msg.id);
    var head = element("div", "", "groups-message-head");
    head.append(profileAuthor(msg.author.id, msg.author.username));
    head.append(element("time", formatMessageTime(msg.created_at), "muted"));
    if (msg.author.id === (typeof user !== "undefined" ? user.id : null) || group.is_creator) {
      var del = element("button", "删除", "danger groups-message-delete");
      del.type = "button";
      del.setAttribute("aria-label", "删除留言");
      del.addEventListener("click", function () {
        deleteMessage(group.id, msg, li, del);
      });
      head.append(del);
    }
    // 纯文本渲染：textContent，不解析 Markdown/HTML，链接也不转成可点击。
    li.append(head, element("p", msg.body, "groups-message-body"));
    return li;
  }

  function formatMessageTime(value) {
    try {
      var tz = typeof user !== "undefined" && user.timezone ? user.timezone : "Asia/Shanghai";
      return new Date(value).toLocaleString("zh-CN", { timeZone: tz, hour12: false });
    } catch (error) {
      return value;
    }
  }

  async function sendMessage(groupId, body, submit, status) {
    var now = Date.now();
    if (isDuplicateSubmit(messageState.lastSentAt, now)) return;
    if (messageState.sending) return;
    messageState.sending = true;
    messageState.lastSentAt = now;
    submit.disabled = true;
    status.classList.remove("error");
    status.textContent = "发送中…";
    try {
      var data = await api("/api/groups/" + groupId + "/messages", {
        method: "POST",
        body: JSON.stringify({ body: body }),
      });
      var group = currentGroup();
      if (group && group.id === groupId) {
        var list = $("#groups-messages-list");
        var empty = list.querySelector(".muted");
        if (empty && list.children.length === 1) list.replaceChildren();
        list.prepend(renderMessageItem(group, data));
      }
      status.textContent = "";
      var input = $("#groups-message-input");
      if (input) input.value = "";
    } catch (error) {
      // 401 由 api() 统一走 signedOut；这里只展示中文错误。
      status.textContent = error.message || "发送失败，请稍后重试";
      status.classList.add("error");
    } finally {
      messageState.sending = false;
      submit.disabled = false;
    }
  }

  async function deleteMessage(groupId, msg, li, button) {
    button.disabled = true;
    try {
      await api("/api/groups/" + groupId + "/messages/" + msg.id, { method: "DELETE" });
      li.remove();
    } catch (error) {
      button.disabled = false;
      message(error.message || "删除失败，请稍后重试", true);
    }
  }

  function renderMessageForm(group) {
    var wrap = $("#groups-message-form-wrap");
    wrap.replaceChildren();
    var form = element("form", "", "groups-message-form");
    var input = element("textarea", "", "");
    input.id = "groups-message-input";
    input.rows = 2;
    input.maxLength = 300;
    input.placeholder = "说点什么吧（最多 300 字）";
    input.setAttribute("aria-label", "留言内容，最多 300 字");
    var submit = element("button", "发送", "primary");
    submit.type = "submit";
    var status = element("p", "", "muted");
    status.setAttribute("role", "status");
    form.append(input, submit, status);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var body = input.value.trim();
      if (!body) {
        status.textContent = "留言不能为空";
        status.classList.add("error");
        return;
      }
      sendMessage(group.id, body, submit, status);
    });
    wrap.append(form);
  }

  /* ---------- 账号设置：今日动态隐私开关 ---------- */

  function renderTodayVisibilitySetting() {
    var checkbox = $("#account-group-today");
    var label = $("#account-group-today-label");
    if (!checkbox || !label || typeof user === "undefined" || !user) return;
    label.hidden = Boolean(user.is_trial);
    checkbox.checked = user.show_group_today !== 0;
    checkbox.onchange = function () {
      var wanted = checkbox.checked;
      setStatus("#account-group-today-status", "保存中…", false);
      api("/api/me/group-today", {
        method: "PUT",
        body: JSON.stringify({ show: wanted }),
      }).then(function (data) {
        user.show_group_today = data.show ? 1 : 0;
        setStatus("#account-group-today-status",
          data.show ? "已开启：在小组里显示我的今日动态" : "已关闭：组内将显示“未公开”", false);
      }).catch(function (error) {
        checkbox.checked = !wanted;
        setStatus("#account-group-today-status", error.message || "保存失败", true);
      });
    };
  }

  /* ---------- 总装配 ---------- */

  function renderAll(group) {
    renderWeeklyGoal(group);
    renderTodayFeed(group);
    renderRecommendForm(group);
    renderSharedProblems(group, true);
    renderMessageForm(group);
    renderMessages(group, { reset: true });
    var more = $("#groups-shared-more");
    if (more && !more.dataset.wired) {
      more.dataset.wired = "1";
      more.addEventListener("click", function () {
        var g = currentGroup();
        if (g) renderSharedProblems(g, false);
      });
    }
    var refresh = $("#groups-messages-refresh");
    if (refresh && !refresh.dataset.wired) {
      refresh.dataset.wired = "1";
      refresh.addEventListener("click", function () {
        var g = currentGroup();
        if (g) renderMessages(g, { reset: true });
      });
    }
    var moreMsg = $("#groups-messages-more");
    if (moreMsg && !moreMsg.dataset.wired) {
      moreMsg.dataset.wired = "1";
      moreMsg.addEventListener("click", function () {
        var g = currentGroup();
        var beforeId = moreMsg.dataset.beforeId ? Number(moreMsg.dataset.beforeId) : null;
        if (g && beforeId) renderMessages(g, { beforeId: beforeId });
      });
    }
  }

  window.GroupExtras = {
    renderAll: renderAll,
    renderTodayVisibilitySetting: renderTodayVisibilitySetting,
    helpers: {
      progressBucket: progressBucket,
      goalTypeLabel: goalTypeLabel,
      goalSummary: goalSummary,
      nextBeforeId: nextBeforeId,
      isDuplicateSubmit: isDuplicateSubmit,
    },
  };
})();
