"""
每個使用者的收藏股號清單，存在 Firestore 的 favorites/{uid} 文件裡。
"""
from firebase_app import db

COLLECTION = "favorites"


def load_favorites(uid: str) -> list[str]:
    doc = db.collection(COLLECTION).document(uid).get()
    if not doc.exists:
        return []
    return doc.to_dict().get("codes", [])


def save_favorites(uid: str, codes: list[str]) -> None:
    db.collection(COLLECTION).document(uid).set({"codes": codes})


def add_favorite(uid: str, code: str) -> list[str]:
    codes = load_favorites(uid)
    if code not in codes:
        codes.append(code)
        save_favorites(uid, codes)
    return codes


def remove_favorite(uid: str, code: str) -> list[str]:
    codes = [c for c in load_favorites(uid) if c != code]
    save_favorites(uid, codes)
    return codes
