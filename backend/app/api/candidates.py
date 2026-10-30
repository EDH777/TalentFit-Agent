import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_hr, stream_session_factory
from app.core.config import settings
from app.core.job_categories import JobCategory
from app.db.session import get_db
from app.models.entities import Candidate, ResumeChunk, User
from app.models.staffing import EmployeeDocument
from app.schemas.candidates import CandidateDetail, CandidateOut, CandidateUpdate, ManualCandidateCreate, SummaryOut
from app.services.resume import ResumeProcessingError, create_chunks, infer_metadata, persist_upload
from app.services.events import event_response, StreamCancelled
from app.services import embeddings
from app.services.knowledge import refresh_document_index, touch_document
from app.services.llm import ModelUnavailableError

router = APIRouter(prefix="/api", tags=["candidates"])


def _build_index(db: Session, candidate: Candidate):
    try:
        create_chunks(db, candidate)
    except ModelUnavailableError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(
    q: str = Query(default="", max_length=120),
    status_filter: str = Query(default="", alias="status"),
    job_category: JobCategory | Literal['unclassified'] | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    query = db.query(Candidate).filter(Candidate.deleted_at.is_(None))
    if status_filter:
        query = query.filter(Candidate.status == status_filter)
    if job_category:
        query = query.filter(Candidate.job_category.is_(None) if job_category == 'unclassified' else Candidate.job_category == job_category)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Candidate.name.ilike(like), Candidate.job_category.ilike(like), Candidate.tags_json.ilike(like)))
    return [CandidateOut.from_model(candidate) for candidate in query.order_by(Candidate.updated_at.desc()).all()]


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
def get_candidate(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.deleted_at.is_(None)).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="人才记录不存在")
    return CandidateDetail.from_model(candidate)


@router.post("/candidates/manual", response_model=CandidateDetail, status_code=201)
def create_manual_candidate(payload: ManualCandidateCreate, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    name, years, inferred_tags = infer_metadata(payload.name, payload.raw_text, payload.tags)
    candidate = Candidate(
        name=payload.name or name,
        job_category=payload.job_category,
        years_experience=payload.years_experience or years,
        tags_json=json.dumps(inferred_tags, ensure_ascii=False),
        raw_text=payload.raw_text,
        status="processing",
        created_by=user.id,
    )
    db.add(candidate)
    db.flush()
    _build_index(db, candidate)
    candidate.status = "ready"
    db.commit()
    db.refresh(candidate)
    return CandidateDetail.from_model(candidate)


@router.post("/candidates/upload", response_model=CandidateDetail, status_code=201)
def upload_candidate(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_hr)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择文件")
    data = file.file.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    try:
        candidate = persist_upload(db, user.id, file.filename, data)
    except ResumeProcessingError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    db.commit()
    db.refresh(candidate)
    return CandidateDetail.from_model(candidate)


@router.post("/candidates/upload/stream")
def upload_stream(files: list[UploadFile] = File(...), user: User = Depends(require_hr), factory=Depends(stream_session_factory)):
    if not 1 <= len(files) <= settings.max_batch_files:
        raise HTTPException(status_code=400, detail=f"每批最多上传{settings.max_batch_files}份简历。")
    prepared = []
    for file in files:
        prepared.append((file.filename or "", file.file.read(settings.max_upload_size_mb * 1024 * 1024 + 1)))
        file.file.close()
    user_id = user.id

    def worker(emit, cancelled):
        emit({"type": "batch_start", "total": len(prepared)})
        succeeded = 0
        for index, (filename, data) in enumerate(prepared):
            if cancelled():
                raise StreamCancelled()
            def progress(stage, value, label):
                emit({"type": "file_progress", "file_index": index, "filename": filename,
                      "stage": stage, "progress": value, "label": label})
            with factory() as work_db:
                try:
                    candidate = persist_upload(work_db, user_id, filename, data, progress=progress)
                    if cancelled():
                        if candidate.file_path:
                            Path(candidate.file_path).unlink(missing_ok=True)
                        raise StreamCancelled()
                    work_db.commit()
                    result = CandidateDetail.from_model(candidate).model_dump(mode="json")
                    ready = candidate.status == "ready"
                    succeeded += int(ready)
                    if ready:
                        progress("done", 100, "入库完成")
                    emit({"type": "file_result", "file_index": index, "filename": filename,
                          "status": candidate.status, "candidate": result, "detail": candidate.error_message})
                except StreamCancelled:
                    raise
                except Exception as exc:
                    work_db.rollback()
                    detail = exc.detail.get('message') if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else str(exc)
                    emit({"type": "file_result", "file_index": index, "filename": filename,
                          "status": "failed", "candidate": None, "detail": detail or "该文件处理失败，请查看服务日志"})
        emit({"type": "batch_done", "total": len(prepared), "succeeded": succeeded, "failed": len(prepared) - succeeded})

    return event_response(worker)


@router.get("/search/status")
def search_status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return embeddings.index_status(db)


@router.post("/candidates/reindex/stream")
def reindex_stream(user: User = Depends(require_hr), factory=Depends(stream_session_factory)):
    def worker(emit, cancelled):
        with factory() as work_db:
            try:
                for event in embeddings.reindex_missing(work_db, cancelled=cancelled):
                    if cancelled():
                        raise StreamCancelled()
                    emit(event)
            except ModelUnavailableError as exc:
                work_db.rollback()
                emit({"type": "error", "detail": str(exc)})
    return event_response(worker)


@router.put("/candidates/{candidate_id}", response_model=CandidateDetail)
def update_candidate(candidate_id: int, payload: CandidateUpdate, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.deleted_at.is_(None)).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="人才记录不存在")
    data = payload.model_dump(exclude_unset=True)
    expected_updated_at = data.pop('expected_updated_at', None)
    linked_category = 'job_category' in data and db.query(EmployeeDocument.id).filter_by(
        candidate_id=candidate.id, candidate_created_at=candidate.created_at, link_status='active', is_primary=True).first() is not None
    if linked_category and expected_updated_at is None:
        raise HTTPException(status_code=409, detail='关联员工的岗位分类需要资料版本，请刷新后重试')
    if expected_updated_at is not None:
        expected = expected_updated_at.astimezone(timezone.utc).replace(tzinfo=None) if expected_updated_at.tzinfo else expected_updated_at
        current = candidate.updated_at.astimezone(timezone.utc).replace(tzinfo=None) if candidate.updated_at.tzinfo else candidate.updated_at
        if expected != current:
            raise HTTPException(status_code=409, detail='文档已被其他操作更新，请刷新后重试')
    if 'raw_text' in data and (candidate.source_filename or candidate.file_path):
        raise HTTPException(status_code=422, detail='上传文档的提取原文不支持修改，请维护人才字段或分段内容。')
    # Hold pending metadata in memory until model preparation and the version CAS finish.
    # Otherwise an autoflush could persist an obsolete category before touch_document.
    with db.no_autoflush:
        index_inputs = (candidate.name, candidate.years_experience,
                        json.loads(candidate.tags_json or '[]'), candidate.raw_text)
        raw_text_changed = "raw_text" in data and data["raw_text"] != candidate.raw_text
        if "name" in data:
            candidate.name = data["name"]
        if 'job_category' in data:
            candidate.job_category = data['job_category']
        if "years_experience" in data:
            candidate.years_experience = data["years_experience"] or ""
        if "tags" in data:
            candidate.tags_json = json.dumps(data["tags"] or [], ensure_ascii=False)
        if "raw_text" in data and data["raw_text"]:
            candidate.raw_text = data["raw_text"]
            if raw_text_changed:
                candidate.processed_text = candidate.processing_version = ''
        has_chunks = db.query(ResumeChunk).filter_by(candidate_id=candidate.id).first() is not None
        category_only = 'job_category' in data and index_inputs == (
            candidate.name, candidate.years_experience,
            json.loads(candidate.tags_json or '[]'), candidate.raw_text)
        if candidate.raw_text.strip() and (raw_text_changed or has_chunks) and not category_only:
            if raw_text_changed:
                _build_index(db, candidate)
            else:
                try:
                    refresh_document_index(db, candidate)
                except ModelUnavailableError as exc:
                    db.rollback()
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
            candidate.status = "ready"
            candidate.error_message = None
        elif 'job_category' in data or expected_updated_at is not None:
            touch_document(db, candidate)
    db.commit()
    db.refresh(candidate)
    return CandidateDetail.from_model(candidate)


@router.delete("/candidates/{candidate_id}", status_code=204)
def delete_candidate(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(require_hr)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.deleted_at.is_(None)).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="人才记录不存在")
    candidate.deleted_at = datetime.now(timezone.utc)
    candidate.status = "deleted"
    candidate.file_sha256 = None
    db.execute(text("DELETE FROM resume_chunks_fts WHERE candidate_id = :candidate_id"), {"candidate_id": candidate.id})
    db.query(ResumeChunk).filter_by(candidate_id=candidate.id).delete(synchronize_session=False)
    if candidate.file_path:
        path = Path(candidate.file_path)
        if path.exists() and path.resolve().is_relative_to(Path(settings.upload_dir).resolve()):
            path.unlink()
    candidate.file_path = None
    db.commit()


@router.get("/dashboard/summary", response_model=SummaryOut)
def summary(db: Session = Depends(get_db), user: User = Depends(current_user)):
    base = db.query(Candidate).filter(Candidate.deleted_at.is_(None))
    return SummaryOut(
        total=base.count(),
        ready=base.filter(Candidate.status == "ready").count(),
        processing=base.filter(Candidate.status == "processing").count(),
        failed=base.filter(Candidate.status == "failed").count(),
    )
