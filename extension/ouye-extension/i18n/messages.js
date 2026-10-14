/* 界面文案集中管理。中文为主；后续加语言只需扩展 OY_MSG。 */
"use strict";

globalThis.OY_MSG = {
  zh: {
    appName: "欧叶OY 刷题伴侣",
    floatBtn: "记到欧叶OY",
    floatBtnTitle: "把这道题记到欧叶OY",
    formTitle: "记到欧叶OY",
    labelTitle: "题目标题",
    labelReason: "一句话错因（可跳过）",
    reasonPlaceholder: "我以为……其实……",
    labelChips: "快捷错因",
    chips: ["边界", "思路错", "审题", "粗心", "不会"],
    submit: "记下来",
    cancel: "取消",
    dupTitle: "这道题已经记过了",
    dupHint: "同一来源的这道题已在欧叶OY 里，无需重复收录。",
    dupOpen: "打开欧叶OY",
    dupForce: "仍要新建一条",
    dupCancel: "取消",
    unsupportedSource: "后端暂不支持该来源导入",
    unsupportedSourceHint:
      "题目信息已提取好。当前后端接口只接受 LeetCode / 力扣 / Codeforces / 牛客，" +
      "洛谷与 AtCoder 需要后端扩展来源白名单（见 EXT_REPORT）。你可以直接复制下面文本去欧叶OY 手动新建。",
    copyInfo: "复制题目信息",
    copied: "已复制",
    noToken: "还没配置 API token：点工具栏插件图标 → 选项，先粘贴 token。",
    noBaseUrl: "还没配置后端地址：点工具栏插件图标 → 选项，先填写。",
    badToken: "API token 无效，请重新签发后粘贴。",
    importOk: "已记到欧叶OY",
    importFail: "记入失败",
    reasonSavedLocal: "错因已一并记到欧叶OY",
    needManualContent: "没能自动提取到题面，请手动粘贴（或留空稍后在网页端补充）。",
    extractFail: "这页没能识别出题目信息（网站可能改版了），请手动填写标题。",
    notProblemPage: "当前页不是支持的题目页。",
    connOk: "已连接",
    connBadToken: "token 无效",
    connUnreachable: "连不上后端",
    connNoConfig: "未配置",
    todayReview: "今日待复习",
    todayReviewPending: "待后端支持",
    openOuye: "打开欧叶OY",
    openOptions: "选项",
    recordedCount: "已收录",
    pendingReasons: "待同步错因",
    collapseTitle: "拖到屏幕边缘可收起",
  },
};

globalThis.OY_T = function OY_T(key) {
  const dict = (globalThis.OY_MSG && globalThis.OY_MSG.zh) || {};
  return Object.prototype.hasOwnProperty.call(dict, key) ? dict[key] : key;
};
