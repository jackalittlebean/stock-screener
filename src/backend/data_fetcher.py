"""
批次抓取所有台股資料：yfinance（價格、PE、EPS）+ FinMind（月營收）
計算技術指標後存進 Firestore 的 meta/stocks_snapshot 文件
執行：python data_fetcher.py
預計耗時：30–60 分鐘
"""
import json
import math
import time
import os
from pathlib import Path
from datetime import datetime, timedelta

import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv

from firebase_app import db

load_dotenv(Path(__file__).parent.parent.parent / ".env")

DATA_DIR = Path(__file__).parent.parent.parent / "data"
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# FinMind 免費額度約 600 次/天。過去的做法是每支股票都打一次，跑到第 570 支左右
# 就被限流，之後 1400 多支全部靜默失敗、營收欄位變成空值（涵蓋率只有 28.8%）。
# 改成游標式輪抓：每天只抓額度內的股票，沒輪到的沿用上一輪的值，幾天內輪完全市場。
FINMIND_DAILY_LIMIT = 600
SAFETY_MARGIN = 20          # 留一點餘裕，避免剛好卡在上限
CURSOR_DOC = "revenue_cursor"
USAGE_DOC = "finmind_usage"


def _revenue_budget() -> int:
    """本輪可用的 FinMind 請求數 = 每日上限 - deep_fetcher 已用掉的 - 安全邊際。"""
    used = 0
    doc = db.collection("meta").document(USAGE_DOC).get()
    if doc.exists:
        data = doc.to_dict()
        # 只認今天的用量紀錄，跨日就歸零重算
        if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
            used = data.get("used", 0)
    return max(0, FINMIND_DAILY_LIMIT - used - SAFETY_MARGIN)


def _load_cursor() -> int:
    doc = db.collection("meta").document(CURSOR_DOC).get()
    return doc.to_dict().get("index", 0) if doc.exists else 0


def _save_cursor(index: int) -> None:
    db.collection("meta").document(CURSOR_DOC).set({
        "index": index,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })


def _load_previous_growth() -> dict:
    """取出上一輪已經抓到的營收年增率。這輪沒輪到的股票沿用這些值，資料才不會倒退。"""
    doc = db.collection("meta").document("stocks_snapshot").get()
    if not doc.exists:
        return {}
    return {
        s["code"]: s.get("revenue_growth")
        for s in doc.to_dict().get("stocks", [])
        if s.get("revenue_growth") is not None
    }


# ── FinMind：月營收年增率 ──────────────────────────────────────────────────────

def fetch_revenue_growth(code: str) -> float | None:
    """
    回傳最新月份的營收年增率（%），無資料回傳 None。
    FinMind 只提供原始營收數字，需自行計算：
    年增率 = (本月營收 - 去年同月營收) / 去年同月營收 * 100
    """
    if not FINMIND_TOKEN or "請在此填入" in FINMIND_TOKEN:
        return None
    # 抓 14 個月確保能找到去年同月
    start = (datetime.now() - timedelta(days=430)).strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            FINMIND_API,
            params={
                "dataset": "TaiwanStockMonthRevenue",
                "data_id": code,
                "start_date": start,
                "token": FINMIND_TOKEN,
            },
            timeout=15,
        )
        data = resp.json().get("data", [])
        if not data:
            return None

        # 以 (year, month) 為 key 建立查詢表
        by_ym = {(d["revenue_year"], d["revenue_month"]): d["revenue"] for d in data}

        # 找最新一筆
        latest = sorted(data, key=lambda x: x["date"], reverse=True)[0]
        y, m = latest["revenue_year"], latest["revenue_month"]
        current_rev = latest["revenue"]

        # 找去年同月
        last_year_rev = by_ym.get((y - 1, m))
        if last_year_rev is None or last_year_rev == 0:
            return None

        growth = (current_rev - last_year_rev) / last_year_rev * 100
        return round(growth, 2)
    except Exception:
        return None


# ── yfinance：價格 + 基本面 ────────────────────────────────────────────────────

def fetch_yfinance(ticker_symbol: str) -> dict | None:
    """回傳歷史價格、PE、EPS；失敗回傳 None"""
    try:
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(period="6mo")
        if hist.empty or len(hist) < 30:
            return None
        info = tk.info or {}
        return {"hist": hist, "info": info}
    except Exception:
        return None


# ── 技術指標計算 ───────────────────────────────────────────────────────────────

def calc_indicators(hist: pd.DataFrame) -> dict:
    close = hist["Close"]

    ma5  = ta.sma(close, length=5)
    ma20 = ta.sma(close, length=20)
    ma60 = ta.sma(close, length=60)
    rsi  = ta.rsi(close, length=14)
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)

    last = -1  # 最後一筆

    ma5_val  = round(float(ma5.iloc[last]),  2) if ma5  is not None and not ma5.isna().all()  else None
    ma20_val = round(float(ma20.iloc[last]), 2) if ma20 is not None and not ma20.isna().all() else None
    ma60_val = round(float(ma60.iloc[last]), 2) if ma60 is not None and not ma60.isna().all() else None
    rsi_val  = round(float(rsi.iloc[last]),  2) if rsi  is not None and not rsi.isna().all()  else None

    dif = sig = None
    if macd_df is not None and not macd_df.empty:
        dif_col = [c for c in macd_df.columns if c.startswith("MACD_") and "h" not in c.lower() and "s" not in c.lower()]
        sig_col = [c for c in macd_df.columns if "MACDs_" in c]
        if dif_col and sig_col:
            dif = float(macd_df[dif_col[0]].iloc[last])
            sig = float(macd_df[sig_col[0]].iloc[last])

    macd_cross = None
    if dif is not None and sig is not None and not (pd.isna(dif) or pd.isna(sig)):
        macd_cross = "golden" if dif > sig else "death"

    price = round(float(close.iloc[last]), 2)

    return {
        "price": price,
        "ma5":   ma5_val,
        "ma20":  ma20_val,
        "ma60":  ma60_val,
        "rsi":   rsi_val,
        "macd_dif": round(dif, 4) if dif else None,
        "macd_sig": round(sig, 4) if sig else None,
        "macd_cross": macd_cross,
    }


# ── 主流程 ─────────────────────────────────────────────────────────────────────

def run():
    stock_list_path = DATA_DIR / "stock_list.json"
    if not stock_list_path.exists():
        print("找不到 stock_list.json，請先執行 stock_list.py")
        return

    with open(stock_list_path, encoding="utf-8") as f:
        stocks = json.load(f)

    total = len(stocks)
    # 這輪由誰負責抓營收：從上次的游標往後數「額度允許的支數」，繞一圈後回到開頭
    prev_growth = _load_previous_growth()
    budget = _revenue_budget()
    cursor = _load_cursor() % total if total else 0
    rotation = [stocks[(cursor + n) % total]["code"] for n in range(min(budget, total))]
    revenue_targets = set(rotation)
    next_cursor = (cursor + len(rotation)) % total if total else 0

    print(f"開始處理 {total} 支股票...")
    print(f"本輪營收額度 {budget} 次，從第 {cursor + 1} 支開始抓 {len(rotation)} 支，"
          f"其餘沿用上一輪的值（已有 {len(prev_growth)} 支有資料）")

    results = []
    for i, s in enumerate(stocks, 1):
        code   = s["code"]
        name   = s["name"]
        market = s["market"]  # "TW" or "TWO"
        ticker = f"{code}.{market}"

        print(f"[{i}/{total}] {ticker} {name}", end=" ", flush=True)

        # yfinance（retry 3 次）
        yf_data = None
        for attempt in range(3):
            yf_data = fetch_yfinance(ticker)
            if yf_data is not None:
                break
            time.sleep(1)

        if yf_data is None:
            print("→ 跳過（無資料）")
            continue

        indicators = calc_indicators(yf_data["hist"])
        info = yf_data["info"]

        pe  = info.get("trailingPE")
        eps = info.get("trailingEps")
        pe  = round(float(pe),  2) if pe  and not pd.isna(pe)  and math.isfinite(pe)  else None
        eps = round(float(eps), 2) if eps and not pd.isna(eps) and math.isfinite(eps) else None

        # FinMind 月營收年增率
        # 只抓本輪負責的股票，其餘沿用舊值。抓失敗（額度用盡）時也回退到舊值，
        # 避免已經有的資料被 None 覆蓋掉，涵蓋率才能隨著輪抓逐步補滿。
        if code in revenue_targets:
            revenue_growth = fetch_revenue_growth(code)
            if revenue_growth is None:
                revenue_growth = prev_growth.get(code)
        else:
            revenue_growth = prev_growth.get(code)

        record = {
            "code": code,
            "name": name,
            "market": market,
            **indicators,
            "pe": pe,
            "eps": eps,
            "revenue_growth": revenue_growth,
        }
        results.append(record)
        print(f"→ 完成 (RSI={indicators['rsi']}, PE={pe})")

        time.sleep(0.5)  # 避免 rate limit

        # 每 100 支存一次中間結果，避免全部跑完才存
        if i % 100 == 0:
            _save(results)
            print(f"  ── 已存 {len(results)} 筆中間結果 ──")

    _save(results)
    _save_cursor(next_cursor)

    covered = sum(1 for r in results if r.get("revenue_growth") is not None)
    print(f"\n完成！共 {len(results)} 支有效股票，已存進 Firestore meta/stocks_snapshot")
    print(f"營收涵蓋率：{covered}/{len(results)} "
          f"({covered / len(results) * 100:.1f}%)，下一輪從第 {next_cursor + 1} 支開始")


def _save(results: list):
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.collection("meta").document("stocks_snapshot").set({
        "updated_at": updated_at,
        "stocks": results,
    })


if __name__ == "__main__":
    run()
