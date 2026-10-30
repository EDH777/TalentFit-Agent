"""Pydantic contracts per business module; import from the concrete module."""
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.schemas.candidates import CandidateDetail, CandidateOut, CandidateUpdate, ManualCandidateCreate, SummaryOut
from app.schemas.chat import ChatMessageCreate, ChatMessageOut, ChatReply, SessionCreate, SessionOut

__all__ = [
    "LoginRequest", "LoginResponse", "UserOut",
    "CandidateDetail", "CandidateOut", "CandidateUpdate", "ManualCandidateCreate", "SummaryOut",
    "ChatMessageCreate", "ChatMessageOut", "ChatReply", "SessionCreate", "SessionOut",
]
