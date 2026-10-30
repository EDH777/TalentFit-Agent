from time import perf_counter

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.knowledge import _document
from app.db.session import get_db
from app.models import Candidate, User
from app.services.retrieval import context_chunks, evidence_text, retrieve_chunks

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=20)
    candidate_id: int | None = Field(default=None, gt=0)


@router.post("/retrieval-test")
def retrieval_test(payload: RetrievalQuery, db: Session = Depends(get_db), user: User = Depends(current_user)):
    started = perf_counter()
    if payload.candidate_id is not None:
        _document(db, payload.candidate_id)
    hits, info = retrieve_chunks(db, payload.query, payload.limit, candidate_id=payload.candidate_id)
    output = []
    for hit in hits:
        chunk = hit["chunk"]
        person = db.get(Candidate, chunk.candidate_id)
        parent_content, _ = evidence_text([chunk, *context_chunks(db, chunk.parent_id)])
        output.append({"chunk_id": chunk.id, "parent_id": chunk.parent_id, "candidate_id": person.id,
                       "name": person.name, "position": chunk.position, "content": chunk.content,
                       "parent_content": parent_content, "score": hit["score"],
                       "keyword_rank": hit["keyword_rank"], "vector_rank": hit["vector_rank"]})
    return {**info, "hits": output, "elapsed_ms": round((perf_counter() - started) * 1000, 2)}
