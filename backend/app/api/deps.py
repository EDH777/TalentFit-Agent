from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_db, SessionLocal
from app.models.entities import User
from app.services.auth import decode_access_token

bearer = HTTPBearer(auto_error=False)


def stream_session_factory():
    return SessionLocal


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效") from exc
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    return user


def require_hr(user: User = Depends(current_user)) -> User:
    if user.role != "hr":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有人事可以管理人才资料")
    return user
