"""掌握度趋势：按分区估算"现在还记得多少"，并找出快被遗忘、该先复习的分区。

模型很简单，也只用现有数据（题目创建时间 + 每次复习的时间和排定的下次日期），不调用 AI：

- 每条易错点在某一天 D 的"记忆保持率" R = 0.9 ** (t / S)
  t = 距离最近一次学习（最近一次复习，没复习过就是录入当天）过去的天数；
  S = 那次学习之后排定的间隔天数（至少 1；还没复习过的新记录按 1 天算）。
  也就是：按计划准时复习时保持率约 90%，拖得越久掉得越快，间隔越长的越牢。
- 分区在 D 的掌握度 = 该分区在 D 这天已经存在的易错点的 R 的平均值（百分数）。
- 时间旅行：算过去某天时，只看那天之前录入的记录、那天之前发生的复习。

所有日期按用户自己的时区划分本地日，与连续打卡、本周战报同一口径。
"""

from bisect import bisect_right
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

RETENTION_AT_DUE = 0.9
FADING_BELOW = 70.0  # 掌握度低于这个百分数，且还有到期的，就算"快被遗忘"
AT_RISK_BELOW = 0.7  # 单条易错点的保持率低于这个值，就算"快忘了"
SAMPLE_STEP_DAYS = 7
CHANGE_WINDOW_DAYS = 14


def retention(days_since, interval_days):
    return RETENTION_AT_DUE ** (days_since / max(1, interval_days))


def _local_day(zone, stamp):
    return datetime.fromisoformat(stamp).astimezone(zone).date()


def load_mistakes(conn, user_id, timezone_name):
    """每条易错点：分区、录入日、到期日，以及按时间排好的"学习事件"[(日期, 间隔天数)]。"""
    zone = ZoneInfo(timezone_name)
    items = {}
    for row in conn.execute(
        """
        SELECT m.id, m.due_date, p.zone, p.created_at
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    ):
        created = _local_day(zone, row["created_at"])
        items[row["id"]] = {
            "zone": row["zone"],
            "created": created,
            "due": date.fromisoformat(row["due_date"]),
            # 录入当天就是第一次"学习"，第一次复习安排在当天，所以间隔按 1 天算。
            "events": [(created, 1)],
        }
    for row in conn.execute(
        """
        SELECT r.mistake_id, r.reviewed_at, r.next_due_date
        FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        ORDER BY r.reviewed_at, r.id
        """,
        (user_id,),
    ):
        studied = _local_day(zone, row["reviewed_at"])
        interval = (date.fromisoformat(row["next_due_date"]) - studied).days
        items[row["mistake_id"]]["events"].append((studied, max(1, interval)))
    for item in items.values():
        item["events"].sort(key=lambda event: event[0])
        item["event_days"] = [event[0] for event in item["events"]]
    return items


def retention_on(item, day):
    """这条易错点在 day 这天的保持率；day 之前还没录入则返回 None。"""
    if item["created"] > day:
        return None
    position = bisect_right(item["event_days"], day) - 1
    studied, interval = item["events"][position]
    return retention((day - studied).days, interval)


def zone_averages(items, day):
    totals = {}
    for item in items.values():
        value = retention_on(item, day)
        if value is None:
            continue
        bucket = totals.setdefault(item["zone"], [0.0, 0])
        bucket[0] += value
        bucket[1] += 1
    return {zone: total / count * 100 for zone, (total, count) in totals.items()}


def mastery_report(conn, user_id, timezone_name, today, weeks):
    # today 是请求一开始取的；读库时恰好跨过午夜的话，会读到"明天"才录入的记录，直接排除，
    # 否则它在 today 这天还不存在（保持率为空）。
    items = {
        mistake_id: item
        for mistake_id, item in load_mistakes(conn, user_id, timezone_name).items()
        if item["created"] <= today
    }
    points = [today - timedelta(days=SAMPLE_STEP_DAYS * offset) for offset in range(weeks, -1, -1)]
    series_by_day = [zone_averages(items, day) for day in points]
    earlier = today - timedelta(days=CHANGE_WINDOW_DAYS)
    earlier_averages = zone_averages(items, earlier)

    zones = []
    for zone in sorted({item["zone"] for item in items.values()}):
        members = [item for item in items.values() if item["zone"] == zone]
        now_values = [retention_on(item, today) for item in members]
        mastery = sum(now_values) / len(now_values) * 100
        before = earlier_averages.get(zone)
        due = sum(1 for item in members if item["due"] <= today)
        overdue = sum(1 for item in members if item["due"] < today)
        zones.append({
            "zone": zone,
            "total": len(members),
            "due": due,
            "overdue": overdue,
            "mastery": round(mastery, 1),
            "change": round(mastery - before, 1) if before is not None else None,
            "at_risk": sum(1 for value in now_values if value < AT_RISK_BELOW),
            "series": [
                round(averages[zone], 1) if zone in averages else None for averages in series_by_day
            ],
        })
    # 掌握度从低到高：最该先复习的排在最前面。
    zones.sort(key=lambda entry: (entry["mastery"], -entry["overdue"], entry["zone"]))

    alert = None
    for entry in zones:
        if entry["mastery"] < FADING_BELOW and entry["due"] > 0 and entry["at_risk"] > 0:
            alert = {key: entry[key] for key in ("zone", "mastery", "due", "overdue", "at_risk")}
            break

    all_now = [retention_on(item, today) for item in items.values()]
    return {
        "today": today.isoformat(),
        "points": [day.isoformat() for day in points],
        "threshold": FADING_BELOW,
        "overall": round(sum(all_now) / len(all_now) * 100, 1) if all_now else None,
        "zones": zones,
        "alert": alert,
    }
