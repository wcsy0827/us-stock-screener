"""主流程編排：串接所有模組從 universe 到 notifier。"""

from __future__ import annotations

import os
import time
import traceback

from universe import fetch_sp500
from market import fetch_market_context, fetch_regime_quick, SECTOR_ETF_MAP
from fetcher import (
    fetch_batch, fetch_info,
    load_price_cache, save_price_cache,
    load_info_cache, save_info_cache,
    clear_old_cache, trim_incomplete_session,
)
from earnings import fetch_earnings_dates
from filter import apply_filters, apply_earnings_filter
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
    use_ai_cache: bool = True,
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

    # ── Step 2: 批次下載日 K 數據（含板塊 ETF 及 SPY，供 scorer RS 計算用）──
    print("\n[pipeline] ── Step 2/6：下載 90 天日 K 數據 ──")
    t = time.time()
    # 板塊 ETF + SPY 與 S&P 500 一起下載並快取（DD-4）
    _etf_tickers = list(SECTOR_ETF_MAP.values()) + ["SPY"]
    symbols_with_etf = list(set(symbols) | set(_etf_tickers))
    try:
        # 若快取中缺少 SPY，視為無效快取並強制重下（DD-4）
        price_data = (use_cache and load_price_cache()) or None
        if price_data is None or "SPY" not in price_data:
            price_data = fetch_batch(symbols_with_etf)
            if use_cache:
                save_price_cache(price_data)
        price_data = trim_incomplete_session(price_data)
        etf_set = set(_etf_tickers)
        sp500_count = sum(1 for k in price_data if k not in etf_set)
        summary["downloaded"] = sp500_count
        print(f"[pipeline] 完成 ({_elapsed(t)})｜S&P 500 成功 {sp500_count} 支（+{len(price_data) - sp500_count} ETF）")

        # 提取 SPY 最後交易日，供 tracker 作為基準日（DD-11）
        spy_df = price_data.get("SPY")
        if spy_df is not None and not spy_df.empty:
            summary["market_date"] = spy_df.index[-1].date().isoformat()
    except Exception as e:
        summary["error"] = f"Step2 fetcher 失敗：{e}"
        print(f"[pipeline] 錯誤：{summary['error']}")
        traceback.print_exc()
        return summary

    # ── Step 2.5: 快速判定大盤 Regime（供 L2 動態門檻使用）──────
    print("\n[pipeline] ── Step 2.5：快速判定大盤 Regime ──")
    t = time.time()
    regime_quick = ""
    breadth_quick: float | None = None
    vix_quick: float | None = None
    vix_ok = False
    try:
        regime_quick, breadth_quick, vix_quick, vix_ok = fetch_regime_quick(price_data)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜Regime={regime_quick}")
    except Exception as e:
        print(f"[pipeline] 警告：Regime 快速判定失敗，L2 使用預設門檻：{e}")

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

    # ── Step 3.5: 財報日三層查詢（Tier 1+2，不觸發 Tier 3）────────
    print("\n[pipeline] ── Step 3.5：財報日查詢（Tier 1+2）──")
    t = time.time()
    earnings_data: dict = {}
    try:
        earnings_data = fetch_earnings_dates(
            list(price_data.keys()), info_data, post_l1_symbols=None
        )
        print(f"[pipeline] 完成 ({_elapsed(t)})｜查詢 {len(earnings_data)} 支")
    except Exception as e:
        print(f"[pipeline] 警告：財報日 Tier 1+2 查詢失敗，將跳過財報防禦牆：{e}")

    # ── Step 4: L1 流動性硬條件篩選 ─────────────────────────────
    print("\n[pipeline] ── Step 4/6：L1 流動性篩選 ──")
    t = time.time()
    try:
        liq_filtered = apply_filters(price_data, info_data)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜通過 {len(liq_filtered)} 支")
    except Exception as e:
        summary["error"] = f"Step4 filter 失敗：{e}"
        print(f"[pipeline] 錯誤：{summary['error']}")
        traceback.print_exc()
        return summary

    # ── Step 4.5: Tier 3 精準補抓 + 財報防禦牆 ──────────────────
    print("\n[pipeline] ── Step 4.5：財報防禦牆（Tier 3 補抓 + 過濾）──")
    t = time.time()
    try:
        earnings_data = fetch_earnings_dates(
            list(price_data.keys()), info_data, post_l1_symbols=liq_filtered
        )
        l1_passed = apply_earnings_filter(liq_filtered, earnings_data)
        summary["l1_count"] = len(l1_passed)
        print(f"[pipeline] 完成 ({_elapsed(t)})｜財報過濾後 {len(l1_passed)} 支")
    except Exception as e:
        print(f"[pipeline] 警告：財報防禦牆失敗，使用流動性篩選結果：{e}")
        l1_passed = liq_filtered
        summary["l1_count"] = len(l1_passed)

    # ── Step 5: L2 技術指標評分 ─────────────────────────────────
    print("\n[pipeline] ── Step 5/6：L2 技術指標評分 ──")
    t = time.time()
    try:
        # D1：info_data 可能缺失部分 sym（API 超時），用 .get() 防 KeyError
        sector_map = {sym: info_data.get(sym, {}).get("sector", "") for sym in l1_passed}
        candidates = score_all(l1_passed, price_data, min_score=min_score, regime=regime_quick, sector_map=sector_map)
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
        market_context = fetch_market_context(
            candidate_sectors,
            all_stocks_data=price_data,
            breadth_pct=breadth_quick,
            vix_value=vix_quick,
        )
        print(f"[pipeline] 完成 ({_elapsed(t)})｜Regime={market_context.get('regime', 'N/A')}")
    except Exception as e:
        print(f"[pipeline] 警告：大盤數據抓取失敗，繼續執行：{e}")
        market_context = {}
    summary["market_context"] = market_context

    # ── VIX Gate：VIX 資料失敗時不進行 L3，避免 AI 拿到錯誤市場背景 ──
    if not vix_ok:
        print("[pipeline] ⚠️  VIX 資料不可靠（下載失敗），跳過 L3 AI 精選以節省資源")
        summary["ranked"] = []
        summary["success"] = True
        return summary

    # ── Step 6: L3 AI 排序 ──────────────────────────────────────
    print("\n[pipeline] ── Step 6/6：L3 AI 排序 ──")
    t = time.time()
    try:
        ranked = rank_candidates(
            candidates, price_data, info_data,
            top_n=top_n, market_context=market_context,
            market_date=summary.get("market_date"),
            use_ai_cache=use_ai_cache,
            earnings_data=earnings_data,
        )
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
