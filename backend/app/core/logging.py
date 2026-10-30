"""Shared stderr logging for the API, worker and stdio MCP server."""
import logging
from logging.config import dictConfig
import re

from app.core.config import settings


class SafeFormatter(logging.Formatter):
    def formatException(self, exc_info):
        return exc_info[0].__name__

    def format(self, record):
        value = super().format(record)
        for secret in (settings.openai_api_key, settings.embedding_api_key, settings.jwt_secret,
                       settings.admin_password, settings.hr_password):
            if secret:
                value = value.replace(secret, '[redacted]')
        value = re.sub(r'(?i)Bearer\s+[^\s,;]+', 'Bearer [redacted]', value)
        value = re.sub(r'(?i)sk-[A-Za-z0-9._-]+', '[redacted]', value)
        value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[redacted]', value)
        value = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[redacted]', value)
        return value


def configure_logging():
    dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {'safe': {'()': SafeFormatter,
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s', 'datefmt': '%Y-%m-%d %H:%M:%S'}},
        'handlers': {'stderr': {'class': 'logging.StreamHandler', 'stream': 'ext://sys.stderr', 'formatter': 'safe'}},
        'root': {'level': 'WARNING', 'handlers': ['stderr']},
        'loggers': {
            'talentfit': {'level': 'INFO', 'handlers': ['stderr'], 'propagate': False},
            'uvicorn.error': {'level': 'INFO', 'handlers': ['stderr'], 'propagate': False},
            'uvicorn.access': {'level': 'CRITICAL', 'handlers': [], 'propagate': False},
            'httpx': {'level': 'WARNING'},
            'httpcore': {'level': 'WARNING'},
            'openai': {'level': 'WARNING'},
        },
    })
