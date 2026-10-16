"""草稿演算区（scratch）：表格归一化与序列化。"""
import json
import re

from core import config

_SCRATCH_CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")

def _js_string_length(value):
    # JavaScript 的 string.length 与 JSON.stringify().length 都按 UTF-16
    # 码元计数；Python 的 len() 按码点计数，emoji 等增补平面字符会少算。
    return len(value.encode("utf-16-le")) // 2


def _scratch_line_count(value):
    # 与 scratch.js 的 countLines 完全一致：空串算 1 行，否则按 \n 切。
    return 1 if value == "" else value.count("\n") + 1


def _scratch_sanitize_cell(value, max_len):
    # 与 trace-table.js 的 sanitize 一致：控制字符（含换行、制表符、DEL）
    # 换成普通空格，再截断到 max_len。
    return _SCRATCH_CONTROL_CHAR.sub(" ", value)[:max_len]


def normalize_scratch_table(value):
    """按 trace-table.js validate 的规则归一化演算表；结构非法返回 None。

    超出上限的行列丢弃；表头/单元格做控制字符替换与截断；短行补空字符串；
    归一化后序列化超过 20000 字符同样判为非法。
    """
    if not isinstance(value, dict):
        return None
    cols = value.get("cols")
    rows = value.get("rows")
    if not isinstance(cols, list) or not isinstance(rows, list):
        return None
    kept_cols = cols[:config.TRACE_TABLE_MAX_COLS]
    kept_rows = rows[:config.TRACE_TABLE_MAX_ROWS]
    if len(kept_cols) < 1 or len(kept_rows) < 1:
        return None
    norm_cols = []
    for col in kept_cols:
        if not isinstance(col, str):
            return None
        norm_cols.append(_scratch_sanitize_cell(col, config.TRACE_TABLE_MAX_HEADER_LEN))
    norm_rows = []
    for row in kept_rows:
        if not isinstance(row, list):
            return None
        norm_row = []
        for index in range(len(norm_cols)):
            cell = row[index] if index < len(row) else ""
            if not isinstance(cell, str):
                return None
            norm_row.append(_scratch_sanitize_cell(cell, config.TRACE_TABLE_MAX_CELL_LEN))
        norm_rows.append(norm_row)
    model = {"cols": norm_cols, "rows": norm_rows}
    if _js_string_length(
        json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    ) > config.TRACE_TABLE_MAX_SERIALIZED:
        return None
    return model


def scratch_serialized_size(code, fixed, table):
    # 与 scratch.js 的 serializedSize 一致：
    # code.length + fixed.length + (table ? JSON.stringify(table).length : 0)。
    total = _js_string_length(code) + _js_string_length(fixed)
    if table is not None:
        total += _js_string_length(
            json.dumps(table, ensure_ascii=False, separators=(",", ":"))
        )
    return total


def scratch_state(conn, mistake_id):
    """组装 GET / 409 current 用的草稿状态（调用方已确认属主）。"""
    row = conn.execute(
        "SELECT version, code, fixed, table_json, updated_at "
        "FROM mistake_scratch WHERE mistake_id = ?",
        (mistake_id,),
    ).fetchone()
    if row is None:
        return {"version": 0, "code": "", "fixed": "", "table": None,
                "updated_at": None}
    return {
        "version": row["version"],
        "code": row["code"],
        "fixed": row["fixed"],
        "table": json.loads(row["table_json"]) if row["table_json"] else None,
        "updated_at": row["updated_at"],
    }

