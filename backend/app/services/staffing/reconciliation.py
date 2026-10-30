"""Deterministic coordination of specialist-agent proposals.

The language model may decide which read-only facts to inspect, but it does not
decide which fact wins.  This module turns the bounded MCP results into small,
auditable claims and reconciles them against the current database snapshot.
"""
from collections import defaultdict

from app.services.staffing.common import json_dump


def _value_key(value):
    return json_dump(value)


def _ref(ref):
    """Keep only identifiers that can be shown as an evidence pointer."""
    if not isinstance(ref, dict):
        return {}
    allowed = ('chunk_id', 'fact_id', 'document_link_id', 'source_fingerprint', 'created_at')
    return {key: ref[key] for key in allowed if ref.get(key) is not None}


def _claim(agent_id, employee_id, claim_type, key, value, evidence_refs=None):
    claim = {'agent_id': agent_id, 'employee_id': employee_id, 'claim_type': claim_type,
             'key': key, 'value': value}
    refs = [_ref(item) for item in (evidence_refs or [])]
    claim['evidence_refs'] = [item for item in refs if item]
    return claim


def proposal_from_tool_results(agent_id, tool_results):
    """Build a controlled proposal from MCP envelopes.

    ``tool_results`` is intentionally an internal value.  The returned proposal
    removes quotes and raw tool output before it can enter graph state or an
    event payload.
    """
    claims, warnings, blockers, observed = [], [], [], set()
    for tool_name, envelope in tool_results:
        if not isinstance(envelope, dict):
            blockers.append({'code': 'INVALID_TOOL_RESULT', 'agent_id': agent_id})
            continue
        warnings.extend(str(item)[:300] for item in envelope.get('warnings', []) if item)
        rows = envelope.get('data') if isinstance(envelope.get('data'), list) else []
        if tool_name == 'get_employee_evidence':
            for facts in rows:
                employee_id = facts.get('employee_id') if isinstance(facts, dict) else None
                if not employee_id:
                    continue
                observed.add(employee_id)
                status = facts.get('status', 'unknown')
                claims.append(_claim(agent_id, employee_id, 'facts_status', 'status', status,
                                      [{'document_link_id': facts.get('document_link_id'),
                                        'source_fingerprint': facts.get('source_fingerprint')}]))
                for item in facts.get('facts', []):
                    if not isinstance(item, dict) or not item.get('usable', False):
                        continue
                    fact_type = item.get('fact_type')
                    if fact_type not in ('skill', 'position', 'project_experience'):
                        continue
                    fact_value = item.get('fact') if isinstance(item.get('fact'), dict) else {}
                    value = item.get('normalized_key') or fact_value.get('name')
                    if not value:
                        continue
                    refs = [{**ref, 'fact_id': item.get('id'),
                             'document_link_id': facts.get('document_link_id'),
                             'source_fingerprint': facts.get('source_fingerprint')}
                            for ref in item.get('source_refs', []) if isinstance(ref, dict)]
                    claims.append(_claim(agent_id, employee_id, fact_type, str(value), True, refs))
        elif tool_name == 'get_capacity':
            for row in rows:
                if not isinstance(row, dict) or not row.get('employee_id') or not row.get('date'):
                    continue
                employee_id, day = row['employee_id'], row['date']
                observed.add(employee_id)
                claims.append(_claim(agent_id, employee_id, 'capacity', day, row.get('capacity_units'),
                                      [{'source_fingerprint': str(envelope.get('data_revision', '')),
                                        'created_at': envelope.get('observed_at')}]))
        elif tool_name == 'get_project_context':
            for row in rows:
                for allocation in row.get('allocations', []) if isinstance(row, dict) else []:
                    employee_id = allocation.get('employee_id')
                    if not employee_id:
                        continue
                    observed.add(employee_id)
                    day_key = ','.join(sorted(allocation.get('work_dates', [])))
                    claims.append(_claim(agent_id, employee_id, 'allocation',
                                         f"{allocation.get('project_id')}:{day_key}",
                                         allocation.get('units_per_day'),
                                         [{'source_fingerprint': str(envelope.get('data_revision', ''))}]))
        elif tool_name == 'search_employees':
            for row in rows:
                if not isinstance(row, dict) or not row.get('employee_id'):
                    continue
                employee_id = row['employee_id']
                observed.add(employee_id)
                claims.append(_claim(agent_id, employee_id, 'employee_scope', 'active',
                                     row.get('facts_status') == 'ready',
                                     [{'document_link_id': row.get('document_link_id'),
                                       'source_fingerprint': row.get('source_fingerprint')}]))
    unique = {}
    for claim in claims:
        unique[(claim['employee_id'], claim['claim_type'], claim['key'], _value_key(claim['value']))] = claim
    return {'schema_version': '1', 'agent_id': agent_id,
            'claims': list(unique.values())[:500],
            'warnings': sorted(set(warnings))[:30], 'blockers': blockers[:30],
            'observed_employee_ids': sorted(observed), 'tool_count': len(tool_results)}


def authoritative_claims(input_data):
    """Create the current database snapshot used to resolve disagreements."""
    result = {}
    for employee in input_data.get('employees', []):
        employee_id = employee.get('employee_id')
        facts = employee.get('facts', {})
        if not employee_id:
            continue
        result[(employee_id, 'facts_status', 'status')] = facts.get('status', 'unknown')
        if facts.get('status') == 'ready':
            for skill in facts.get('skills', []):
                result[(employee_id, 'skill', str(skill))] = True
            for item in facts.get('facts', []):
                if item.get('usable') and item.get('fact_type') in ('position', 'project_experience'):
                    fact_value = item.get('fact') if isinstance(item.get('fact'), dict) else {}
                    key = item.get('normalized_key') or fact_value.get('name')
                    if key:
                        result[(employee_id, item['fact_type'], str(key))] = True
    for row in input_data.get('capacities', []):
        if row.get('employee_id') and row.get('date'):
            result[(row['employee_id'], 'capacity', row['date'])] = row.get('capacity_units')
    return result


def reconcile_proposals(proposals, authoritative):
    """Merge proposals, classifying resolved and unresolved disagreements."""
    grouped = defaultdict(list)
    for proposal in proposals:
        if not proposal:
            continue
        for claim in proposal.get('claims', []):
            key = (claim.get('employee_id'), claim.get('claim_type'), claim.get('key'))
            if all(key):
                grouped[key].append(claim)
    accepted, disagreements, unresolved = [], [], []
    for key, claims in sorted(grouped.items(), key=lambda item: tuple(str(x) for x in item[0])):
        values = {_value_key(claim.get('value')) for claim in claims}
        if len(values) == 1:
            accepted.append(claims[0])
            continue
        authoritative_value = authoritative.get(key, object())
        match = next((claim for claim in claims if _value_key(claim.get('value')) == _value_key(authoritative_value)), None)
        disagreement = {'code': 'AGENT_DISAGREEMENT', 'key': list(key),
                        'agents': sorted({claim.get('agent_id') for claim in claims}),
                        'claim_count': len(claims)}
        disagreements.append(disagreement)
        if match is not None:
            disagreement['resolution'] = 'authoritative_snapshot'
            accepted.append(match)
        else:
            disagreement['resolution'] = 'unresolved'
            unresolved.append(disagreement)
    proposal_warnings = [warning for proposal in proposals or [] for warning in proposal.get('warnings', [])]
    proposal_blockers = [blocker for proposal in proposals or [] for blocker in proposal.get('blockers', [])]
    return {'schema_version': '1', 'accepted_claims': accepted[:500],
            'disagreements': disagreements[:100], 'unresolved': unresolved[:100],
            'warnings': sorted(set(proposal_warnings))[:30], 'blockers': proposal_blockers[:30],
            'summary': {'proposal_count': len([item for item in proposals or [] if item]),
                        'claim_count': sum(len(item.get('claims', [])) for item in proposals or [] if item),
                        'accepted_count': len(accepted), 'disagreement_count': len(disagreements),
                        'unresolved_count': len(unresolved)}}

