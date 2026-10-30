from app.services.staffing.solver import solve_teams, validate_assignments


def requirements(slots=1, required=None):
    return {'schema_version':'1', 'revision':1, 'start_date':'2026-11-02','end_date':'2026-11-04',
            'work_dates':['2026-11-02','2026-11-03'],
            'employee_ids':['a','b','c'], 'required_employee_ids':required or [], 'excluded_employee_ids':[],
            'slots':[{'slot_id':f's{i}','role_name':'开发','units_per_day':50,
                      'work_dates':['2026-11-02','2026-11-03'], 'required_skills':['python'],
                      'preferred_skills':[]} for i in range(slots)]}


def employees(ids=('a','b','c')):
    return [{'employee_id':i,'employment_status':'active','facts':{'status':'ready','skills':['python']}} for i in ids]


def capacities(ids=('a','b','c')):
    return [{'employee_id':i,'date':d,'capacity_units':100,'allocated_units':0}
            for i in ids for d in ['2026-11-02','2026-11-03']]


def test_produces_two_different_member_sets_and_unique_people():
    result=solve_teams(requirements(2), employees(), capacities())
    assert result['status']=='OPTIMAL'
    assert len(result['teams'])==2
    sets=[frozenset(a['employee_id'] for a in t['assignments']) for t in result['teams']]
    assert sets[0]!=sets[1]
    assert all(len(s)==2 for s in sets)
    for team in result['teams']:
        assert validate_assignments(requirements(2), employees(), capacities(), [], team['assignments'])['valid']


def test_daily_peak_and_missing_capacity_are_not_average_free_time():
    rows=capacities()
    for row in rows:
        if row['employee_id']=='a' and row['date']=='2026-11-03': row['allocated_units']=60
        if row['employee_id']=='b': row['capacity_units']=None
    r=solve_teams(requirements(),employees(),rows)
    assert [a['employee_id'] for a in r['teams'][0]['assignments']]==['c']
    assert any(d['code']=='UNKNOWN_CAPACITY' for d in r['diagnostics'])


def test_unknown_facts_does_not_claim_global_infeasible():
    people=employees()
    for person in people: person['facts']['status']='pending'
    r=solve_teams(requirements(), people, capacities())
    assert r['status']=='DATA_INCOMPLETE'
    assert r['teams']==[]


def test_unknown_capacity_does_not_claim_global_infeasible():
    rows=capacities()
    for row in rows: row['capacity_units']=None
    assert solve_teams(requirements(),employees(),rows)['status']=='DATA_INCOMPLETE'


def test_missing_recorded_skill_is_infeasible_within_explicit_scope():
    people=employees()
    for person in people: person['facts']['skills']=[]
    r=solve_teams(requirements(),people,capacities())
    assert r['status']=='INFEASIBLE'
    assert any(d['code']=='SKILL_NOT_RECORDED' for d in r['diagnostics'])


def test_required_excluded_contradiction_and_out_of_scope_are_invalid():
    req=requirements(required=['a']); req['excluded_employee_ids']=['a']
    assert solve_teams(req,employees(),capacities())['status']=='MODEL_INVALID'
    req=requirements(); req['employee_ids']=[str(i) for i in range(101)]
    assert solve_teams(req,employees(),capacities())['status']=='MODEL_INVALID'


def test_validator_rejects_tampered_dates_units_duplicates_and_skill():
    req=requirements(2)
    assignments=[{'slot_id':'s0','employee_id':'a','units_per_day':1}, {'slot_id':'s1','employee_id':'a'}]
    r=validate_assignments(req,employees(),capacities(),[],assignments)
    assert not r['valid']
    assert {'DUPLICATE_EMPLOYEE','ASSIGNMENT_MISMATCH'} <= {d['code'] for d in r['blockers']}


def test_single_member_set_not_role_permutation_and_zero_budget_unknown():
    req=requirements(2); req['employee_ids']=['a','b']
    assert len(solve_teams(req,employees(('a','b')),capacities(('a','b')))['teams'])==1
    assert solve_teams(req,employees(),capacities(),budget_seconds=0)['status']=='UNKNOWN'


def test_preferred_skills_are_first_priority():
    req=requirements(); req['slots'][0]['preferred_skills']=['sql']
    people=employees(); people[1]['facts']['skills'].append('sql')
    result=solve_teams(req,people,capacities())
    assert result['teams'][0]['assignments'][0]['employee_id']=='b'


def test_complete_scope_finds_employee_beyond_display_top_k_and_end_exclusive():
    req=requirements(); req['employee_ids']=[f'e{i}' for i in range(100)]
    people=employees(req['employee_ids'])
    for person in people[:-1]: person['facts']['skills']=[]
    rows=capacities(req['employee_ids'])
    result=solve_teams(req,people,rows)
    assert result['teams'][0]['assignments'][0]['employee_id']=='e99'
    req['slots'][0]['work_dates'].append(req['end_date'])
    assert solve_teams(req,people,rows)['status']=='MODEL_INVALID'
