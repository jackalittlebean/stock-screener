import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from screener import screen_stocks
from ai_insight import get_stock_insight
from auth import get_current_user
from firebase_app import db
import favorites as favorites_store

app = FastAPI(title="台股篩選器")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent.parent.parent
FRONTEND_DIR = BASE_DIR / "src" / "frontend"

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def _load_snapshot():
    """讀 Firestore 的 meta/stocks_snapshot，回傳 (stocks, updated_at)；尚未產生資料回傳 (None, None)。"""
    doc = db.collection("meta").document("stocks_snapshot").get()
    if not doc.exists:
        return None, None
    data = doc.to_dict()
    return data.get("stocks", []), data.get("updated_at")


class ScreenRequest(BaseModel):
    ma_type: Optional[str] = None        # "5_20_bull", "20_60_bull", "5_20_bear", "20_60_bear"
    rsi_min: Optional[float] = None
    rsi_max: Optional[float] = None
    macd_signal: Optional[str] = None   # "golden", "death"
    pe_max: Optional[float] = None
    eps_min: Optional[float] = None
    revenue_growth: Optional[str] = None  # "positive", "negative"


@app.get("/")
def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/api/status")
def status(user_id: str = Depends(get_current_user)):
    stocks, updated_at = _load_snapshot()
    if stocks is None:
        return {"ready": False, "message": "尚未產生資料，請先執行 data_fetcher.py"}
    return {"ready": True, "updated_at": updated_at, "stock_count": len(stocks)}


@app.post("/api/stocks/screen")
def screen(req: ScreenRequest, user_id: str = Depends(get_current_user)):
    stocks, _ = _load_snapshot()
    if stocks is None:
        raise HTTPException(status_code=503, detail="資料尚未準備好，請先執行資料更新")
    results = screen_stocks(stocks, req.dict())
    return {"count": len(results), "stocks": results}


@app.get("/api/favorites")
def get_favorites(user_id: str = Depends(get_current_user)):
    stocks, _ = _load_snapshot()
    if stocks is None:
        raise HTTPException(status_code=503, detail="資料尚未準備好，請先執行資料更新")
    codes = set(favorites_store.load_favorites(user_id))
    all_rows = screen_stocks(stocks, {})
    results = [s for s in all_rows if s["code"] in codes]
    return {"count": len(results), "stocks": results}


@app.post("/api/favorites/{code}")
def add_favorite(code: str, user_id: str = Depends(get_current_user)):
    codes = favorites_store.add_favorite(user_id, code)
    return {"codes": codes}


@app.delete("/api/favorites/{code}")
def remove_favorite(code: str, user_id: str = Depends(get_current_user)):
    codes = favorites_store.remove_favorite(user_id, code)
    return {"codes": codes}


@app.get("/api/stocks/{code}/insight")
def stock_insight(code: str, user_id: str = Depends(get_current_user)):
    stocks, updated_at = _load_snapshot()
    if stocks is None:
        raise HTTPException(status_code=503, detail="資料尚未準備好，請先執行資料更新")
    stock = next((s for s in stocks if s["code"] == code), None)
    if stock is None:
        raise HTTPException(status_code=404, detail="找不到此股票代碼")

    updated_at = (updated_at or "")[:10]
    try:
        insight = get_stock_insight(stock, updated_at)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 服務呼叫失敗：{e}")

    return {"code": code, "name": stock.get("name"), **insight}
