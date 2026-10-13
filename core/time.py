"""时间工具。utc_now / today_for；datetime 类也从这里暴露，
测试 freeze 时间时 patch core.time.datetime 即可。"""
from datetime import datetime, timezone  # noqa: F401 (datetime 供调用方 patch)

from scheduler import today_in_timezone

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_for(user):
    return today_in_timezone(user["timezone"])

