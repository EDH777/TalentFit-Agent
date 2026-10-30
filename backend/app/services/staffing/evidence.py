"""Derived facts from every effective child of the current primary resume.

Preparation may call a model and is deliberately outside resource_write. Publication
checks the current complete source fingerprint under the resource write lock.
``current_facts`` is a pure function of the current source snapshot and the stored
fact rows, so its result is cached per (employee, source fingerprint, fact rows) to
avoid repeating the literal re-validation on every read.
"""
import hashlib
import re
from copy import deepcopy
from functools import lru_cache
from threading import Lock

from app.models import Candidate
from app.core.technology_aliases import ALIASES, normalize as normalize_technology
from app.models.staffing import EmployeeResumeFact
from app.services import embeddings, llm
from app.services.staffing.common import fail, json_dump, json_load, resource_write
from app.services.staffing.employees import primary_document, require_employee

EXTRACTOR_VERSION = '2'
EXPLICIT_TECHNOLOGY_TERMS = (
    'spring security', 'spring cloud', 'spring boot', 'spring mvc', 'javascript', 'typescript',
    'node.js', 'elasticsearch', 'clickhouse', 'postgresql', 'rabbitmq', 'rocketmq', 'mongodb',
    'kubernetes', 'langgraph', 'langchain', 'tensorflow', 'pytorch', 'fastapi', 'django',
    'flask', 'python', 'java', 'c++', 'c#', 'spring', 'mybatis', 'mysql', 'oracle', 'redis',
    'kafka', 'nacos', 'dubbo', 'vue.js', 'vue', 'react', 'angular', 'vite', 'docker', 'linux',
    'git', 'jenkins', 'sql', 'hbase', 'mqtt', 'grpc', 'websocket', 'php', 'go', '.net',
    # Product, delivery, hardware and procurement terms found in the company resumes.
    '产品规划', '产品设计', '需求分析', '用户研究', '竞品分析', 'prd', '原型设计', 'ui设计', 'ux',
    '交互设计', '项目管理', '项目计划', '项目协调', '项目实施', '系统实施', '现场实施', '项目交付',
    '产品运营', '用户运营', '活动运营', '市场调研', '数据分析', '客户沟通', '售前咨询', '售后服务',
    '技术支持', '方案设计', '方案编写', '培训', '招投标', '投标', '业务流程', '供应商管理',
    '供应商开发', '询价', '比价', '议价', '合同管理', '采购计划', '采购执行', '采购谈判', '成本控制',
    '到货验收', '订单管理', '供应链', '物料管理', '库存管理', '供应商考核', '设备调试', '设备安装',
    '现场调试', '设备维护', '故障排查', '工程管理', '施工管理', '安全管理', '质量管理', 'plc',
    'cad', 'autocad', 'eplan', 'solidworks', '电气设计', '电气工程', '机械设计', '自动化控制',
    '仪表自动化', '变频器', '伺服', 'modbus', 'rs485', 's7-200', 's7-1200', 's7-1500', 'gx works2',
    'scada', '组态', '传感器', '电路设计', '设备选型', 'bom', 'erp', 'mes', 'crm', 'wms', 'qms',
    'oa', 'bi', 'sop', 'cmmi', 'scrum', 'ipd', 'pmp', 'axure', 'visio', 'xmind', 'figma',
)


def normalize_skill(value):
    return normalize_technology(value)


CACHE_LIMIT = 128
_facts_cache: dict[tuple, dict] = {}
_facts_lock = Lock()


def _cached_facts(key):
    with _facts_lock:
        value = _facts_cache.get(key)
    return deepcopy(value) if value is not None else None


def _store_facts(key, value):
    with _facts_lock:
        if len(_facts_cache) >= CACHE_LIMIT:
            _facts_cache.clear()
        _facts_cache[key] = deepcopy(value)


def source_snapshot(db, employee_id, extractor_version=EXTRACTOR_VERSION):
    require_employee(db, employee_id)
    link = primary_document(db, employee_id)
    if not link:
        return None
    candidate = db.query(Candidate).filter_by(id=link.candidate_id).populate_existing().first()
    if not candidate or candidate.created_at.replace(tzinfo=None) != link.candidate_created_at.replace(tzinfo=None) or candidate.deleted_at or not candidate.enabled or candidate.status != 'ready':
        return None
    children = embeddings.active_chunks(db).filter(Candidate.id == candidate.id).populate_existing().order_by(
        embeddings.ResumeChunk.position, embeddings.ResumeChunk.id).all()
    if not children:
        return None
    chunks = [{'id': child.id, 'parent_id': child.parent_id, 'created_at': child.created_at.replace(tzinfo=None).isoformat(),
        'content': child.content, 'content_sha256': hashlib.sha256(child.content.encode()).hexdigest()} for child in children]
    source = {'employee_id': employee_id, 'document_link_id': link.id, 'candidate_id': candidate.id,
        'candidate_created_at': candidate.created_at.replace(tzinfo=None).isoformat(), 'extractor_version': extractor_version,
        'document_version': link.version, 'chunks': chunks}
    source['source_fingerprint'] = hashlib.sha256(json_dump(source).encode()).hexdigest()
    return source


def redact(value):
    from app.services.agent import redact_contacts
    return redact_contacts(value)


def _literal(value, text):
    if value is None:
        return True
    if isinstance(value, str):
        return value.casefold() in text.casefold()
    if isinstance(value, list):
        return all(_literal(item, text) for item in value)
    return False


@lru_cache(maxsize=512)
def _skill_pattern(key):
    names = [key] + [alias for alias, canonical in ALIASES.items() if canonical == key]
    return re.compile('|'.join(r'(?<![a-z0-9_])' + re.escape(name) + r'(?![a-z0-9_])'
        if re.search('[a-z]', name) else re.escape(name) for name in sorted(names, key=len, reverse=True)), re.IGNORECASE)


def _skill_claims(source, key):
    """Conservative literal claim checks; an occurrence is never itself a skill."""
    pattern = _skill_pattern(key)
    claims = []
    for chunk in source['chunks']:
        content = redact(chunk['content'])
        for sentence_match in re.finditer(r'[^。！？!?；;\n]+', content):
            sentence = sentence_match.group()
            # English full stops split sentences, but not dotted technology names.
            for part in re.split(r'\.\s+|\.$', sentence):
                if not part:
                    continue
                project = re.search(r'(?:项目|project)\s*([^\s:：,，()（）]{1,40})', part, re.IGNORECASE)
                scope = project.group().casefold() if project else None
                for match in pattern.finditer(part):
                    before, after = part[:match.start()], part[match.end():]
                    clause_before = re.split(r'[,，]', before)[-1]
                    subjects = list(re.finditer(r'本人|个人|(?<![A-Za-z])我|\b(?:I|my|myself)\b|同事|他人|其他人|团队|公司|\b(?:colleague\w*|coworker\w*|teammate\w*|team|company|he|she|they)\b', clause_before, re.IGNORECASE))
                    subject = subjects[-1].group().casefold() if subjects else None
                    own = subject in ('本人', '个人', '我', 'i', 'my', 'myself')
                    if subject and not own:
                        continue
                    # A technology immediately followed by “项目” describes
                    # project context, not necessarily the person's skill.
                    if re.match(r'\s*(?:项目(?:的|中|里)?|project(?:\s|$))', after, re.IGNORECASE) and not re.search(r'使用|熟悉|掌握|精通|技术栈', clause_before, re.IGNORECASE):
                        continue
                    # Project/organization descriptions cannot confer a personal skill.
                    if not own and re.search(r'项目|\bproject\b', clause_before, re.IGNORECASE):
                        continue
                    negative_before = re.search(r'(?:未|不|无|没|并非|尚未|从未|\bnot\b|\bnever\b|\bno\b|\b(?:don|doesn|didn|haven|hasn|hadn|can|couldn)[’\x27]t\b)[^。；;,，\n]{0,35}$', clause_before, re.IGNORECASE)
                    negative_after = re.match(r'\s*[,，:：-]?\s*(?:(?:尚未|从未|未|不|没有)(?:使用|掌握|熟悉|了解|接触|具备)(?:过)?|无(?:使用经验|经验|基础)|no\s+(?:experience|knowledge|skills?)|not\s+(?:used|familiar|experienced|known)|never\s+used)(?=\s*(?:$|[,，]))', after, re.IGNORECASE)
                    negative = bool(negative_before or negative_after)
                    absolute = re.search(r'从未|\bnever\b|不会|不具备|\bno\s+(?:experience|knowledge|skills?)\b', part, re.IGNORECASE)
                    positive = bool(re.search(r'熟悉|精通|掌握|擅长|熟练|具备|使用|负责|主导|参与|承担|协助|完成|开发|设计|规划|管理|实施|采购|运营|分析|沟通|维护|调试|安装|选型|谈判|技能|技术栈|\b(?:use[ds]?|know|proficient|experienced|skills?|stack)\b', clause_before, re.IGNORECASE))
                    positive = positive or bool(re.match(r'\s*[:：-]?\s*(?:熟悉|精通|熟练|掌握|proficient|experienced)', after, re.IGNORECASE))
                    # A technology immediately used as a role or language phrase
                    # (for example "Java 开发工程师") is an explicit claim.
                    positive = positive or bool(re.match(r'\s*(?:开发|研发|工程师|程序员|编程|语言|技术|框架)', after, re.IGNORECASE))
                    # A standalone technology list in the resume belongs to its author.
                    positive = positive or (not project and not pattern.sub('', part).strip(' \t,，、:：/|'))
                    # PDF font decoding can leave the surrounding Chinese text
                    # unreadable while preserving Latin technology names. Treat
                    # an otherwise unscoped, non-negative Latin term as an
                    # explicit skill so the cached facts do not lose it.
                    garbled_context = bool(re.search(r'[\ufffd\u00a1-\u00ff]', part))
                    positive = positive or (not project and garbled_context and bool(re.search(r'[A-Za-z]', match.group())))
                    if negative or positive:
                        claims.append({'chunk': chunk, 'quote': part.strip()[:500], 'negative': negative,
                            'scope': None if absolute else scope})
        # A decoded PDF can split a technology line in a way that prevents the
        # sentence-level pass above from recognizing its context. Recover the
        # literal term from that line while preserving obvious negation and
        # third-person exclusions.
        for line in content.splitlines():
            match = pattern.search(line)
            if not match:
                continue
            before = line[:match.start()]
            after = line[match.end():]
            if re.search(r'(?:未|不|无|没有|不会|从未|同事|他人|团队|公司|colleague|coworker|teammate|team|company)\s*', before[-80:], re.IGNORECASE):
                continue
            if '��Ŀ' in line:
                continue
            if '项目' in line or '��Ŀ' in line or re.search(r'\bproject\b', line, re.IGNORECASE):
                continue
            if re.search(r'(?:未|不|无|没有|不会|从未|not|never|no)\s*(?:使用|掌握|熟悉|experience|used|use)', after, re.IGNORECASE):
                continue
            if any(claim['chunk']['id'] == chunk['id'] and pattern.search(claim['quote']) for claim in claims):
                continue
            claims.append({'chunk': chunk, 'quote': line.strip()[:500], 'negative': False, 'scope': None})
    return claims


def _source_ref(source, chunk, quote):
    return {'candidate_id': source['candidate_id'], 'candidate_created_at': source['candidate_created_at'],
        'chunk_id': chunk['id'], 'parent_id': chunk['parent_id'], 'created_at': chunk['created_at'],
        'content_sha256': chunk['content_sha256'], 'quote': redact(quote)}


def validate_fact(item, source):
    if not isinstance(item, dict) or item.get('fact_type') not in ('skill', 'position', 'project_experience'):
        return None
    fact = item.get('fact', {})
    if not isinstance(fact, dict) or fact.get('conflicted') is True and item['fact_type'] != 'skill':
        return None
    key = normalize_skill(item.get('normalized_key', ''))
    if not key or len(key) > 160:
        return None
    refs = item.get('source_refs', [])
    if not isinstance(refs, list) or not refs:
        return None
    chunks = {chunk['id']: chunk for chunk in source['chunks']}
    validated = []
    for ref in refs:
        if not isinstance(ref, dict):
            return None
        chunk = chunks.get(ref.get('chunk_id'))
        quote = ref.get('quote', '')
        if not chunk or not isinstance(quote, str) or not quote.strip() or quote not in redact(chunk['content']) or len(quote) > 500:
            return None
        validated.append(_source_ref(source, chunk, quote))
    quotes = '\n'.join(ref['quote'] for ref in validated)
    if item['fact_type'] == 'skill':
        pattern = _skill_pattern(key)
        if not pattern.search(quotes):
            return None
        claims = _skill_claims(source, key)
        cited_ids = {ref['chunk_id'] for ref in validated}
        positive = [claim for claim in claims if not claim['negative'] and claim['chunk']['id'] in cited_ids and any(
            ref['chunk_id'] == claim['chunk']['id'] and (ref['quote'] in claim['quote'] or claim['quote'] in ref['quote']) for ref in validated)]
        if not positive:
            return None
        fact = {'name': key}
        conflicting = [claim for claim in claims if claim['negative'] and any(
            claim['scope'] is None or claim['scope'] == recorded['scope'] for recorded in positive)]
        if conflicting or item.get('fact', {}).get('conflicted') is True:
            fact['conflicted'] = True
            for claim in conflicting:
                ref = _source_ref(source, claim['chunk'], claim['quote'])
                if ref not in validated:
                    validated.append(ref)
    else:
        allowed = {'name', 'time', 'role', 'technologies', 'responsibilities', 'results'} if item['fact_type'] == 'project_experience' else {'name'}
        fact = {field: value for field, value in fact.items() if field in allowed and value is not None}
        if not fact or not all(_literal(value, quotes) for value in fact.values()):
            return None
        if not _literal(key, quotes):
            return None
    return {'fact_type': item['fact_type'], 'normalized_key': key, 'fact': fact, 'source_refs': validated}


def _fallback_explicit_skills(source, existing_keys):
    """Recover common stack terms that are explicitly present but omitted by the model."""
    known = set(existing_keys)
    recovered = []
    # One literal scan replaces ~190 per-chunk scans; a term absent from the whole
    # source cannot produce any claim, so skipping it changes no result.
    haystack = '\n'.join(redact(chunk['content']) for chunk in source['chunks'])
    for raw_key in sorted(EXPLICIT_TECHNOLOGY_TERMS, key=len, reverse=True):
        key = normalize_skill(raw_key)
        if key in known:
            continue
        if any(existing.startswith(key + ' ') or existing.startswith(key + '.') for existing in known):
            continue
        if not _skill_pattern(key).search(haystack):
            continue
        claims = [claim for claim in _skill_claims(source, key) if not claim['negative']]
        for claim in claims:
            candidate = validate_fact({'fact_type': 'skill', 'normalized_key': key,
                'fact': {'name': raw_key}, 'source_refs': [{'chunk_id': claim['chunk']['id'], 'quote': claim['quote']}]}, source)
            if candidate:
                recovered.append(candidate)
                known.add(key)
                break
    return recovered


def prepare(db, employee_id, extractor=None, extractor_version=EXTRACTOR_VERSION):
    source = source_snapshot(db, employee_id, extractor_version)
    if not source:
        return {'employee_id': employee_id, 'source': None, 'facts': [], 'status': 'unavailable', 'warnings': ['当前主简历没有有效分段']}
    # End the read snapshot before model work; caller owns this private read Session.
    db.rollback()
    try:
        if extractor is None:
            from app.services.staffing.model import extract_resume, RunBudget
            output = extract_resume(source, RunBudget())
        else:
            output = extractor(source)
        if not isinstance(output, dict) or not isinstance(output.get('facts'), list):
            raise ValueError('invalid extraction structure')
        facts, seen = [], set()
        rejected = 0
        for item in output['facts']:
            fact = validate_fact(item, source)
            if not fact:
                rejected += 1
                continue
            identity = (fact['fact_type'], fact['normalized_key'], json_dump(fact['fact']))
            if identity not in seen:
                facts.append(fact)
                seen.add(identity)
        for fact in _fallback_explicit_skills(source, {item['normalized_key'] for item in facts}):
            identity = (fact['fact_type'], fact['normalized_key'], json_dump(fact['fact']))
            if identity not in seen:
                facts.append(fact)
                seen.add(identity)
        warnings = [f'事实抽取未覆盖全部资料（已确认{len(facts)}项），不能作完整无解判断'] if output.get('complete', True) is not True else []
        if rejected:
            warnings.append('部分抽取项缺少本人有效肯定引用，已排除')
        if any(item['fact'].get('conflicted') for item in facts):
            warnings.append('部分技能存在明确矛盾，已保留双方引用且不用于技能匹配')
        return {'employee_id': employee_id, 'source': source, 'facts': facts, 'status': 'ready' if output.get('complete', True) is True else 'partial', 'warnings': warnings}
    except (llm.ModelUnavailableError, ValueError, TypeError):
        return {'employee_id': employee_id, 'source': source, 'facts': [], 'status': 'failed', 'warnings': ['简历事实抽取未完成，可重试']}


def refresh(factory, employee_id, prepared, guard=None):
    with resource_write(factory) as db:
        if guard is not None:
            guard(db)
        employee = require_employee(db, employee_id)
        original = prepared.get('source')
        current = source_snapshot(db, employee_id, original['extractor_version'] if original else EXTRACTOR_VERSION)
        if prepared.get('employee_id') != employee_id or (original or {}).get('source_fingerprint') != (current or {}).get('source_fingerprint'):
            fail(409, 'STALE_INPUT', '主简历依据已变化，请重新抽取')
        db.query(EmployeeResumeFact).filter_by(employee_id=employee_id).delete(synchronize_session=False)
        if original and prepared['status'] == 'ready':
            for item in prepared['facts']:
                # Never trust a caller-supplied prepared object without repeating literal checks.
                fact = validate_fact(item, current)
                if not fact:
                    fail(422, 'INVALID_EVIDENCE', '派生事实引用无效')
                db.add(EmployeeResumeFact(employee_id=employee_id, document_link_id=current['document_link_id'],
                    fact_type=fact['fact_type'], normalized_key=fact['normalized_key'], fact_json=json_dump(fact['fact']),
                    source_refs_json=json_dump(fact['source_refs']), source_fingerprint=current['source_fingerprint'], extractor_version=current['extractor_version']))
        employee.facts_status = prepared['status']
        employee.facts_fingerprint = current['source_fingerprint'] if current else ''
        employee.facts_extractor_version = current['extractor_version'] if current else ''
        employee.facts_error = ';'.join(prepared.get('warnings', [])) or None
        employee.version += 1
        return True


def _rows_digest(rows):
    return hashlib.sha256(json_dump([
        {'id': row.id, 'link': row.document_link_id, 'fingerprint': row.source_fingerprint,
         'extractor': row.extractor_version, 'type': row.fact_type, 'key': row.normalized_key,
         'fact': row.fact_json, 'refs': row.source_refs_json} for row in rows]).encode()).hexdigest()


def current_facts(db, employee_id):
    employee = require_employee(db, employee_id)
    source = source_snapshot(db, employee_id, EXTRACTOR_VERSION)
    rows = (db.query(EmployeeResumeFact).filter_by(employee_id=employee_id, source_fingerprint=source['source_fingerprint'])
            .order_by(EmployeeResumeFact.id).all()) if source else []
    key = (employee_id, source['source_fingerprint'] if source else '', EXTRACTOR_VERSION, employee.facts_status,
           employee.facts_fingerprint, employee.facts_extractor_version, employee.facts_error, _rows_digest(rows))
    cached = _cached_facts(key)
    if cached is not None:
        return cached
    result = _build_current_facts(employee, source, rows)
    _store_facts(key, result)
    return result


def _build_current_facts(employee, source, rows):
    status = employee.facts_status
    if not source:
        status = 'stale' if employee.facts_fingerprint else 'unavailable'
    elif employee.facts_fingerprint and (source['source_fingerprint'] != employee.facts_fingerprint or employee.facts_extractor_version != EXTRACTOR_VERSION):
        status = 'stale'
    result = {'schema_version': '1', 'employee_id': employee.id, 'document_link_id': source['document_link_id'] if source else None,
        'source_fingerprint': source['source_fingerprint'] if source else '', 'status': status, 'skills': [], 'positions': [],
        'project_experiences': [], 'facts': [], 'conflicts': [], 'warnings': [employee.facts_error] if employee.facts_error else []}
    if status != 'ready':
        return result
    for row in rows:
        item = {'id': row.id, 'fact_type': row.fact_type, 'normalized_key': row.normalized_key, 'fact': json_load(row.fact_json, {}), 'source_refs': json_load(row.source_refs_json, [])}
        checked = validate_fact(item, source)
        if row.document_link_id != source['document_link_id']:
            result.update(status='stale', skills=[], positions=[], project_experiences=[], facts=[], conflicts=[])
            return result
        if not checked:
            result['warnings'].append('部分历史派生项未通过当前证据归属与肯定校验，已排除')
            continue
        item = {'id': row.id, **checked, 'usable': not checked['fact'].get('conflicted', False)}
        result['facts'].append(item)
        if not item['usable']:
            result['conflicts'].append(item)
            if not any('矛盾' in warning for warning in result['warnings']):
                result['warnings'].append('部分技能存在明确矛盾，已保留双方引用且不用于技能匹配')
        elif row.fact_type == 'skill':
            result['skills'].append(checked['normalized_key'])
        else:
            result['positions' if row.fact_type == 'position' else 'project_experiences'].append(item)
    # Project technologies are explicit claims about the employee's work and
    # must participate in required-skill matching alongside standalone skills.
    for item in result['project_experiences']:
        technologies = item['fact'].get('technologies', [])
        if isinstance(technologies, list):
            result['skills'].extend(normalize_skill(value) for value in technologies if str(value).strip())
    existing_fact_keys = {item['normalized_key'] for item in result['facts']}
    for fact in _fallback_explicit_skills(source, existing_fact_keys | set(result['skills'])):
        item = {'id': None, **fact, 'usable': not fact['fact'].get('conflicted', False), 'derived': 'explicit_text_fallback'}
        result['facts'].append(item)
        if item['usable']:
            result['skills'].append(item['normalized_key'])
        else:
            result['conflicts'].append(item)
    result['skills'] = sorted(set(result['skills']))
    return result
