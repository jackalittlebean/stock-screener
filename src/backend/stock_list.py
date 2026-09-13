"""
從 TWSE（上市）和 TPEX（上櫃）下載股票清單，存成 data/stock_list.json
執行：python stock_list.py
"""
import requests
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def fetch_twse() -> list[dict]:
    """抓上市股票（含股號、股名）"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    stocks = []
    for item in raw:
        code = item.get("Code", "")
        name = item.get("Name", "")
        # 只保留純數字 4 碼的股票（排除 ETF、權證等）
        if re.fullmatch(r"\d{4}", code):
            stocks.append({"code": code, "name": name, "market": "TW"})
    return stocks


def fetch_tpex() -> list[dict]:
    """抓上櫃股票"""
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    stocks = []
    for item in raw:
        code = item.get("SecuritiesCompanyCode", "")
        name = item.get("CompanyName", "")
        if re.fullmatch(r"\d{4}", code):
            stocks.append({"code": code, "name": name, "market": "TWO"})
    return stocks


def build_stock_list():
    print("抓取上市股票...")
    twse = fetch_twse()
    print(f"  TWSE：{len(twse)} 支")

    print("抓取上櫃股票...")
    tpex = fetch_tpex()
    print(f"  TPEX：{len(tpex)} 支")

    all_stocks = twse + tpex
    # 去重（以股號為準）
    seen = set()
    unique = []
    for s in all_stocks:
        if s["code"] not in seen:
            seen.add(s["code"])
            unique.append(s)

    DATA_DIR.mkdir(exist_ok=True)
    out = DATA_DIR / "stock_list.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)
    print(f"完成，共 {len(unique)} 支，存至 {out}")
    return unique


if __name__ == "__main__":
    build_stock_list()
