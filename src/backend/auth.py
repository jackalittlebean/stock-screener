"""
Firebase 登入驗證。
所有需要登入的路由都用 get_current_user 這個 dependency 檢查請求帶的 Google 登入 token。
"""
from fastapi import Header, HTTPException
from firebase_admin import auth as firebase_auth

from firebase_app import SERVICE_ACCOUNT_PATH, app as _app


def get_current_user(authorization: str = Header(None)) -> str:
    """驗證 Authorization: Bearer <idToken>，回傳 Firebase uid。"""
    if _app is None:
        raise HTTPException(
            status_code=503,
            detail=f"尚未設定 Firebase 服務帳號金鑰，請將金鑰檔放到 {SERVICE_ACCOUNT_PATH}",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少登入憑證")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = firebase_auth.verify_id_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="登入憑證無效或已過期，請重新登入")

    return decoded["uid"]
