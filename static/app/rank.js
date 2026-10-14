// static/app/rank.js — 榜单。
// 由 static/app.js 按领域拆分（C2），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

async function loadLeaderboard() {
  const currentUser = user;
  window.Rank?.load(); // 今日一条、昨日之星、本周热门题目：各自带登录代次与请求序号守卫
  const page = $("#leaderboard-page");
  const status = $("#leaderboard-status");
  const entries = $("#leaderboard-entries");
  const mine = $("#leaderboard-me");
  page.setAttribute("aria-busy", "true");
  const tableWrap = $("#leaderboard-table-wrap");
  entries.replaceChildren();
  mine.replaceChildren();
  tableWrap.hidden = true;
  // 手感层：骨架屏代替"正在加载…"文字；feel.js 未提供骨架 API 时回退到文字。
  if (window.Feel?.skeletonShow) {
    tableWrap.hidden = false;
    window.Feel.skeletonShow(tableWrap, 3);
    status.textContent = "";
  } else {
    status.textContent = "正在加载连续打卡天数排行榜…";
  }

  try {
    const data = await api("/api/leaderboard");
    if (user !== currentUser || !user || view !== "leaderboard") return;
    const { me } = data;
    window.Feel?.skeletonHide?.(tableWrap);
    $("#leaderboard-list-title").textContent = `连续打卡天数排行榜 · 前 ${data.leaderboard_size} 名`;
    const rankText = me.is_trial
      ? "体验账号不参与排名，你仍可查看自己的连续打卡天数。"
      : me.rank === null
        ? (user.public_rank_opt_out
          ? "你没有参与公开榜单（默认不参与），不会出现在榜单上；你仍可查看自己的连续打卡天数。"
          : "暂无排名，连续打卡至少 1 天即可参与排名。")
        : `第 ${me.rank} 名${me.rank > data.leaderboard_size ? `（未进入前 ${data.leaderboard_size} 名）` : ""}`;
    const rankPara = element("p", "", "leaderboard-my-rank");
    if (!me.is_trial && me.rank !== null) {
      // 手感层：名次数字单独成 span 且最终值已在 DOM，feel.js 做数字滚动。
      const rankNumber = element("span", String(me.rank));
      rankNumber.setAttribute("data-countup", "");
      rankPara.append(
        document.createTextNode("第 "),
        rankNumber,
        document.createTextNode(` 名${me.rank > data.leaderboard_size ? `（未进入前 ${data.leaderboard_size} 名）` : ""}`),
      );
    } else {
      rankPara.textContent = rankText;
    }
    mine.append(
      element("p", `${me.streak_days} 天`, "leaderboard-streak"),
      rankPara
    );
    // 默认不参与公开榜单（迁移 86）：在榜单页直接给一个加入入口，走账号菜单里同一个开关。
    const optIn = $("#account-public-rank");
    if (!me.is_trial && user.public_rank_opt_out && optIn && !optIn.checked) {
      const join = element("button", "加入公开榜单", "leaderboard-join");
      join.type = "button";
      join.addEventListener("click", () => {
        join.disabled = true;
        if (!optIn.checked) optIn.click();
      });
      mine.append(join);
    }
    for (const entry of data.entries) {
      const row = element("tr");
      row.append(
        element("td", `第 ${entry.rank} 名`, "leaderboard-rank"),
        element("td", entry.display_name),
        element("td", `${entry.streak_days} 天`, "leaderboard-days")
      );
      entries.append(row);
    }
    status.textContent = data.entries.length ? "" : "暂无上榜用户，完成一次复习评分，开始连续打卡吧。";
    $("#leaderboard-table-wrap").hidden = !data.entries.length;
  } catch (error) {
    window.Feel?.skeletonHide?.(tableWrap);
    tableWrap.hidden = true;
    if (user === currentUser && user && view === "leaderboard") {
      status.textContent = "连续打卡天数排行榜加载失败，请点击上方“刷新”重试。";
    }
    throw error;
  } finally {
    if (user === currentUser && user && view === "leaderboard") {
      page.setAttribute("aria-busy", "false");
    }
  }
}

function configureRank() {
  const hooks = { api, getUser: () => user, getEpoch: () => sessionEpoch, getView: () => view, avatar: avatarElement, refreshPage: () => loadLeaderboard().catch(() => {}) };
  window.Rank?.configure(hooks);
  window.RankAdmin?.configure(hooks);
  window.Typical?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch, getView: () => view, print: () => window.print() });
}
