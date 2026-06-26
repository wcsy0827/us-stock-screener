"""美股 AI 選股系統 — 主程式入口。"""

import argparse
import os
import sys
from datetime import datetime

# Windows 終端機強制 UTF-8，避免 emoji 輸出報錯
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pipeline import run
from tracker import check_already_run_today, run_tracker
from publisher import publish


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="美股 AI 選股系統")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="執行完整流程但不推送至 GitHub，只在本機生成 HTML",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=int(os.getenv("MAX_OUTPUT", "5")),
        metavar="N",
        help="輸出幾支候選股（預設 5）",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=float(os.getenv("MIN_SCORE", "60")),
        metavar="N",
        help="L2 最低評分門檻（預設 60）",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="忽略快取，強制重新下載所有數據",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳過今日重複執行確認（CI 環境用）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    print("=" * 60)
    print("  美股 AI 選股系統")
    print(f"  模式：{'Dry-run（只生成 HTML）' if args.dry_run else '正式執行（生成並推送）'}")
    print(f"  最低評分：{args.min_score}　輸出數量：Top {args.top}")
    print("=" * 60)

    if check_already_run_today() and not args.yes:
        print(f"\n⚠️  今日已執行過選股，再次執行不會增加追蹤天數。")
        try:
            confirm = input("是否繼續？(y/N) ").strip().lower()
        except EOFError:
            confirm = "n"
        if confirm != "y":
            print("已取消。")
            sys.exit(0)

    summary = run(
        min_score=args.min_score,
        top_n=args.top,
        dry_run=args.dry_run,
        use_cache=not args.no_cache,
    )

    if not summary.get("success"):
        print(f"\n[main] 流程失敗：{summary.get('error')}")
        sys.exit(1)

    ranked = summary.get("ranked", [])
    _, categories = run_tracker(ranked)

    stats = {
        "total":    summary.get("total", 0),
        "l1_count": summary.get("l1_count", 0),
        "l2_count": summary.get("l2_count", 0),
        "ai_count": len(ranked),
        "date":     datetime.now(),
    }
    publish(categories, stats, dry_run=args.dry_run)
