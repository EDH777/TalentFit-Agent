from fastapi import APIRouter, Depends, HTTPException
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.api.deps import current_user, stream_session_factory
from app.db.session import get_db
from app.models.entities import ChatMessage, ChatSession, User
from app.schemas.chat import ChatMessageCreate, ChatMessageOut, ChatReply, SessionCreate, SessionOut
from app.services.agent import AgentUnavailableError, answer, stream_answer
from app.services.events import event_response, StreamCancelled

router = APIRouter(prefix="/api/chat", tags=["chat"])


def owned_session(db: Session, session_id: int, user_id: int) -> ChatSession:
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


def update_owned_session(db: Session, session_id: int, user_id: int, created_at: datetime, title: str | None = None):
    values = {"updated_at": datetime.now(timezone.utc)}
    if title is not None:
        values["title"] = title
    # Lock the surviving session before writing; SQLite may reuse a deleted ID.
    updated = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user_id,
                                          ChatSession.created_at == created_at).update(values, synchronize_session=False)
    if not updated:
        db.rollback()
        raise HTTPException(status_code=404, detail="会话已删除，请开始新对话")


def recent_history(db: Session, session_id: int) -> tuple[list[ChatMessage], list[dict]]:
    previous = db.query(ChatMessage).filter_by(session_id=session_id).order_by(ChatMessage.id.desc()).limit(12).all()
    return previous, [{"role": item.role, "content": item.content} for item in reversed(previous)]


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session(payload: SessionCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    session = ChatSession(user_id=user.id, title=payload.title or "新会话")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.query(ChatSession).filter(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).all()


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    session = owned_session(db, session_id, user.id)
    update_owned_session(db, session_id, user.id, session.created_at)
    # Delete children under the same write lock used when saving replies.
    db.query(ChatMessage).filter(ChatMessage.session_id == session.id).delete(synchronize_session=False)
    db.query(ChatSession).filter(ChatSession.id == session.id).delete()
    db.commit()


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def list_messages(session_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    session = owned_session(db, session_id, user.id)
    return db.query(ChatMessage).filter(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at.asc()).all()


@router.post("/sessions/{session_id}/messages", response_model=ChatReply)
def send_message(session_id: int, payload: ChatMessageCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    session = owned_session(db, session_id, user.id)
    created_at = session.created_at
    previous, history = recent_history(db, session.id)
    update_owned_session(db, session_id, user.id, created_at)
    user_message = ChatMessage(session_id=session.id, role="user", content=payload.content)
    db.add(user_message)
    db.commit()
    try:
        content, intent, sources = answer(db, payload.content, history=history)
    except AgentUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    update_owned_session(db, session_id, user.id, created_at, payload.content[:40] if not previous else None)
    assistant = ChatMessage(session_id=session.id, role="assistant", content=content, intent=intent,
                            sources_json=json.dumps(sources, ensure_ascii=False))
    db.add(assistant)
    db.commit()
    db.refresh(assistant)
    return ChatReply(message=assistant, intent=intent, sources=sources)


@router.post("/sessions/{session_id}/messages/stream")
def send_stream(session_id: int, payload: ChatMessageCreate, db: Session = Depends(get_db),
                user: User = Depends(current_user), factory=Depends(stream_session_factory)):
    session = owned_session(db, session_id, user.id)
    created_at = session.created_at
    user_id = user.id

    def worker(emit, cancelled):
        with factory() as work_db:
            try:
                owned_session(work_db, session_id, user_id)
                previous, history = recent_history(work_db, session_id)
                update_owned_session(work_db, session_id, user_id, created_at, payload.content[:40] if not previous else None)
                work_db.add(ChatMessage(session_id=session_id, role="user", content=payload.content))
                work_db.commit()
                for event in stream_answer(work_db, payload.content, history=history, cancelled=cancelled):
                    if event["type"] != "result":
                        emit(event)
                        continue
                    if cancelled():
                        raise StreamCancelled()
                    update_owned_session(work_db, session_id, user_id, created_at)
                    message = ChatMessage(session_id=session_id, role="assistant", content=event["content"],
                                          intent=event["intent"], sources_json=json.dumps(event["sources"], ensure_ascii=False),
                                          trace_json=json.dumps(event.get("trace", []), ensure_ascii=False))
                    work_db.add(message)
                    work_db.commit()
                    work_db.refresh(message)
                    emit({"type": "done", "message": ChatMessageOut.model_validate(message).model_dump(mode="json"),
                          "intent": event["intent"], "sources": event["sources"]})
            except AgentUnavailableError as exc:
                work_db.rollback()
                emit({"type": "error", "detail": str(exc)})
            except HTTPException as exc:
                work_db.rollback()
                emit({"type": "error", "detail": exc.detail})

    return event_response(worker)
