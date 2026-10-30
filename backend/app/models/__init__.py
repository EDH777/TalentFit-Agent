from app.models.entities import ChatMessage, ChatSession, Candidate, ResumeChunk, User
from app.models import staffing  # Register additive tables on the shared Base metadata.

__all__ = ["User", "Candidate", "ResumeChunk", "ChatSession", "ChatMessage"]
