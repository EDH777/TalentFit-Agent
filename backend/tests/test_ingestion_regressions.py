import pytest

from app.core.config import settings
from app.models import Candidate, ResumeChunk
from app.services import resume


@pytest.mark.parametrize("tag_count", [49, 34])
def test_many_extracted_tags_do_not_reject_valid_resume(client, hr, db, monkeypatch, tag_count):
    monkeypatch.setattr(settings, "openai_api_key", "test")
    monkeypatch.setattr(resume.embeddings, "enabled", lambda: False)
    tags = [" Java ", "Java", ""] + [f"技能{i}" for i in range(tag_count)]
    monkeypatch.setattr(resume, "structured", lambda _: {
        "is_resume": True, "name": "测试人才", "tags": tags,
        "job_category": "前端开发工程师", "years_experience": "5年",
    })
    raw = "姓名：测试人才\n工作经历：前端开发与项目维护。项目经验：React 管理系统。教育背景：计算机本科。技能48是正文中的技能。"
    monkeypatch.setattr(resume.preprocessing, 'complete', lambda _: '候选人：测试人才\n父段主题：简历信息\n板块：简历正文\n' + raw)
    response = client.post("/api/candidates/upload", headers=hr, files={"file": ("resume.txt", raw.encode())})
    assert response.status_code == 201
    record = response.json()
    assert record["status"] == "ready", record["error_message"]
    assert len(record["tags"]) == 30 and record["tags"][0] == "Java"
    assert db.get(Candidate, record['id']).raw_text == raw
    assert record['raw_text'] is None
    assert db.query(ResumeChunk).filter_by(candidate_id=record["id"], chunk_type="child").count() > 0
    assert resume.search_candidates(db, "技能48")[0]["candidate_id"] == record["id"]


def test_invalid_profile_reports_field_extraction_not_pdf_failure(client, hr, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test")
    monkeypatch.setattr(resume, "structured", lambda _: {"is_resume": True, "name": None})
    raw = "姓名：测试人才\n工作经历：前端开发与项目维护。项目经验：React 管理系统。教育背景：计算机本科。"
    record = client.post("/api/candidates/upload", headers=hr, files={"file": ("resume.txt", raw.encode())}).json()
    assert record["status"] == "failed"
    assert "字段" in record["error_message"] and "文件解析失败" not in record["error_message"]
