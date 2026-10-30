"""Document/chunk mutations. Callers own commit/rollback and authorization."""
from pathlib import Path
import re

from fastapi import HTTPException
from sqlalchemy import bindparam, text, update
from sqlalchemy.orm import Session

from app.models.entities import Candidate, ResumeChunk, utcnow
from app.schemas.candidates import CandidateOut
from app.schemas.knowledge import MAX_CHILD_CHARS, MAX_PARENT_CHARS, ChunkOut, ChunkSettings, DocumentStats, KnowledgeDocument, decode_delimiter as delimiter
from app.services import embeddings


def settings_for(candidate: Candidate) -> ChunkSettings:
    return ChunkSettings(parent_size=candidate.parent_chunk_size, child_size=candidate.child_chunk_size,
                         child_overlap=candidate.child_chunk_overlap, parent_delimiter=candidate.parent_delimiter,
                         child_delimiter=candidate.child_delimiter, replace_consecutive_spaces=candidate.replace_consecutive_spaces,
                         delete_urls_email=candidate.delete_urls_email)


def embedding_text(candidate: Candidate, content: str) -> str:
    return f"{candidate.name}\n{candidate.years_experience}\n{candidate.tags_json}\n{content}"


def split_text(content: str, size: int, overlap: int = 0) -> list[str]:
    """Overflow fallback only: prefer line/sentence boundaries, then characters."""
    parts, start = [], 0
    while start < len(content):
        end = min(start + size, len(content))
        if end < len(content):
            for separator in ('\n\n', '\n', '。', '！', '？', '. ', '; ', ' '):
                boundary = content.rfind(separator, start + max(size // 2, overlap + 1), end)
                if boundary >= 0:
                    end = boundary + len(separator)
                    break
        parts.append(content[start:end])
        if end == len(content):
            break
        start = end - overlap
    return parts


def separator_text(value: str) -> str:
    return value if '\n' in value else f'\n{value}\n'


def preview_chunks(content: str, config: ChunkSettings) -> dict:
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    if config.replace_consecutive_spaces:
        content = re.sub(r'[ \t\u3000]{2,}', ' ', re.sub(r'\n{3,}', '\n\n', content))
    if config.delete_urls_email:
        content = re.sub(r'https?://[^\s<>，。；）)]+|[a-zA-Z0-9_.+!-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+', '', content)
    parents = []
    child_separator = delimiter(config.child_delimiter)
    joiner = separator_text(child_separator)
    for block in content.split(delimiter(config.parent_delimiter)):
        units = [part.strip() for part in block.split(child_separator) if part.strip()]
        # Keep every complete child together when a parent exceeds its upper bound.
        groups, current = [], []
        for unit in units:
            for piece in split_text(unit, config.parent_size):
                if current and len(joiner.join([*current, piece])) > config.parent_size:
                    groups.append(current); current = []
                current.append(piece)
        if current:
            groups.append(current)
        for group in groups:
            # Dify keeps the child markers in the parent; only children are indexed.
            # This also keeps boundaries intact when the parent is edited later.
            part = joiner.join(group)
            children = []
            for unit in group:
                for child in split_text(unit, config.child_size, config.child_overlap):
                    children.append({'position': len(children), 'content': child, 'char_count': len(child)})
            parents.append({'position': len(parents), 'content': part, 'char_count': len(part), 'children': children})
    return {"parents": parents, "parent_count": len(parents),
            "child_count": sum(len(parent["children"]) for parent in parents)}


def touch_document(db: Session, candidate: Candidate) -> None:
    """Reject stale edits and invalidate embedding jobs that captured an older version."""
    version = candidate.updated_at
    now = utcnow()
    with db.no_autoflush:
        result = db.execute(update(Candidate).where(
            Candidate.id == candidate.id, Candidate.updated_at == version, Candidate.deleted_at.is_(None),
        ).values(updated_at=now).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="文档已被其他操作更新，请刷新后重试")
    candidate.updated_at = now


def _prepared_index(candidate: Candidate, children: list[ResumeChunk], progress=None):
    # Lazy import avoids a cycle with resume.create_chunks, the legacy entry point.
    from app.services.resume import tokenize
    texts = [embedding_text(candidate, child.content) for child in children]
    keywords = [" ".join(tokenize(value)) for value in texts]
    if progress:
        progress("keyword", 75, "关键词索引已准备")
        progress("embedding", 80, "正在建立向量索引")
    vectors = embeddings.embed_texts(texts) if embeddings.enabled() and children else [None] * len(children)
    if len(vectors) != len(children):
        from app.services.llm import ModelUnavailableError
        raise ModelUnavailableError("嵌入模型返回数量不完整，请重试")
    return keywords, vectors


def _write_index(db: Session, candidate: Candidate, children: list[ResumeChunk], keywords, vectors):
    db.flush()
    for child, tokens, vector in zip(children, keywords, vectors, strict=True):
        db.execute(text("DELETE FROM resume_chunks_fts WHERE chunk_id=:id"), {"id": child.id})
        db.execute(text("INSERT INTO resume_chunks_fts(chunk_id,candidate_id,content) VALUES(:id,:candidate,:content)"),
                   {"id": child.id, "candidate": candidate.id, "content": tokens})
        child.embedding_json = child.embedding_model = child.embedding_dimensions = None
        if vector is not None:
            embeddings.assign(child, vector)


def _remove_chunks(db: Session, chunks: list[ResumeChunk]):
    ids = [chunk.id for chunk in chunks]
    if not ids:
        return
    db.execute(text("DELETE FROM resume_chunks_fts WHERE chunk_id IN :ids").bindparams(bindparam("ids", expanding=True)), {"ids": ids})
    # Remove children first for databases with foreign-key enforcement enabled.
    for parent_group in (False, True):
        for chunk in chunks:
            if (chunk.chunk_type == "parent") == parent_group:
                db.delete(chunk)
        db.flush()


def rebuild_chunks(db: Session, candidate: Candidate, progress=None) -> None:
    config = settings_for(candidate)
    source = candidate.processed_text or candidate.raw_text
    planned = preview_chunks(source, config)
    if progress:
        progress("chunk", 60, "正文分段已准备")
    parents, children = [], []
    for part in planned["parents"]:
        parent = ResumeChunk(candidate_id=candidate.id, chunk_type="parent", content=part["content"], position=part["position"])
        parents.append(parent)
        children.append([ResumeChunk(candidate_id=candidate.id, chunk_type="child", content=child["content"], position=child["position"])
                         for child in part["children"]])
    flat = [child for group in children for child in group]
    keywords, vectors = _prepared_index(candidate, flat, progress)
    # Old content stays committed while the model works. The conditional update prevents
    # a late rebuild from overwriting edits or document deletion during that request.
    touch_document(db, candidate)
    _remove_chunks(db, db.query(ResumeChunk).filter_by(candidate_id=candidate.id).all())
    for parent, group in zip(parents, children, strict=True):
        db.add(parent)
        db.flush()
        for child in group:
            child.parent_id = parent.id
            db.add(child)
    _write_index(db, candidate, flat, keywords, vectors)
    candidate.parent_chunk_size = config.parent_size
    candidate.child_chunk_size = config.child_size
    candidate.child_chunk_overlap = config.child_overlap
    candidate.parent_delimiter = config.parent_delimiter
    candidate.child_delimiter = config.child_delimiter
    candidate.replace_consecutive_spaces = config.replace_consecutive_spaces
    candidate.delete_urls_email = config.delete_urls_email
    if progress:
        progress("embedding", 95, "向量索引已完成" if embeddings.enabled() else "离线模式：关键词索引已完成")


def refresh_document_index(db: Session, candidate: Candidate, progress=None) -> None:
    children = db.query(ResumeChunk).filter_by(candidate_id=candidate.id, chunk_type="child").order_by(ResumeChunk.id).all()
    keywords, vectors = _prepared_index(candidate, children, progress)
    touch_document(db, candidate)
    _write_index(db, candidate, children, keywords, vectors)


def validate_chunk_length(content: str, child: bool) -> None:
    limit = MAX_CHILD_CHARS if child else MAX_PARENT_CHARS
    if len(content) > limit:
        raise HTTPException(status_code=422, detail=f"{'子' if child else '父'}分段最多{limit}字符")


def create_chunk(db: Session, candidate: Candidate, content: str, parent: ResumeChunk | None) -> ResumeChunk:
    validate_chunk_length(content, child=parent is not None)
    scope = db.query(ResumeChunk).filter_by(candidate_id=candidate.id, parent_id=parent.id if parent else None)
    position = max((chunk.position for chunk in scope.all()), default=-1) + 1
    row = ResumeChunk(candidate_id=candidate.id, parent_id=parent.id if parent else None,
                      chunk_type="child" if parent else "parent", content=content, position=position)
    if parent:
        children = [row]
    else:
        config = settings_for(candidate)
        planned = preview_chunks(content, config)
        children = [ResumeChunk(candidate_id=candidate.id, parent_id=row.id, chunk_type="child", content=part['content'], position=index)
                    for index, part in enumerate(child for parent in planned['parents'] for child in parent['children'])]
    keywords, vectors = _prepared_index(candidate, children)
    touch_document(db, candidate)
    db.add(row)
    db.flush()
    for child in children:
        if not parent:
            child.parent_id = row.id
        db.add(child)
    _write_index(db, candidate, children, keywords, vectors)
    return row


def update_chunk(db: Session, candidate: Candidate, chunk: ResumeChunk, changes: dict) -> None:
    if "content" in changes:
        validate_chunk_length(changes["content"], child=chunk.chunk_type == "child")
    if "content" not in changes or changes["content"] == chunk.content:
        touch_document(db, candidate)
        if "enabled" in changes:
            chunk.enabled = changes["enabled"]
        return
    if chunk.chunk_type == "parent":
        old = db.query(ResumeChunk).filter_by(candidate_id=candidate.id, parent_id=chunk.id).all()
        config = settings_for(candidate)
        planned = preview_chunks(changes['content'], config)
        children = [ResumeChunk(candidate_id=candidate.id, parent_id=chunk.id, chunk_type="child", content=part['content'], position=index)
                    for index, part in enumerate(child for parent in planned['parents'] for child in parent['children'])]
    else:
        children = [ResumeChunk(content=changes["content"])]
    keywords, vectors = _prepared_index(candidate, children)
    touch_document(db, candidate)
    chunk.content = changes["content"]
    chunk.updated_at = utcnow()
    if "enabled" in changes:
        chunk.enabled = changes["enabled"]
    if chunk.chunk_type == "parent":
        _remove_chunks(db, old)
        db.add_all(children)
    else:
        children = [chunk]
    _write_index(db, candidate, children, keywords, vectors)


def delete_chunks(db: Session, candidate: Candidate, chunks: list[ResumeChunk]) -> None:
    touch_document(db, candidate)
    parent_ids = [chunk.id for chunk in chunks if chunk.chunk_type == "parent"]
    rows = {chunk.id: chunk for chunk in chunks}
    if parent_ids:
        rows.update({chunk.id: chunk for chunk in db.query(ResumeChunk).filter(
            ResumeChunk.candidate_id == candidate.id, ResumeChunk.parent_id.in_(parent_ids)).all()})
    _remove_chunks(db, list(rows.values()))


def effective_enabled(candidate: Candidate, chunk: ResumeChunk, parents: dict[int, ResumeChunk]) -> bool:
    if not (candidate.enabled and candidate.status == "ready" and candidate.deleted_at is None and chunk.enabled):
        return False
    if chunk.chunk_type == "child":
        parent = parents.get(chunk.parent_id)
        return bool(parent and parent.enabled)
    return True


def chunk_output(candidate: Candidate, chunk: ResumeChunk, all_chunks: list[ResumeChunk]) -> ChunkOut:
    parents = {row.id: row for row in all_chunks if row.chunk_type == "parent"}
    children = [row for row in all_chunks if row.parent_id == chunk.id and row.chunk_type == "child"]
    return ChunkOut(id=chunk.id, candidate_id=chunk.candidate_id, parent_id=chunk.parent_id,
                    chunk_type=chunk.chunk_type, position=chunk.position, content=chunk.content,
                    enabled=chunk.enabled, effective_enabled=effective_enabled(candidate, chunk, parents),
                    char_count=len(chunk.content), hit_count=chunk.hit_count,
                    child_count=len(children), enabled_child_count=sum(effective_enabled(candidate, row, parents) for row in children),
                    indexed=all(embeddings.current(row) for row in children) if children else (embeddings.current(chunk) if chunk.chunk_type == "child" else False),
                    created_at=chunk.created_at, updated_at=chunk.updated_at)


def document_stats(candidate: Candidate, chunks: list[ResumeChunk]) -> DocumentStats:
    parents = {row.id: row for row in chunks if row.chunk_type == "parent"}
    children = [row for row in chunks if row.chunk_type == "child"]
    active = [row for row in children if effective_enabled(candidate, row, parents)]
    return DocumentStats(parent_chunks=len(parents), child_chunks=len(children), enabled_child_chunks=len(active),
                         indexed_child_chunks=sum(embeddings.current(row) for row in active),
                         character_count=len(candidate.raw_text), hit_count=sum(row.hit_count for row in parents.values()))


def document_output(db: Session, candidate: Candidate) -> KnowledgeDocument:
    chunks = db.query(ResumeChunk).filter_by(candidate_id=candidate.id).all()
    size = None
    if candidate.file_path:
        try:
            size = Path(candidate.file_path).stat().st_size
        except OSError:
            pass
    return KnowledgeDocument(candidate=CandidateOut.from_model(candidate), stats=document_stats(candidate, chunks), source_file_size=size)
