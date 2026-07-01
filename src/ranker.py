"""L3 AI 排序：用 DeepSeek 對 L2 候選股做橫向比較，輸出 Top N。"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from openai import OpenAI

from market import SECTOR_ETF_MAP

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
MAX_CANDIDATES_TO_AI = 40   # 最多送給 AI 的候選股數量（已按 L2 分排序，取前 N）
MAX_SECTOR_CANDIDATES = 8   # 每產業最多保留支數（_diversify_candidates，DD-11）
MAX_RETRIES = 3

# CONSOLIDATION_VOLATILE Regime 的額外 Prompt 指引（D5）
_REGIME_EXTRA_HINT: dict[str, str] = {
    "CONSOLIDATION_VOLATILE": (
        "⚠️ 注意：當前為【高 VIX 整理期】，策略應採保守突破，"
        "要求更顯著的確認訊號（VTF_Score >= 2.0、MACD POS_INC、RSI 在 50~65 之間），"
        "訊號不夠明確的標的一律跳過。"
    ),
}

_CACHE_DIR = Path(__file__).parent.parent / ".cache"


def _ranked_cache_path(market_date: str) -> Path:
    date_str = market_date.replace("-", "")
    return _CACHE_DIR / f"ranked_{date_str}.json"


# ── 指標計算（純 pandas / numpy）────────────────────────────────

def _ema(series: pd.Series, span: int) -> float:
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = (100 - 100 / (1 + rs)).dropna()
    return float(rsi.iloc[-1]) if not rsi.empty else float("nan")


def _macd_hist(series: pd.Series) -> float:
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = (macd - signal).dropna()
    return float(hist.iloc[-1]) if not hist.empty else float("nan")


def compute_indicators(
    sym: str,
    df: pd.DataFrame,
    r_spy_global: pd.Series | None = None,
    earnings_days_left: int | None = 99,
) -> dict:
    """
    計算單支股票指標供 Markdown 表格使用。
    r_spy_global 由呼叫端預計算一次傳入（不在函式內重複計算），None 時 beta_60d 回傳 None。
    earnings_days_left 由外層依財報快取計算後傳入（架構解耦）。
    """
    close = df["Close"].dropna()
    volume = df["Volume"].dropna()
    high = df["High"].dropna()
    low = df["Low"].dropna()

    price_now  = float(close.iloc[-1])
    price_prev = float(close.iloc[-2])  if len(close) >= 2  else price_now
    price_5d   = float(close.iloc[-5])  if len(close) >= 5  else price_now
    price_20d  = float(close.iloc[-20]) if len(close) >= 20 else price_now

    change_1d = (price_now - price_prev) / price_prev * 100 if price_prev else 0.0
    change_5d = (price_now - price_5d)   / price_5d   * 100 if price_5d   else 0.0

    # EMA
    ema5  = _ema(close, 5)  if len(close) >= 5  else float("nan")
    ema10 = _ema(close, 10) if len(close) >= 10 else float("nan")
    ema20 = _ema(close, 20) if len(close) >= 20 else float("nan")
    ema50 = _ema(close, 50) if len(close) >= 50 else float("nan")
    rsi   = _rsi(close)     if len(close) >= 14 else float("nan")

    # VTF_Score（量能推進因子，DD-9）
    avg_vol_30 = float(volume.tail(30).mean()) if len(volume) >= 30 else (float(volume.mean()) if len(volume) > 0 else 0.0)
    vol_ratio  = float(volume.iloc[-1]) / avg_vol_30 if avg_vol_30 > 0 else 1.0
    h_last = float(high.iloc[-1]) if len(high) >= 1 else price_now
    l_last = float(low.iloc[-1])  if len(low)  >= 1 else price_now
    k_pos = 0.5 if h_last == l_last else (price_now - l_last) / (h_last - l_last)
    vtf_score: float | None = round(max(-5.0, vol_ratio * (2 * k_pos - 1)), 2)

    # Vol_vs_5DAvg（動能策略回檔量縮確認，DD-12）
    avg_vol_5 = float(volume.tail(5).mean()) if len(volume) >= 5 else avg_vol_30
    vol_vs_5d_avg = float(volume.iloc[-1]) / avg_vol_5 if avg_vol_5 > 0 else 1.0

    # ATR14
    atr14: float | None = None
    if len(close) >= 15 and len(high) >= 15 and len(low) >= 15:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.ewm(alpha=1 / 14, adjust=False).mean().dropna()
        if not atr_series.empty:
            atr14 = float(atr_series.iloc[-1])

    # Momentum_ATR（ATR 標準化 20 日動能）
    momentum_atr: float | None = None
    if len(close) >= 20 and atr14 is not None and atr14 > 0:
        momentum_atr = round((price_now - price_20d) / atr14, 2)

    # Beta_60D（需 r_spy_global）
    beta_60d: float | None = None
    if r_spy_global is not None and len(close) >= 31:
        r_stock = close.pct_change().dropna()
        r_s_aln, r_spy_aln = r_stock.align(r_spy_global, join="inner")
        clean = pd.concat([r_s_aln, r_spy_aln], axis=1).dropna()
        clean_60 = clean.tail(60)
        if len(clean_60) >= 30:
            cov = np.cov(clean_60.iloc[:, 0], clean_60.iloc[:, 1])
            if cov[1, 1] != 0:
                beta_60d = round(float(cov[0, 1] / cov[1, 1]), 2)

    # Strategy_Tag 所需欄位（保留）
    macd_h = _macd_hist(close) if len(close) >= 35 else float("nan")
    high_20d = float(df["High"].tail(20).max()) if len(df) >= 20 else price_now
    low_20d  = float(df["Low"].tail(20).min())  if len(df) >= 20 else price_now
    dist_from_20d_high_pct = round((price_now - high_20d) / high_20d * 100, 2) if high_20d else 0.0
    rsi_5d_ago = _rsi(close.iloc[:-5]) if len(close) >= 19 else float("nan")
    low14  = float(df["Low"].tail(14).min())  if len(df) >= 14 else price_now
    high14 = float(df["High"].tail(14).max()) if len(df) >= 14 else price_now
    stoch_k = round((price_now - low14) / (high14 - low14) * 100, 1) if high14 != low14 else 50.0
    dist_from_ema50_pct = round((price_now - ema50) / ema50 * 100, 2) if (ema50 == ema50 and ema50) else None

    def _fmt(v: float, decimals: int = 2) -> float | None:
        return None if (v != v) else round(v, decimals)  # NaN check

    return {
        "symbol": sym,
        "price": round(price_now, 2),
        "change_1d_pct":  round(change_1d, 2),
        "change_5d_pct":  round(change_5d, 2),
        "ema5": _fmt(ema5),
        "ema10": _fmt(ema10),
        "ema20": _fmt(ema20),
        "ema50": _fmt(ema50),
        "rsi": _fmt(rsi),
        "macd_hist": _fmt(macd_h, 4),
        "vtf_score": vtf_score,
        "momentum_atr": momentum_atr,
        "beta_60d": beta_60d,
        "earnings_days_left": earnings_days_left,
        "vol_vs_5d_avg": round(vol_vs_5d_avg, 2),
        # 供 _strategy_tag 使用
        "volume_ratio": round(vol_ratio, 2),
        "high_20d": round(high_20d, 2),
        "low_20d":  round(low_20d, 2),
        "dist_from_20d_high_pct": dist_from_20d_high_pct,
        "rsi_5d_ago":          _fmt(rsi_5d_ago),
        "stoch_k":             stoch_k,
        "dist_from_ema50_pct": dist_from_ema50_pct,
    }


# ── Markdown 表格輔助函數 ────────────────────────────────────────

def _ma_trend_tag(ema5, ema10, ema20, ema50) -> str:
    """均線排列狀態編碼：BULL_1 完美多頭，BULL_2 標準多頭，MIXED 混合，BEAR 空頭。"""
    vals = [ema5, ema10, ema20, ema50]
    if any(v is None or (isinstance(v, float) and v != v) for v in vals):
        return "N/A"
    if ema5 > ema10 > ema20 > ema50:
        return "BULL_1"
    if ema5 > ema20 > ema50:
        return "BULL_2"
    if ema20 < ema50:
        return "BEAR"
    return "MIXED"


def _macd_hist_tag(close: pd.Series) -> str:
    """MACD 直方圖狀態編碼：POS_INC / POS_DEC / NEG_INC / NEG_DEC / N/A。"""
    if len(close) < 36:
        return "N/A"
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = (macd - signal).dropna()
    if len(hist) < 2:
        return "N/A"
    cur, prev = float(hist.iloc[-1]), float(hist.iloc[-2])
    if cur >= 0:
        return "POS_INC" if cur > prev else "POS_DEC"
    return "NEG_INC" if cur > prev else "NEG_DEC"


def _strategy_tag(indic: dict) -> str:
    """根據技術指標推薦最可能適用的策略標籤（MOMENTUM / BREAKOUT / REVERSAL / NEUTRAL）。"""
    rsi      = indic.get("rsi") or 0.0
    vol      = indic.get("volume_ratio") or 0.0
    stoch    = indic.get("stoch_k") or 50.0
    dist_20d = indic.get("dist_from_20d_high_pct") or -99.0
    rsi_prev = indic.get("rsi_5d_ago")
    ema5, ema20, ema50 = indic.get("ema5"), indic.get("ema20"), indic.get("ema50")

    if stoch < 25 and rsi_prev is not None and rsi > rsi_prev:
        return "REVERSAL"
    if vol >= 2.0 and -2.0 <= dist_20d <= 2.0:
        return "BREAKOUT"
    if ema5 and ema20 and ema50 and ema5 > ema20 > ema50 and 50 <= rsi <= 75 and vol >= 1.5:
        return "MOMENTUM"
    return "NEUTRAL"


def _calc_rs_vs_sector(
    sym: str,
    df: pd.DataFrame,
    sector: str,
    price_data: dict,
) -> float | None:
    """
    計算個股 5 日報酬率 − 板塊 ETF 5 日報酬率（DD-10）。
    板塊未知或 ETF 缺資料 → fallback SPY；ETF 數據不足 5 日 → None。
    """
    close = df["Close"].dropna()
    if len(close) < 5:
        return None
    p_start = float(close.iloc[-5])
    if p_start == 0:
        return None
    stock_5d = (float(close.iloc[-1]) - p_start) / p_start * 100

    etf_ticker = SECTOR_ETF_MAP.get(sector, "") if sector else ""
    if not etf_ticker or etf_ticker not in price_data:
        etf_ticker = "SPY"
    etf_df = price_data.get(etf_ticker)
    if etf_df is None or etf_df.empty:
        return None
    etf_close = etf_df["Close"].dropna()
    if len(etf_close) < 5:
        return None
    etf_start = float(etf_close.iloc[-5])
    if etf_start == 0:
        return None
    etf_5d = (float(etf_close.iloc[-1]) - etf_start) / etf_start * 100

    return round(stock_5d - etf_5d, 1)


def _calc_earnings_days(
    sym: str,
    earnings_data: dict | None,
    current_date: date,
) -> int | None:
    """
    從 earnings_data 計算距下次財報的日曆天數。
    None = 數據斷裂（AI 排除）；99 = 安全。
    """
    if earnings_data is None:
        return 99
    ed_str = earnings_data.get(sym)
    if ed_str is None:
        return 99
    try:
        ed_date = date.fromisoformat(str(ed_str)[:10])
        days = (ed_date - current_date).days
        return min(days, 99) if days >= 0 else 99
    except Exception:
        return None  # 日期格式異常 = 數據斷裂


def _generate_candidates_markdown_table(
    candidates: list[dict],
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
    earnings_data: dict | None = None,
    current_date: date | None = None,
) -> str:
    """
    將 L2 候選股清單轉換為 15 欄 Markdown 表格（含 RS_vs_Sector，DD-10）。
    """
    _SECTOR_ABBR = {
        " Services": "", " Cyclical": "", " Defensive": "",
    }

    # SPY 報酬率序列在迴圈外預計算一次（Beta_60D 用）
    r_spy_global: pd.Series | None = None
    spy_df = price_data.get("SPY")
    if spy_df is not None and not spy_df.empty:
        r_spy_global = spy_df["Close"].pct_change().dropna()

    ref_date = current_date or date.today()

    header = (
        "| Ticker | Close_Price | Sector | L2_Score | Strategy_Tag | MA_Trend"
        " | EMA5 | EMA10 | EMA20 | Vol_vs_5DAvg"
        " | RSI | MACD_Hist | VTF_Score | Price_5D_Pct | Momentum_ATR"
        " | RS_vs_Sector | 52W_High_Dist | Beta_60D | Earnings_Days_Left |"
    )
    sep = (
        "|--------|-------------|--------|----------|--------------|----------"
        "|------|-------|-------|--------------"
        "|-----|-----------|-----------|--------------|-------------|"
        "--------------|---------------|----------|-------------------|"
    )
    rows = [header, sep]

    for c in candidates[:MAX_CANDIDATES_TO_AI]:
        sym = c["symbol"]
        df  = price_data.get(sym)
        if df is None:
            continue

        ed_left = _calc_earnings_days(sym, earnings_data, ref_date)
        indic = compute_indicators(sym, df, r_spy_global=r_spy_global, earnings_days_left=ed_left)
        info  = info_data.get(sym, {})
        close = df["Close"].dropna()

        # 欄位計算
        ma_trend  = _ma_trend_tag(indic.get("ema5"), indic.get("ema10"), indic.get("ema20"), indic.get("ema50"))
        macd_tag  = _macd_hist_tag(close)
        strategy  = _strategy_tag(indic)
        price_str = f"${indic['price']:.2f}"

        # EMA5/10/20 與 Vol_vs_5DAvg（動能策略回檔買進區間依據，DD-12）
        ema5_v, ema10_v, ema20_v = indic.get("ema5"), indic.get("ema10"), indic.get("ema20")
        ema5_str  = f"${ema5_v:.2f}"  if ema5_v  is not None else "N/A"
        ema10_str = f"${ema10_v:.2f}" if ema10_v is not None else "N/A"
        ema20_str = f"${ema20_v:.2f}" if ema20_v is not None else "N/A"
        vol5_v = indic.get("vol_vs_5d_avg")
        vol5_str = f"{vol5_v:.2f}" if vol5_v is not None else "N/A"

        rsi_val = indic.get("rsi")
        rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "N/A"

        vtf = indic.get("vtf_score")
        vtf_str = f"{vtf:.2f}" if vtf is not None else "N/A"

        p5d_str = f"{indic.get('change_5d_pct', 0.0):+.1f}%"

        mom = indic.get("momentum_atr")
        mom_str = f"{mom:.2f}" if mom is not None else "N/A"

        # RS_vs_Sector（DD-10）
        sector_raw = c.get("sector") or info.get("sector", "")
        rs = _calc_rs_vs_sector(sym, df, sector_raw, price_data)
        rs_str = f"{rs:+.1f}%" if rs is not None else "N/A"

        fw_high  = info.get("fifty_two_week_high")
        dist_52w = round((indic["price"] - fw_high) / fw_high * 100, 1) if fw_high else None
        dist_str = f"{dist_52w:+.1f}%" if dist_52w is not None else "N/A"

        beta = indic.get("beta_60d")
        beta_str = f"{beta:.2f}" if beta is not None else "N/A"

        ed = indic.get("earnings_days_left")
        ed_str = "N/A" if ed is None else str(ed)

        sector_display = sector_raw or "Unknown"
        for k, v in _SECTOR_ABBR.items():
            sector_display = sector_display.replace(k, v)

        rows.append(
            f"| {sym} | {price_str} | {sector_display} | {c['total_score']:.0f} | {strategy}"
            f" | {ma_trend} | {ema5_str} | {ema10_str} | {ema20_str} | {vol5_str}"
            f" | {rsi_str} | {macd_tag} | {vtf_str}"
            f" | {p5d_str} | {mom_str} | {rs_str} | {dist_str} | {beta_str} | {ed_str} |"
        )

    return "\n".join(rows)


# ── 產業多樣性保護 ───────────────────────────────────────────────

def _diversify_candidates(
    candidates: list[dict],
    regime: str = "",
    max_per_sector: int = MAX_SECTOR_CANDIDATES,
) -> list[dict]:
    """
    對候選池做每產業上限截斷，防止同一板塊霸榜（DD-11）。
    PANIC_REVERSAL 環境下，total_score < 40 的強制放行反轉股不受產業配額限制。
    """
    sector_counts: dict[str, int] = {}
    result: list[dict] = []
    for c in sorted(candidates, key=lambda x: x["total_score"], reverse=True):
        sector = c.get("sector", "Unknown") or "Unknown"
        is_panic_force = (regime == "PANIC_REVERSAL" and c["total_score"] < 40.0)
        if is_panic_force or sector_counts.get(sector, 0) < max_per_sector:
            result.append(c)
            if not is_panic_force:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
    return result


# ── Prompt 建構 ──────────────────────────────────────────────────

def _build_prompt(
    candidates: list[dict],
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
    market_context: dict | None = None,
    earnings_data: dict | None = None,
    current_date: date | None = None,
) -> str:
    """以 XML 標籤包裹三大區塊，組裝結構化 Prompt 送給 DeepSeek。"""
    mc = market_context or {}
    regime_code = mc.get("regime", "")

    # ── <Market_Regime> ───────────────────────────────────────────
    regime_hint = mc.get("ai_prompt_hint", "")
    primary     = mc.get("primary_strategy", "")
    breadth     = mc.get("market_breadth_pct")
    vix_info    = mc.get("vix", {})
    spy_info    = mc.get("sp500", {})
    sectors     = mc.get("sectors", {})

    regime_lines = []
    if regime_hint:
        regime_lines.append(regime_hint)

    # D5：CONSOLIDATION_VOLATILE 的額外保守提示
    extra_hint = _REGIME_EXTRA_HINT.get(regime_code, "")
    if extra_hint:
        regime_lines.append(extra_hint)

    stats = []
    if breadth is not None:
        stats.append(f"市場廣度={breadth:.1f}%")
    vix_val = vix_info.get("value")
    if vix_val is not None:
        stats.append(f"VIX={vix_val:.1f}({vix_info.get('label', '')})")
    above_ema20 = spy_info.get("above_ema20")
    if above_ema20 is not None:
        stats.append(f"SPY={'EMA20之上' if above_ema20 else 'EMA20之下'}")
    if stats:
        regime_lines.append("大盤數據：" + "｜".join(stats))

    if sectors:
        etf_parts = [
            f"{sec}({d.get('etf', '')}) 5日={d.get('change_5d_pct', 0):+.1f}% 20日={d.get('change_20d_pct', 0):+.1f}%"
            f"{'↑' if d.get('above_ema20') else '↓'}"
            for sec, d in sectors.items()
        ]
        regime_lines.append("產業ETF（5日/20日漲跌）：" + "  ".join(etf_parts))

    regime_block = "\n".join(regime_lines)

    # ── <Candidate_Pool> ──────────────────────────────────────────
    table = _generate_candidates_markdown_table(
        candidates, price_data, info_data,
        earnings_data=earnings_data, current_date=current_date,
    )
    field_defs = (
        "欄位定義：\n"
        "- MA_Trend: BULL_1=EMA5>EMA10>EMA20>EMA50完美多頭｜BULL_2=EMA5>EMA20>EMA50標準多頭｜MIXED=混合｜BEAR=空頭\n"
        "- EMA5/EMA10/EMA20: 指數移動均線價位（美元）。動能策略買進區間應設在EMA20~EMA10之間的回檔帶；"
        "股價距EMA5超過+5%視為過度延伸，不宜追價；EMA5附近可作為極端強勢股的探針帶進場價\n"
        "- Vol_vs_5DAvg: 當日成交量÷5日均量；<0.7代表回檔量縮（拋壓衰竭，無恐慌賣壓），"
        "為動能策略回檔進場的量能確認條件\n"
        "- MACD_Hist: POS_INC=正且遞增(最強)｜POS_DEC=正但遞減｜NEG_INC=負但回升｜NEG_DEC=負且下降(最弱)\n"
        "- VTF_Score: 量能推進因子=量比×(2×K線位置-1)，正值=帶量推進，負值=高檔出貨；>5.0=史詩級機構建倉\n"
        "- Price_5D_Pct: 近5日漲跌幅（短線爆發力）\n"
        "- Momentum_ATR: 20日價格位移÷ATR14（跨行業標準化動能）；>=2.0強勢；<=-2.0超賣\n"
        "- RS_vs_Sector: 個股5日報酬率-板塊ETF5日報酬率；>+2%=板塊領頭羊優先加分；<-2%=板塊落後者需額外確認\n"
        "- 52W_High_Dist: 距52週高點（-2%=接近高點，-30%=遠離高點）\n"
        "- Beta_60D: 60日Beta vs SPY；N/A=數據不足，不排除，改用Momentum_ATR判斷\n"
        "- Earnings_Days_Left: 距下次財報曆天數；N/A=數據斷裂請排除；99=安全；<=5請排除\n"
        "- Strategy_Tag: 系統預判策略（MOMENTUM/BREAKOUT/REVERSAL/NEUTRAL），僅供參考"
    )
    pool_block = f"{table}\n\n{field_defs}"

    # ── <Output_Constraint> ───────────────────────────────────────
    strategy_line = (
        f"本日主推策略【{primary}】，只選符合此策略邏輯的個股。"
        if primary else "全面防禦模式，不建議建立新倉位。"
    )
    constraint_block = (
        f"必須無條件服從 <Market_Regime> 的策略方向。\n"
        f"{strategy_line}\n"
        f"從 <Candidate_Pool> 中篩選最多 5 支最佳標的。\n"
        f"【型態限制】hold_period 必須輸出純整數（Integer），代表預期持有的美股交易日天數，"
        f"嚴禁輸出任何非數字字元（禁止如 '5 days'、'2 weeks'、'5~7' 等字串格式）。\n"
        f'以 JSON 格式輸出，不附加任何說明文字：{{"selections": [{{...}}, ...]}}'
    )

    return (
        f"<Market_Regime>\n{regime_block}\n</Market_Regime>\n\n"
        f"<Candidate_Pool>\n{pool_block}\n</Candidate_Pool>\n\n"
        f"<Output_Constraint>\n{constraint_block}\n</Output_Constraint>"
    )


SYSTEM_PROMPT = """你是一位經驗豐富的美股量化分析師，擅長技術面與動能選股。
你的任務是從 S&P 500 候選股中，根據技術指標、量價關係、趨勢動能，
挑選出你認為值得「買入」的標的（最多 5 支），並給出具體操作建議。
若符合買入條件的標的不足 5 支，只輸出實際符合條件的數量，不要勉強湊數。

選股原則：
1. 優先選擇均線多頭排列完整、RSI 健康（50~70）、MACD 向上的個股
2. VTF_Score > 1.0 代表帶量推進（主力進場），VTF_Score < 0 代表高檔出貨，後者應大幅降權
3. RS_vs_Sector > +2%：板塊領頭羊，同等條件下優先選擇；RS_vs_Sector < -2%：板塊落後者，需額外確認
4. Momentum_ATR 為跨行業標準化動能（ATR 倍數），比絕對漲跌幅更公平；>=2.0 代表強勢動能
5. 避免過度集中於同一產業（已由系統做初步分散，但 AI 可進一步考量）
6. 若候選股 Earnings_Days_Left <= 5 或 = N/A（數據斷裂），一律排除

市場背景判斷原則：
- 大盤（S&P 500）：若大盤 5 日跌幅 > 2% 或處於 EMA20 之下，整體提高警覺，傾向「觀望」
- VIX：若 VIX > 25，市場恐慌情緒高，操作建議應更保守；VIX < 15 代表市場樂觀，可積極
- 產業 ETF：個股所屬產業 ETF 若近 5 日下跌，即使個股技術面佳也需提示風險；ETF 強勢則加分
- 產業 ETF 的趨勢應反映在 reason 中，說明產業走勢對個股的支撐或壓制

【動能策略（momentum）】：
- 條件：均線多頭排列（EMA5>EMA10>EMA20>EMA50）、RSI 50~70、VTF_Score >= 1.0
- 優先：Momentum_ATR >= 2.0 且 RS_vs_Sector > +1%（板塊內領頭羊動能延續）
- 買入區間（拒絕盲目追價，優先選有結構確認的回檔）：
  1. 標準回檔進場：股價已回落至 EMA20~EMA10 之間，且 Vol_vs_5DAvg < 0.7（量縮無賣壓）→ buy_zone 設在該 EMA20~EMA10 區間
  2. 極端強勢例外：股價緊貼 EMA5（尚未明顯回檔）但 VTF_Score 仍強 → buy_zone 可設在 EMA5 附近（5MA 探針帶）
  3. 股價距 EMA5 已超過 +5%（過度延伸、未回檔）→ 大幅降低信心分數，禁止以當前收盤價設為買入區間上限
- 目標：+10%~20%；止損：跌破 EMA20
- 持有：1~4 週

【突破策略（breakout）】：
- 條件：VTF_Score >= 1.5、股價在 20 日高點附近（dist_from_20d_high_pct 在 -2%~+2%）
- VTF_Score < 0 一律視為假突破派發陷阱，禁止入選
- 買入：突破點附近前後 1%；目標：+10%~20%；止損：跌回突破點下方 2%
- 持有：1~2 週

【反轉策略（oversold_reversal）】：
- 條件：Momentum_ATR <= -2.0（超賣）且 VTF_Score 由負轉正或向 0 軸收斂（拋壓衰竭）
- stoch_k < 25 且 RSI 從低位回升（rsi > rsi_5d_ago）為底背離確認
- PANIC_REVERSAL 環境下，帶有 REVERSAL 標籤的個股 L2_Score 偏低是正常的，忽略低分偏見
- 買入：EMA50 附近支撐區；目標：+8%~15%；止損：近期低點下方

N/A 差異化處理：
- Earnings_Days_Left = N/A → 直接排除（財報時間未知，黑天鵝風險無法評估）
- Momentum_ATR = N/A 或 VTF_Score = N/A → 直接排除（技術數據不足）
- Beta_60D = N/A → 不排除，以 Momentum_ATR 和 VTF_Score 作為核心多空判斷

請以如下 JSON 格式輸出（根節點為物件，陣列放在 "selections" key 中），不要其他說明文字：
{"selections": [ {...}, {...}, ... ]}

每個元素包含：
- rank: 排名（整數，從 1 開始）
- ticker: 股票代號
- reason: 繁體中文選股理由，聚焦技術面優勢與策略依據（50字以內）
- risk: 繁體中文風險提示（50字以內）
- confidence: 信心分數（整數 1~10）
- buy_zone: 建議買入價格區間，格式如 "$185～$188"
- target: 目標價，格式如 "$210"
- stop_loss: 止損價，格式如 "$180"
- hold_period: 預期持有的美股交易日天數，純整數（例如 10），不得含任何非數字字元
- strategy: 套用的選股策略，只能是「動能策略」、「突破策略」、「反轉策略」三者之一
- strategy_reason: 繁體中文，說明選擇此策略的具體依據，需引用指標數值（例如：RSI=62、VTF=2.3、RS=+3.1%）（50字以內）
- confidence_reason: 繁體中文，說明信心分數給分原因，需具體說明加分或扣分的主因（50字以內）"""


# ── DeepSeek API 呼叫 ────────────────────────────────────────────

def _call_deepseek(user_content: str) -> list[dict]:
    """呼叫 DeepSeek API，回傳解析後的排名列表。"""
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=6000,
            )
            raw = resp.choices[0].message.content.strip()
            finish_reason = resp.choices[0].finish_reason
            print(f"[ranker] API 回傳 {len(raw)} 字元，finish_reason={finish_reason}")
            if finish_reason == "length":
                print("[ranker] 警告：回應因 max_tokens 截斷，考慮再調高 max_tokens")

            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
            for v in parsed.values():
                if isinstance(v, list):
                    print(f"[ranker] 解析成功，取得 {len(v)} 筆結果")
                    return v
            return []

        except json.JSONDecodeError as e:
            print(f"[ranker] JSON 解析失敗（第{attempt}次）：{e}")
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
        except Exception as e:
            print(f"[ranker] API 呼叫失敗（第{attempt}次）：{e}")

        if attempt < MAX_RETRIES:
            time.sleep(5 * attempt)

    return []


# ── 主函式 ───────────────────────────────────────────────────────

def _enrich_fallback(
    candidates: list[dict],
    info_data: dict[str, dict],
    price_data: dict[str, pd.DataFrame],
) -> list[dict]:
    """為 fallback（不呼叫 AI）的候選股補充 name/sector/price_data 欄位。"""
    result = []
    for i, c in enumerate(candidates):
        sym = c["symbol"]
        info = info_data.get(sym, {})
        result.append({
            **c,
            "rank": i + 1,
            "name": info.get("name", sym),
            "sector": c.get("sector") or info.get("sector", "Unknown"),
            "reason": "L2 技術指標評分排名",
            "risk": "請手動確認各項指標",
            "confidence": 5,
            "buy_zone": "-",
            "target": "-",
            "stop_loss": "-",
            "hold_period": "-",
            "strategy": "-",
            "strategy_reason": "",
            "confidence_reason": "",
            "_price_data": price_data.get(sym),
        })
    return result


def rank_candidates(
    candidates: list[dict],
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
    top_n: int = 10,
    market_context: dict | None = None,
    market_date: str | None = None,
    use_ai_cache: bool = True,
    earnings_data: dict | None = None,
) -> list[dict]:
    """
    接收 L2 候選股，呼叫 DeepSeek AI 排序，回傳 Top N 結果。
    每個結果含原始 L2 資料 + AI 排名/理由/信心分數。
    同日重複執行時，若 use_ai_cache=True 則複用 .cache/ranked_YYYYMMDD.json，
    不重複呼叫 DeepSeek API。
    """
    if not candidates:
        print("[ranker] 無候選股，跳過 AI 排序")
        return []

    if not DEEPSEEK_API_KEY:
        print("[ranker] 未設定 DEEPSEEK_API_KEY，跳過 AI 排序，改用 L2 分數直接輸出 Top N")
        return _enrich_fallback(candidates[:top_n], info_data, price_data)

    market_context = market_context or {}
    regime = market_context.get("regime", "")

    # BEAR_DISTRIBUTION 防禦機制：直接回傳空列表，不送 AI 請求
    if regime == "BEAR_DISTRIBUTION":
        print("[ranker] 大盤進入【陰跌熊市 BEAR_DISTRIBUTION】，系統全面防禦，不輸出買入標的")
        return []

    # 產業多樣性保護（P5，DD-11）：送給 AI 前先截斷同產業霸榜
    diversified = _diversify_candidates(candidates, regime=regime)
    print(f"[ranker] 候選池：{len(candidates)} → 產業分散後 {len(diversified)} 支")

    cache_path = _ranked_cache_path(market_date or date.today().isoformat())

    # 嘗試讀取 AI 快取（同日重複執行時避免重複呼叫 API）
    if use_ai_cache and cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            for item in cached:
                item["_price_data"] = price_data.get(item["symbol"])
            print(f"[ranker] 複用今日 AI 快取（{cache_path.name}），共 {len(cached)} 支")
            return cached[:top_n]
        except Exception as e:
            print(f"[ranker] AI 快取讀取失敗，重新呼叫 DeepSeek：{e}")

    # 取得 current_date 供 Earnings_Days_Left 計算
    current_date: date | None = None
    if market_date:
        try:
            current_date = date.fromisoformat(market_date)
        except Exception:
            pass

    print(f"[ranker] 送出 {min(len(diversified), MAX_CANDIDATES_TO_AI)} 支候選股給 DeepSeek AI...")
    prompt_content = _build_prompt(
        diversified, price_data, info_data, market_context,
        earnings_data=earnings_data, current_date=current_date,
    )

    ranked_raw = _call_deepseek(prompt_content)
    if not ranked_raw:
        print("[ranker] AI 排序失敗，改用 L2 分數直接輸出 Top N")
        return _enrich_fallback(candidates[:top_n], info_data, price_data)

    # 建立 L2 資料查詢表（原始 candidates，含全部通過 L2 的個股）
    l2_map = {c["symbol"]: c for c in candidates}

    ranked: list[dict] = []
    for item in ranked_raw:
        ticker = str(item.get("ticker", "")).strip().upper()
        l2 = l2_map.get(ticker, {})
        ranked.append({
            "rank": int(item.get("rank", len(ranked) + 1)),
            "symbol": ticker,
            "name": info_data.get(ticker, {}).get("name", ticker),
            "sector": l2.get("sector") or info_data.get(ticker, {}).get("sector", "Unknown"),
            "price": l2.get("price", 0.0),
            "total_score": l2.get("total_score", 0.0),
            "reason": str(item.get("reason", "")),
            "risk": str(item.get("risk", "")),
            "confidence": int(item.get("confidence", 5)),
            "buy_zone": str(item.get("buy_zone", "-")),
            "target": str(item.get("target", "-")),
            "stop_loss": str(item.get("stop_loss", "-")),
            "hold_period": str(item.get("hold_period", "-")),
            "strategy": str(item.get("strategy", "-")),
            "strategy_reason": str(item.get("strategy_reason", "")),
            "confidence_reason": str(item.get("confidence_reason", "")),
            "_price_data": price_data.get(ticker),
        })

    ranked.sort(key=lambda x: x["rank"])
    result = ranked[:top_n]
    print(f"[ranker] AI 排序完成，回傳 Top {len(result)}")

    # 儲存 AI 結果至快取（_price_data 為 DataFrame，不序列化）
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        serializable = [{k: v for k, v in item.items() if k != "_price_data"} for item in result]
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        print(f"[ranker] AI 結果已快取：{cache_path.name}")
    except Exception as e:
        print(f"[ranker] AI 快取儲存失敗（不影響結果）：{e}")

    return result
