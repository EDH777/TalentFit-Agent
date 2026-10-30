from datetime import datetime, timezone
import json
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.core.job_categories import JOB_CATEGORY_CHECK


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="hr")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    candidates: Mapped[list["Candidate"]] = relationship(back_populates="creator")
    sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (CheckConstraint(JOB_CATEGORY_CHECK, name='ck_candidate_job_category'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    job_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    years_experience: Mapped[str] = mapped_column(String(40), default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    processed_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    processing_version: Mapped[str] = mapped_column(String(40), default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), default="ready", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    parent_chunk_size: Mapped[int] = mapped_column(Integer, default=4000, server_default="4000")
    child_chunk_size: Mapped[int] = mapped_column(Integer, default=2048, server_default="2048")
    child_chunk_overlap: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    parent_delimiter: Mapped[str] = mapped_column(String(256), default="<<<RESUME_PARENT>>>", server_default="<<<RESUME_PARENT>>>")
    child_delimiter: Mapped[str] = mapped_column(String(256), default="<<<RESUME_SECTION>>>", server_default="<<<RESUME_SECTION>>>")
    replace_consecutive_spaces: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    delete_urls_email: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    creator: Mapped[User] = relationship(back_populates="candidates")
    chunks: Mapped[list["ResumeChunk"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class ResumeChunk(Base):
    __tablename__ = "resume_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("resume_chunks.id"), nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(20), default="child")
    content: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    candidate: Mapped[Candidate] = relationship(back_populates="chunks")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    trace_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    @property
    def sources(self) -> list[dict]:
        return json.loads(self.sources_json or "[]")

    @property
    def trace(self) -> list[dict]:
        return json.loads(self.trace_json or "[]")
