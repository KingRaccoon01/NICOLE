"""
Nicole — Teşhis: son hatalar (arayüzü yok; sorun olduğunda bakmak için).

Tarayıcıdaki JavaScript hataları ve dikte akışındaki hatalar burada tutulur:
  GET  /api/diag/errors        — son 30 hata
  POST /api/diag/client-error  — tarayıcı kendi hatasını bildirir (api.js)
"""
import time
from collections import deque

from fastapi import APIRouter

router = APIRouter(prefix="/api/diag", tags=["diag"])
_recent_errors: deque = deque(maxlen=30)


def record_error(source: str, message: str) -> None:
    _recent_errors.appendleft({"time": time.strftime("%H:%M:%S"), "source": source, "message": message})


@router.get("/errors")
async def recent_errors():
    return list(_recent_errors)


@router.post("/client-error")
async def client_error(payload: dict):
    record_error(f"tarayıcı {payload.get('page', '')}", str(payload.get("message", ""))[:500])
    return {"ok": True}
