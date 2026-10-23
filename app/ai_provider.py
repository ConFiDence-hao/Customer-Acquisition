"""One bounded JSON provider adapter; queue workers own scheduling and publication."""
import asyncio
import json
import logging
import re
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from threading import local, Thread
from time import monotonic
from urllib.parse import urlsplit

import httpx

from . import model_config, clock

BACKOFF_BASE = 1.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ATTEMPTS = 3
ATTEMPT_TIMEOUT = 120.0
WORKER = local()
LOG=logging.getLogger(__name__)


@contextmanager
def worker_context(config, cancelled):
    old=getattr(WORKER,'value',None)
    WORKER.value=(dict(config),cancelled)
    try:yield
    finally:WORKER.value=old


def current_config():
    value=getattr(WORKER,'value',None)
    return dict(value[0]) if value else model_config.runtime()


class ProviderError(RuntimeError):
    def __init__(self, code, message, *, retryable=False, waiting_config=False, retry_after=0):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.waiting_config = waiting_config
        self.retry_after=retry_after


def retry_delay(value):
    try:seconds=float(value)
    except (ValueError,TypeError):
        try:seconds=(parsedate_to_datetime(value)-clock.utc_now()).total_seconds()
        except (ValueError,TypeError,OverflowError):return 0
    return max(0,min(8.0,seconds))


def validate_json(value, schema):
    kind = schema.get('type')
    if isinstance(kind, list):
        if value is None and 'null' in kind:return
        kind = next((candidate for candidate in kind if candidate != 'null'), None)
    if 'anyOf' in schema:
        for candidate in schema['anyOf']:
            try:
                validate_json(value, candidate)
                return
            except ValueError:pass
        raise ValueError('schema union')
    if kind == 'object':
        if not isinstance(value, dict):raise ValueError('object required')
        properties = schema.get('properties', {})
        if any(key not in value for key in schema.get('required', [])):raise ValueError('missing field')
        if schema.get('additionalProperties') is False and any(key not in properties for key in value):raise ValueError('unknown field')
        for key, data in value.items():
            if key in properties:validate_json(data, properties[key])
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > schema.get('maxItems', 1000):raise ValueError('array required')
        for data in value:validate_json(data, schema.get('items', {}))
    elif kind == 'string':
        if not isinstance(value, str) or len(value) > schema.get('maxLength', MAX_RESPONSE_BYTES):raise ValueError('string required')
    elif kind in ('integer', 'number'):
        allowed = (int,) if kind == 'integer' else (int, float)
        if isinstance(value, bool) or not isinstance(value, allowed):raise ValueError('number required')
        if value < schema.get('minimum', float('-inf')) or value > schema.get('maximum', float('inf')):raise ValueError('number bounds')
    elif kind == 'boolean' and not isinstance(value, bool):raise ValueError('boolean required')
    elif kind == 'null' and value is not None:raise ValueError('null required')
    if 'enum' in schema and value not in schema['enum']:raise ValueError('enum')


def request_payload(messages, schema, config, budget, task_kind):
    payload = {'model': config['model'], 'messages': messages, 'temperature': 0.1, 'max_tokens': budget}
    output_format = config.get('response_format', 'json_object')
    if output_format == 'json_schema':
        payload['response_format'] = {'type': 'json_schema', 'json_schema': {'name': re.sub(r'[^a-zA-Z0-9_]', '_', task_kind)[:50], 'strict': True, 'schema': schema}}
    elif output_format == 'json_object':payload['response_format'] = {'type': 'json_object'}
    if config['mode'] == 'local':payload['chat_template_kwargs'] = {'enable_thinking': False}
    elif urlsplit(config['base_url']).hostname == 'api.deepseek.com':payload['thinking'] = {'type': 'disabled'}
    return payload


async def attempt(messages, schema, config, budget, task_kind, deadline):
    remaining = min(ATTEMPT_TIMEOUT, deadline - monotonic())
    if remaining <= 0:raise ProviderError('timeout', '模型处理超时，已保留原始数据', retryable=True)
    headers = {'Accept-Encoding': 'identity'}
    if config.get('api_key'):headers['Authorization'] = 'Bearer ' + config['api_key']
    # asyncio cancellation enforces total time even when the provider continuously sends keepalive bytes.
    try:
        async with asyncio.timeout(remaining):
            async with httpx.AsyncClient(timeout=remaining, follow_redirects=False, trust_env=False) as client:
                async with client.stream('POST', config['base_url'].rstrip('/') + '/chat/completions',
                    headers=headers, json=request_payload(messages, schema, config, budget, task_kind)) as response:
                    status = response.status_code
                    if status in (400, 401, 403, 404):
                        raise ProviderError('authentication' if status in (401, 403) else 'configuration',
                            '模型认证或配置需要管理员检查，已暂停重试', waiting_config=True)
                    if status == 429 or status == 408 or status >= 500:
                        raise ProviderError('rate_limit' if status == 429 else 'service', '模型服务暂时不可用，已保留原始数据', retryable=True,retry_after=retry_delay(response.headers.get('Retry-After')))
                    if not 200 <= status < 300:raise ProviderError('http_error', '模型服务未接受请求，已保留原始数据')
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ProviderError('response_too_large', '模型响应超过大小限制，已保留原始数据')
                        body.extend(chunk)
                try:
                    envelope = json.loads(body)
                    choice = envelope['choices'][0]
                    finish = choice.get('finish_reason', 'stop')
                    if finish == 'length':raise ProviderError('truncated', '模型输出被截断，已保留原始数据')
                    text = re.sub(r'<think>.*?</think>', '', choice['message']['content'], flags=re.S).strip()
                    if text.startswith('```'):
                        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
                    parsed = json.loads(text)
                    validate_json(parsed, schema)
                except ProviderError:raise
                except (ValueError, TypeError, KeyError, IndexError, RecursionError):
                    raise ProviderError('invalid_output', '模型输出未通过格式校验，已保留原始数据') from None
                usage = envelope.get('usage', {})
                usage = {key: value for key, value in usage.items() if key in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(value, int)} if isinstance(usage, dict) else {}
                return {'data': parsed, 'usage': usage, 'finish_reason': finish}
    except TimeoutError:
        raise ProviderError('timeout', '模型处理超时，已保留原始数据', retryable=True) from None
    except httpx.TimeoutException:
        raise ProviderError('timeout', '模型请求超时，已保留原始数据', retryable=True) from None
    except httpx.HTTPError:
        raise ProviderError('transport', '模型连接暂不可用，已保留原始数据', retryable=True) from None


async def complete(messages, schema, config, budget, task_kind, deadline):
    upgraded = False
    for number in range(MAX_ATTEMPTS):
        try:
            return await attempt(messages, schema, config, budget, task_kind, deadline)
        except ProviderError as error:
            if error.waiting_config:raise
            if error.code == 'truncated':
                if upgraded or budget >= 8192:raise
                budget = 8192
                upgraded = True
            elif not error.retryable:raise
            if number + 1 == MAX_ATTEMPTS:raise
            delay = max(error.retry_after,min(BACKOFF_BASE * 2 ** number, 8.0))
            if deadline - monotonic() <= delay:
                raise ProviderError('timeout', '模型处理超时，已保留原始数据') from None
            if delay:await asyncio.sleep(delay)
    raise ProviderError('transport', '模型暂不可用，已保留原始数据')


def complete_json(messages, schema, *, task_kind, config, budget, deadline):
    if not config.get('base_url') or not config.get('model') or config.get('mode') == 'disabled':
        raise ProviderError('configuration', '未配置或尚未启用模型，请管理员配置并测试连接', waiting_config=True)
    began = monotonic()
    worker=getattr(WORKER,'value',None)
    cancelled=worker[1] if worker else None
    async def run():
        async def work():
            if config['mode']=='external':
                # DNS validation is bounded too; a stuck resolver cannot hold a worker on shutdown.
                loop=asyncio.get_running_loop();ready=loop.create_future()
                def resolve():
                    try:model_config.validate_endpoint(config['base_url'],resolve=True);error=None
                    except Exception:error=ProviderError('configuration','模型服务地址需要管理员检查',waiting_config=True)
                    def finish():
                        if not ready.done():
                            if error:ready.set_exception(error)
                            else:ready.set_result(None)
                    try:loop.call_soon_threadsafe(finish)
                    except RuntimeError:pass
                Thread(target=resolve,daemon=True,name='ai-endpoint-validation').start()
                await ready
            return await complete(messages,schema,config,min(8192,max(4096,budget)),task_kind,deadline)
        async def cancellation():
            while cancelled is None or not cancelled.is_set():await asyncio.sleep(0.05)
        task=asyncio.create_task(work());watch=asyncio.create_task(cancellation())
        try:
            async with asyncio.timeout(max(0,deadline-monotonic())):
                done,_=await asyncio.wait((task,watch),return_when=asyncio.FIRST_COMPLETED)
                if watch in done:raise ProviderError('cancelled','模型任务已暂停，保留原始数据')
                return await task
        except TimeoutError:raise ProviderError('timeout','模型处理超时，已保留原始数据') from None
        finally:
            task.cancel();watch.cancel()
            await asyncio.gather(task,watch,return_exceptions=True)
    result = asyncio.run(run())
    result['elapsed_seconds'] = monotonic() - began
    LOG.info('AI completion task=%s finish=%s tokens=%s bytes=%d elapsed=%.2f',task_kind,
             result['finish_reason'],result['usage'].get('total_tokens'),len(json.dumps(result['data']).encode()),result['elapsed_seconds'])
    return result
