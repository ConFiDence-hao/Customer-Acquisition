"""Bounded provider requests must retry explicitly and honor a real total timeout."""
import asyncio
import importlib
import importlib.util
import json
from threading import Event, Thread
from time import monotonic

import httpx
import pytest

SCHEMA={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}


def test_worker_shutdown_cancels_inflight_provider_even_with_keepalives(monkeypatch):
    started=Event();cancel=Event();errors=[]
    async def handle(request):
        started.set()
        await asyncio.sleep(10)
        return httpx.Response(200,json=completion())
    module=transport(monkeypatch,handle)
    assert hasattr(module,'worker_context'), 'provider has no cancellable worker context'
    def run():
        try:
            with module.worker_context(config(),cancel):invoke(module)
        except module.ProviderError as error:errors.append(error.code)
    thread=Thread(target=run);thread.start()
    assert started.wait(1)
    cancel.set();thread.join(1)
    assert not thread.is_alive() and errors==['cancelled']


def test_retry_after_is_honored_with_a_bounded_delay(monkeypatch):
    calls=[];delays=[]
    def handle(request):
        calls.append(True)
        return httpx.Response(429,headers={'Retry-After':'99999'}) if len(calls)==1 else httpx.Response(200,json=completion())
    module=transport(monkeypatch,handle)
    original_sleep=asyncio.sleep
    async def sleep(seconds):
        if seconds==0.05:await original_sleep(seconds)
        else:delays.append(seconds)
    monkeypatch.setattr(module.asyncio,'sleep',sleep)
    assert invoke(module)['data']=={'answer':'checked'}
    assert delays==[8.0]


def provider_module():
    assert importlib.util.find_spec('app.ai_provider') is not None, 'bounded unified provider is not implemented'
    return importlib.import_module('app.ai_provider')


def config(host='api.deepseek.com', mode='external'):
    return {'mode':mode,'base_url':f'https://{host}/v1','model':'test-model',
            'api_key':'synthetic-test-key','response_format':'json_object','version':1}


def completion(data=None, finish='stop'):
    return {'choices':[{'finish_reason':finish,'message':{'content':json.dumps(data or {'answer':'checked'},ensure_ascii=False)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':4}}


def transport(monkeypatch, handler):
    module=provider_module()
    client=httpx.AsyncClient
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:client(transport=httpx.MockTransport(handler),**kwargs))
    monkeypatch.setattr(module.model_config,'validate_endpoint',lambda *args,**kwargs:None)
    monkeypatch.setattr(module,'BACKOFF_BASE',0)
    return module


def invoke(module, settings=None, deadline=None):
    return module.complete_json([{'role':'user','content':'Synthetic public connection test'}],SCHEMA,
        task_kind='connection_test',config=settings or config(),budget=4096,deadline=deadline or monotonic()+120)


@pytest.mark.parametrize('host,mode,thinking', [
    ('api.deepseek.com','external',{'type':'disabled'}), ('compatible.example','external',None),
    ('local.example','local',None),
])
def test_provider_parameters_follow_actual_provider_without_guessing_vendor_options(monkeypatch,host,mode,thinking):
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=completion())
    module=transport(monkeypatch,handle)
    result=invoke(module,config(host,mode))
    assert result['data']=={'answer':'checked'} and result['finish_reason']=='stop'
    assert requests[0].get('thinking')==thinking
    assert requests[0]['max_tokens']==4096
    if mode=='local':assert requests[0]['chat_template_kwargs']=={'enable_thinking':False}
    elif host!='api.deepseek.com':assert 'chat_template_kwargs' not in requests[0]


def test_explicit_truncation_upgrades_budget_only_once(monkeypatch):
    budgets=[]
    def handle(request):
        budgets.append(json.loads(request.content)['max_tokens'])
        return httpx.Response(200,json=completion(finish='length' if len(budgets)==1 else 'stop'))
    module=transport(monkeypatch,handle)
    assert invoke(module)['data']=={'answer':'checked'}
    assert budgets==[4096,8192]


def test_repeated_truncation_fails_without_an_unbounded_budget_loop(monkeypatch):
    budgets=[]
    def handle(request):
        budgets.append(json.loads(request.content)['max_tokens'])
        return httpx.Response(200,json=completion(finish='length'))
    module=transport(monkeypatch,handle)
    with pytest.raises(module.ProviderError) as error:invoke(module)
    assert error.value.code=='truncated'
    assert budgets==[4096,8192]


@pytest.mark.parametrize('status',[429,500,503])
def test_transient_failures_retry_and_never_exceed_three_attempts(monkeypatch,status):
    attempts=[]
    def handle(request):
        attempts.append(1)
        return httpx.Response(status if len(attempts)<3 else 200,json=completion())
    module=transport(monkeypatch,handle)
    assert invoke(module)['data']=={'answer':'checked'}
    assert len(attempts)==3


def test_authentication_failure_waits_for_configuration_without_retries_or_error_body_leak(monkeypatch):
    attempts=[]
    def handle(request):
        attempts.append(1)
        return httpx.Response(401,json={'error':{'message':'synthetic-secret-error-body'}})
    module=transport(monkeypatch,handle)
    with pytest.raises(module.ProviderError) as error:invoke(module)
    assert error.value.waiting_config is True
    assert len(attempts)==1 and 'synthetic-secret' not in str(error.value)


@pytest.mark.parametrize('data',[{'answer':17},{'other':'unexpected'},{'answer':'valid','extra':'not allowed'}])
def test_json_schema_validation_rejects_invalid_values(monkeypatch,data):
    module=transport(monkeypatch,lambda request:httpx.Response(200,json=completion(data)))
    with pytest.raises(module.ProviderError) as error:invoke(module)
    assert error.value.code=='invalid_output'


def test_response_size_is_bounded_at_two_mib(monkeypatch):
    module=transport(monkeypatch,lambda request:httpx.Response(200,content=b' '*(2*1024*1024+1)))
    with pytest.raises(module.ProviderError) as error:invoke(module)
    assert error.value.code=='response_too_large'


def test_keepalive_bytes_do_not_extend_total_deadline(monkeypatch):
    class Keepalive(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(200):
                await asyncio.sleep(0.01)
                yield b' '
    module=transport(monkeypatch,lambda request:httpx.Response(200,stream=Keepalive()))
    began=monotonic()
    with pytest.raises(module.ProviderError) as error:invoke(module,deadline=began+0.06)
    assert error.value.code=='timeout' and monotonic()-began<0.4


def test_network_errors_retry_within_the_attempt_limit(monkeypatch):
    attempts=[]
    def handle(request):
        attempts.append(1)
        raise httpx.ConnectError('synthetic-untrusted-error-text',request=request)
    module=transport(monkeypatch,handle)
    with pytest.raises(module.ProviderError) as error:invoke(module)
    assert error.value.code=='transport' and len(attempts)==3
    assert 'synthetic-untrusted' not in str(error.value)
