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
  status.textContent = "正在加载连续打卡天数排行榜…";
  entries.replaceChildren();
  mine.replaceChildren();
  $("#leaderboard-table-wrap").hidden = true;

  try {
    const data = await api("/api/leaderboard");
    if (user !== currentUser || !user || view !== "leaderboard") return;
    const { me } = data;
    $("#leaderboard-list-title").textContent = `连续打卡天数排行榜 · 前 ${data.leaderboard_size} 名`;
    const rankText = me.is_trial
      ? "体验账号不参与排名，你仍可查看自己的连续打卡天数。"
      : me.rank === null
        ? (user.public_rank_opt_out
          ? "你已设置不参与公开榜单，不会出现在榜单上；你仍可查看自己的连续打卡天数。"
          : "暂无排名，连续打卡至少 1 天即可参与排名。")
        : `第 ${me.rank} 名${me.rank > data.leaderboard_size ? `（未进入前 ${data.leaderboard_size} 名）` : ""}`;
    mine.append(
      element("p", `${me.streak_days} 天`, "leaderboard-streak"),
      element("p", rankText, "leaderboard-my-rank")
    );
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
