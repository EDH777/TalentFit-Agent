"""Shared chunk retrieval for Agent evidence and the knowledge-base recall test."""
import hashlib
from sqlalchemy import bindparam, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Candidate, ResumeChunk
from app.services import embeddings
from app.services.llm import ModelUnavailableError


def retrieve_chunks(db: Session, query: str, limit: int = 80, names=None, candidate_id=None, candidate_ids=None):
    from app.services.resume import tokenize
    tokens = tokenize(query)[:40]
    keyword = []
    if tokens:
        match = " OR ".join('"' + word.replace('"', '') + '"' for word in tokens)
        scope, parameters = "", {"query": match}
        if candidate_id is not None:
            scope += " AND c.id=:candidate_id"
            parameters["candidate_id"] = candidate_id
        if names:
            scope += " AND c.name IN :names"
            parameters["names"] = names
        if candidate_ids is not None:
            scope += " AND c.id IN :candidate_ids"
            parameters["candidate_ids"] = candidate_ids
        statement = text("""SELECT ch.id FROM resume_chunks_fts f
            JOIN resume_chunks ch ON ch.id=f.chunk_id AND ch.candidate_id=f.candidate_id
            JOIN candidates c ON c.id=ch.candidate_id
            JOIN resume_chunks p ON p.id=ch.parent_id AND p.candidate_id=c.id
            WHERE resume_chunks_fts MATCH :query AND c.deleted_at IS NULL AND c.status='ready'
              AND c.enabled=1 AND ch.enabled=1 AND p.enabled=1
              AND ch.chunk_type='child' AND p.chunk_type='parent'
            """ + scope + " ORDER BY bm25(resume_chunks_fts), ch.id LIMIT 160")
        if names:
            statement = statement.bindparams(bindparam("names", expanding=True))
        if candidate_ids is not None:
            statement = statement.bindparams(bindparam("candidate_ids", expanding=True))
        rows = db.execute(statement, parameters).scalars().all()
        keyword = list(rows)
    info = {"mode": "keyword", "embedding_model": None, "warning": None}
    vector = []
    scope = {}
    if candidate_ids is not None:
        scope["candidate_ids"] = candidate_ids
    if names:
        scope["names"] = names
    if candidate_id is not None:
        scope["candidate_id"] = candidate_id
    if embeddings.enabled():
        try:
            usage = {}
            vector = embeddings.vector_ranking(db, query, usage=usage, **scope)
            if usage.get("attempted"):
                info.update(mode="hybrid", embedding_model=settings.embedding_model)
            if embeddings.index_status(db, **scope)["pending_chunks"]:
                info["warning"] = "部分启用分段尚未建立向量索引，请由人事补建。"
        except ModelUnavailableError:
            info["warning"] = "向量检索暂时不可用，已降级为关键词检索。"
    # Recheck after the provider returns, including changes to cached ORM objects.
    eligible = embeddings.active_chunks(db).populate_existing()
    if names:
        eligible = eligible.filter(Candidate.name.in_(names))
    if candidate_id is not None:
        eligible = eligible.filter(Candidate.id == candidate_id)
    if candidate_ids is not None:
        eligible = eligible.filter(Candidate.id.in_(candidate_ids))
    active = {chunk.id: chunk for chunk in eligible.all()}
    keyword = [identity for identity in keyword if identity in active]
    vector = [identity for identity in vector if identity in active]
    scores, keyword_ranks, vector_ranks = {}, {}, {}
    for ranking, ranks in ((keyword, keyword_ranks), (vector, vector_ranks)):
        for rank, identity in enumerate(ranking, 1):
            scores[identity] = scores.get(identity, 0.0) + 1 / (60 + rank)
            ranks[identity] = rank
    hits = [{"chunk": active[identity], "score": scores[identity],
             "keyword_rank": keyword_ranks.get(identity), "vector_rank": vector_ranks.get(identity)}
            for identity in sorted(scores, key=scores.get, reverse=True)[:limit]]
    return hits, info


def context_chunks(db: Session, parent_id: int):
    return embeddings.active_chunks(db).filter(ResumeChunk.parent_id == parent_id).populate_existing().order_by(
        ResumeChunk.position, ResumeChunk.id).all()


def evidence_text(chunks: list[ResumeChunk], limit=4000) -> tuple[str, list[int]]:
    """Preserve ranked hits first; append only effective child content, never raw parents."""
    parts, included, remaining = [], [], limit
    for chunk in chunks:
        if chunk.id in included or remaining <= 1:
            continue
        value = chunk.content[:remaining - (1 if parts else 0)]
        if value:
            parts.append(value)
            included.append(chunk.id)
            remaining -= len(value) + (1 if len(parts) > 1 else 0)
    return "\n".join(parts), included


def record_hits(db: Session, sources: list[dict]):
    snapshots = {item["id"]: item for source in sources for item in source.get("_chunks", [])}
    ids = set(snapshots)
    if not ids:
        return
    chunks = embeddings.active_chunks(db).filter(ResumeChunk.id.in_(ids)).populate_existing().all()
    chunks = [row for row in chunks if chunk_snapshot(row) == snapshots[row.id]]
    parents = set()
    for row in chunks:
        # Check identity/content again when acquiring the write lock, not only in Python.
        result = db.execute(update(ResumeChunk).where(
            ResumeChunk.id == row.id, ResumeChunk.candidate_id == row.candidate_id,
            ResumeChunk.parent_id == row.parent_id, ResumeChunk.created_at == row.created_at,
            ResumeChunk.content == row.content,
            ResumeChunk.id.in_(embeddings.active_chunks(db).with_entities(ResumeChunk.id).statement),
        ).values(hit_count=ResumeChunk.hit_count + 1).execution_options(synchronize_session=False))
        if result.rowcount:
            parents.add(row.parent_id)
    if parents:
        # The caller commits counters with the successful reply, after generation.
        db.execute(update(ResumeChunk).where(ResumeChunk.id.in_(parents)).values(
            hit_count=ResumeChunk.hit_count + 1).execution_options(synchronize_session=False))


def chunk_snapshot(chunk: ResumeChunk):
    return {"id": chunk.id, "candidate_id": chunk.candidate_id, "parent_id": chunk.parent_id,
            "created_at": chunk.created_at.replace(tzinfo=None).isoformat(),
            "content_sha256": hashlib.sha256(chunk.content.encode()).hexdigest()}
