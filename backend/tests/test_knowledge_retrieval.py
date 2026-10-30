import json

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.models import Candidate, ResumeChunk
from app.services import embeddings, llm, resume


def document(db, name='合成人才', contents=None, tags=None):
    contents = contents or ['safeSkill 服务端开发经验。', 'forbiddenSkillZulu 项目保密部分。']
    person = Candidate(name=name, raw_text='\n'.join(contents), tags_json=json.dumps(tags or []), created_by=2)
    db.add(person)
    db.flush()
    parent = ResumeChunk(candidate_id=person.id, chunk_type='parent', content=person.raw_text)
    db.add(parent)
    db.flush()
    children = []
    for position, content in enumerate(contents, 1):
        child = ResumeChunk(candidate_id=person.id, parent_id=parent.id, chunk_type='child', content=content)
        child.position = position
        db.add(child)
        db.flush()
        db.execute(text('INSERT INTO resume_chunks_fts(chunk_id,candidate_id,content) VALUES (:id,:candidate,:content)'),
                   {'id': child.id, 'candidate': person.id, 'content': ' '.join(resume.tokenize(person.name + ' ' + person.tags_json + ' ' + content))})
        children.append(child)
    db.commit()
    return person, parent, children


def vectors(db, monkeypatch):
    monkeypatch.setattr(embeddings, 'enabled', lambda: True)
    monkeypatch.setattr(settings, 'embedding_dimensions', 2)
    monkeypatch.setattr(embeddings, 'embed_texts', lambda texts: [[1.0, 0.0] for _ in texts])
    for child in db.query(ResumeChunk).filter_by(chunk_type='child'):
        embeddings.assign(child, [1.0, 0.0])
    db.commit()


def test_document_disable_excludes_keyword_vector_and_named_fallback(db, monkeypatch):
    person, _, _ = document(db)
    vectors(db, monkeypatch)
    person.enabled = False
    db.commit()
    assert resume.search_candidates(db, 'safeSkill') == []
    assert resume.search_candidates(db, '无关键词语义查询') == []
    assert resume.search_candidates(db, '分析', names=[person.name]) == []
    person.enabled = True
    db.commit()
    assert resume.search_candidates(db, 'safeSkill')[0]['candidate_id'] == person.id


def test_parent_disable_masks_children_without_changing_child_flags(db, monkeypatch):
    person, parent, children = document(db)
    vectors(db, monkeypatch)
    parent.enabled = False
    db.commit()
    assert resume.search_candidates(db, 'safeSkill') == []
    assert resume.search_candidates(db, '画像', names=[person.name]) == []
    assert embeddings.index_status(db)['total_chunks'] == 0
    parent.enabled = True
    db.commit()
    assert all(child.enabled for child in children)
    assert embeddings.index_status(db)['total_chunks'] == 2


def test_disabled_child_never_returns_through_parent_raw_or_profile_tags(db):
    person, _, children = document(db, tags=['forbiddenSkillZulu'])
    children[1].enabled = False
    db.commit()
    found = resume.search_candidates(db, 'forbiddenSkillZulu', names=[person.name])
    assert found and 'safeSkill' in found[0]['evidence']
    assert 'forbiddenskillzulu' not in json.dumps(found).lower()


def test_child_edit_or_deletion_never_restores_stale_parent_content(db):
    person, _, children = document(db)
    children[1].content = 'newSkill 更新后的有效经验。'
    db.commit()
    found = resume.search_candidates(db, '分析', names=[person.name])
    assert 'newSkill' in found[0]['evidence']
    assert 'forbiddenSkillZulu' not in found[0]['evidence']
    db.delete(children[1])
    db.commit()
    found = resume.search_candidates(db, '分析', names=[person.name])
    assert 'forbiddenSkillZulu' not in found[0]['evidence'] and 'newSkill' not in found[0]['evidence']


def test_disabling_all_children_removes_person_from_named_lookup(db):
    person, _, children = document(db)
    for child in children:
        child.enabled = False
    db.commit()
    assert resume.search_candidates(db, '分析', names=[person.name]) == []


def test_disable_during_query_embedding_is_rechecked_before_evidence(db, monkeypatch):
    person, _, _ = document(db)
    vectors(db, monkeypatch)
    def embedding(texts):
        person.enabled = False
        db.commit()
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(embeddings, 'embed_texts', embedding)
    assert resume.search_candidates(db, 'safeSkill') == []


def test_late_hit_is_preserved_inside_bounded_parent_context(db):
    person, _, children = document(db, contents=['早期经历。' * 1500, 'lateUniqueToken 最后一个项目的关键经验。'])
    found = resume.search_candidates(db, 'lateUniqueToken')
    assert 'lateUniqueToken' in found[0]['evidence']
    assert len(found[0]['evidence']) <= 4000


def test_index_coverage_excludes_disabled_layers(db, monkeypatch):
    person, _, children = document(db)
    vectors(db, monkeypatch)
    children[1].enabled = False
    db.commit()
    status = embeddings.index_status(db)
    assert status['total_chunks'] == status['indexed_chunks'] == 1
    assert len(embeddings.vector_ranking(db, '查询')) == 1


def test_retrieval_test_shares_filtering_but_does_not_increment_hits(client, hr, admin, db):
    person, parent, children = document(db)
    children[1].enabled = False
    db.commit()
    for headers in [hr, admin]:
        response = client.post('/api/knowledge/retrieval-test', headers=headers,
                               json={'query': 'safeSkill', 'limit': 5, 'candidate_id': person.id})
        assert response.status_code == 200
        result = response.json()
        assert result['mode'] == 'keyword' and result['elapsed_ms'] >= 0
        assert len(result['hits']) == 1
        hit = result['hits'][0]
        assert hit['chunk_id'] == children[0].id and hit['parent_id'] == parent.id
        assert hit['score'] > 0 and hit['keyword_rank'] == 1
        assert 'forbiddenSkillZulu' not in hit['parent_content']
        assert 'embedding_json' not in hit
    assert all(child.hit_count == 0 for child in children)
    assert parent.hit_count == 0
    assert client.post('/api/knowledge/retrieval-test', json={'query': 'safeSkill'}).status_code == 401


@pytest.mark.parametrize('body', [{'query': ''}, {'query': 'a', 'limit': 0}, {'query': 'a', 'limit': 21}])
def test_retrieval_test_validates_inputs(client, hr, body):
    assert client.post('/api/knowledge/retrieval-test', headers=hr, json=body).status_code == 422


def test_completed_agent_increments_only_effective_context_hits(client, hr, db, monkeypatch):
    person, parent, children = document(db)
    children[1].enabled = False
    db.commit()
    monkeypatch.setattr(llm, 'structured', lambda _: {'intent': 'person_profile', 'query': 'safeSkill', 'names': [person.name]})
    monkeypatch.setattr(llm, 'complete', lambda messages: '依据合成简历的有效经验给出分析。')
    session = client.post('/api/chat/sessions', headers=hr, json={}).json()['id']
    result = client.post(f'/api/chat/sessions/{session}/messages', headers=hr, json={'content': '分析合成人才'})
    assert result.status_code == 200
    db.expire_all()
    assert db.get(ResumeChunk, children[0].id).hit_count == 1
    assert db.get(ResumeChunk, children[1].id).hit_count == 0
    assert db.get(ResumeChunk, parent.id).hit_count == 1


@pytest.mark.parametrize('scope', ['candidate_id', 'names'])
def test_keyword_scope_is_applied_before_global_result_limit(db, scope):
    from app.services.retrieval import retrieve_chunks
    document(db, name='干扰文档', contents=['scopedUniqueSkill'] * 161)
    wanted, _, children = document(db, name='目标文档', contents=['scopedUniqueSkill'])
    arguments = {'candidate_id': wanted.id} if scope == 'candidate_id' else {'names': [wanted.name]}
    hits, _ = retrieve_chunks(db, 'scopedUniqueSkill', **arguments)
    assert [hit['chunk'].id for hit in hits] == [children[0].id]


def test_hit_count_does_not_follow_reused_chunk_id_after_generation(db):
    from app.services.retrieval import record_hits
    person, parent, children = document(db, contents=['originalSkill'])
    sources = resume.search_candidates(db, 'originalSkill')
    identity = children[0].id
    db.delete(children[0])
    db.flush()
    replacement = ResumeChunk(candidate_id=person.id, parent_id=parent.id, chunk_type='child', content='newSkill')
    db.add(replacement)
    db.commit()
    assert replacement.id == identity
    record_hits(db, sources)
    db.commit()
    db.refresh(replacement)
    assert replacement.hit_count == 0
