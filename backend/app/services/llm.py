import json
import logging
import re

from openai import APIError, OpenAI

from app.core.config import settings


logger = logging.getLogger("talentfit.model")


class ModelUnavailableError(RuntimeError):
    pass


def model_options() -> dict:
    if settings.model_name.lower().startswith(("qwen3", "qwen-plus", "qwen-flash")) and "thinking" not in settings.model_name.lower():
        return {"extra_body": {"enable_thinking": settings.qwen_enable_thinking}}
    return {}


def complete(messages: list[dict], *, json_output: bool = False) -> str:
    if not settings.openai_api_key or not settings.openai_base_url:
        raise ModelUnavailableError("模型服务未配置，请联系管理员检查模型配置。")
    options = {"response_format": {"type": "json_object"}} if json_output else {}
    try:
        with OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url.rstrip("/"),
                    timeout=settings.llm_timeout_seconds, max_retries=1) as client:
            result = client.chat.completions.create(
                model=settings.model_name, messages=messages, temperature=0.2, **options, **model_options(),
            )
        if getattr(result.choices[0], 'finish_reason', 'stop') != 'stop':
            raise ModelUnavailableError('模型未完整生成结果，请重试或缩小单份输入，未保存不完整内容。')
        content = (result.choices[0].message.content or "").strip()
        if not content:
            raise ModelUnavailableError("模型返回为空，请稍后重试。")
        return content
    except APIError as exc:
        logger.warning("model_request_failed model=%s status=%s exception=%s", settings.model_name, getattr(exc, "status_code", None), type(exc).__name__)
        # Provider errors may contain URLs, headers or submitted private data.
        raise ModelUnavailableError("模型服务连接失败或调用超时，请稍后重试并检查服务配置。") from exc


def structured(messages: list[dict]) -> dict:
    raw = complete(messages, json_output=True)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (ValueError, TypeError) as exc:
        raise ModelUnavailableError("模型返回的数据格式不正确，请重试。") from exc


def stream_complete(messages: list[dict]):
    if not settings.openai_api_key or not settings.openai_base_url:
        raise ModelUnavailableError("模型服务未配置，请联系管理员检查模型配置。")
    completed = False
    try:
        with OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url.rstrip("/"),
                    timeout=settings.llm_timeout_seconds, max_retries=1) as client:
            with client.chat.completions.create(model=settings.model_name, messages=messages,
                                                temperature=0.2, stream=True, **model_options()) as stream:
                for event in stream:
                    if event.choices and event.choices[0].delta.content:
                        yield event.choices[0].delta.content
                    if event.choices and event.choices[0].finish_reason:
                        if event.choices[0].finish_reason != "stop":
                            raise ModelUnavailableError("模型回答未完整生成，请缩小问题范围后重试。")
                        completed = True
        if not completed:
            raise ModelUnavailableError("模型连接提前结束，回答未完成，请重试。")
    except APIError as exc:
        logger.warning("model_request_failed model=%s status=%s exception=%s", settings.model_name, getattr(exc, "status_code", None), type(exc).__name__)
        raise ModelUnavailableError("模型流式输出中断，请稍后重试。") from exc
