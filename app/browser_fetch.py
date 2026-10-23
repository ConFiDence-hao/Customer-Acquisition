"""Bounded rendering of verified public pages using a separate temporary session."""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit,parse_qs


class BrowserReadError(RuntimeError):
    pass


def permitted_page(url: str) -> bool:
    try:
        p=urlsplit(url)
        if p.scheme!='https' or p.port not in (None,443) or p.username or p.password or p.fragment:
            return False
        if p.hostname=='www.bidizhaobiao.com':
            if re.fullmatch(r'/info-\d+\.html',p.path):return not p.query
            query=parse_qs(p.query,keep_blank_values=True)
            return bool(re.fullmatch(r'/(?:zbyg|zbgg|cgyx)/?',p.path)) and (not p.query or
                set(query)=={'page'} and len(query['page'])==1 and bool(re.fullmatch(r'[1-9]\d{0,3}',query['page'][0])))
        if p.hostname in ('huanbao.bjx.com.cn','news.bjx.com.cn','ljfd.bjx.com.cn','gfcl.bjx.com.cn'):
            return not p.query and bool(re.fullmatch(r'/news/\d{8}/\d+\.shtml|/zb/(?:[2-5]/)?',p.path))
    except (TypeError,ValueError):
        pass
    return False


def validate_snapshot(snapshot: dict) -> str:
    if snapshot.get('challenge'):
        if re.search('安全认证|IP存在访问异常',snapshot.get('visible_text','')):
            raise BrowserReadError('浏览器页面要求IP安全认证，当前采集机访问被网站拦截，保留已有线索')
        raise BrowserReadError('浏览器页面要求访问验证，保留公开列表线索，需人工在原网站查阅')
    if snapshot.get('login'):
        raise BrowserReadError('浏览器页面要求登录后阅读，保留公开列表线索')
    html=snapshot.get('html','')
    if not html or len(html)>4_000_000:
        raise BrowserReadError('浏览器正文为空或超过大小限制')
    return html


def resource_allowed(url: str,root_host: str,kind: str,allowed) -> bool:
    """Publisher assets are explicit; data requests also need verified robots rules."""
    try:
        if root_host!='www.bidizhaobiao.com' and kind=='image':return False
        p=urlsplit(url)
        if p.scheme!='https' or p.port not in (None,443) or p.username or p.password:return False
        assets=({'www.bidizhaobiao.com','storage-cdn.bidizhaobiao.com','ip.bidizhaobiao.com'}
                if root_host=='www.bidizhaobiao.com' else
                {'huanbao.bjx.com.cn','news.bjx.com.cn','ljfd.bjx.com.cn','gfcl.bjx.com.cn',
                 'www.bjx.com.cn','img.bjx.com.cn','img01.mybjx.net','img.mybjx.net','g.alicdn.com'})
        if p.hostname not in assets:return False
        if kind in ('fetch','xhr'):
            return p.hostname==root_host and bool(allowed(url))
        return kind not in ('media','font')
    except Exception:return False


SNAPSHOT_JS=r'''() => {
    const visible = el => { const s=getComputedStyle(el),r=el.getBoundingClientRect();
        return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0; };
    const text=document.body?.innerText || '';
    const challengeText=/访问验证|人机验证|安全认证|IP存在访问异常|请完成.{0,12}验证|拖动.{0,20}滑块|滑块.{0,20}验证|请按住滑块/.test(text);
    const challengeNode=[...document.querySelectorAll('[id*="captcha" i],[class*="captcha" i],iframe[src*="captcha" i],[id*="nc_1"]')]
        .some(el=>visible(el));
    const login=[...document.querySelectorAll('[role="dialog"],dialog,[class*="login" i],[id*="login" i]')]
        .some(el=>visible(el) && /登录|注册|login|sign in/i.test(el.innerText||'') &&
            (getComputedStyle(el).position==='fixed' || el.getAttribute('role')==='dialog' || el.tagName==='DIALOG'));
    const clone=document.documentElement.cloneNode(true);
    const originals=[...document.documentElement.querySelectorAll('*')];
    const copies=[...clone.querySelectorAll('*')];
    originals.forEach((el,i)=>{const s=getComputedStyle(el);
        if (['SCRIPT','STYLE','NOSCRIPT'].includes(el.tagName)||s.display==='none'||s.visibility==='hidden') copies[i]?.remove();});
    return {visible_text:text,challenge:challengeText||challengeNode,login,html:clone.outerHTML};
}'''


class BrowserReader:
    def __init__(self,max_pages: int=40):
        self.max_pages=max_pages
        self.pages=0
        self.network_requests=0
        self._runtime=None
        self._browser=None
        self._context=None

    def _start(self):
        if self._context is not None:return
        try:
            from playwright.sync_api import sync_playwright
            self._runtime=sync_playwright().start()
            channel=os.getenv('RADAR_BROWSER_CHANNEL','').strip()
            options={'headless':True,'timeout':20000}
            if channel:
                if channel not in ('chrome','msedge','chromium'):
                    raise BrowserReadError('浏览器组件未就绪：不支持的浏览器通道')
                options['channel']=channel
            try:self._browser=self._runtime.chromium.launch(**options)
            except Exception:
                if channel or os.name!='nt':raise
                self._browser=self._runtime.chromium.launch(headless=True,channel='msedge',timeout=20000)
            self._context=self._browser.new_context(locale='zh-CN',timezone_id='Asia/Shanghai',
                user_agent='TenderRadar/1.0 (+internal procurement research; low-rate public pages)',
                accept_downloads=False,service_workers='block')
        except Exception as exc:
            self.close()
            raise BrowserReadError('浏览器组件未就绪，请安装 Chromium 和运行依赖') from exc

    def get(self,url: str,allowed) -> str:
        if not permitted_page(url):raise BrowserReadError('浏览器页面超出已核实公开范围')
        if not allowed(url):raise BrowserReadError('站点robots规则不允许浏览器读取此页面')
        if self.pages>=self.max_pages:raise BrowserReadError('本轮已达到浏览器渲染页数上限，保留列表线索')
        if self.network_requests>=500:raise BrowserReadError('本轮已达到浏览器请求数量上限，保留列表线索')
        self._start()
        self.pages+=1
        page=self._context.new_page()
        root_host=urlsplit(url).hostname
        denied=[]
        def route_request(route):
            request=route.request
            try:
                self.network_requests+=1
                if self.network_requests>500 or request.method not in ('GET','HEAD'):
                    return route.abort()
                p=urlsplit(request.url)
                host=p.hostname or ''
                if p.scheme!='https' or p.port not in (None,443) or p.username or p.password:
                    return route.abort()
                if request.is_navigation_request():
                    if not permitted_page(request.url) or host!=root_host or not allowed(request.url):
                        denied.append(True);return route.abort()
                else:
                    # Only publisher/CDN resources needed to render the verified page.
                    if not resource_allowed(request.url,root_host,request.resource_type,allowed):return route.abort()
                return route.continue_()
            except Exception:
                denied.append(True);return route.abort()
        page.route('**/*',route_request)
        try:
            response=page.goto(url,wait_until='domcontentloaded',timeout=25000)
            if self.network_requests>500:raise BrowserReadError('本轮已达到浏览器请求数量上限，保留列表线索')
            if response and response.status in (401,403,429):
                raise BrowserReadError(f'浏览器站点限制访问（HTTP {response.status}），保留列表线索')
            try:
                page.wait_for_function(r'''() => [...document.querySelectorAll('#article,.cc-article,.article-content,.article-content-main,.cp-title a[href*="info-"],.article-content-detail,.content-detail')]
                    .some(el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0&&(el.tagName==='A'||(el.innerText||'').trim().length>=80);})
                    || /访问验证|人机验证|安全认证|IP存在访问异常|请按住滑块/.test(document.body?.innerText||'')''',timeout=8000)
            except Exception:
                pass  # Snapshot determines whether this is a real article or an empty shell.
            # Publisher scripts may replace an initial SEO shell with a verification page.
            # Inspect after pending network work instead of accepting that intermediate DOM.
            try:page.wait_for_load_state('networkidle',timeout=8000)
            except Exception:pass
            snapshot=page.evaluate(SNAPSHOT_JS)
            if self.network_requests>500:raise BrowserReadError('本轮已达到浏览器请求数量上限，保留列表线索')
            validate_snapshot(snapshot)
            if denied:raise BrowserReadError('浏览器页面发生未核实跳转，停止读取')
            if not permitted_page(page.url) or urlsplit(page.url).hostname!=root_host:
                raise BrowserReadError('浏览器页面发生未核实跳转，停止读取')
            return validate_snapshot(snapshot)
        except BrowserReadError:
            raise
        except Exception as exc:
            if self.network_requests>500:
                raise BrowserReadError('本轮已达到浏览器请求数量上限，保留列表线索') from exc
            raise BrowserReadError('浏览器页面加载失败或超时，保留已取得线索') from exc
        finally:
            page.close()

    def close(self):
        for resource in (self._context,self._browser,self._runtime):
            if resource is not None:
                try:
                    if resource is self._runtime:resource.stop()
                    else:resource.close()
                except Exception:pass
        self._context=self._browser=self._runtime=None
