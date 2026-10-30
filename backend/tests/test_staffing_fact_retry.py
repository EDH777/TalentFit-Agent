from datetime import date


def test_normalize_skill_unifies_common_spring_boot_spellings():
    from app.services.staffing.evidence import normalize_skill

    assert normalize_skill('SpringBoot') == 'spring boot'
    assert normalize_skill('spring-boot') == 'spring boot'
    assert normalize_skill('SPRING_BOOT') == 'spring boot'
    assert normalize_skill('Vue.js') == 'vue.js'
    assert normalize_skill('Vue3') == 'vue'
    assert normalize_skill('NodeJS') == 'node.js'
    assert normalize_skill('Postgre SQL') == 'postgresql'


def test_validate_fact_accepts_explicit_technology_role_phrase():
    from app.services.staffing import evidence

    source = {'candidate_id': 1, 'candidate_created_at': '2027-01-01T00:00:00', 'chunks': [{
        'id': 7, 'parent_id': 6, 'created_at': '2027-01-01T00:00:00',
        'content': 'JAVA 开发工程师', 'content_sha256': 'unused',
    }]}
    fact = evidence.validate_fact({'fact_type': 'skill', 'normalized_key': 'java', 'fact': {'name': 'JAVA'},
        'source_refs': [{'chunk_id': 7, 'quote': 'JAVA 开发工程师'}]}, source)
    assert fact and fact['normalized_key'] == 'java'


def test_explicit_skill_fallback_recovers_common_stack_terms_with_evidence():
    from app.services.staffing import evidence

    source = {'candidate_id': 1, 'candidate_created_at': '2027-01-01T00:00:00', 'chunks': [{
        'id': 7, 'parent_id': 6, 'created_at': '2027-01-01T00:00:00',
        'content': '技术栈：Java、Spring Boot、MySQL。', 'content_sha256': 'unused',
    }]}
    facts = evidence._fallback_explicit_skills(source, set())
    assert {item['normalized_key'] for item in facts} >= {'java', 'spring boot', 'mysql'}


def test_explicit_skill_fallback_recovers_latin_terms_from_garbled_pdf_text():
    from app.services.staffing import evidence

    source = {'candidate_id': 1, 'candidate_created_at': '2027-01-01T00:00:00', 'chunks': [{
        'id': 7, 'parent_id': 6, 'created_at': '2027-01-01T00:00:00',
        'content': '��ѡ�˶�λ��Java ��˿���\n���ı�ǩ��Java, SpringBoot, MySQL, Redis', 'content_sha256': 'unused',
    }]}
    facts = evidence._fallback_explicit_skills(source, set())
    assert {item['normalized_key'] for item in facts} >= {'java', 'spring boot', 'mysql'}


def test_explicit_skill_fallback_covers_product_implementation_and_procurement_terms():
    from app.services.staffing import evidence

    source = {'candidate_id': 1, 'candidate_created_at': '2027-01-01T00:00:00', 'chunks': [{
        'id': 7, 'parent_id': 6, 'created_at': '2027-01-01T00:00:00',
        'content': '负责产品规划、PRD编写、项目实施；负责供应商开发、询价比价。', 'content_sha256': 'unused',
    }]}
    facts = evidence._fallback_explicit_skills(source, set())
    assert {item['normalized_key'] for item in facts} >= {'产品规划', 'prd', '项目实施', '供应商开发', '询价', '比价'}


def test_retry_facts_rebuilds_partial_cache(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.session import Base
    from app.models import Candidate, ResumeChunk, User
    from app.models.staffing import Employee, EmployeeDocument
    from app.services.staffing.common import ensure_state
    from app.services.staffing import evidence, employees
    engine = create_engine('sqlite:///' + str(tmp_path / 'facts.db'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        ensure_state(db)
        user = User(username='facts-hr', password_hash='unused', role='hr')
        db.add(user); db.flush()
        candidate = Candidate(name='事实员工', created_by=user.id, job_category='后端开发工程师')
        employee = Employee(display_name='事实员工', facts_status='partial')
        db.add_all([candidate, employee]); db.flush()
        parent = ResumeChunk(candidate_id=candidate.id, chunk_type='parent', content='熟悉 Python SpringBoot')
        db.add(parent); db.flush()
        child = ResumeChunk(candidate_id=candidate.id, parent_id=parent.id, chunk_type='child', content='熟悉 Python SpringBoot')
        db.add(child); db.flush()
        db.add(EmployeeDocument(employee_id=employee.id, candidate_id=candidate.id, candidate_created_at=candidate.created_at, is_primary=True, linked_by=user.id))
        employee_id, child_id, actor = employee.id, child.id, user.id
    result = employees.retry_facts(factory, actor, employee_id, extractor=lambda source: {'complete': True, 'facts': [{'fact_type': 'skill', 'normalized_key': 'python', 'fact': {'name': 'Python'}, 'source_refs': [{'chunk_id': child_id, 'quote': '熟悉 Python SpringBoot'}]}]})
    assert result['status'] == 'ready'
    with factory() as db:
        current = evidence.current_facts(db, employee_id)
        assert current['status'] == 'ready'
        assert current['skills'] == ['python', 'spring boot']
        # Legacy caches may contain an unnormalized SpringBoot spelling.
        row = db.query(__import__('app.models.staffing', fromlist=['EmployeeResumeFact']).EmployeeResumeFact).filter_by(employee_id=employee_id).first()
        row.normalized_key = 'springboot'
        db.commit()
    with factory() as db:
        current = evidence.current_facts(db, employee_id)
        assert current['skills'] == ['python', 'spring boot']
    engine.dispose()
