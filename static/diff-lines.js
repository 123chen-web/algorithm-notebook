var DiffLines = (function () {
  "use strict";

  var DEFAULT_MAX_LINES = 2000;

  // 按 \n 切行；空文本视为 0 行；仅去掉每一行末尾的一个 \r（兼容 \r\n）。
  function toLines(text) {
    if (text === null || text === undefined || text === "") return [];
    var lines = String(text).split("\n");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (line.length > 0 && line.charAt(line.length - 1) === "\r") {
        lines[i] = line.slice(0, -1);
      }
    }
    return lines;
  }

  function diffLines(oldText, newText, options) {
    var maxLines =
      options && options.maxLines != null ? options.maxLines : DEFAULT_MAX_LINES;

    var a = toLines(oldText);
    var b = toLines(newText);
    var n = a.length;
    var m = b.length;

    if (n > maxLines || m > maxLines) {
      return { tooLarge: true };
    }

    // LCS 动态规划表，逻辑上是 (n+1) x (m+1)，底层用一维 Uint32Array。
    var width = m + 1;
    var dp = new Uint32Array((n + 1) * width);

    for (var i = 1; i <= n; i++) {
      var rowBase = i * width;
      var prevBase = (i - 1) * width;
      var lineA = a[i - 1];
      for (var j = 1; j <= m; j++) {
        if (lineA === b[j - 1]) {
          dp[rowBase + j] = dp[prevBase + j - 1] + 1;
        } else {
          var up = dp[prevBase + j];
          var left = dp[rowBase + j - 1];
          dp[rowBase + j] = up >= left ? up : left;
        }
      }
    }

    // 从右下角回溯，生成 same / removed / added 序列。
    var reversed = [];
    var ii = n;
    var jj = m;
    while (ii > 0 || jj > 0) {
      if (ii > 0 && jj > 0 && a[ii - 1] === b[jj - 1]) {
        reversed.push({
          type: "same",
          oldLine: ii,
          newLine: jj,
          text: a[ii - 1]
        });
        ii--;
        jj--;
      } else if (
        ii > 0 &&
        (jj === 0 || dp[(ii - 1) * width + jj] >= dp[ii * width + jj - 1])
      ) {
        reversed.push({
          type: "removed",
          oldLine: ii,
          newLine: null,
          text: a[ii - 1]
        });
        ii--;
      } else {
        reversed.push({
          type: "added",
          oldLine: null,
          newLine: jj,
          text: b[jj - 1]
        });
        jj--;
      }
    }

    reversed.reverse();
    return reversed;
  }

  function summarize(diffResult) {
    var counts = { added: 0, removed: 0, same: 0 };
    if (!diffResult || typeof diffResult.length !== "number") return counts;
    for (var i = 0; i < diffResult.length; i++) {
      var type = diffResult[i].type;
      if (type === "added") counts.added++;
      else if (type === "removed") counts.removed++;
      else if (type === "same") counts.same++;
    }
    return counts;
  }

  return {
    diffLines: diffLines,
    summarize: summarize
  };
})();

if (typeof window !== "undefined") window.DiffLines = DiffLines;
if (typeof module !== "undefined") module.exports = DiffLines;
