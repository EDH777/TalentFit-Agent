"""User-facing document management must not expose ingestion internals."""
import pytest

from app.models import Candidate, ResumeChunk


@pytest.fixture
def uploaded(db):
    row = Candidate(name='合成人才', source_filename='synthetic.pdf', raw_text='内部提取原文',
                    processed_text='内部处理文本', processing_version='resume-pipeline-v1', created_by=2)
    db.add(row); db.commit()
    return row


@pytest.mark.parametrize('suffix', ['preview', 'rebuild/stream', 'reprocess/stream'])
def test_processing_control_routes_are_removed(client, hr, uploaded, suffix):
    response = client.post(f'/api/knowledge/documents/{uploaded.id}/{suffix}', headers=hr, json={})
    assert response.status_code == 404


def test_document_detail_excludes_raw_processed_text_and_settings(client, hr, uploaded):
    data = client.get(f'/api/knowledge/documents/{uploaded.id}', headers=hr).json()
    assert 'settings' not in data
    assert not {'raw_text', 'processed_text', 'processing_version'} & data['candidate'].keys()
    assert data['candidate']['name'] == uploaded.name


def test_uploaded_text_is_not_exposed_or_editable(client, hr, uploaded, db):
    data = client.get(f'/api/candidates/{uploaded.id}', headers=hr).json()
    assert not data.get('raw_text')
    assert 'processed_text' not in data and 'processing_version' not in data
    response = client.put(f'/api/candidates/{uploaded.id}', headers=hr, json={'raw_text': '试图覆盖提取原文'})
    assert response.status_code == 422
    db.expire_all()
    assert db.get(Candidate, uploaded.id).raw_text == '内部提取原文'
    assert db.get(Candidate, uploaded.id).processed_text == '内部处理文本'


def test_manual_input_text_remains_maintainable(client, hr):
    created = client.post('/api/candidates/manual', headers=hr, json={'name': '人工资料', 'raw_text': '人事录入的Python开发经验'}).json()
    response = client.put(f"/api/candidates/{created['id']}", headers=hr, json={'raw_text': '人事修正的Python和SQL开发经验'})
    assert response.status_code == 200
    assert response.json()['raw_text'] == '人事修正的Python和SQL开发经验'


def test_metadata_edit_does_not_reingest_failed_upload_or_restore_deleted_chunks(client, hr, uploaded, db):
    uploaded.status = 'failed'
    db.commit()
    response = client.put(f'/api/candidates/{uploaded.id}', headers=hr, json={'job_category': '产品经理'})
    assert response.status_code == 200 and response.json()['status'] == 'failed'
    assert db.query(ResumeChunk).filter_by(candidate_id=uploaded.id).count() == 0


@pytest.mark.parametrize('field', ['processed_text', 'processing_version', 'parent_delimiter', 'child_chunk_size'])
def test_internal_processing_fields_cannot_be_modified_through_candidate_update(client, hr, uploaded, field):
    response = client.put(f'/api/candidates/{uploaded.id}', headers=hr, json={field: 'attempt'})
    assert response.status_code == 422


def test_metadata_edit_does_not_restore_manually_deleted_segments(client, hr, db):
    created = client.post('/api/candidates/manual', headers=hr, json={'name': '人工资料', 'raw_text': '人事录入经验'}).json()
    parent = db.query(ResumeChunk).filter_by(candidate_id=created['id'], chunk_type='parent').one()
    assert client.delete(f"/api/knowledge/documents/{created['id']}/chunks/{parent.id}", headers=hr).status_code == 204
    assert client.put(f"/api/candidates/{created['id']}", headers=hr, json={'name': '更新人工姓名'}).status_code == 200
    assert db.query(ResumeChunk).filter_by(candidate_id=created['id']).count() == 0
