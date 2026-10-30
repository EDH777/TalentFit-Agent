from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: str = Field(default="新会话", max_length=160)


class SessionOut(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ChatMessageCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=8000)


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    intent: str | None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ChatReply(BaseModel):
    message: ChatMessageOut
    intent: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
