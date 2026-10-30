import json

from app.services import embeddings, preprocessing, resume


def arranged():
    return '候选人：合成在职员工\n父段主题：候选人概览与能力画像\n板块：核心技能\n接口测试与自动化测试维护'


def test_approved_resume_with_contradictory_preprocess_false_is_rearranged(client, hr, monkeypatch):
    monkeypatch.setattr(resume.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(embeddings, 'enabled', lambda: False)
    audit_calls, arrange_calls = [], []
    monkeypatch.setattr(resume, 'structured', lambda messages: audit_calls.append(messages) or {'is_resume': True, 'name': '合成在职员工', 'job_category': '测试工程师'})
    def complete(messages):
        arrange_calls.append(messages)
        return 'false' if len(arrange_calls) == 1 else arranged()
    monkeypatch.setattr(preprocessing, 'complete', complete)
    raw = '姓名：合成在职员工\n工作经历：公司接口测试与自动化测试维护。\n教育经历：计算机专业。'
    response = client.post('/api/candidates/upload/stream', headers=hr, files={'files': ('synthetic.txt', raw.encode())})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    result = next(e for e in events if e['type'] == 'file_result')
    assert result['status'] == 'ready', result['detail']
    assert result['candidate']['job_category'] == '测试工程师'
    assert len(audit_calls) == 1 and len(arrange_calls) == 2
    assert any(e.get('stage') == 'repair_format' for e in events)
    assert arrange_calls[0][-1]['content'] == arrange_calls[1][-1]['content'] == raw


def test_repeated_contradictory_false_reports_arrangement_not_audit_rejection(client, hr, monkeypatch):
    monkeypatch.setattr(resume.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(resume, 'structured', lambda _: {'is_resume': True, 'name': '合成在职员工'})
    calls = []
    monkeypatch.setattr(preprocessing, 'complete', lambda messages: calls.append(messages) or 'False')
    raw = '工作经历：在职员工维护公司测试平台。教育经历：计算机本科。接口测试与数据核对。'
    row = client.post('/api/candidates/upload', headers=hr, files={'file': ('synthetic.txt', raw.encode())}).json()
    assert row['status'] == 'failed'
    assert '整理' in row['error_message'] and '无法确认为个人求职简历' not in row['error_message']
    assert len(calls) == 2


def test_upstream_non_resume_cannot_bypass_audit_even_with_headings(client, hr, monkeypatch):
    monkeypatch.setattr(resume.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(resume, 'structured', lambda _: {'is_resume': False})
    def forbidden(*args):
        raise AssertionError('rejected content must not reach preprocessing')
    monkeypatch.setattr(preprocessing, 'complete', forbidden)
    raw = '招聘广告：需要测试工程师，工作经历三年，教育经历本科。请投递简历，展示的并非个人职业履历。'
    row = client.post('/api/candidates/upload', headers=hr, files={'file': ('advertisement.txt', raw.encode())}).json()
    assert row['status'] == 'failed' and '审核' in row['error_message']
