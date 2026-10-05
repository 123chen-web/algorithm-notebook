"""build_anki_text 的 pytest 测试集。

每个用例的预期值都按以下规则手工推得：
- 文件头 4 行原样输出；
- 每条记录 = GUID \t 正面 \t 背面 \t 标签；
- 用户文字先 HTML 转义（& < > " '），再把 \n -> <br>、\t -> 4 空格，
  其余控制字符删除；
- url 仅 http/https 前缀才输出链接行；
- 标签空白转下划线、去 HTML 特殊字符、去空、追加 oy-export、顺序去重。
"""

from anki_export import build_anki_text

HEADER = "#separator:tab\n#html:true\n#guid column:1\n#tags column:4\n"


def _rec(**kw):
    """构造一条“全缺省”的记录，按需覆盖字段。"""
    base = dict(id=1, title="t", zone="z", url="", cause="", notes="",
                code="", fixed_code="", tags=[])
    base.update(kw)
    return base


# ---------- 文件头与整体结构 ----------

def test_empty_records_outputs_header_only():
    # 空列表只输出文件头，文件头本身每行一个换行，整体以换行结尾
    assert build_anki_text([]) == HEADER


def test_header_first_four_lines_exact():
    out = build_anki_text([_rec()])
    lines = out.splitlines()
    assert lines[:4] == ["#separator:tab", "#html:true",
                         "#guid column:1", "#tags column:4"]


def test_output_ends_with_newline():
    assert build_anki_text([_rec()]).endswith("\n")
    assert not build_anki_text([_rec()]).endswith("\n\n")


def test_record_count_equals_lines_minus_header():
    # splitlines 检查：每条记录恰好一行
    out = build_anki_text([_rec(id=1), _rec(id=2), _rec(id=3)])
    assert len(out.splitlines()) == 4 + 3


def test_each_record_line_has_four_columns():
    # 4 列 = 3 个制表符（字段内的 \t 已被替换成空格）
    out = build_anki_text([_rec(code="a\tb")])
    row = out.splitlines()[4]
    assert row.count("\t") == 3
    assert len(row.split("\t")) == 4


# ---------- GUID ----------

def test_guid_format():
    row = build_anki_text([_rec(id=42)]).splitlines()[4]
    assert row.split("\t")[0] == "oy-42"


def test_guid_stable_across_calls():
    # 同一输入两次调用输出完全一致（GUID 稳定、无随机性）
    r = [_rec(id=7, tags=["a b"])]
    assert build_anki_text(r) == build_anki_text(r)


def test_guid_uses_str_of_int():
    row = build_anki_text([_rec(id=0)]).splitlines()[4]
    assert row.startswith("oy-0\t")


# ---------- 单条 / 多条 / 顺序 ----------

def test_single_record_full_expected_line():
    rec = _rec(id=7, title="两数之和", zone="哈希表",
               url="https://leetcode.cn/problems/two-sum",
               cause="暴力超时", notes="先排序再双指针",
               code="if a < b:\n    pass", fixed_code="return a & b",
               tags=["数组", "hot 100"])
    front = ("<div><b>两数之和</b></div><div>分区：哈希表</div>"
             "<div>链接：https://leetcode.cn/problems/two-sum</div>")
    back = ("<div><b>错因</b>：暴力超时</div>"
            "<div><b>当时的思路</b>：先排序再双指针</div>"
            "<div><b>错误代码</b></div><pre><code>if a &lt; b:<br>    pass</code></pre>"
            "<div><b>修正代码</b></div><pre><code>return a &amp; b</code></pre>")
    tags = "数组 hot_100 oy-export"  # 空白转下划线，追加 oy-export
    expected = HEADER + "\t".join(("oy-7", front, back, tags)) + "\n"
    assert build_anki_text([rec]) == expected


def test_multiple_records_preserve_input_order():
    out = build_anki_text([_rec(id=3), _rec(id=1), _rec(id=2)])
    rows = out.splitlines()[4:]
    assert [r.split("\t")[0] for r in rows] == ["oy-3", "oy-1", "oy-2"]


# ---------- 正面：题名 / 分区 / 链接 ----------

def test_front_title_and_zone():
    front = build_anki_text([_rec(title="T", zone="Z")]).splitlines()[4].split("\t")[1]
    assert front == "<div><b>T</b></div><div>分区：Z</div>"


def test_front_url_http_included():
    front = build_anki_text([_rec(url="http://a.b/c")]).splitlines()[4].split("\t")[1]
    assert "<div>链接：http://a.b/c</div>" in front


def test_front_url_https_included():
    front = build_anki_text([_rec(url="https://a.b/c?x=1&y=2")]).splitlines()[4].split("\t")[1]
    # url 也是用户文字，& 要被转义
    assert "<div>链接：https://a.b/c?x=1&amp;y=2</div>" in front


def test_front_url_javascript_dropped():
    front = build_anki_text([_rec(url="javascript:alert(1)")]).splitlines()[4].split("\t")[1]
    assert "链接" not in front
    assert "javascript" not in front


def test_front_url_other_scheme_dropped():
    front = build_anki_text([_rec(url="ftp://a.b/c")]).splitlines()[4].split("\t")[1]
    assert "链接" not in front


def test_front_url_empty_dropped():
    front = build_anki_text([_rec(url="")]).splitlines()[4].split("\t")[1]
    assert "链接" not in front


def test_front_url_missing_key_dropped():
    rec = {"id": 1, "title": "t", "zone": "z"}  # 没有 url 键
    front = build_anki_text([rec]).splitlines()[4].split("\t")[1]
    assert front == "<div><b>t</b></div><div>分区：z</div>"


# ---------- HTML 转义 ----------

def test_escape_script_tag():
    front = build_anki_text([_rec(title="<script>alert(1)</script>")]).splitlines()[4].split("\t")[1]
    assert "<script>" not in front
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in front


def test_escape_ampersand_first():
    # "&lt;" 里的 & 必须先转义为 &amp;，得到 &amp;lt;
    front = build_anki_text([_rec(title="a & b < c")]).splitlines()[4].split("\t")[1]
    assert "a &amp; b &lt; c" in front


def test_escape_quotes():
    front = build_anki_text([_rec(title='say "hi" it\'s')]).splitlines()[4].split("\t")[1]
    assert "say &quot;hi&quot; it&#x27;s" in front


def test_escape_in_zone():
    front = build_anki_text([_rec(zone="DP<hard>&more")]).splitlines()[4].split("\t")[1]
    assert "<div>分区：DP&lt;hard&gt;&amp;more</div>" in front


# ---------- 换行 / 制表符 / 控制字符 ----------

def test_newline_replaced_with_br():
    back = build_anki_text([_rec(notes="line1\nline2\nline3")]).splitlines()[4].split("\t")[2]
    assert "line1<br>line2<br>line3" in back
    assert "\n" not in back


def test_crlf_becomes_br_without_cr():
    # \r\n：\n 变 <br>，\r 作为控制字符被删除 -> 只剩一个 <br>
    back = build_anki_text([_rec(cause="a\r\nb")]).splitlines()[4].split("\t")[2]
    assert "a<br>b" in back


def test_tab_replaced_with_four_spaces():
    back = build_anki_text([_rec(code="x=1\t# c")]).splitlines()[4].split("\t")[2]
    assert "x=1    # c" in back
    assert "\t" not in back


def test_control_characters_removed():
    back = build_anki_text([_rec(notes="a\x00b\x07c\x1fd")]).splitlines()[4].split("\t")[2]
    assert "abcd" in back


# ---------- 背面结构与代码块 ----------

def test_back_section_order():
    rec = _rec(cause="C", notes="N", code="x", fixed_code="y")
    back = build_anki_text([rec]).splitlines()[4].split("\t")[2]
    assert back == ("<div><b>错因</b>：C</div><div><b>当时的思路</b>：N</div>"
                    "<div><b>错误代码</b></div><pre><code>x</code></pre>"
                    "<div><b>修正代码</b></div><pre><code>y</code></pre>")


def test_back_code_block_escapes_html():
    back = build_anki_text([_rec(code='if a < b && c > d:')]).splitlines()[4].split("\t")[2]
    assert "<pre><code>if a &lt; b &amp;&amp; c &gt; d:</code></pre>" in back


def test_back_empty_back_when_all_absent():
    row = build_anki_text([_rec()]).splitlines()[4]
    assert row.split("\t")[2] == ""  # 背面为空字符串，列仍然存在


def test_back_partial_sections_only():
    # 只有 notes 和 fixed_code 有内容时，只输出这两段
    back = build_anki_text([_rec(notes="N", fixed_code="f")]).splitlines()[4].split("\t")[2]
    assert back == ("<div><b>当时的思路</b>：N</div>"
                    "<div><b>修正代码</b></div><pre><code>f</code></pre>")


def test_empty_string_treated_as_absent():
    back = build_anki_text([_rec(cause="", code="")]).splitlines()[4].split("\t")[2]
    assert back == ""


def test_missing_optional_keys():
    # 缺省字段（键不存在）不报错，按无内容处理
    rec = {"id": 5, "title": "t", "zone": "z"}
    row = build_anki_text([rec]).splitlines()[4]
    assert row.split("\t")[2] == ""
    assert row.split("\t")[3] == "oy-export"


# ---------- 标签列 ----------

def test_tags_always_appended():
    tags = build_anki_text([_rec(tags=["a"])]).splitlines()[4].split("\t")[3]
    assert tags == "a oy-export"


def test_tags_empty_list_gives_only_default():
    tags = build_anki_text([_rec(tags=[])]).splitlines()[4].split("\t")[3]
    assert tags == "oy-export"


def test_tags_whitespace_to_underscore():
    tags = build_anki_text([_rec(tags=["hot 100", "a\tb", "c\nd"])]).splitlines()[4].split("\t")[3]
    assert tags == "hot_100 a_b c_d oy-export"


def test_tags_html_special_chars_removed():
    tags = build_anki_text([_rec(tags=["<b>&\"'x"])]).splitlines()[4].split("\t")[3]
    assert tags.split(" ")[0] == "bx"  # < > & " ' 全部被去掉


def test_tags_empty_after_cleaning_dropped():
    tags = build_anki_text([_rec(tags=["", "  ", "<>&", "ok"])]).splitlines()[4].split("\t")[3]
    # "  " -> "__" 非空保留；"<>&" 清洗后为空被丢弃
    assert tags == "__ ok oy-export"


def test_tags_dedupe_preserves_order():
    tags = build_anki_text([_rec(tags=["b", "a", "b", "c", "a"])]).splitlines()[4].split("\t")[3]
    assert tags == "b a c oy-export"


def test_tags_user_supplied_oy_export_deduped():
    tags = build_anki_text([_rec(tags=["oy-export", "x"])]).splitlines()[4].split("\t")[3]
    assert tags == "oy-export x"  # 只出现一次，位置在首次出现处


# ---------- 健壮性 ----------

def test_long_text_not_truncated():
    long_title = "题" * 10000
    front = build_anki_text([_rec(title=long_title)]).splitlines()[4].split("\t")[1]
    assert long_title in front
    assert len(front) > 10000


def test_id_zero_and_negative():
    rows = build_anki_text([_rec(id=0), _rec(id=-3)]).splitlines()[4:]
    assert rows[0].startswith("oy-0\t")
    assert rows[1].startswith("oy--3\t")  # str(-3) -> "-3"，前缀照拼
