"""Server-only model credentials and explicit local/external provider modes."""
import base64
import hashlib
import ipaddress
import json
import os
import socket
from typing import Literal
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, model_validator
from . import db

class ModelSettings(BaseModel):
    mode:Literal['local','external','disabled']='local'
    base_url:str=Field(default='',max_length=500)
    model:str=Field(default='',max_length=100)
    api_key:str=Field(default='',max_length=1000)
    clear_key:bool=False
    response_format:Literal['json_schema','json_object','text']='json_object'
    auto_analyze:bool=False
    max_concurrency:int=Field(default=1,ge=1,le=8)

    @model_validator(mode='after')
    def validate_url(self):
        self.base_url=self.base_url.strip().rstrip('/')
        self.model=self.model.strip()
        if self.mode=='external':
            validate_endpoint(self.base_url)
            if not self.model:raise ValueError('请输入模型名称')
        if self.mode=='local' and self.base_url:
            validate_local_endpoint(self.base_url)
            if not self.model:raise ValueError('请输入本地模型名称')
        return self

def validate_endpoint(url,resolve=False):
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.port not in (None,443):
        raise ValueError('外部 API 必须使用不含账号、查询参数的 HTTPS 地址（443端口）')
    if p.path.endswith('/chat/completions'):raise ValueError('请填写 Base URL，不包含 /chat/completions')
    hostname=p.hostname.lower()
    if hostname=='localhost' or hostname.endswith(('.localhost','.local','.internal')):raise ValueError('外部 API 不能使用内网地址；本地模型请选择本地模式')
    try:addresses=[ipaddress.ip_address(hostname)]
    except ValueError:
        addresses=[]
        if resolve:
            try:addresses=[ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(hostname,443,type=socket.SOCK_STREAM)]
            except OSError as exc:raise ValueError('无法解析外部 API 域名') from exc
    if any(not addr.is_global for addr in addresses):raise ValueError('外部 API 地址必须是公网地址')

def validate_local_endpoint(url):
    """Local mode targets a model server on the company network. Only administrators save it; the
    container itself and link-local metadata addresses are refused."""
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('本地模型地址须为不含账号、查询参数的 http(s) 地址，例如 http://10.0.0.10:8000/v1')
    try:p.port
    except ValueError:raise ValueError('本地模型地址端口无效') from None
    if p.path.endswith('/chat/completions'):raise ValueError('请填写 Base URL，不包含 /chat/completions')
    hostname=p.hostname.lower()
    if hostname=='localhost' or hostname.endswith('.localhost'):
        raise ValueError('系统运行在容器中，localhost 指向本服务自身；请填写模型所在服务器的局域网 IP')
    try:address=ipaddress.ip_address(hostname)
    except ValueError:return
    if address.is_loopback or address.is_unspecified:
        raise ValueError('系统运行在容器中，127.0.0.1 指向本服务自身；请填写模型所在服务器的局域网 IP')
    if address.is_link_local or address.is_multicast:raise ValueError('该地址不能作为模型服务地址')

def cipher():
    secret=os.environ['SESSION_SECRET']
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('tender-radar/model-key/'+secret).encode()).digest()))

def stored():
    with db.connect() as c:
        # Unit tests may use the model module without bootstrapping the application.
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='model_settings'").fetchone():return {}
        row=c.execute('SELECT value FROM model_settings WHERE id=1').fetchone()
    return json.loads(row[0]) if row else {}

def runtime():
    value=stored();mode=value.get('mode','local')
    policy={k:value.get(k,default) for k,default in [('max_concurrency',1),('version',0),('generation_version',0)]}
    if mode=='local':
        if value.get('base_url'):
            # Administrator-entered local server takes precedence over the deployment .env.
            return {**policy,'mode':mode,'base_url':value['base_url'],'model':value.get('model',''),'api_key':_key(value),
                    'response_format':value.get('response_format','json_schema'),'auto_analyze':value.get('auto_analyze',True),'source':'page'}
        return {**policy,'mode':mode,'base_url':os.getenv('LLM_BASE_URL',''),'model':os.getenv('LLM_MODEL',''),'api_key':os.getenv('LLM_API_KEY',''),'response_format':'json_schema','auto_analyze':value.get('auto_analyze',True),'source':'env'}
    if mode=='disabled':return {**policy,'mode':mode,'base_url':'','model':'','api_key':'','auto_analyze':False}
    return {**value,**policy,'api_key':_key(value)}

def _key(value):
    return cipher().decrypt(value['encrypted_key'].encode()).decode() if value.get('encrypted_key') else ''

def public():
    value=stored();mode=value.get('mode','local')
    page=mode=='external' or (mode=='local' and bool(value.get('base_url')))
    return {'mode':mode,'base_url':value.get('base_url','') if page else '',
            'model':value.get('model','') if page else os.getenv('LLM_MODEL',''),
            'has_key':bool(value.get('encrypted_key')) if page else bool(os.getenv('LLM_API_KEY')),
            'local_source':('page' if page else 'env') if mode=='local' else None,
            'env_configured':bool(os.getenv('LLM_BASE_URL')),
            'response_format':value.get('response_format','json_object'),'auto_analyze':value.get('auto_analyze',mode=='local'),
            'updated_at':value.get('updated_at'),
            'max_concurrency':value.get('max_concurrency',1),'version':value.get('version',0),
            'docs':[{'name':'DeepSeek 接入文档','url':'https://api-docs.deepseek.com/zh-cn/'},{'name':'OpenAI Chat Completions 文档','url':'https://platform.openai.com/docs/api-reference/chat'}]}

def save(body):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT value FROM model_settings WHERE id=1').fetchone()
        old=json.loads(row[0]) if row else {}
        value=body.model_dump(exclude={'api_key','clear_key'})
        key=old.get('encrypted_key','')
        key_changed=body.clear_key or bool(body.api_key)
        if body.clear_key:key=''
        elif body.api_key:key=cipher().encrypt(body.api_key.encode()).decode()
        elif old.get('base_url') and old.get('base_url')!=body.base_url:key='';key_changed=True
        semantic_changed=key_changed or any(old.get(k,default)!=value[k] for k,default in [('mode','local'),('base_url',''),('model',''),('response_format','json_object')])
        value.update(encrypted_key=key,updated_at=db.now(),version=old.get('version',0)+1,
                     generation_version=old.get('generation_version',0)+int(semantic_changed))
        c.execute('INSERT INTO model_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(db.dumps(value),))
    return public()
