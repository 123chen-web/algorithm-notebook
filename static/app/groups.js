// static/app/groups.js — 小组：学习小组列表/详情/等级/邀请。
// 由 static/app.js 按领域拆分（C2），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

// ---- 领域状态 ----
let groupsGeneration = 0;
let groupLevelsGeneration = 0;
let groupLevelRules = null;
let groupCopyTimer = null;
let selectedGroupId = null;
let studyGroup = null;

function resetGroups() {
  groupsGeneration += 1;
  groupLevelsGeneration += 1;
  groupLevelRules = null;
  window.clearTimeout(groupCopyTimer);
  selectedGroupId = null;
  studyGroup = null;
  $("#groups-page").hidden = true;
  $("#groups-page").setAttribute("aria-busy", "false");
  $("#groups-overview").hidden = false;
  $("#groups-detail").hidden = true;
  $("#groups-detail-content").hidden = true;
  $("#groups-list").replaceChildren();
  $("#groups-members").replaceChildren();
  $("#groups-member-actions").hidden = true;
  $("#groups-weakness").replaceChildren();
  $("#groups-detail-title").textContent = "";
  $("#groups-invite-code").textContent = "";
  $("#groups-copy-invite").textContent = "复制邀请码";
  $("#groups-upgrade").open = false;
  $("#groups-level-ladder").replaceChildren();
  $("#groups-level-rules").replaceChildren();
  $("#groups-levels-status").textContent = "";
  $("#groups-levels-retry").hidden = true;
  $("#groups-delete").hidden = true;
  $("#groups-status").textContent = "";
  $("#groups-status").classList.remove("error");
  $("#groups-retry").hidden = true;
  $("#groups-create-form").reset();
  $("#groups-join-form").reset();
  $("#groups-coach-section").hidden = true;
  $("#groups-coach-summary").replaceChildren();
  $("#groups-coach-status").textContent = "";
  $("#groups-summary-notice").hidden = true;
  const pref = $("#groups-summary-pref");
  pref.checked = true;
  pref.onchange = null;
}

async function requestGroups(action, onSuccess, loadingText, { retry = false, skeletonHost = null } = {}) {
  if (!user || view !== "groups") return;
  const generation = ++groupsGeneration;
  const userId = user.id;
  const isCurrent = () => user?.id === userId && view === "groups"
    && generation === groupsGeneration;
  const page = $("#groups-page");
  const status = $("#groups-status");
  page.setAttribute("aria-busy", "true");
  status.classList.remove("error");
  // 手感层：骨架屏代替"正在加载…"文字；feel.js 未提供骨架 API 时回退到文字。
  if (skeletonHost && window.Feel?.skeletonShow) {
    window.Feel.skeletonShow(skeletonHost, 3);
    status.textContent = "";
  } else {
    status.textContent = loadingText;
  }
  $("#groups-retry").hidden = true;
  try {
    const data = await action();
    if (!isCurrent()) return;
    window.Feel?.skeletonHide?.(skeletonHost);
    await onSuccess(data);
    if (isCurrent()) status.textContent = "";
  } catch (error) {
    if (!isCurrent()) return;
    window.Feel?.skeletonHide?.(skeletonHost);
    status.classList.add("error");
    status.textContent = `暂时无法完成：${error.message || "请检查网络后重试"}`;
    $("#groups-retry").hidden = !retry;
  } finally {
    if (isCurrent()) page.setAttribute("aria-busy", "false");
  }
}

function groupLevelBadge(level, size = "medium") {
  const badge = element("span", "", `groups-level-badge groups-level-badge--${size}`);
  badge.dataset.level = level.number;
  badge.setAttribute("aria-hidden", "true");
  badge.append(
    element("span", level.name.slice(0, 1), "groups-seal-character"),
    element("span", `Lv.${level.number}`, "groups-seal-number")
  );
  return badge;
}

function groupNextLevelText(level) {
  return level.next_points == null ? "已满级"
    : `距 Lv.${level.number + 1} ${level.next_name}还差 ${level.points_to_next} 分`;
}

function setGroupProgress(bar, level) {
  const progress = Math.max(0, Math.min(1, level.progress));
  bar.style.setProperty("--groups-progress", progress);
  bar.setAttribute("aria-valuenow", String(Math.round(progress * 100)));
  bar.setAttribute("aria-valuetext", `${level.points} 分，${groupNextLevelText(level)}`);
}

function renderGroups(groups) {
  const list = $("#groups-list");
  list.replaceChildren();
  if (!groups.length) {
    list.append(element("p", "还没有加入小组。创建一个，或用熟人的邀请码加入吧。", "muted"));
    return;
  }
  for (const group of groups) {
    const button = element("button", "", "groups-card");
    button.type = "button";
    button.disabled = busy;
    const level = group.level;
    const heading = element("span", "", "groups-card-heading");
    const copy = element("span", "", "groups-card-copy");
    const meta = element("span", `成员 ${group.member_count}/${group.member_limit}${group.is_creator ? " · 我是组长" : ""}`, "groups-card-meta");
    if (group.member_count >= group.member_limit) meta.append(element("span", "已满员", "groups-full-tag"));
    const name = element("strong", group.name, "groups-card-name");
    name.title = group.name;
    copy.append(name, element("span", `Lv.${level.number} ${level.name}`, "groups-card-level"), meta);
    heading.append(groupLevelBadge(level), copy);
    const progress = element("span", "", "groups-progress");
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-label", `${group.name}的等级进度`);
    progress.setAttribute("aria-valuemin", "0");
    progress.setAttribute("aria-valuemax", "100");
    progress.append(element("span"));
    setGroupProgress(progress, level);
    const avatars = element("span", "", "groups-avatars");
    for (const member of group.members_preview) {
      const avatar = avatarElement(member.id, member.username, member.avatar_version, { hasAvatar: member.has_avatar });
      avatar.setAttribute("aria-hidden", "true");
      avatar.title = member.username;
      avatars.append(avatar);
    }
    if (group.member_count > group.members_preview.length) {
      avatars.append(element("span", `+${group.member_count - group.members_preview.length}`, "groups-avatars-more"));
    }
    button.append(heading, progress, element("span", groupNextLevelText(level), "groups-card-caption"), avatars);
    button.addEventListener("click", () => run(() => openStudyGroup(group.id)));
    list.append(button);
  }
}

async function loadGroups() {
  groupLevelsGeneration += 1;
  selectedGroupId = null;
  studyGroup = null;
  $("#groups-overview").hidden = false;
  $("#groups-detail").hidden = true;
  $("#groups-detail-content").hidden = true;
  $("#groups-list").replaceChildren();
  await requestGroups(
    () => api("/api/groups"),
    (data) => renderGroups(data.groups),
    "正在加载你的小组…",
    { retry: true, skeletonHost: $("#groups-list") }
  );
}

function formatGroupDate(value, timeZone) {
  const date = new Date(value);
  try {
    return date.toLocaleDateString("zh-CN", { timeZone });
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return `${date.toLocaleDateString("zh-CN", { timeZone: "UTC" })}（UTC）`;
  }
}

function renderStudyGroup(group) {
  studyGroup = group;
  selectedGroupId = group.id;
  $("#groups-overview").hidden = true;
  $("#groups-detail").hidden = false;
  $("#groups-detail-content").hidden = false;
  $("#groups-detail-title").textContent = group.name;
  $("#groups-invite-code").textContent = group.invite_code;
  window.clearTimeout(groupCopyTimer);
  $("#groups-copy-invite").textContent = "复制邀请码";
  $("#groups-invite-code").setAttribute("aria-label", `小组邀请码 ${group.invite_code}`);
  $("#groups-hero-badge").replaceChildren(groupLevelBadge(group.level, "large"));
  const meta = $("#groups-detail-meta");
  meta.replaceChildren(element("span", `Lv.${group.level.number} ${group.level.name} · 成员 ${group.members.length}/${group.member_limit}`));
  if (group.members.length >= group.member_limit) meta.append(element("span", "已满员", "groups-full-tag"));
  meta.append(element("span", ` · 创建于 ${formatGroupDate(group.created_at, user.timezone)}`));
  setGroupProgress($("#groups-level-progress"), group.level);
  $("#groups-level-caption").textContent = group.level.next_points == null
    ? `${group.level.floor}+ 分 · 已满级`
    : `${group.points} / ${group.level.next_points} 分 · ${groupNextLevelText(group.level)}`;
  $("#groups-members-title").textContent = `小组成员 · ${group.members.length}/${group.member_limit} 人`;
  $("#groups-member-actions").hidden = !group.is_creator;
  const members = $("#groups-members");
  members.replaceChildren();
  const highestContribution = Math.max(0, ...group.members.map((member) => member.points));
  // 服务端已按连续天数降序、用户名升序排列，保留相同的排序口径。
  for (const member of group.members) {
    const row = element("li", "", "groups-member-card");
    const identity = element("div", "", "groups-member-identity");
    const name = element("div", "", "groups-member-name");
    name.append(profileAuthor(member.id, member.username));
    const tags = element("span", "", "groups-member-tags");
    if (member.is_creator) tags.append(element("span", "组长", "groups-creator-tag"));
    if (member.id === user.id) tags.append(element("span", "我", "groups-self-tag"));
    name.append(tags);
    identity.append(avatarElement(member.id, member.username, member.avatar_version, { hasAvatar: member.has_avatar }), name);
    if (group.is_creator && member.id !== user.id) {
      const remove = element("button", "移除", "danger");
      remove.type = "button";
      remove.disabled = busy;
      remove.setAttribute("aria-label", `移除成员 ${member.username}`);
      remove.addEventListener("click", () => run(() => removeStudyGroupMember(member)));
      identity.append(remove);
    }
    const stats = element("dl", "", "groups-member-stats");
    for (const [label, value, unit] of [["连续打卡", member.current_streak_days, "天"], ["贡献", `+${member.points}`, "分"]]) {
      const block = element("div");
      const number = element("dd");
      number.append(element("strong", value), element("span", unit));
      block.append(element("dt", label), number);
      stats.append(block);
    }
    const contribution = element("div", "", "groups-contribution-bar");
    contribution.setAttribute("aria-hidden", "true");
    contribution.style.setProperty("--groups-progress", highestContribution ? member.points / highestContribution : 0);
    contribution.append(element("span"));
    row.append(identity, stats, contribution);
    members.append(row);
  }
  renderGroupWeakness(group.weakness_by_zone);
  renderGroupCoachSection(group);
  renderGroupSummaryPref(group);
  maybeShowSummaryNotice(group);
  window.GroupExtras?.renderAll(group);
  $("#groups-delete").hidden = !group.is_creator;
  void loadGroupLevelRules();
  rememberGroupLevel(group);
}

function renderGroupWeakness(weaknessByZone) {
  const signals = $("#groups-weakness");
  signals.replaceChildren();
  const entries = Object.entries(weaknessByZone);
  if (!entries.length) {
    signals.append(element("p", "还没有足够的学习记录，积累一些易错点后再来看看。", "muted"));
    return;
  }
  const available = entries.filter(([, signal]) => signal.struggling_ratio != null);
  const missing = entries.filter(([, signal]) => signal.struggling_ratio == null).map(([zone]) => zone);
  if (!available.length) {
    signals.append(element("p", "各分区的有效样本还不够，暂不显示群体挣扎占比。继续积累，一起发现需要多练的方向。", "groups-signal-empty"));
    return;
  }
  for (const [zone, signal] of available) {
    const ratio = Math.max(0, Math.min(1, signal.struggling_ratio));
    const row = element("div", "", "groups-signal-row");
    const bar = element("div", "", "groups-signal-bar");
    bar.setAttribute("aria-hidden", "true");
    bar.style.setProperty("--groups-progress", ratio);
    bar.append(element("span"));
    row.append(element("span", zone, "groups-signal-name"), bar, element("strong", `${Math.round(ratio * 100)}%`));
    signals.append(row);
  }
  if (missing.length) signals.append(element("p", `样本暂不足：${missing.join("、")}`, "groups-signal-missing"));
}

// ---- 薄弱点汇总（仅组长可见，纯聚合） ----

function renderGroupCoachSection(group) {
  const section = $("#groups-coach-section");
  section.hidden = !group.is_creator;
  if (!group.is_creator) return;
  loadGroupCoachSummary(group.id);
}

async function loadGroupCoachSummary(groupId) {
  const status = $("#groups-coach-status");
  const host = $("#groups-coach-summary");
  host.replaceChildren();
  status.textContent = "正在加载全组汇总…";
  try {
    const data = await api(`/api/groups/${groupId}/weakness-summary`);
    if (!studyGroup || studyGroup.id !== groupId || !studyGroup.is_creator) return;
    status.textContent = "";
    renderGroupCoachSummary(host, data);
  } catch (error) {
    if (!studyGroup || studyGroup.id !== groupId) return;
    status.textContent = `汇总暂时无法加载：${error.message || "请稍后重试"}`;
  }
}

function coachBarRow(label, count, percent) {
  const row = element("div", "", "coach-bar-row");
  row.append(element("span", label, "coach-bar-label"));
  const track = element("span", "", "coach-bar-track");
  const fill = element("span", "", "coach-bar-fill");
  fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  track.append(fill);
  row.append(track);
  row.append(element("span", `${count} · ${percent}%`, "coach-bar-value"));
  return row;
}

function coachBlock(title) {
  const block = element("div", "", "coach-block");
  block.append(element("h4", title));
  return block;
}

function renderGroupCoachSummary(host, data) {
  host.replaceChildren();
  const meta = element("p", `参与汇总 ${data.eligible_members} 人`, "muted");
  host.append(meta);
  if (!data.has_summary) {
    host.append(element("p", data.reason, "muted"));
    return;
  }
  // ① 错因分布：4 大类。
  const groupBlock = coachBlock("错因分布");
  const groupBars = element("div", "", "coach-bars");
  for (const g of data.cause_groups) {
    groupBars.append(coachBarRow(g.label, g.count, g.percent));
  }
  groupBlock.append(groupBars);
  host.append(groupBlock);
  // 14 个错因明细（只显示有计数的）。
  const counted = data.causes.filter((c) => c.count > 0);
  if (counted.length) {
    const causeBlock = coachBlock("错因明细");
    const causeBars = element("div", "", "coach-bars");
    const max = Math.max(...counted.map((c) => c.count));
    for (const c of counted) {
      causeBars.append(coachBarRow(
        `${c.label}`, c.count, max ? Math.round(c.count / max * 100) : 0
      ));
    }
    causeBlock.append(causeBars);
    host.append(causeBlock);
  }
  // ② 判题类型分布。
  if (data.verdicts.length) {
    const verdictBlock = coachBlock("判题类型");
    const verdictBars = element("div", "", "coach-bars");
    for (const v of data.verdicts) {
      verdictBars.append(coachBarRow(v.label, v.count, v.percent));
    }
    verdictBlock.append(verdictBars);
    host.append(verdictBlock);
  }
  // ③ 套路：错得最多的 5 个 + 学习路线进度。
  const patternBlock = coachBlock("错得最多的套路");
  if (data.top_patterns.length) {
    const list = element("ul", "", "coach-pattern-list");
    for (const p of data.top_patterns) {
      const li = element("li");
      li.append(element("span", p.title));
      li.append(element("span", `${p.mistake_count} 条易错点`, "coach-pattern-count"));
      list.append(li);
    }
    patternBlock.append(list);
  } else {
    patternBlock.append(element("p", "还没有套路关联数据。", "muted"));
  }
  const roadmap = element("p", "", "muted");
  roadmap.textContent = `全组学习路线：${data.roadmap.mastered} 个已掌握 / ${data.roadmap.learning} 个学习中`;
  patternBlock.append(roadmap);
  host.append(patternBlock);
  // ④ 本周活跃与复习完成率。
  const weekBlock = coachBlock("本周");
  const stats = element("div", "", "coach-stats");
  const activeStat = element("div", "", "coach-stat");
  activeStat.append(element("strong", String(data.active_this_week)), element("span", "活跃人数"));
  const rateStat = element("div", "", "coach-stat");
  rateStat.append(
    element("strong", `${data.review_completion.percent}%`),
    element("span", `复习完成率（${data.review_completion.reviewed}/${data.review_completion.due}）`)
  );
  stats.append(activeStat, rateStat);
  weekBlock.append(stats);
  host.append(weekBlock);
}

// ---- 成员汇总偏好（退出开关）与首次提示 ----

function renderGroupSummaryPref(group) {
  const pref = $("#groups-summary-pref");
  pref.checked = group.include_in_summary !== false;
  pref.onchange = () => {
    const include = pref.checked;
    pref.disabled = true;
    api(`/api/groups/${group.id}/summary-preference`, {
      method: "POST",
      body: JSON.stringify({ include_in_summary: include }),
    }).then(
      () => {
        pref.disabled = false;
        studyGroup.include_in_summary = include;
        message(include ? "已重新参与全组汇总。" : "已退出全组汇总，你的数据不再计入。");
      },
      (error) => {
        pref.disabled = false;
        pref.checked = !include;
        message(`设置失败：${error.message || "请稍后重试"}`);
      }
    );
  };
}

function maybeShowSummaryNotice(group) {
  const banner = $("#groups-summary-notice");
  let seen = false;
  try {
    seen = localStorage.getItem(`groupSummaryNotice:${user.id}:${group.id}`) === "1";
  } catch {
    seen = true;
  }
  banner.hidden = seen;
  if (seen) return;
  $("#groups-summary-notice-dismiss").onclick = () => {
    banner.hidden = true;
    try {
      localStorage.setItem(`groupSummaryNotice:${user.id}:${group.id}`, "1");
    } catch { /* 忽略 */ }
  };
}

function renderGroupLevelRules(data) {
  const ladder = $("#groups-level-ladder");
  ladder.replaceChildren();
  for (const level of data.levels) {
    const step = element("li", "", "groups-level-step");
    step.dataset.state = level.number === studyGroup.level.number ? "current"
      : level.number < studyGroup.level.number ? "reached" : "future";
    if (step.dataset.state === "current") step.setAttribute("aria-current", "step");
    step.append(groupLevelBadge(level, "small"), element("strong", `Lv.${level.number} ${level.name}`), element("span", `${level.min_points} 分`));
    ladder.append(step);
  }
  const rules = $("#groups-level-rules");
  rules.replaceChildren();
  const units = { review: "次", checkin: "天", record: "条" };
  for (const rule of data.rules) {
    const item = element("li");
    item.append(element("strong", rule.label), element("span", `+${rule.points} / ${units[rule.key] || "次"}`), element("span", `每人每天最多 ${rule.daily_cap} 分${rule.key === "checkin" ? "，完成复习即打卡，每天一次" : ""}`));
    rules.append(item);
  }
}

async function loadGroupLevelRules() {
  if (!user || view !== "groups" || !studyGroup) return;
  const generation = ++groupLevelsGeneration;
  const userId = user.id;
  const groupId = studyGroup.id;
  const isCurrent = () => user?.id === userId && view === "groups"
    && studyGroup?.id === groupId && generation === groupLevelsGeneration;
  const details = $("#groups-upgrade");
  const status = $("#groups-levels-status");
  status.textContent = "正在读取等级说明…";
  status.classList.remove("error");
  $("#groups-levels-retry").hidden = true;
  $("#groups-level-ladder").replaceChildren();
  $("#groups-level-rules").replaceChildren();
  details.setAttribute("aria-busy", "true");
  try {
    const data = groupLevelRules || await api("/api/group-levels");
    if (!isCurrent()) return;
    groupLevelRules = data;
    renderGroupLevelRules(data);
    status.textContent = "";
  } catch {
    if (!isCurrent()) return;
    status.textContent = "等级说明暂时无法读取，小组仍可正常使用。可以稍后重试。";
    status.classList.add("error");
    $("#groups-levels-retry").hidden = false;
  } finally {
    if (isCurrent()) details.setAttribute("aria-busy", "false");
  }
}

function rememberGroupLevel(group) {
  try {
    const key = `groupLevelSeen:${user.id}:${group.id}:${group.created_at}`;
    const previous = localStorage.getItem(key);
    localStorage.setItem(key, String(group.level.number));
    if (previous !== null && Number.isInteger(Number(previous)) && Number(previous) >= 1
        && group.level.number > Number(previous)) {
      stampSeal(`升至 Lv.${group.level.number}`, { anchor: $("#groups-hero-badge"), tone: "gold" });
    }
  } catch {
    // Private browsing or a full storage quota must not interrupt the group.
  }
}

async function copyGroupInvite() {
  if (!user || view !== "groups" || !studyGroup) return;
  const groupId = studyGroup.id;
  const userId = user.id;
  const code = studyGroup.invite_code;
  const isCurrent = () => user?.id === userId && view === "groups" && studyGroup?.id === groupId;
  try {
    await navigator.clipboard.writeText(code);
    if (!isCurrent()) return;
    const button = $("#groups-copy-invite");
    button.textContent = "已复制";
    message("邀请码已复制，可以分享给一起学习的人。");
    window.clearTimeout(groupCopyTimer);
    groupCopyTimer = window.setTimeout(() => {
      if (isCurrent()) button.textContent = "复制邀请码";
    }, 1800);
  } catch {
    if (!isCurrent()) return;
    const label = $("#groups-invite-code");
    label.focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(label);
    selection?.removeAllRanges();
    selection?.addRange(range);
    message("自动复制未成功，已选中邀请码。请按 Ctrl+C，或长按复制。");
  }
}

async function openStudyGroup(groupId) {
  groupLevelsGeneration += 1;
  $("#groups-upgrade").open = false;
  selectedGroupId = groupId;
  studyGroup = null;
  $("#groups-overview").hidden = true;
  $("#groups-detail").hidden = false;
  // 骨架屏需要可见的宿主：只有 feel.js 提供骨架 API 时才提前显示内容区。
  const detailContent = $("#groups-detail-content");
  const showSkeleton = Boolean(window.Feel?.skeletonShow);
  if (showSkeleton) detailContent.hidden = false;
  await requestGroups(
    () => api(`/api/groups/${groupId}`),
    renderStudyGroup,
    "正在加载小组成员与学习信号…",
    { retry: true, skeletonHost: $("#groups-members") }
  );
  // renderStudyGroup 成功时 studyGroup 已赋值；失败时恢复详情区隐藏（与原来一致）。
  if (showSkeleton && studyGroup == null) detailContent.hidden = true;
}

async function submitStudyGroup(form, path, fieldName) {
  if (!user || view !== "groups") return;
  const value = form.elements.namedItem(fieldName).value.trim();
  if (!value) {
    $("#groups-status").classList.add("error");
    $("#groups-status").textContent = fieldName === "name" ? "请输入小组名称。" : "请输入邀请码。";
    return;
  }
  await requestGroups(
    () => api(path, { method: "POST", body: JSON.stringify({ [fieldName]: value }) }),
    (data) => {
      form.reset();
      renderStudyGroup(data);
    },
    fieldName === "name" ? "正在创建小组…" : "正在通过邀请码加入小组…"
  );
}

async function leaveStudyGroup(dissolve = false) {
  if (!user || view !== "groups" || !studyGroup) return;
  if (dissolve && (!studyGroup.is_creator || !confirm(`确定解散“${studyGroup.name}”吗？所有成员都将退出此小组。`))) return;
  const groupId = studyGroup.id;
  await requestGroups(
    () => api(`/api/groups/${groupId}${dissolve ? "" : "/leave"}`, { method: dissolve ? "DELETE" : "POST" }),
    async () => {
      message(dissolve ? "小组已解散。" : "已退出小组。");
      await loadGroups();
    },
    dissolve ? "正在解散小组…" : "正在退出小组…"
  );
}

async function removeStudyGroupMember(member) {
  if (!user || view !== "groups" || !studyGroup || !studyGroup.is_creator || member.id === user.id) return;
  if (!confirm(`确定将“${member.username}”移出“${studyGroup.name}”吗？`)) return;
  const groupId = studyGroup.id;
  await requestGroups(
    () => api(`/api/groups/${groupId}/members/${member.id}`, { method: "DELETE" }),
    async () => {
      message("已移除成员。");
      await openStudyGroup(groupId);
    },
    "正在移除成员…"
  );
}
