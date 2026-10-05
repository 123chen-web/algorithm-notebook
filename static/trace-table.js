(function () {
  "use strict";

  var MAX_COLS = 20;
  var MAX_ROWS = 60;
  var MAX_HEADER_LEN = 20;
  var MAX_CELL_LEN = 60;
  var MAX_SERIALIZED = 20000;

  function clampInt(value, min, max, fallback) {
    var n = Math.floor(Number(value));
    if (!isFinite(n)) n = fallback;
    if (n < min) n = min;
    if (n > max) n = max;
    return n;
  }

  // 将除普通空格以外的控制字符（\x00-\x1F 与 \x7F，含换行、制表符）
  // 逐个替换为单个普通空格，再截断到 maxLen。
  function sanitize(text, maxLen) {
    var s = String(text).replace(/[\x00-\x1F\x7F]/g, " ");
    if (s.length > maxLen) s = s.slice(0, maxLen);
    return s;
  }

  function cloneModel(model) {
    return {
      cols: model.cols.slice(),
      rows: model.rows.map(function (row) { return row.slice(); })
    };
  }

  function emptyRow(width) {
    var row = [];
    for (var i = 0; i < width; i++) row.push("");
    return row;
  }

  function nextColName(cols) {
    var n = 1;
    while (cols.indexOf("v" + n) !== -1) n++;
    return "v" + n;
  }

  function inRange(index, length) {
    return Number.isInteger(index) && index >= 0 && index < length;
  }

  function create(cols, rows) {
    if (cols === undefined) cols = 3;
    if (rows === undefined) rows = 3;
    var colCount = clampInt(cols, 1, MAX_COLS, 3);
    var rowCount = clampInt(rows, 1, MAX_ROWS, 3);
    var colNames = [];
    for (var i = 0; i < colCount; i++) colNames.push("v" + (i + 1));
    var grid = [];
    for (var j = 0; j < rowCount; j++) grid.push(emptyRow(colCount));
    return { cols: colNames, rows: grid };
  }

  function addRow(model, index) {
    if (model.rows.length >= MAX_ROWS) return model;
    var next = cloneModel(model);
    if (!Number.isInteger(index) || index < 0 || index > next.rows.length) {
      index = next.rows.length;
    }
    next.rows.splice(index, 0, emptyRow(next.cols.length));
    return next;
  }

  function removeRow(model, index) {
    if (model.rows.length <= 1) return model;
    if (!inRange(index, model.rows.length)) return model;
    var next = cloneModel(model);
    next.rows.splice(index, 1);
    return next;
  }

  function addCol(model, index) {
    if (model.cols.length >= MAX_COLS) return model;
    var next = cloneModel(model);
    if (!Number.isInteger(index) || index < 0 || index > next.cols.length) {
      index = next.cols.length;
    }
    next.cols.splice(index, 0, nextColName(model.cols));
    for (var i = 0; i < next.rows.length; i++) next.rows[i].splice(index, 0, "");
    return next;
  }

  function removeCol(model, index) {
    if (model.cols.length <= 1) return model;
    if (!inRange(index, model.cols.length)) return model;
    var next = cloneModel(model);
    next.cols.splice(index, 1);
    for (var i = 0; i < next.rows.length; i++) next.rows[i].splice(index, 1);
    return next;
  }

  function setCell(model, r, c, text) {
    if (!inRange(r, model.rows.length) || !inRange(c, model.cols.length)) return model;
    var next = cloneModel(model);
    next.rows[r][c] = sanitize(text, MAX_CELL_LEN);
    return next;
  }

  function setHeader(model, c, text) {
    if (!inRange(c, model.cols.length)) return model;
    var next = cloneModel(model);
    next.cols[c] = sanitize(text, MAX_HEADER_LEN);
    return next;
  }

  function pasteGrid(model, r, c, text) {
    if (!Number.isInteger(r) || !Number.isInteger(c) || r < 0 || c < 0) {
      return { model: model, truncated: false };
    }
    if (typeof text !== "string" || text.length === 0) {
      return { model: model, truncated: false };
    }
    var lines = text.split(/\r\n|\r|\n/);
    // 表格软件复制的文本常以一个换行结尾，去掉由此产生的空尾行
    if (lines.length > 1 && lines[lines.length - 1] === "" && /[\r\n]$/.test(text)) {
      lines.pop();
    }
    var grid = lines.map(function (line) { return line.split("\t"); });

    // 起点本身已超出上限，所有内容都被丢弃
    if (r >= MAX_ROWS || c >= MAX_COLS) {
      return { model: model, truncated: true };
    }

    var truncated = false;
    if (r + grid.length > MAX_ROWS) truncated = true;
    var i, j;
    var targetCols = model.cols.length;
    for (i = 0; i < grid.length; i++) {
      if (c + grid[i].length > MAX_COLS) truncated = true;
      if (r + i < MAX_ROWS) {
        targetCols = Math.max(targetCols, Math.min(c + grid[i].length, MAX_COLS));
      }
    }
    var targetRows = Math.min(Math.max(model.rows.length, r + grid.length), MAX_ROWS);

    var next = cloneModel(model);
    while (next.cols.length < targetCols) {
      next.cols.push(nextColName(next.cols));
      for (i = 0; i < next.rows.length; i++) next.rows[i].push("");
    }
    while (next.rows.length < targetRows) {
      next.rows.push(emptyRow(next.cols.length));
    }
    for (i = 0; i < grid.length; i++) {
      var rr = r + i;
      if (rr >= MAX_ROWS) break;
      for (j = 0; j < grid[i].length; j++) {
        var cc = c + j;
        if (cc >= MAX_COLS) break;
        next.rows[rr][cc] = sanitize(grid[i][j], MAX_CELL_LEN);
      }
    }
    return { model: next, truncated: truncated };
  }

  function validate(value) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
    var cols = value.cols;
    var rows = value.rows;
    if (!Array.isArray(cols) || !Array.isArray(rows)) return null;

    // 超出上限的行列直接丢弃（丢弃部分不再做类型检查）
    var keptCols = cols.slice(0, MAX_COLS);
    var keptRows = rows.slice(0, MAX_ROWS);
    if (keptCols.length < 1 || keptRows.length < 1) return null;

    var normCols = [];
    for (var i = 0; i < keptCols.length; i++) {
      if (typeof keptCols[i] !== "string") return null;
      normCols.push(sanitize(keptCols[i], MAX_HEADER_LEN));
    }
    var normRows = [];
    for (var j = 0; j < keptRows.length; j++) {
      var row = keptRows[j];
      if (!Array.isArray(row)) return null;
      var normRow = [];
      for (var k = 0; k < normCols.length; k++) {
        var cell = k < row.length ? row[k] : "";
        if (typeof cell !== "string") return null;
        normRow.push(sanitize(cell, MAX_CELL_LEN));
      }
      normRows.push(normRow);
    }
    var model = { cols: normCols, rows: normRows };
    if (JSON.stringify(model).length > MAX_SERIALIZED) return null;
    return model;
  }

  function toJSON(model) {
    return JSON.stringify(model);
  }

  var TraceTable = {
    create: create,
    addRow: addRow,
    removeRow: removeRow,
    addCol: addCol,
    removeCol: removeCol,
    setCell: setCell,
    setHeader: setHeader,
    pasteGrid: pasteGrid,
    validate: validate,
    toJSON: toJSON
  };

  if (typeof window !== "undefined") window.TraceTable = TraceTable;
  if (typeof module !== "undefined") module.exports = TraceTable;
})();
