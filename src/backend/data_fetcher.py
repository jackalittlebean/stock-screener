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
    print(f"開始處理 {total} 支股票...")

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
        revenue_growth = fetch_revenue_growth(code)

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
    print(f"\n完成！共 {len(results)} 支有效股票，已存進 Firestore meta/stocks_snapshot")


def _save(results: list):
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.collection("meta").document("stocks_snapshot").set({
        "updated_at": updated_at,
        "stocks": results,
    })


if __name__ == "__main__":
    run()
