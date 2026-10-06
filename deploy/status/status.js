(function () {
  "use strict";
  var summary = document.getElementById("summary");
  var list = document.getElementById("checks");
  var time = document.getElementById("time");

  function row(label, ok) {
    var item = document.createElement("li");
    var name = document.createElement("span");
    var state = document.createElement("span");
    name.textContent = label;
    state.textContent = ok ? "正常" : "异常";
    state.className = ok ? "ok" : "bad";
    item.append(name, state);
    return item;
  }

  fetch("status.json", { cache: "no-store" })
    .then(function (response) { return response.json(); })
    .then(function (data) {
      summary.textContent = data.ok ? "一切正常" : "部分功能异常，正在处理";
      summary.className = "summary " + (data.ok ? "ok" : "bad");
      Object.keys(data.checks).forEach(function (label) {
        list.append(row(label, data.checks[label]));
      });
      time.textContent = "最近检查：" + new Date(data.checked_at).toLocaleString("zh-CN");
    })
    .catch(function () {
      summary.textContent = "暂时读不到状态，请稍后再试";
      summary.className = "summary bad";
    });
})();
