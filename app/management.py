"""Account, subscription and model administration endpoints."""
import sqlite3
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from . import accounts, ai, db, model_config, subscriptions, workbench, querying, clock

router=APIRouter(prefix='/api')

class NewUser(BaseModel):
    username:str=Field(pattern=r'^[a-zA-Z0-9_.-]{3,40}$')
    display_name:str=Field(min_length=1,max_length=60)
    password:str=Field(min_length=12,max_length=256)
    role:Literal['admin','user']='user'

class UserPatch(BaseModel):
    display_name:str|None=Field(default=None,min_length=1,max_length=60)
    password:str|None=Field(default=None,min_length=12,max_length=256)
    role:Literal['admin','user']|None=None
    enabled:bool|None=None

@router.get('/users')
def users(user=Depends(accounts.require_admin)):
    with db.connect() as c:rows=c.execute('SELECT * FROM users ORDER BY id').fetchall()
    return {'items':[accounts.public(r) for r in rows]}

@router.post('/users')
def create_user(body:NewUser,user=Depends(accounts.require_admin)):
    try:
        with db.connect() as c:
            ident=c.execute('INSERT INTO users(username,display_name,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?,?)',(body.username.strip().lower(),body.display_name.strip(),accounts.password_hash(body.password),body.role,db.now(),db.now())).lastrowid
            from .default_topics import ensure_for_user
            ensure_for_user(c, ident)
            row=c.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()
    except sqlite3.IntegrityError:raise HTTPException(409,'账号已存在')
    db.audit('创建账号',str(ident),'操作人：'+user['username'])
    return accounts.public(row)

@router.patch('/users/{ident}')
def update_user(ident:int,body:UserPatch,user=Depends(accounts.require_admin)):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()
        if not row:raise HTTPException(404,'账号不存在')
        value=dict(row)
        for key in ('display_name','role','enabled'):
            if getattr(body,key) is not None:value[key]=getattr(body,key)
        if row['role']=='admin' and row['enabled'] and (value['role']!='admin' or not value['enabled']):
            count=c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1 AND id!=?",(ident,)).fetchone()[0]
            if count==0:raise HTTPException(409,'必须保留至少一位启用的超级管理员')
        if body.password:value['password_hash']=accounts.password_hash(body.password)
        c.execute('UPDATE users SET display_name=?,role=?,enabled=?,password_hash=?,updated_at=? WHERE id=?',(value['display_name'],value['role'],value['enabled'],value['password_hash'],db.now(),ident))
        c.execute('DELETE FROM sessions WHERE user_id=?',(ident,))
    db.audit('更新账号',str(ident),'操作人：'+user['username'])
    return accounts.public(value)

class PasswordChange(BaseModel):
    current_password:str=Field(min_length=1,max_length=256)
    new_password:str=Field(min_length=12,max_length=256)

@router.put('/me/password')
def change_password(body:PasswordChange,response:Response,user=Depends(accounts.require_user)):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT password_hash FROM users WHERE id=?',(user['id'],)).fetchone()
        if not accounts.verify(body.current_password,row[0]):raise HTTPException(400,'当前密码不正确')
        c.execute('UPDATE users SET password_hash=?,updated_at=? WHERE id=?',(accounts.password_hash(body.new_password),db.now(),user['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
    response.delete_cookie('radar_session')
    db.audit('修改本人密码',str(user['id']))
    return {'status':'密码已更新，请重新登录'}

@router.get('/subscriptions')
def topic_list(user=Depends(accounts.require_user)):
    items=subscriptions.rows(user)
    return {'items':items,'total':len(items),'subscribers':len({x['user_id'] for x in items})}

@router.post('/subscriptions/preview')
def preview(body:subscriptions.Topic,user=Depends(accounts.require_user)):
    items=subscriptions.recommendations(user,[body.model_dump()])
    return {'items':items[:20],'total':len(items),'notice':'仅预览已入库的公共公告；保存并启用订阅后，关键词会参与所选已接入来源的后续采集；数据覆盖仍取决于网站内容及采集规则'}

@router.post('/subscriptions')
def create_topic(body:subscriptions.Topic,user=Depends(accounts.require_user)):
    with db.connect() as c:
        count=c.execute('''SELECT COUNT(*) FROM subscriptions AS s WHERE s.user_id=? AND s.deleted=0
            AND NOT EXISTS (SELECT 1 FROM subscription_defaults AS d WHERE d.user_id=s.user_id AND d.subscription_id=s.id)''',(user['id'],)).fetchone()[0]
        if count>=50:raise HTTPException(409,'每个账号最多50个自建订阅，预置主题不计入此限额')
        ident=c.execute('INSERT INTO subscriptions(user_id,config,enabled,created_at,updated_at) VALUES(?,?,?,?,?)',(user['id'],db.dumps(body.model_dump()),body.enabled,db.now(),db.now())).lastrowid
        workbench.request_subscription_check(c, user['id'])
    db.audit('创建订阅',str(ident),'操作人：'+user['username'])
    return subscriptions.get(ident,user)

@router.put('/subscriptions/{ident}')
def update_topic(ident:int,body:subscriptions.Topic,user=Depends(accounts.require_user)):
    topic=subscriptions.get(ident,user)
    with db.connect() as c:
        c.execute('UPDATE subscriptions SET config=?,enabled=?,updated_at=? WHERE id=?',(db.dumps(body.model_dump()),body.enabled,db.now(),ident))
        workbench.request_subscription_check(c, topic['user_id'])
    db.audit('更新订阅',str(ident),'操作人：'+user['username'])
    return subscriptions.get(ident,user)

@router.delete('/subscriptions/{ident}')
def delete_topic(ident:int,user=Depends(accounts.require_user)):
    topic=subscriptions.get(ident,user)
    with db.connect() as c:
        c.execute('UPDATE subscriptions SET deleted=1,enabled=0,updated_at=? WHERE id=?',(db.now(),ident))
        workbench.request_subscription_check(c, topic['user_id'])
    db.audit('归档订阅',str(ident),'操作人：'+user['username'])
    return {'status':'已归档'}

@router.get('/recommendations')
def recommendations(request:Request,subscription_id:int|None=None,q:str=Query('',max_length=150),terms:str=Query('',max_length=3000),term_mode:Literal['any','all']='any',status:str='',page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),sort:Literal['published_at','score']='score',user=Depends(accounts.require_user)):
    querying.validate_params('recommendations',request.query_params.keys())
    spec=querying.normalize('recommendations',{'q':q,'terms':terms,'term_mode':term_mode,'status':status,'sort':sort},clock.now())
    topics=[subscriptions.get(subscription_id,user)] if subscription_id else [t for t in subscriptions.rows(user) if t['enabled'] and t['user_id']==user['id']]
    items=[item for item in subscriptions.recommendations(user,topics,full=True) if querying.matches_notice(item,spec,clock.now())]
    for item in items:item.pop('content',None)
    if sort=='published_at':items.sort(key=lambda x:(x.get('published_at') or '',x['topic_score'],x['id']),reverse=True)
    return {'items':items[(page-1)*page_size:page*page_size],'total':len(items),'page':page,'page_size':page_size}

@router.get('/model-config')
def model_settings(user=Depends(accounts.require_admin)):return model_config.public()

@router.put('/model-config')
def save_model(body:model_config.ModelSettings,user=Depends(accounts.require_admin)):
    value=model_config.save(body);ai.STATE='待验证'
    db.audit('更新模型配置',body.mode,'操作人：'+user['username'])
    return value

@router.post('/model-config/test')
def test_model(user=Depends(accounts.require_admin)):
    from . import ai_job_requests
    result=ai_job_requests.wait(ai_job_requests.submit_connection(user)['id'],user)['result']
    db.audit('测试模型连接',result['model'],'操作人：'+user['username'])
    return {'status':'连接成功，输出格式校验通过','model':result['model'],'tested_at':db.now()}
