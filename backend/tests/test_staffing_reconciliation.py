from app.services.staffing.reconciliation import (
    authoritative_claims,
    proposal_from_tool_results,
    reconcile_proposals,
)
from app.services.staffing.graph import minimum_tool_calls


def test_tool_results_become_small_auditable_proposal_without_quotes():
    proposal = proposal_from_tool_results('capability', [
        ('get_employee_evidence', {'data': [{
            'employee_id': 'e-1', 'status': 'ready', 'document_link_id': 'doc-1',
            'source_fingerprint': 'fp-1', 'facts': [{
                'id': 7, 'fact_type': 'skill', 'normalized_key': 'python', 'usable': True,
                'source_refs': [{'chunk_id': 3, 'quote': '不能进入提案'}],
            }],
        }], 'warnings': []}),
    ])
    assert proposal['claims'][0]['claim_type'] == 'facts_status'
    assert any(item['claim_type'] == 'skill' for item in proposal['claims'])
    assert '不能进入提案' not in str(proposal)
    assert proposal['claims'][-1]['evidence_refs'][0]['chunk_id'] == 3


def test_disagreement_uses_authoritative_snapshot_when_available():
    proposals = [
        {'agent_id': 'capability', 'claims': [{
            'agent_id': 'capability', 'employee_id': 'e-1', 'claim_type': 'skill',
            'key': 'python', 'value': True, 'evidence_refs': [],
        }]},
        {'agent_id': 'resource', 'claims': [{
            'agent_id': 'resource', 'employee_id': 'e-1', 'claim_type': 'skill',
            'key': 'python', 'value': False, 'evidence_refs': [],
        }]},
    ]
    result = reconcile_proposals(proposals, {('e-1', 'skill', 'python'): True})
    assert result['summary']['disagreement_count'] == 1
    assert result['summary']['unresolved_count'] == 0
    assert result['disagreements'][0]['resolution'] == 'authoritative_snapshot'
    assert result['accepted_claims'][0]['value'] is True


def test_unresolved_disagreement_is_a_blocking_signal():
    result = reconcile_proposals([
        {'agent_id': 'a', 'claims': [{'employee_id': 'e-1', 'agent_id': 'a', 'claim_type': 'capacity',
                                      'key': '2027-01-05', 'value': 40}]},
        {'agent_id': 'b', 'claims': [{'employee_id': 'e-1', 'agent_id': 'b', 'claim_type': 'capacity',
                                      'key': '2027-01-05', 'value': 80}]},
    ], {})
    assert result['summary']['unresolved_count'] == 1
    assert result['unresolved'][0]['resolution'] == 'unresolved'


def test_authoritative_claims_keep_unknown_capacity_unknown():
    result = authoritative_claims({'employees': [{'employee_id': 'e-1', 'facts': {'status': 'ready', 'skills': ['python'], 'facts': []}}],
                                    'capacities': [{'employee_id': 'e-1', 'date': '2027-01-05', 'capacity_units': None}]})
    assert result[('e-1', 'skill', 'python')] is True
    assert result[('e-1', 'capacity', '2027-01-05')] is None


def test_empty_planner_falls_back_to_scoped_read_only_queries():
    requirements = {'start_date': '2027-01-04', 'end_date': '2027-01-06',
                    'employee_ids': ['e-1', 'e-2'],
                    'slots': [{'required_skills': ['Python']}]}
    calls = minimum_tool_calls('resource', 'p-1', 'r-1', requirements, requirements['employee_ids'])
    assert len(calls) == 1 and calls[0]['name'] == 'get_capacity'
    assert calls[0]['arguments']['employee_ids'] == ['e-1', 'e-2']
    assert calls[0]['arguments']['start_date'] == '2027-01-04'
    assert len(minimum_tool_calls('single', 'p-1', 'r-1', requirements, ['e-1'])) == 2
