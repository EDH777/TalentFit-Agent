import logging

from app.core.config import settings


def test_log_formatter_redacts_credentials_and_contacts(monkeypatch):
    from app.core.logging import SafeFormatter

    monkeypatch.setattr(settings, 'openai_api_key', 'test-provider-secret')
    record = logging.LogRecord('talentfit.test', logging.WARNING, __file__, 1,
        'request failed %s', ('test-provider-secret Bearer sample-token user@example.test 13812345678',), None)
    output = SafeFormatter('%(message)s').format(record)
    assert 'request failed' in output
    for value in ('test-provider-secret', 'sample-token', 'user@example.test', '13812345678'):
        assert value not in output


def test_log_formatter_omits_exception_payload():
    from app.core.logging import SafeFormatter

    try:
        raise ValueError('private resume and provider response')
    except ValueError as exc:
        record = logging.LogRecord('talentfit.test', logging.ERROR, __file__, 1,
            'operation failed', (), (type(exc), exc, exc.__traceback__))
    output = SafeFormatter('%(message)s').format(record)
    assert 'ValueError' in output
    assert 'private resume' not in output
