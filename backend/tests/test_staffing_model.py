from types import SimpleNamespace
import json

import pytest

from app.services.llm import ModelUnavailableError


def test_budget_tracks_tools_and_refuses_over_limit():
    from app.services.staffing.model import RunBudget, BudgetExceeded
    budget = RunBudget(tool_limit=2)
    budget.use_tool()
    budget.use_tool()
    with pytest.raises(BudgetExceeded):
        budget.use_tool()
    assert budget.metrics()['tool_calls'] == 2
    with pytest.raises(BudgetExceeded):
        RunBudget(seconds=0).check()


def test_config_cannot_raise_guide_hard_budget_limits():
    from app.services.staffing.model import RunBudget
    budget = RunBudget(seconds=1000,tool_limit=100)
    assert budget.seconds==180 and budget.tool_limit==20


def test_requirements_stay_draft_and_invalid_schema_gets_one_correction(monkeypatch):
    from app.services.staffing import model
    responses = ['{"bad":true}', json.dumps({'schema_version':'1','requirements':{},'missing_fields':['工作日'],'changes':{}})]
    calls = []
    class Client:
        def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def create(self,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=responses.pop(0)))],
                                   usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8))
    monkeypatch.setattr(model, 'OpenAI', Client)
    monkeypatch.setattr(model.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(model.settings, 'openai_base_url', 'https://synthetic.invalid')
    result = model.requirement_draft('下周需要后端', {})
    assert result['missing_fields'] == ['工作日'] and len(calls) == 2
    assert '不能默认' in calls[0]['messages'][0]['content']


def test_model_cannot_add_execution_tool(monkeypatch):
    from app.services.staffing import model
    class Client:
        def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def create(self, **kwargs):
            tool = SimpleNamespace(id='t', function=SimpleNamespace(name='approve', arguments='{}'))
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='tool_calls',message=SimpleNamespace(content=None,tool_calls=[tool]))], usage=None)
    monkeypatch.setattr(model, 'OpenAI', Client)
    monkeypatch.setattr(model.settings, 'openai_api_key', 'synthetic')
    monkeypatch.setattr(model.settings, 'openai_base_url', 'https://synthetic.invalid')
    with pytest.raises(ModelUnavailableError):
        model.tool_decision([], [{'type':'function','function':{'name':'get_capacity','parameters':{}}}], model.RunBudget())
