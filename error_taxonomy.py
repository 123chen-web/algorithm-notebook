"""错因固定词表 + 判题类型 + 最小反例的后端常量。

词表是唯一的事实来源：前端通过 GET /api/error-taxonomy 读取，不要在前后端各写一份。
- 错因（cause）：稳定 code、中文名、所属大类、一句提示。总数 ≤14。
- 判题类型（verdict）：AtCoder 术语表（WA/TLE/RE/MLE，另加 OLE/IE 的思路但本站只收常用项 + NOANS）。
- 最小反例（counterexample）：纯文本，≤1000 字，放在每条易错点上。

大类沿用“想错了 / 写错了”二分（Swarthmore 算法课讲义的思路），统计页能回答
“你更多是想错了还是写错了”。“超时”不是错因：complexity（算法复杂度）和
perf_io（常数与输入输出慢）分开（Codeforces TLE 研究的结论）。
overflow 归“写错了”：溢出是选错了数据类型（int 撑不住、浮点精度丢），
表现是答案错误（WA）而不是变慢，不是性能问题。
"""

# 大类：(code, 中文名)
CAUSE_GROUPS = (
    ("reasoning", "想错了"),
    ("implementation", "写错了"),
    ("performance", "跑得慢"),
    ("environment", "环境与其他"),
)

# 错因：(code, 中文名, 大类 code, 一句提示)。总数 14，不超过上限。
CAUSES = (
    ("wrong_idea", "思路错误", "reasoning", "方向想错了，换个思路再试"),
    ("misread", "审题/漏条件", "reasoning", "题面条件没看全，漏了一句"),
    ("concept", "概念混淆", "reasoning", "概念记混、用错地方了"),
    ("formula", "公式/模板记错", "reasoning", "公式或模板凭记忆写错了"),
    ("boundary", "边界/特例", "implementation", "n=0、n=1、最大值这类边界"),
    ("impl", "实现细节", "implementation", "下标、循环、变量写错了"),
    ("careless", "粗心/笔误", "implementation", "看错、写错、手滑"),
    ("api", "语法/接口", "implementation", "语法或库函数用法不对"),
    ("overflow", "溢出/精度", "implementation", "int 撑不住、浮点精度丢了"),
    ("complexity", "算法复杂度", "performance", "算法本身太慢，复杂度太高"),
    ("perf_io", "常数与输入输出慢", "performance", "常数大、IO 太慢被卡掉"),
    ("uninit", "未初始化", "environment", "本机能过、评测机上翻车"),
    ("untested", "测试没覆盖", "environment", "样例没测到这个情况"),
    ("other", "其他", "environment", "以上都不是，先记下来"),
)

# 判题类型：(code, 中文名)。'' 表示未填，不在词表里，单独处理。
VERDICTS = (
    ("WA", "答案错误"),
    ("TLE", "超时"),
    ("MLE", "超内存"),
    ("RE", "运行错误"),
    ("CE", "编译错误"),
    ("NOANS", "没做出来/不会"),
)

COUNTEREXAMPLE_MAX_LENGTH = 1000

_CAUSE_BY_CODE = {code: (label, group, hint) for code, label, group, hint in CAUSES}
_GROUP_LABEL = dict(CAUSE_GROUPS)
_VERDICT_LABEL = dict(VERDICTS)


class TaxonomyError(ValueError):
    """校验失败时的中文提示，接口层原样返回 422。"""


def cause_codes():
    return [code for code, _, _, _ in CAUSES]


def cause_label(code):
    return _CAUSE_BY_CODE[code][0]


def cause_group(code):
    return _CAUSE_BY_CODE[code][1]


def group_label(group):
    return _GROUP_LABEL[group]


def verdict_label(code):
    return _VERDICT_LABEL[code]


def _options_text():
    return "、".join(f"{label}（{code}）" for code, label, _, _ in CAUSES)


def validate_cause_codes(values):
    """标签必须是词表里的 code；非法时 422 并在错误里给出可选项。"""
    codes = []
    seen = set()
    for value in values:
        code = str(value).strip()
        if code not in _CAUSE_BY_CODE:
            raise TaxonomyError(f"错因标签只能从固定词表里选：{_options_text()}")
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def validate_verdict(value):
    """判题类型必须是词表里的 code 或空串（未填）。"""
    text = str(value or "").strip().upper()
    if text and text not in _VERDICT_LABEL:
        options = "、".join(f"{label}（{code}）" for code, label in VERDICTS)
        raise TaxonomyError(f"判题类型只能是：{options}，或留空")
    return text


def validate_counterexample(value):
    text = str(value or "")
    if len(text) > COUNTEREXAMPLE_MAX_LENGTH:
        raise TaxonomyError(f"最小反例最多 {COUNTEREXAMPLE_MAX_LENGTH} 个字")
    return text


# 迁移 80 用的同义词表：历史自由标签 → 词表 code。
# 覆盖 tags.SUGGESTED_TAGS 和 tags.REASON_SUGGEST_TAGS 里已有的全部写法，
# 再加常见的同义说法。查不到的归 other。
SYNONYMS = {
    # wrong_idea
    "思路错误": "wrong_idea", "思路错": "wrong_idea", "没思路": "wrong_idea",
    "思路不对": "wrong_idea", "想错了": "wrong_idea", "方向错了": "wrong_idea",
    # misread
    "没读清题": "misread", "漏条件": "misread", "审题": "misread",
    "没看清题": "misread", "读错题": "misread", "漏看条件": "misread",
    # concept
    "概念混淆": "concept", "概念不清": "concept", "概念记混": "concept",
    # formula
    "公式记错": "formula", "模板记错": "formula", "公式错了": "formula",
    # boundary
    "边界": "boundary", "边界没处理": "boundary", "越界": "boundary",
    "边界条件": "boundary", "特例": "boundary", "边界没考虑": "boundary",
    "漏边界": "boundary",
    # impl
    "实现细节": "impl", "下标": "impl", "下标越界": "impl", "循环": "impl",
    "循环条件": "impl", "变量": "impl", "写错了": "impl",
    # careless
    "粗心": "careless", "笔误": "careless", "手滑": "careless", "看错了": "careless",
    # api
    "语法或接口": "api", "语法坑": "api", "语法": "api", "接口": "api",
    "库函数": "api", "api": "api",
    # overflow
    "溢出": "overflow", "精度": "overflow", "int溢出": "overflow",
    "爆int": "overflow", "longlong": "overflow",
    # complexity
    "复杂度": "complexity", "超时": "complexity", "算法太慢": "complexity",
    "tle": "complexity",
    # perf_io
    "输入输出慢": "perf_io", "io": "perf_io", "常数": "perf_io", "常数大": "perf_io",
    # uninit
    "未初始化": "uninit", "本地过评测机不过": "uninit",
    # untested
    "测试没覆盖": "untested", "样例没覆盖": "untested", "没测到": "untested",
    # other（显式归类，避免被当成未知）
    "其他": "other", "其它": "other",
}


def map_legacy_tag(tag):
    """历史自由标签 → 词表 code；已经是 code 的原样返回；查不到的归 other。"""
    text = str(tag or "").strip()
    if text in _CAUSE_BY_CODE:
        return text
    key = text.casefold()
    for synonym, code in SYNONYMS.items():
        if synonym.casefold() == key:
            return code
    return "other"
