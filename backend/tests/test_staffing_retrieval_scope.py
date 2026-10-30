"""Staffing scopes are applied before either keyword/vector ranking truncates."""
import pytest
from sqlalchemy import text

from app.core.config import settings
from app.models import Candidate, ResumeChunk
from app.services import embeddings, retrieval, resume


def scoped_library(db, count):
    ids = []
    for index in range(count):
        person = Candidate(name=f'合成员工{index}', created_by=2)
        db.add(person)
        db.flush()
        parent = ResumeChunk(candidate_id=person.id, chunk_type='parent', content='Python')
        db.add(parent)
        db.flush()
        child = ResumeChunk(candidate_id=person.id, parent_id=parent.id, content='Python', chunk_type='child',
                            embedding_json='[1,0]', embedding_model=settings.embedding_model,
                            embedding_dimensions=2)
        db.add(child)
        db.flush()
        db.execute(text('INSERT INTO resume_chunks_fts(chunk_id,candidate_id,content) VALUES (:ch,:ca,:t)'),
                   {'ch': child.id, 'ca': person.id, 't': ' '.join(resume.tokenize('Python'))})
        ids.append((person.id, child.id))
    db.commit()
    return ids


@pytest.mark.parametrize('vector', [False, True])
def test_scope_precedes_ranking_limit(db, monkeypatch, vector):
    ids = scoped_library(db, 165)
    monkeypatch.setattr(settings, 'embedding_dimensions', 2)
    monkeypatch.setattr(embeddings, 'enabled', lambda: vector)
    monkeypatch.setattr(embeddings, 'embed_texts', lambda _: [[1., 0.]])
    selected = ids[-1]
    hits, info = retrieval.retrieve_chunks(db, 'Python', candidate_ids=[selected[0]])
    assert [hit['chunk'].id for hit in hits] == [selected[1]]
    assert info['mode'] == ('hybrid' if vector else 'keyword')
    assert retrieval.retrieve_chunks(db, 'Python', candidate_ids=[])[0] == []


def test_scope_vector_status_is_local_and_effective(db, monkeypatch):
    ids = scoped_library(db, 2)
    monkeypatch.setattr(settings, 'embedding_dimensions', 2)
    monkeypatch.setattr(embeddings, 'enabled', lambda: True)
    db.get(ResumeChunk, ids[0][1]).embedding_json = None
    db.commit()
    monkeypatch.setattr(embeddings, 'embed_texts', lambda _: [[1., 0.]])
    hits, info = retrieval.retrieve_chunks(db, 'Python', candidate_ids=[ids[1][0]])
    assert hits and not info['warning']
    db.get(Candidate, ids[1][0]).enabled = False
    db.commit()
    assert retrieval.retrieve_chunks(db, 'Python', candidate_ids=[ids[1][0]])[0] == []
