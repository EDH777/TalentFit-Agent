import pytest
from app.models import ResumeChunk
from app.services.resume import search_candidates


def test_candidate_source_classification_removed_and_original_creation_time_preserved(client, hr, db):
    from sqlalchemy import inspect
    response = client.post('/api/candidates/manual', headers=hr, json={'name': '合成字段验收', 'raw_text': 'Python开发工作经历'})
    assert response.status_code == 201
    result = response.json()
    assert 'source_type' not in result
    assert 'source_filename' in result
    assert result['created_at']
    columns = {column['name'] for column in inspect(db.get_bind()).get_columns('candidates')}
    assert 'source_type' not in columns
    assert 'created_at' in columns
    original_time = result['created_at']
    updated = client.put('/api/candidates/' + str(result['id']), headers=hr, json={'name': '合成字段验收更新'})
    assert updated.status_code == 200 and updated.json()['created_at'] == original_time


def create_person(client, hr, **changes):
    payload = {"name": "测试张三", "job_category": "采购经理", "tags": ["供应链"],
               "raw_text": "测试张三，5年采购工作经验。负责供应商管理、合同采购和供应链项目，能够进行成本控制和质量管理。"}
    return client.post("/api/candidates/manual", headers=hr, json=payload | changes)


def test_chinese_query_retrieves_candidate(db, client, hr):
    record = create_person(client, hr).json()
    results = search_candidates(db, "推荐有供应商管理经验的采购经理")
    assert record["id"] in [item["candidate_id"] for item in results]


def test_metadata_edit_rebuilds_search_index(db, client, hr):
    record = create_person(client, hr).json()
    client.put(f"/api/candidates/{record['id']}", headers=hr, json={"tags": ["SAP专家"]})
    assert search_candidates(db, "SAP专家")[0]["candidate_id"] == record["id"]


def test_delete_removes_chunks_and_search(db, client, hr):
    record = create_person(client, hr).json()
    assert client.delete(f"/api/candidates/{record['id']}", headers=hr).status_code == 204
    assert db.query(ResumeChunk).filter_by(candidate_id=record["id"]).count() == 0
    assert search_candidates(db, "采购经理") == []


def test_deleted_file_can_be_uploaded_again(client, hr):
    body = "姓名：李四\n工作经历：5年Java开发。熟悉Spring与SQL，负责企业系统开发与项目交付。教育背景：计算机本科。".encode()
    first = client.post("/api/candidates/upload", headers=hr, files={"file": ("lisi.txt", body)})
    assert first.status_code == 201
    assert first.json()["status"] == "ready"
    assert client.post("/api/candidates/upload", headers=hr, files={"file": ("lisi.txt", body)}).status_code == 409
    client.delete(f"/api/candidates/{first.json()['id']}", headers=hr)
    assert client.post("/api/candidates/upload", headers=hr, files={"file": ("lisi.txt", body)}).json()["status"] == "ready"


def test_article_rejected_without_search_index(client, hr, db):
    body = ("今日天气晴朗，公园百花盛开。欢迎阅读我们的旅游攻略，附近有许多值得参观的景点。" * 5).encode()
    result = client.post("/api/candidates/upload", headers=hr, files={"file": ("article.txt", body)})
    assert result.status_code == 201
    assert result.json()["status"] == "failed"
    assert db.query(ResumeChunk).count() == 0


@pytest.mark.parametrize("field,value", [("name", "   "), ("raw_text", "  "), ("name", None), ("raw_text", None)])
def test_invalid_update_rejected(client, hr, field, value):
    record = create_person(client, hr).json()
    response = client.put(f"/api/candidates/{record['id']}", headers=hr, json={field: value})
    assert response.status_code == 422


def test_unsupported_and_oversized_files(client, hr):
    assert client.post("/api/candidates/upload", headers=hr, files={"file": ("bad.exe", b"bad")}).status_code == 400
    assert client.post("/api/candidates/upload", headers=hr, files={"file": ("huge.txt", b"a" * (15 * 1024 * 1024 + 1))}).status_code == 413


def test_detail_does_not_expose_server_path(client, hr):
    record = create_person(client, hr).json()
    assert "file_path" not in record
