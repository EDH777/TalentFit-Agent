"""Qwen embeddings and small-library vector search. No extra database service."""
import json

import numpy as np
from openai import APIError, OpenAI
from sqlalchemy.orm import Session, aliased
from sqlalchemy import exists, select, update

from app.core.config import settings
from app.models import Candidate, ResumeChunk
from app.services.llm import ModelUnavailableError
from app.services.events import StreamCancelled


def enabled() -> bool:
    return settings.embedding_enabled and bool(settings.embedding_api_key or settings.openai_api_key)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not enabled():
        raise ModelUnavailableError("嵌入模型未配置，当前使用关键词检索。")
    output = []
    try:
        with OpenAI(api_key=settings.embedding_api_key or settings.openai_api_key,
                    base_url=settings.embedding_base_url or settings.openai_base_url,
                    timeout=settings.llm_timeout_seconds, max_retries=1) as client:
            for start in range(0, len(texts), 10):
                batch = texts[start:start + 10]
                response = client.embeddings.create(model=settings.embedding_model, input=batch,
                                                     dimensions=settings.embedding_dimensions, encoding_format="float")
                data = sorted(response.data, key=lambda item: item.index)
                if [item.index for item in data] != list(range(len(batch))):
                    raise ValueError("missing embedding")
                for item in data:
                    vector = np.asarray(item.embedding, dtype=float)
                    if vector.shape != (settings.embedding_dimensions,) or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
                        raise ValueError("invalid embedding")
                    output.append(vector.tolist())
    except (APIError, ValueError, TypeError) as exc:
        raise ModelUnavailableError("嵌入模型调用失败，请稍后重试或检查模型配置。") from exc
    return output


def assign(chunk: ResumeChunk, vector: list[float]) -> None:
    chunk.embedding_json = json.dumps(vector)
    chunk.embedding_model = settings.embedding_model
    chunk.embedding_dimensions = settings.embedding_dimensions


def active_chunks(db: Session):
    parent = aliased(ResumeChunk)
    return db.query(ResumeChunk).join(Candidate).join(parent, ResumeChunk.parent_id == parent.id).filter(
        ResumeChunk.chunk_type == "child", ResumeChunk.enabled.is_(True), Candidate.enabled.is_(True),
        Candidate.deleted_at.is_(None), Candidate.status == "ready", parent.chunk_type == "parent",
        parent.candidate_id == ResumeChunk.candidate_id, parent.enabled.is_(True),
    )


def current(chunk: ResumeChunk) -> bool:
    if not chunk.embedding_json or chunk.embedding_model != settings.embedding_model or chunk.embedding_dimensions != settings.embedding_dimensions:
        return False
    try:
        vector = np.asarray(json.loads(chunk.embedding_json), dtype=float)
        return vector.shape == (settings.embedding_dimensions,) and bool(np.isfinite(vector).all()) and bool(np.linalg.norm(vector) > 0)
    except (ValueError, TypeError):
        return False


def index_status(db: Session, candidate_ids=None, candidate_id=None, names=None) -> dict:
    query = active_chunks(db)
    if candidate_ids is not None:
        query = query.filter(Candidate.id.in_(candidate_ids))
    elif candidate_id is not None:
        query = query.filter(Candidate.id == candidate_id)
    elif names:
        query = query.filter(Candidate.name.in_(names))
    chunks = query.all()
    indexed = sum(current(chunk) for chunk in chunks)
    return {"mode": "hybrid" if enabled() and indexed and indexed == len(chunks) else "keyword",
            "embedding_model": settings.embedding_model if enabled() else None,
            "dimensions": settings.embedding_dimensions,
            "indexed_chunks": indexed, "total_chunks": len(chunks), "pending_chunks": len(chunks) - indexed}


def reindex_missing(db: Session, cancelled=None):
    from app.services.knowledge import embedding_text
    if not enabled():
        raise ModelUnavailableError("请先配置嵌入模型服务。")
    pending = [chunk for chunk in active_chunks(db).order_by(ResumeChunk.id).all() if not current(chunk)]
    total = len(pending)
    yield {"type": "index_progress", "completed": 0, "total": total}
    for start in range(0, total, 10):
        batch = pending[start:start + 10]
        contents = []
        snapshots = []
        for chunk in batch:
            db.refresh(chunk)
            person = db.get(Candidate, chunk.candidate_id)
            db.refresh(person)
            snapshots.append((chunk.id, chunk.candidate_id, chunk.content, person.updated_at))
            contents.append(embedding_text(person, chunk.content))
        if cancelled and cancelled():
            raise StreamCancelled()
        vectors = embed_texts(contents)
        if cancelled and cancelled():
            raise StreamCancelled()
        for (identity, candidate_id, content, version), vector in zip(snapshots, vectors, strict=True):
            # IDs may be reused after an edit. Do not overwrite a newer content/version.
            unchanged_candidate = exists(select(Candidate.id).where(
                Candidate.id == candidate_id, Candidate.updated_at == version,
                Candidate.deleted_at.is_(None), Candidate.status == "ready", Candidate.enabled.is_(True),
            ))
            db.execute(update(ResumeChunk).where(
                ResumeChunk.id == identity, ResumeChunk.candidate_id == candidate_id,
                ResumeChunk.content == content, ResumeChunk.enabled.is_(True), unchanged_candidate,
            ).values(embedding_json=json.dumps(vector), embedding_model=settings.embedding_model,
                     embedding_dimensions=settings.embedding_dimensions).execution_options(synchronize_session=False))
        if cancelled and cancelled():
            raise StreamCancelled()
        db.commit()
        db.expire_all()
        yield {"type": "index_progress", "completed": min(start + 10, total), "total": total}
    status = index_status(db)
    yield {"type": "index_done", "indexed_chunks": status["indexed_chunks"], "total_chunks": status["total_chunks"]}


def vector_ranking(db: Session, query: str, names: list[str] | None = None, usage: dict | None = None,
                   candidate_id: int | None = None, candidate_ids: list[int] | None = None) -> list[int]:
    chunks = active_chunks(db)
    if names:
        chunks = chunks.filter(Candidate.name.in_(names))
    if candidate_id is not None:
        chunks = chunks.filter(Candidate.id == candidate_id)
    if candidate_ids is not None:
        chunks = chunks.filter(Candidate.id.in_(candidate_ids))
    valid = [chunk for chunk in chunks.all() if current(chunk)]
    if not valid:
        return []
    if usage is not None:
        usage["attempted"] = True
    vector = np.asarray(embed_texts([query])[0])
    # Model calls may outlive a document edit or disable operation.
    eligible = {chunk.id for chunk in active_chunks(db).populate_existing().all() if current(chunk)}
    valid = [chunk for chunk in valid if chunk.id in eligible]
    if not valid:
        return []
    matrix = np.asarray([json.loads(chunk.embedding_json) for chunk in valid])
    similarities = matrix @ vector / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector))
    ranked = sorted(zip(valid, similarities, strict=True), key=lambda item: item[1], reverse=True)
    return [chunk.id for chunk, score in ranked[:80] if score >= 0.2]
