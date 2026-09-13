"""
集中管理 Firebase Admin SDK 的初始化，避免多個模組各自呼叫 initialize_app() 互相衝突。
其他模組（auth.py、favorites.py、ai_insight.py、data_fetcher.py）都從這裡拿 app / db。
"""
import os
from pathlib import Path

import firebase_admin
from dotenv import load_dotenv
from firebase_admin import credentials, firestore

load_dotenv(Path(__file__).parent.parent.parent / ".env")

SERVICE_ACCOUNT_PATH = Path(__file__).parent.parent.parent / os.getenv(
    "FIREBASE_SERVICE_ACCOUNT_PATH", "./firebase-service-account.json"
)

app = None
db = None
if SERVICE_ACCOUNT_PATH.exists():
    app = firebase_admin.initialize_app(credentials.Certificate(str(SERVICE_ACCOUNT_PATH)))
    # firebase_admin 預設會連去叫 "(default)" 的資料庫，但 Firebase Console 建立出來的
    # 實際資料庫 ID 是 "default"（不帶括號），要明確指定，不然會一直報 NotFound。
    db = firestore.client(app, database_id="default")
