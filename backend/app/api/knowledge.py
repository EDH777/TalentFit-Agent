from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_hr
from app.db.session import get_db
from app.models.entities import Candidate, ResumeChunk, User
from app.schemas.candidates import CandidateOut
from app.schemas.knowledge import (ChunkBatch, ChunkCreate, ChunkOut, ChunkPage, ChunkUpdate,
                                   DocumentBatch, EnabledUpdate, KnowledgeDocument, KnowledgeSummary)
from app.services import knowledge
from app.services.llm import ModelUnavailableError

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _document(db: Session, candidate_id: int) -> Candidate:
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.deleted_at.is_(None)).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="文档不存在")
    return candidate


def _chunk(db: Session, candidate_id: int, chunk_id: int, parent_only=False) -> ResumeChunk:
    row = db.query(ResumeChunk).filter_by(id=chunk_id, candidate_id=candidate_id).first()
    if not row or (parent_only and row.chunk_type != "parent"):
        raise HTTPException(status_code=404, detail="分段不存在或不属于当前文档")
    return row


def _commit(db: Session, operation):
    try:
        result = operation()
        db.commit()
        return result
    except ModelUnavailableError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except BaseException:
        db.rollback()
        raise


def _output(db: Session, candidate: Candidate, chunk: ResumeChunk) -> ChunkOut:
    db.refresh(chunk)
    return knowledge.chunk_output(candidate, chunk, db.query(ResumeChunk).filter_by(candidate_id=candidate.id).all())


@router.get("/summary", response_model=KnowledgeSummary)
def summary(db: Session = Depends(get_db), user: User = Depends(current_user)):
    candidates = db.query(Candidate).filter(Candidate.deleted_at.is_(None)).all()
    rows = db.query(ResumeChunk).join(Candidate).filter(Candidate.deleted_at.is_(None)).all()
    stats = [knowledge.document_stats(candidate, [row for row in rows if row.candidate_id == candidate.id]) for candidate in candidates]
    return KnowledgeSummary(documents=len(candidates), enabled_documents=sum(candidate.enabled for candidate in candidates),
                            **{field: sum(getattr(item, field) for item in stats) for field in
                               ("parent_chunks", "child_chunks", "enabled_child_chunks", "indexed_child_chunks", "hit_count")})


@router.post("/documents/batch")
def batch_documents(payload: DocumentBatch, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    ids = set(payload.ids)
    candidates = db.query(Candidate).filter(Candidate.id.in_(ids), Candidate.deleted_at.is_(None)).all()
    if len(candidates) != len(ids):
        raise HTTPException(status_code=404, detail="部分文档不存在，请刷新后重试")
    def operation():
        for candidate in candidates:
            knowledge.touch_document(db, candidate)
            candidate.enabled = payload.action == "enable"
    _commit(db, operation)
    return {"updated": len(candidates)}


@router.get("/documents/{candidate_id}", response_model=KnowledgeDocument)
def get_document(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return knowledge.document_output(db, _document(db, candidate_id))


@router.patch("/documents/{candidate_id}/enabled", response_model=CandidateOut)
def enable_document(candidate_id: int, payload: EnabledUpdate, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    candidate = _document(db, candidate_id)
    def operation():
        knowledge.touch_document(db, candidate)
        candidate.enabled = payload.enabled
    _commit(db, operation)
    db.refresh(candidate)
    return CandidateOut.from_model(candidate)


@router.get("/documents/{candidate_id}/chunks", response_model=ChunkPage)
def list_chunks(candidate_id: int, parent_id: int | None = None, page: int = Query(default=1, ge=1),
                page_size: int = Query(default=10, ge=1, le=100), q: str = Query(default="", max_length=200),
                enabled: Literal["all", "true", "false"] = "all", db: Session = Depends(get_db), user: User = Depends(current_user)):
    candidate = _document(db, candidate_id)
    if parent_id is not None:
        _chunk(db, candidate_id, parent_id, parent_only=True)
    query = db.query(ResumeChunk).filter_by(candidate_id=candidate_id, parent_id=parent_id,
                                           chunk_type="child" if parent_id is not None else "parent")
    if q:
        query = query.filter(ResumeChunk.content.contains(q, autoescape=True))
    if enabled != "all":
        query = query.filter(ResumeChunk.enabled.is_(enabled == "true"))
    total = query.count()
    items = query.order_by(ResumeChunk.position, ResumeChunk.id).offset((page - 1) * page_size).limit(page_size).all()
    all_chunks = db.query(ResumeChunk).filter_by(candidate_id=candidate_id).all()
    return ChunkPage(items=[knowledge.chunk_output(candidate, row, all_chunks) for row in items], total=total, page=page, page_size=page_size)


@router.post("/documents/{candidate_id}/chunks", response_model=ChunkOut, status_code=201)
def create_chunk(candidate_id: int, payload: ChunkCreate, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    candidate = _document(db, candidate_id)
    parent = _chunk(db, candidate_id, payload.parent_id, parent_only=True) if payload.parent_id is not None else None
    row = _commit(db, lambda: knowledge.create_chunk(db, candidate, payload.content, parent))
    return _output(db, candidate, row)


@router.post("/documents/{candidate_id}/chunks/batch")
def batch_chunks(candidate_id: int, payload: ChunkBatch, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    candidate = _document(db, candidate_id)
    ids = set(payload.ids)
    rows = db.query(ResumeChunk).filter(ResumeChunk.candidate_id == candidate_id, ResumeChunk.id.in_(ids)).all()
    if len(rows) != len(ids):
        raise HTTPException(status_code=404, detail="部分分段不存在或不属于当前文档，请刷新后重试")
    def operation():
        if payload.action == "delete":
            knowledge.delete_chunks(db, candidate, rows)
        else:
            knowledge.touch_document(db, candidate)
            for row in rows:
                row.enabled = payload.action == "enable"
    _commit(db, operation)
    return {"updated": len(rows)}


@router.patch("/documents/{candidate_id}/chunks/{chunk_id}", response_model=ChunkOut)
def update_chunk(candidate_id: int, chunk_id: int, payload: ChunkUpdate, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    candidate = _document(db, candidate_id)
    chunk = _chunk(db, candidate_id, chunk_id)
    _commit(db, lambda: knowledge.update_chunk(db, candidate, chunk, payload.model_dump(exclude_unset=True)))
    return _output(db, candidate, chunk)


@router.delete("/documents/{candidate_id}/chunks/{chunk_id}", status_code=204)
def delete_chunk(candidate_id: int, chunk_id: int, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    candidate = _document(db, candidate_id)
    chunk = _chunk(db, candidate_id, chunk_id)
    _commit(db, lambda: knowledge.delete_chunks(db, candidate, [chunk]))
