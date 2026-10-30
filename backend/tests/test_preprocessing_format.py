"""Synthetic replicas of malformed model structure; no real resume material."""
import pytest

from app.services import preprocessing as p
from app.services.llm import ModelUnavailableError


def valid():
    return ('候选人：合成员工\n父段主题：候选人概览与能力画像\n核心标签：Python\n'
            '候选人：合成员工\n板块：核心技能\nPython服务研发\n'
            f'{p.SECTION}\n{p.PARENT}\n'
            '候选人：合成员工\n父段主题：工作经历\n'
            '候选人：合成员工\n板块：工作经历\n甲公司接口开发')


def test_parent_marker_after_header_is_relocated_without_losing_facts():
    misplaced = ('候选人：合成员工\n父段主题：候选人概览与能力画像\n核心标签：Python\n'
                 f'{p.SECTION}\n{p.PARENT}\n'
                 '候选人：合成员工\n板块：核心技能\nPython服务研发\n'
                 f'{p.SECTION}\n候选人：合成员工\n父段主题：工作经历\n'
                 f'{p.SECTION}\n{p.PARENT}\n'
                 '候选人：合成员工\n板块：工作经历\n甲公司接口开发')
    result = p.normalize_processed_text(misplaced)
    parents = result.split(p.PARENT)
    assert len(parents) == 2
    assert '核心标签：Python' in parents[0] and 'Python服务研发' in parents[0]
    assert '甲公司接口开发' in parents[1] and '核心技能' not in parents[1]
    assert all('父段主题：' in parent and '板块：' in parent for parent in parents)


def test_explicit_overview_facts_in_header_are_preserved_as_summary_child():
    value = f'候选人：合成员工\n父段主题：候选人概览与能力画像\n核心标签：UniqueFact\n{p.PARENT}\n' + valid().split(p.PARENT)[1]
    result = p.normalize_processed_text(value)
    first = result.split(p.PARENT)[0]
    assert '板块：候选人检索摘要' in first
    assert '核心标签：UniqueFact' in first.split('板块：候选人检索摘要')[1]
    assert len(result.split(p.PARENT)) == 2


@pytest.mark.parametrize('topic', ['候选人概览与能力画像', '工作经历', '项目经历'])
def test_header_without_overview_facts_or_experience_body_is_rejected(topic):
    value = f'候选人：合成员工\n父段主题：{topic}\n'
    if topic != '候选人概览与能力画像':
        value += '候选人定位：工程师\n'
    with pytest.raises(ModelUnavailableError, match='格式'):
        p.normalize_processed_text(value)


def test_unlabelled_parent_boundary_is_not_inferred_from_previous_topic():
    value = valid().split(p.PARENT)[0] + f'{p.PARENT}\n候选人：合成员工\n板块：核心技能\nOtherFact'
    with pytest.raises(ModelUnavailableError, match='格式'):
        p.normalize_processed_text(value)


def test_structural_failure_regenerates_once_from_source(monkeypatch):
    calls, progress = [], []
    monkeypatch.setattr(p.settings, 'openai_api_key', 'synthetic')
    def complete(messages):
        calls.append(messages)
        return '候选人：合成员工\n板块：核心技能\nPython服务研发' if len(calls) == 1 else valid()
    monkeypatch.setattr(p, 'complete', complete)
    result, _ = p.prepare_resume('合成原始履历资料', '合成员工', lambda *event: progress.append(event))
    assert '甲公司接口开发' in result
    assert len(calls) == 2
    assert all(call[-1]['content'] == '合成原始履历资料' for call in calls)
    assert any(event[0] == 'repair_format' for event in progress)


def test_persistent_format_error_is_bounded_and_never_reported_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(p.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(p, 'complete', lambda messages: calls.append(messages) or '板块：核心技能\nPython')
    with pytest.raises(ModelUnavailableError, match='格式'):
        p.prepare_resume('合成原始履历资料', '合成员工')
    assert len(calls) == 2


def test_service_errors_do_not_trigger_format_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(p.settings, 'openai_api_key', 'synthetic')
    def complete(messages):
        calls.append(messages)
        raise ModelUnavailableError('模型服务连接失败')
    monkeypatch.setattr(p, 'complete', complete)
    with pytest.raises(ModelUnavailableError):
        p.prepare_resume('合成原始履历资料', '合成员工')
    assert len(calls) == 1


def test_exhausted_format_retry_is_one_file_failure_and_keeps_old_documents(client, hr, db, monkeypatch):
    import json
    from app.models import Candidate, ResumeChunk
    from app.services import embeddings, knowledge, resume

    monkeypatch.setattr(embeddings, 'enabled', lambda: False)
    old = Candidate(name='已有合成员工', raw_text='保留的人工确认技能', status='ready', created_by=2)
    db.add(old)
    db.flush()
    knowledge.rebuild_chunks(db, old)
    db.commit()
    snapshot = [(row.id, row.content, row.enabled) for row in db.query(ResumeChunk).filter_by(candidate_id=old.id)]
    monkeypatch.setattr(p.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(resume, 'structured', lambda _: {'is_resume': True, 'name': '新合成员工', 'tags': ['Python']})
    calls = []
    def complete(messages):
        calls.append(messages[-1]['content'])
        return '板块：核心技能\nPython' if 'FAIL' in messages[-1]['content'] else valid()
    monkeypatch.setattr(p, 'complete', complete)
    response = client.post('/api/candidates/upload/stream', headers=hr, files=[
        ('files', ('bad.txt', ('FAIL合成工作经历教育经历与技能信息' * 4).encode())),
        ('files', ('good.txt', ('GOOD合成工作经历教育经历与技能信息' * 4).encode())),
    ])
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events[-1] == {'type': 'batch_done', 'total': 2, 'succeeded': 1, 'failed': 1}
    records = [event['candidate'] for event in events if event['type'] == 'file_result']
    assert [record['status'] for record in records] == ['failed', 'ready']
    assert db.query(ResumeChunk).filter_by(candidate_id=records[0]['id']).count() == 0
    assert len(calls) == 3
    assert snapshot == [(row.id, row.content, row.enabled) for row in db.query(ResumeChunk).filter_by(candidate_id=old.id)]
    assert db.get(Candidate, old.id).raw_text == '保留的人工确认技能'
