"""Official MCP SDK connection; server command and host credentials are never model inputs."""
from contextlib import AsyncExitStack
from datetime import timedelta
import json
import os

import jwt
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.core.config import settings, BASE_DIR
from app.models.entities import utcnow

TOOL_NAMES = {'search_employees', 'get_employee_evidence', 'get_capacity', 'get_project_context'}


class ToolFailure(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def authorization_meta(lease):
    now = utcnow()
    token = jwt.encode({'iss':'talentfit-worker','aud':'talentfit-resources', 'sub':str(lease['actor_id']),
        'iat':now, 'exp':now + timedelta(seconds=45), 'project_id':lease['project_id'],
        'run_id':lease['id'], 'fence':lease['fence_token']}, settings.jwt_secret, algorithm='HS256')
    return {'talentfit/authorization': token}


class StaffingMcpClient:
    def __init__(self, database_url=None, record_path=None):
        self.database_url = database_url or settings.database_url
        self.record_path = record_path
        self.stack = AsyncExitStack()
        self.session = None
        self.tools = []
        self.tool_names = []

    async def __aenter__(self):
        return await self.open()

    async def open(self):
        env = {**os.environ, 'DATABASE_URL': self.database_url, 'JWT_SECRET': settings.jwt_secret,
               'OPENAI_API_KEY': settings.openai_api_key, 'OPENAI_BASE_URL':settings.openai_base_url,
               'EMBEDDING_ENABLED':str(settings.embedding_enabled).lower(),
               'EMBEDDING_API_KEY':settings.embedding_api_key, 'EMBEDDING_BASE_URL':settings.embedding_base_url,
               'EMBEDDING_MODEL':settings.embedding_model, 'EMBEDDING_DIMENSIONS':str(settings.embedding_dimensions),
               'PYTHONIOENCODING':'utf-8'}
        if self.record_path:
            env['STAFFING_MCP_RECORD_PATH'] = str(self.record_path)
        else:
            env.pop('STAFFING_MCP_RECORD_PATH', None)
        python = BASE_DIR.parent / 'chAgent' / 'Scripts' / 'python.exe'
        parameters = StdioServerParameters(command=str(python), args=['-m','app.mcp.staffing_server'],
                                          cwd=str(BASE_DIR), env=env)
        try:
            read, write = await self.stack.enter_async_context(stdio_client(parameters))
            self.session = await self.stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            discovered = await self.session.list_tools()
            self.tools = [tool for tool in discovered.tools if tool.name in TOOL_NAMES]
            self.tool_names = [tool.name for tool in self.tools]
            if set(self.tool_names) != TOOL_NAMES:
                raise ToolFailure('SOURCE_UNAVAILABLE', '业务工具目录不完整')
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *args):
        await self.stack.aclose()

    async def call(self, name, arguments, metadata, timeout=15):
        if name not in TOOL_NAMES or set(arguments) & {'_meta','meta','user_id','role','token','authorization'}:
            raise ToolFailure('SCOPE_DENIED', '不允许的工具或身份参数')
        try:
            return await self._call(name, arguments, metadata, timeout)
        except ToolFailure:
            raise
        except Exception:
            # A crashed stdio child leaves the session unusable; rebuild it once for the next run.
            await self.stack.aclose()
            self.stack = AsyncExitStack()
            self.session = None
            raise ToolFailure('SOURCE_UNAVAILABLE', '业务工具进程已中断，请重试') from None

    async def _call(self, name, arguments, metadata, timeout):
        if self.session is None:
            await self.open()
        result = await self.session.call_tool(name, arguments, meta=metadata, read_timeout_seconds=timeout)
        if result.is_error:
            text = ' '.join(getattr(item,'text','') for item in result.content)
            code = next((code for code in ('STALE_INPUT','SCOPE_DENIED','INVALID_ARGUMENT','SOURCE_UNAVAILABLE') if code in text), 'SOURCE_UNAVAILABLE')
            raise ToolFailure(code, '工具读取失败，请检查运行版本、范围和数据来源')
        data = result.structured_content
        if data is None:
            data = json.loads(next(item.text for item in result.content if getattr(item,'type','') == 'text'))
        if not isinstance(data, dict) or data.get('schema_version') != '1' or 'data' not in data:
            raise ToolFailure('SOURCE_UNAVAILABLE', '工具返回格式不正确')
        return data

    def model_tools(self, allowed):
        return [{'type':'function','function':{'name':tool.name, 'description':tool.description or '',
                    'parameters':tool.input_schema}} for tool in self.tools if tool.name in allowed]
