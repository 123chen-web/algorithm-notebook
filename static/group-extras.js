"use strict";
/* 学习小组新功能：每周小目标 / 今日动态。
 * 依赖 app.js 的全局函数：$、element、api、message、avatarElement、profileAuthor。
 * 所有用户文本一律经 textContent 写入，不用 innerHTML；不用行内 style，
 * 进度条用 data-progress 属性（5% 一档）配合 CSS。 */
(function () {
  var generation = 0;

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

  /* ---------- 通用 ---------- */

  function currentGroup() {
    // app.js 维护的当前小组对象（renderStudyGroup 里赋值）。
    return typeof studyGroup !== "undefined" ? studyGroup : null;
  }

  function isCurrent(groupId, gen) {
    var group = currentGroup();
    return group && group.id === groupId && gen === generation;
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
  }

  window.GroupExtras = {
    renderAll: renderAll,
    renderTodayVisibilitySetting: renderTodayVisibilitySetting,
    helpers: {
      progressBucket: progressBucket,
      goalTypeLabel: goalTypeLabel,
      goalSummary: goalSummary,
    },
  };
})();
