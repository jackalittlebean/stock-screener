"""
篩選邏輯：讀取 stocks.json，套用條件，回傳符合清單。
所有條件都是可選的，未傳入 = 不限。
"""
import math
from typing import Optional


def _clean(v):
    """inf/nan 不是合法 JSON，一律當成無資料"""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _ma_match(stock: dict, ma_type: Optional[str]) -> bool:
    if not ma_type:
        return True
    ma5, ma20, ma60 = stock.get("ma5"), stock.get("ma20"), stock.get("ma60")
    if ma_type == "5_20_bull":
        return ma5 is not None and ma20 is not None and ma5 > ma20
    if ma_type == "5_20_bear":
        return ma5 is not None and ma20 is not None and ma5 < ma20
    if ma_type == "20_60_bull":
        return ma20 is not None and ma60 is not None and ma20 > ma60
    if ma_type == "20_60_bear":
        return ma20 is not None and ma60 is not None and ma20 < ma60
    return True


def _rsi_match(stock: dict, rsi_min: Optional[float], rsi_max: Optional[float]) -> bool:
    if rsi_min is None and rsi_max is None:
        return True
    rsi = stock.get("rsi")
    if rsi is None:
        return False
    if rsi_min is not None and rsi < rsi_min:
        return False
    if rsi_max is not None and rsi > rsi_max:
        return False
    return True


def _macd_match(stock: dict, macd_signal: Optional[str]) -> bool:
    if not macd_signal:
        return True
    cross = stock.get("macd_cross")
    if cross is None:
        return False
    return cross == macd_signal


def _pe_match(stock: dict, pe_max: Optional[float]) -> bool:
    if pe_max is None:
        return True
    pe = stock.get("pe")
    if pe is None:
        return False
    return pe <= pe_max


def _eps_match(stock: dict, eps_min: Optional[float]) -> bool:
    if eps_min is None:
        return True
    eps = stock.get("eps")
    if eps is None:
        return False
    return eps >= eps_min


def _revenue_match(stock: dict, revenue_growth: Optional[str]) -> bool:
    if not revenue_growth:
        return True
    rg = stock.get("revenue_growth")
    if rg is None:
        return False
    if revenue_growth == "positive":
        return rg > 0
    if revenue_growth == "negative":
        return rg < 0
    return True


def screen_stocks(stocks: list[dict], conditions: dict) -> list[dict]:
    ma_type        = conditions.get("ma_type")
    rsi_min        = conditions.get("rsi_min")
    rsi_max        = conditions.get("rsi_max")
    macd_signal    = conditions.get("macd_signal")
    pe_max         = conditions.get("pe_max")
    eps_min        = conditions.get("eps_min")
    revenue_growth = conditions.get("revenue_growth")

    results = []
    for s in stocks:
        if not _ma_match(s, ma_type):
            continue
        if not _rsi_match(s, rsi_min, rsi_max):
            continue
        if not _macd_match(s, macd_signal):
            continue
        if not _pe_match(s, pe_max):
            continue
        if not _eps_match(s, eps_min):
            continue
        if not _revenue_match(s, revenue_growth):
            continue
        results.append({
            "code":           s["code"],
            "name":           s["name"],
            "price":          _clean(s.get("price")),
            "ma5":            _clean(s.get("ma5")),
            "ma20":           _clean(s.get("ma20")),
            "ma60":           _clean(s.get("ma60")),
            "rsi":            _clean(s.get("rsi")),
            "macd_cross":     s.get("macd_cross"),
            "pe":             _clean(s.get("pe")),
            "eps":            _clean(s.get("eps")),
            "revenue_growth": _clean(s.get("revenue_growth")),
        })

    return results
