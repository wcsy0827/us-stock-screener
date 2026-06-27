"""主流程編排：串接所有模組從 universe 到 notifier。"""

from __future__ import annotations

import os
import time
import traceback

from universe import fetch_sp500
from market import fetch_market_context, SECTOR_ETF_MAP
from fetcher import (
    fetch_batch, fetch_info,
    load_price_cache, save_price_cache,
    load_info_cache, save_info_cache,
    clear_old_cache,
)
from filter import apply_filters
from scorer import score_all
from ranker import rank_candidates


def _elapsed(start: float) -> str:
    sec = time.time() - start
    m, s = divmod(int(sec), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def run(
    min_score: float = 60.0,
    top_n: int = 10,
    dry_run: bool = False,
    use_cache: bool = True,
) -> dict:
    """
    執行完整選股流程。
    回傳包含各階段結果的摘要字典。
    """
    total_start = time.time()
    summary: dict = {"success": False, "error": None}
    clear_old_cache()

    # ── Step 1: 取得 S&P 500 股票池 ────────────────────────────
    print("\n[pipeline] ── Step 1/6：取得 S&P 500 股票池 ──")
    t = time.time()
    try:
        symbols = fetch_sp500()
        summary["total"] = len(symbols)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜共 {len(symbols)} 支")
    except Exception as e:
        summary["error"] = f"Step1 universe 失敗：{e}"
        print(f"[pipeline] 錯誤：{summary['error']}")
        traceback.print_exc()
        return summary

    # ── Step 2: 批次下載日 K 數據 ───────────────────────────────
    print("\n[pipeline] ── Step 2/6：下載 90 天日 K 數據 ──")
    t = time.time()
    try:
        price_data = (use_cache and load_price_cache()) or None
        if price_data is None:
            price_data = fetch_batch(symbols)
            if use_cache:
                save_price_cache(price_data)
        summary["downloaded"] = len(price_data)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜成功 {len(price_data)} 支")
    except Exception as e:
        summary["error"] = f"Step2 fetcher 失敗：{e}"
        print(f"[pipeline] 錯誤：{summary['error']}")
        traceback.print_exc()
        return summary

    # ── Step 3: 抓取基本面資訊 ──────────────────────────────────
    print("\n[pipeline] ── Step 3/6：抓取基本面資訊（市值、產業、公司名稱）──")
    t = time.time()
    try:
        info_data = (use_cache and load_info_cache()) or None
        if info_data is None:
            info_data = fetch_info(list(price_data.keys()))
            if use_cache:
                save_info_cache(info_data)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜共 {len(info_data)} 支")
    except Exception as e:
        print(f"[pipeline] 警告：基本面抓取部分失敗，繼續執行：{e}")
        info_data = {}

    # ── Step 4: L1 硬條件篩選 ───────────────────────────────────
    print("\n[pipeline] ── Step 4/6：L1 硬條件篩選 ──")
    t = time.time()
    try:
        l1_passed = apply_filters(price_data, info_data)
        summary["l1_count"] = len(l1_passed)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜通過 {len(l1_passed)} 支")
    except Exception as e:
        summary["error"] = f"Step4 filter 失敗：{e}"
        print(f"[pipeline] 錯誤：{summary['error']}")
        traceback.print_exc()
        return summary

    # ── Step 5: L2 技術指標評分 ─────────────────────────────────
    print("\n[pipeline] ── Step 5/6：L2 技術指標評分 ──")
    t = time.time()
    try:
        candidates = score_all(l1_passed, price_data, min_score=min_score)
        summary["l2_count"] = len(candidates)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜{len(candidates)} 支 >= {min_score:.0f} 分")

        if not candidates:
            print("[pipeline] 無候選股，流程結束")
            summary["ranked"] = []
            summary["market_context"] = {}
            summary["success"] = True
            return summary
    except Exception as e:
        summary["error"] = f"Step5 scorer 失敗：{e}"
        print(f"[pipeline] 錯誤：{summary['error']}")
        traceback.print_exc()
        return summary

    # ── Step 5.5: 抓大盤 & 產業 ETF 背景數據 + 計算市場廣度 ──────
    print("\n[pipeline] ── Step 5.5：抓大盤與產業 ETF 數據、計算市場廣度 ──")
    t = time.time()
    try:
        candidate_sectors = {
            info_data.get(c["symbol"], {}).get("sector", "")
            for c in candidates
        } & set(SECTOR_ETF_MAP.keys())
        market_context = fetch_market_context(candidate_sectors, all_stocks_data=price_data)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜Regime={market_context.get('regime', 'N/A')}")
    except Exception as e:
        print(f"[pipeline] 警告：大盤數據抓取失敗，繼續執行：{e}")
        market_context = {}
    summary["market_context"] = market_context

    # ── Step 6: L3 AI 排序 ──────────────────────────────────────
    print("\n[pipeline] ── Step 6/6：L3 AI 排序 ──")
    t = time.time()
    try:
        ranked = rank_candidates(candidates, price_data, info_data, top_n=top_n, market_context=market_context)
        summary["ranked"] = ranked
        print(f"[pipeline] AI 排序完成 ({_elapsed(t)})｜{len(ranked)} 支買入候選")
    except Exception as e:
        print(f"[pipeline] 警告：AI 排序失敗，改用 L2 分數前 {top_n} 名：{e}")
        traceback.print_exc()
        from ranker import _enrich_fallback
        ranked = _enrich_fallback(candidates[:top_n], info_data, price_data)
        summary["ranked"] = ranked

    summary["success"] = True
    total_time = _elapsed(total_start)
    print(f"\n[pipeline] ✅ 全流程完成，總耗時：{total_time}")
    return summary
