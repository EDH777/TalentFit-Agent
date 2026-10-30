"""Synthetic Dify pipeline regressions. Never read or rewrite the real knowledge base."""
import json

import pytest
from sqlalchemy import text

from app.models import Candidate, ResumeChunk
from app.schemas.knowledge import ChunkSettings
from app.services import embeddings, knowledge

PARENT = '<<<RESUME_PARENT>>>'
SECTION = '<<<RESUME_SECTION>>>'


def semantic_text():
    return (f'候选人：合成测试人\n父段主题：概览\n板块：核心技能\nPython自动化\n{SECTION}\n'
            f'候选人：合成测试人\n板块：教育经历\n计算机本科\n{SECTION}\n{PARENT}\n'
            f'候选人：合成测试人\n父段主题：工作经历\n板块：工作经历\n甲公司接口测试\n{SECTION}\n'
            '候选人：合成测试人\n板块：工作经历\n乙公司部署服务')


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_delimiters_split_short_semantic_sections_instead_of_filling_maximum():
    result = knowledge.preview_chunks(semantic_text(), ChunkSettings())
    assert result['parent_count'] == 2
    assert [len(p['children']) for p in result['parents']] == [2, 2]
    assert '甲公司' not in result['parents'][0]['content']
    assert all(PARENT not in p['content'] and all(SECTION not in c['content'] for c in p['children']) for p in result['parents'])
    assert '乙公司' in result['parents'][1]['children'][1]['content']


def test_oversize_parent_packs_whole_child_sections_without_cutting_a_project():
    sections = ['项目甲：' + '甲' * 75, '项目乙：' + '乙' * 75, '项目丙：' + '丙' * 75]
    result = knowledge.preview_chunks(SECTION.join(sections), ChunkSettings(parent_size=200, child_size=100, child_overlap=0))
    assert [c['content'] for p in result['parents'] for c in p['children']] == sections
    assert all(p['char_count'] <= 200 for p in result['parents'])
    assert result['parent_count'] == 2


def test_oversize_single_child_is_bounded_with_no_fact_loss():
    original = ''.join(f'事实{i}。' for i in range(150))
    result = knowledge.preview_chunks(original, ChunkSettings(parent_size=200, child_size=70, child_overlap=0))
    assert ''.join(p['content'] for p in result['parents']) == original
    assert ''.join(c['content'] for p in result['parents'] for c in p['children']) == original
    assert all(p['char_count'] <= 200 and all(c['char_count'] <= 70 for c in p['children']) for p in result['parents'])


def test_custom_chinese_and_escaped_newline_delimiters_preserve_unicode():
    result = knowledge.preview_chunks('甲段\n\n技能一。换父段乙段\n\n技能二。',
                                     ChunkSettings(parent_delimiter='换父段', child_delimiter=r'\n\n'))
    assert [p['content'] for p in result['parents']] == ['甲段\n\n技能一。', '乙段\n\n技能二。']
    assert [[c['content'] for c in p['children']] for p in result['parents']] == [['甲段', '技能一。'], ['乙段', '技能二。']]


def test_optional_cleaners_apply_independently_without_changing_source():
    source = 'Python  开发\n\n\n主页 https://example.invalid/test 邮箱 qa@example.invalid'
    baseline = knowledge.preview_chunks(source, ChunkSettings())
    assert 'https://' in baseline['parents'][0]['content']
    cleaned = knowledge.preview_chunks(source, ChunkSettings(replace_consecutive_spaces=True, delete_urls_email=True))
    value = cleaned['parents'][0]['content']
    assert 'Python 开发' in value and '\n\n\n' not in value
    assert 'https://' not in value and '@' not in value
    assert 'https://' in source


@pytest.mark.parametrize('field', ['parent_delimiter', 'child_delimiter'])
def test_empty_delimiter_rejected(field):
    with pytest.raises(ValueError):
        ChunkSettings(**{field: ''})


def test_upload_preprocesses_before_index_and_preserves_raw_text(client, hr, db, monkeypatch):
    from app.services import preprocessing, resume
    monkeypatch.setattr(preprocessing, 'complete', lambda _: semantic_text())
    monkeypatch.setattr(preprocessing.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(resume, 'structured', lambda _: {'is_resume': True, 'name': '合成测试人', 'tags': ['Python']})
    monkeypatch.setattr(embeddings, 'enabled', lambda: False)
    raw = '姓名：合成测试人\n工作经历：甲公司接口测试、乙公司部署服务。\n教育经历：计算机本科。\n核心技能：Python自动化。'
    response = client.post('/api/candidates/upload/stream', headers=hr, files={'files': ('synthetic.txt', raw.encode())})
    value = events(response)
    record = next(e['candidate'] for e in value if e['type'] == 'file_result')
    assert record['status'] == 'ready'
    stored = db.get(Candidate, record['id'])
    assert stored.raw_text == raw and SECTION in stored.processed_text
    assert stored.processing_version == 'resume-pipeline-v1'
    assert not record['raw_text'] and 'processed_text' not in record
    stages = [e['stage'] for e in value if e['type'] == 'file_progress']
    assert stages.index('extract') < stages.index('preprocess') < stages.index('normalize') < stages.index('chunk') < stages.index('keyword') < stages.index('embedding') < stages.index('done')
    assert db.query(ResumeChunk).filter_by(candidate_id=record['id'], chunk_type='parent').count() == 2
    assert db.query(ResumeChunk).filter_by(candidate_id=record['id'], chunk_type='child').count() == 4
    assert db.execute(text('SELECT count(*) FROM resume_chunks_fts')).scalar() == 4


def test_normalization_keeps_first_child_when_optional_header_line_is_missing():
    from app.services.preprocessing import normalize_processed_text
    value = '候选人：合成人才\n父段主题：工作经历\n候选人定位：测试\n核心标签：Python\n候选人：合成人才\n板块：工作经历\n第一份经历'
    result = normalize_processed_text(value + '\n' + SECTION)
    assert '第一份经历' in result and not result.endswith(SECTION)


def test_false_inside_a_legitimate_skill_is_not_non_resume():
    from app.services.preprocessing import normalize_processed_text
    from app.services.llm import ModelUnavailableError
    assert 'False判断' in normalize_processed_text('候选人：合成人才\n父段主题：概览\n板块：核心技能\nPython的True/False判断')
    with pytest.raises(ModelUnavailableError, match='整理模型未返回'):
        normalize_processed_text('False\n')


def test_truncated_model_completion_is_rejected_before_it_can_be_indexed(monkeypatch):
    from types import SimpleNamespace
    from app.services import llm
    monkeypatch.setattr(llm.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(llm.settings, 'openai_base_url', 'https://example.invalid/v1')
    class Client:
        def __init__(self, **kwargs): self.chat = self.completions = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=semantic_text()), finish_reason='length')])
    monkeypatch.setattr(llm, 'OpenAI', Client)
    with pytest.raises(llm.ModelUnavailableError, match='完整'):
        llm.complete([{'role': 'user', 'content': '合成简历'}])


def test_raw_edit_invalidates_processed_text_but_metadata_edit_preserves_it(client, hr, db, monkeypatch):
    monkeypatch.setattr(embeddings, 'enabled', lambda: False)
    row = Candidate(name='合成人才', raw_text='原始正文', processed_text=semantic_text(), processing_version='resume-pipeline-v1', created_by=2)
    db.add(row); db.commit()
    knowledge.rebuild_chunks(db, row); db.commit()
    route = f'/api/candidates/{row.id}'
    updated = client.put(route, headers=hr, json={'job_category': '产品经理'}).json()
    assert 'processed_text' not in updated
    assert db.get(Candidate, row.id).processed_text == semantic_text()
    updated = client.put(route, headers=hr, json={'raw_text': '新的人工确认正文与技能'}).json()
    assert db.get(Candidate, row.id).processed_text == db.get(Candidate, row.id).processing_version == ''
    assert all('新的人工确认正文' in c.content for c in db.query(ResumeChunk).filter_by(chunk_type='child'))


def test_parent_edit_roundtrip_keeps_child_boundaries_in_parent_content():
    value = '项目甲：接口研发' + SECTION + '项目乙：部署平台'
    first = knowledge.preview_chunks(value, ChunkSettings())
    edited = first['parents'][0]['content'].replace('接口研发', '接口研发与测试')
    assert SECTION in edited
    second = knowledge.preview_chunks(edited, ChunkSettings())
    assert [c['content'] for c in second['parents'][0]['children']] == ['项目甲：接口研发与测试', '项目乙：部署平台']


@pytest.mark.parametrize('parent,child', [('[P]', '[P]'), ('[P]', '[P]C'), (r'\n', r'\r\n')])
def test_ambiguous_parent_child_delimiters_are_rejected(parent, child):
    with pytest.raises(ValueError, match='标志'):
        ChunkSettings(parent_delimiter=parent, child_delimiter=child)


def test_crlf_delimiters_share_the_text_newline_normalization():
    result = knowledge.preview_chunks('概览\r\n技能\r\n\r\n工作\r\n项目',
                                     ChunkSettings(parent_delimiter=r'\r\n\r\n', child_delimiter=r'\r\n'))
    assert result['parent_count'] == 2 and result['child_count'] == 4


def test_empty_sections_are_removed_without_losing_parent_headers():
    from app.services.preprocessing import normalize_processed_text
    source = (f'候选人：合成人才\n父段主题：工作经历\n{SECTION}\n候选人：合成人才\n板块：工作经历\n'
              f'{SECTION}\n候选人：合成人才\n板块：工作经历\n甲公司测试经历')
    result = normalize_processed_text(source)
    assert '父段主题：工作经历' in result
    assert result.count('板块：工作经历') == 1
    assert '甲公司测试经历' in result


def test_misgrouped_or_unlabelled_model_sections_are_not_indexed_as_success():
    from app.services.preprocessing import normalize_processed_text
    from app.services.llm import ModelUnavailableError
    with pytest.raises(ModelUnavailableError, match='格式|主题'):
        normalize_processed_text('候选人：合成人才\n父段主题：候选人概览与能力画像\n板块：工作经历\n甲公司接口开发')


@pytest.mark.parametrize('fact', ['核心标签：UniqueSkillOnly', '经验范围：5年', '候选人定位：Python工程师'])
def test_nonempty_fact_fields_inside_a_section_are_not_removed_as_headers(fact):
    from app.services.preprocessing import normalize_processed_text
    source = (f'候选人：合成人才\n父段主题：候选人概览与能力画像\n板块：候选人检索摘要\n后端经验\n{SECTION}\n'
              f'候选人：合成人才\n板块：核心技能\n{fact}')
    result = normalize_processed_text(source)
    assert fact in result
    assert knowledge.preview_chunks(result, ChunkSettings())['child_count'] == 2
