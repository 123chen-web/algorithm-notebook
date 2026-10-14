"""做题验证复习：成绩由做题结果决定，不由自评决定。

自评（只回忆）容易偏高，而 FSRS 完全相信给它的成绩，所以这里把规矩集中放一处：

1. 默写、重做原题、做类似题三种“验证”方式，成绩由结果算出（`quality_from_evidence`），
   前端只上报做题结果，不再让用户二次选分；服务端重算后覆盖前端传来的分数。
2. 只回忆（自评）最高算 4（记得），不能给 5（很熟）。
3. 连续两次都是自评，下一次间隔最多 14 天；中间做过一次验证，限制就解除。

第 2、3 条只对算法分区生效：其他分区（数学、408 等）没有做题验证的办法，自评是唯一的评分方式，
给它封顶只会让这些题永远长不大。
"""
import json
from datetime import timedelta

from fastapi import HTTPException

VERIFIABLE_ZONE = "算法"
VERIFIED_MODES = ("rewrite", "redo", "similar")
RESULTS = ("clean", "retry", "hinted", "failed")
SELF_RATING_MAX_QUALITY = 4
UNVERIFIED_STREAK_MAX_INTERVAL_DAYS = 14
# 原题重做：用时不超过时限的 1/3 才算“很熟”。
EASY_TIME_FRACTION = 3
# 默写：逐行相似度 >= 0.85 记得，>= 0.6 很吃力，否则答错。默写不给“很熟”。
REWRITE_GOOD_SIMILARITY = 0.85
REWRITE_OK_SIMILARITY = 0.6


def quality_from_evidence(mode: str, evidence: dict) -> int:
    """按做题结果算成绩（0 答错 / 3 很吃力 / 4 记得 / 5 很熟）。

    redo / similar 看 result：
      failed  没做出来或看了题解 → 0
      hinted  看了提示才做出来 → 3
      retry   改了几次才通过 → 4（超时则 3）
      clean   一次通过 → 4（超时则 3）；原题重做且用时不超过时限 1/3 → 5
    rewrite 看 similarity。
    """
    if mode == "rewrite":
        similarity = evidence.get("similarity")
        if not isinstance(similarity, (int, float)) or isinstance(similarity, bool):
            raise ValueError("默写缺少相似度")
        if similarity >= REWRITE_GOOD_SIMILARITY:
            return 4
        return 3 if similarity >= REWRITE_OK_SIMILARITY else 0
    if mode not in ("redo", "similar"):
        raise ValueError("只有默写、重做和类似题可以按结果计分")
    result = evidence.get("result")
    if result not in RESULTS:
        raise ValueError("缺少做题结果")
    seconds = evidence.get("seconds")
    limit = evidence.get("limit_seconds")
    timed = isinstance(seconds, int) and isinstance(limit, int) and limit > 0
    over_limit = timed and seconds > limit
    if result == "failed":
        return 0
    if result == "hinted" or over_limit:
        return 3
    if result == "clean" and mode == "redo" and timed and seconds * EASY_TIME_FRACTION <= limit:
        return 5
    return 4


def settle(mode: str, quality: int, evidence_text: str, zone: str = "算法") -> tuple[int, str]:
    """返回真正用来排期的成绩和要保存的 evidence。

    - 带 auto 标记的验证结果：服务端重算成绩，忽略前端传来的分数；
    - 只回忆：最高 4，被压低时把原分记进 evidence，方便以后看自评是否虚高。
    """
    evidence = json.loads(evidence_text) if evidence_text else {}
    if evidence.get("auto") is True:
        if mode not in VERIFIED_MODES:
            raise HTTPException(422, "只有默写、重做和类似题可以按结果计分")
        try:
            computed = quality_from_evidence(mode, evidence)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        evidence["suggested"] = computed
        evidence["final"] = computed
        return computed, json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    if mode == "recall" and zone == VERIFIABLE_ZONE and quality > SELF_RATING_MAX_QUALITY:
        evidence.setdefault("suggested", quality)
        evidence["final"] = SELF_RATING_MAX_QUALITY
        return SELF_RATING_MAX_QUALITY, json.dumps(evidence, separators=(",", ":"))
    return quality, evidence_text


def previous_mode(conn, mistake_id: int) -> str | None:
    row = conn.execute(
        "SELECT mode FROM reviews WHERE mistake_id = ? ORDER BY id DESC LIMIT 1",
        (mistake_id,),
    ).fetchone()
    return row["mode"] if row else None


def last_modes(conn, mistake_ids) -> dict:
    """每条易错点最近一次评分的方式，复习队列用它提示“上次只是自评”。"""
    result = {}
    ids = list(mistake_ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            "SELECT mistake_id, mode FROM reviews WHERE id IN ("
            f"SELECT MAX(id) FROM reviews WHERE mistake_id IN ({marks}) GROUP BY mistake_id)",
            chunk,
        ):
            result[row["mistake_id"]] = row["mode"]
    return result


def limit_unverified_streak(
    state: dict, day, mode: str, last_mode: str | None, zone: str = "算法",
) -> tuple[dict, bool]:
    """连续两次自评：间隔最多 14 天。只改间隔和到期日，不动稳定度和难度。"""
    if (
        zone == VERIFIABLE_ZONE and mode == "recall" and last_mode == "recall"
        and state["interval_days"] > UNVERIFIED_STREAK_MAX_INTERVAL_DAYS
    ):
        capped = UNVERIFIED_STREAK_MAX_INTERVAL_DAYS
        return {**state, "interval_days": capped, "due_date": (day + timedelta(days=capped)).isoformat()}, True
    return state, False


def limit_previews(previews: dict, day, mode: str, last_mode: str | None, zone: str = "算法") -> dict:
    """预览要和真正评分一致：自评没有“很熟”，连续自评有间隔上限。"""
    result = {}
    for quality, state in previews.items():
        if mode == "recall" and zone == VERIFIABLE_ZONE and int(quality) > SELF_RATING_MAX_QUALITY:
            state = previews[str(SELF_RATING_MAX_QUALITY)]
        result[quality] = limit_unverified_streak(state, day, mode, last_mode, zone)[0]
    return result
