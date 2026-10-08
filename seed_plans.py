"""创建默认的付费套餐（数据库里目前没有一条正式套餐记录，见 main.py 的
GET /api/plans、payments.py 的 list_plans/create_order）。

V1 没有套餐管理后台（见 README「部署说明」），新增/调整套餐目前只能用
这种独立脚本手动跑一次：
    .venv\\Scripts\\python.exe seed_plans.py

按名字判断套餐是否已存在，已存在的套餐不会被覆盖（不会改价格、不会改
额度），重复运行也不会把管理员后续手动调整过的值又改回默认值。

默认插入的两个套餐 purchasable = 0：只在"我的套餐"页展示、暂时不能真的
下单，现有的支付宝/微信支付通道不会被触发。确认价格没问题后，再用
--enable-purchase 把已插入的套餐打开（只改现有记录的开关，不会新增套餐）。

已上线套餐仅更新额度：--update-limits 默认 dry-run，逐行显示 id 和旧值 ->
新值；显式加 --apply 才写入标准版 50、进阶版 120。不改价格、周期和开关，
不碰其他名字的套餐。此模式只打开已有数据库，不建库、不执行结构迁移。
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import connect, init_db

# name, period_days, ai_daily_limit, price_cents
DEFAULT_PLANS = (
    ("标准版", 30, 50, 990),
    ("进阶版", 30, 120, 1990),
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印会做什么，不真的写入"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--enable-purchase", action="store_true",
        help="把默认套餐里已存在的记录改成可购买（purchasable = 1），不会新增套餐",
    )
    mode.add_argument(
        "--update-limits", action="store_true",
        help="仅更新已存在的标准版/进阶版额度，默认 dry-run；加 --apply 才写入",
    )
    parser.add_argument("--apply", action="store_true", help="正式执行 --update-limits")
    args = parser.parse_args(argv)
    if args.apply and not args.update_limits:
        parser.error("--apply 必须与 --update-limits 一起使用")
    if args.apply and args.dry_run:
        parser.error("--apply 与 --dry-run 不能同时使用")

    if args.update_limits:
        updated = 0
        with connect(write=args.apply, create=False) as conn:
            if not args.apply:
                conn.execute("PRAGMA query_only = ON")
            for name, _, target, _ in DEFAULT_PLANS:
                rows = conn.execute(
                    "SELECT id, ai_daily_limit FROM plans WHERE name = ? ORDER BY id",
                    (name,),
                ).fetchall()
                if not rows:
                    print(f"「{name}」不存在，跳过，不新增。")
                for row in rows:
                    old = row["ai_daily_limit"]
                    print(f"id={row['id']}「{name}」：{old} -> {target}"
                          + ("（无需变更）" if old == target else ""))
                    if old == target:
                        continue
                    updated += 1
                    if args.apply:
                        conn.execute(
                            "UPDATE plans SET ai_daily_limit = ? WHERE id = ?",
                            (target, row["id"]),
                        )
        if args.apply:
            print(f"完成，更新 {updated} 个套餐额度；价格、周期及开关未改动。")
        else:
            print(f"dry-run：会更新 {updated} 个套餐额度，未写入；加 --apply 正式执行。")
        return 0

    init_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with connect(write=not args.dry_run) as conn:
        if args.enable_purchase:
            names = [name for name, *_ in DEFAULT_PLANS]
            if args.dry_run:
                placeholders = ",".join("?" * len(names))
                count = conn.execute(
                    f"SELECT COUNT(*) FROM plans WHERE name IN ({placeholders}) "
                    "AND purchasable = 0",
                    names,
                ).fetchone()[0]
                print(f"dry-run：会把 {count} 个套餐标记为可购买，未写入。")
                return 0
            updated = 0
            for name in names:
                cursor = conn.execute(
                    "UPDATE plans SET purchasable = 1 WHERE name = ?", (name,)
                )
                updated += cursor.rowcount
            print(f"完成，{updated} 个套餐已标记为可购买。")
            return 0

        created = []
        for name, period_days, ai_daily_limit, price_cents in DEFAULT_PLANS:
            exists = conn.execute(
                "SELECT 1 FROM plans WHERE name = ?", (name,)
            ).fetchone()
            if exists:
                print(f"「{name}」已存在，跳过。")
                continue
            created.append(name)
            if not args.dry_run:
                conn.execute(
                    """
                    INSERT INTO plans(
                        name, period_days, ai_daily_limit, price_cents,
                        purchasable, created_at
                    ) VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (name, period_days, ai_daily_limit, price_cents, now),
                )

    if args.dry_run:
        print(f"dry-run 完成，会新增 {len(created)} 个套餐：{', '.join(created) or '（无）'}")
    else:
        print(f"完成，新增 {len(created)} 个套餐：{', '.join(created) or '（无，都已存在）'}")
        if created:
            print("这些套餐目前不能购买（purchasable = 0），确认价格无误后运行：")
            print("  .venv\\Scripts\\python.exe seed_plans.py --enable-purchase")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
