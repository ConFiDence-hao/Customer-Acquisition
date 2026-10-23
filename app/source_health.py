"""Public source health uses fixed messages, never raw diagnostics or URLs."""
import re


ISSUES = {
    'text_only': ('已保留网页文字', '网页文字已采集；图片未下载或识别，图片中的内容需查看来源原文。'),
    'content_partial': ('已采集部分内容', '已保留可取得的正文和列表信息，缺失信息未补写；完整内容请核对来源原文。'),
    'pdf_truncated': ('PDF已按上限截断', '已保留处理上限内的PDF正文，可能仅包含前30页或已完成页面；其余内容请查看来源原附件。'),
    'ocr_image_blocked': ('原图禁止自动读取', '网页中的图片由独立图片服务提供，其robots规则禁止自动下载，当前未识别图片内容；请打开原网页查阅。'),
    'ip_security_check': ('IP安全认证', '当前采集机访问被网站安全认证拦截，未取得公开公告正文；需在允许的访问条件下核查。'),
    'browser_verification': ('浏览器验证', '页面要求浏览器或人机访问验证，本轮未取得完整公开内容；需核查网站访问条件。'),
    'login_required': ('需要登录', '公告或附件要求登录，当前未读取登录后的内容；需核实公开入口或授权渠道。'),
    'browser_unavailable': ('浏览器组件未就绪', '浏览器读取组件尚未安装或不可用，需由维护人员完成运行环境配置。'),
    'browser_budget_exhausted': ('浏览器页数已达上限', '本轮浏览器渲染页数达到限制，已取得的列表线索保留，未读取的详情需后续核查。'),
    'browser_requests_exhausted': ('浏览器请求已达上限', '本轮浏览器网络请求达到限制，已取得的列表保留，其余正文留待后续采集。'),
    'ocr_unavailable': ('OCR组件未就绪', '文字识别组件或模型尚未就绪，图片正文仅保留原图查阅；需由维护人员检查识别环境。'),
    'ocr_failed': ('OCR识别失败', '图片或扫描件的文字识别未完成，不能将未识别内容作为正文；请核对原附件。'),
    'image_body': ('正文为图片', '正文为图片或扫描PDF，当前未取得足够文字证据；可打开原页面或附件查阅。'),
    'detail_missing': ('详情正文未取得', '详情正文容器缺失、内容不足或暂不可读，当前仅使用已取得的公开线索。'),
    'pagination_failed': ('补页未完成', '补充列表或后续分页未完成；已取得的前页候选保留，覆盖范围受到限制。'),
    'access_restricted': ('网站限制访问', '网站访问规则或响应限制了自动读取；需核查允许使用的公开渠道。'),
    'network_failed': ('网站连接失败', '网站连接超时或暂时不可达，本轮未完成读取。'),
    'read_incomplete': ('读取未完成', '本轮读取未完成，具体原因尚未分类；由管理员查看运行记录核查。'),
}

_RULES = (
    ('text_only', r'图片未下载|图片中的内容'),
    ('pdf_truncated', r'PDF.{0,80}(?:截断|前30页|页数上限)|(?:前30页|处理上限).{0,40}PDF'),
    ('content_partial', r'正文不完整|内容不完整|仅保留公开列表信息|仅取得较少|仅取得列表信息|仅取得公告页基本信息|未取得新的详情正文'),
    ('browser_requests_exhausted', r'浏览器请求数量上限'),
    ('ocr_image_blocked', r'原图robots规则禁止自动读取'),
    ('ip_security_check', r'安全认证|IP(?:存在访问)?异常'),
    ('browser_unavailable', r'(?:浏览器|playwright|chromium).{0,40}(?:未安装|不可用|缺失|未就绪|未准备|安装失败|not installed|not ready|unavailable|missing)|executable doesn.t exist'),
    ('browser_budget_exhausted', r'浏览器.{0,25}(?:页数|预算).{0,15}(?:上限|耗尽)|(?:达到|超过).{0,15}浏览器.{0,20}上限'),
    ('browser_verification', r'访问验证|人机验证|浏览器验证|验证码|captcha|CF_APP_WAF|checking your browser'),
    ('login_required', r'要求登录|需要登录|请登录|登录后|登录界面|未登录|登录Token|login required|sign.?in required'),
    ('ocr_unavailable', r'OCR.{0,15}(?:组件|模型|运行环境).{0,15}(?:未安装|未就绪|缺失|不可用|安装失败)'),
    ('ocr_failed', r'OCR.{0,30}(?:失败|未完成|不可用|未就绪|超时|错误|failed|unavailable)|(?:识别失败|文字识别未完成)'),
    ('image_body', r'扫描\s*PDF|PDF\s*扫描页|图片型\s*PDF|正文(?:为|是)图片|图片正文|scanned.?pdf|image.?only'),
    ('detail_missing', r'(?:正文|详情).{0,25}(?:缺失|不足|未取得|不可读|未获取|为空)|正文容器|(?:正文|详情页?)结构(?:发生)?变化'),
    ('pagination_failed', r'补充列表|补页|分页.{0,20}(?:失败|未完成|未读|受限)|后续页.{0,20}(?:失败|未完成|未读|受限)'),
    ('access_restricted', r'robots|HTTP\s*(?:401|403|405|429)|限制访问|拒绝访问'),
    ('network_failed', r'连接超时|连接失败|不可达|网络.{0,15}(?:超时|失败)|connect(?:ion)?.{0,15}(?:timeout|error)|timed out'),
)


def public_issues(source):
    """Accept only known issue codes; labels and notes always come from here."""
    codes = []
    structured = source.get('issues')
    if isinstance(structured, list):
        for value in structured:
            code = value.get('code') if isinstance(value, dict) else value
            if isinstance(code, str) and code in ISSUES and code not in codes:
                codes.append(code)
    error = source.get('last_error')
    # A private URL's path/query is not evidence of an issue category.
    text = re.sub(r'https?://\S+', '', error, flags=re.I) if isinstance(error, str) else ''
    for code, pattern in _RULES:
        if code == 'ocr_failed' and 'ocr_unavailable' in codes:
            continue
        if re.search(pattern, text, re.I) and code not in codes:
            codes.append(code)
    # IP security interception is the primary cause even after structured issues.
    codes.sort(key=lambda code: code != 'ip_security_check')
    return [{'code': code, 'label': ISSUES[code][0], 'note': ISSUES[code][1]} for code in codes]


def failure_note(source):
    """Compatibility hook for collectors; fixed safe notes only."""
    issues = public_issues(source)
    return '；'.join(issue['label'] + '：' + issue['note'] for issue in issues) if issues else ISSUES['read_incomplete'][1]


def _availability_health(source, requires_authorization=False):
    if not source.get('enabled'):
        if source.get('adapter_available'):
            return {'availability': 'paused', 'availability_label': '未启用采集',
                    'availability_note': '采集规则已配置，但当前未启用；历史资讯仍保留。', 'issues': []}
        if requires_authorization:
            return {'availability': 'authorization', 'availability_label': '待授权接入',
                    'availability_note': '已登记参考入口；需核实授权数据渠道及采集规则，当前不自动提供资讯。', 'issues': []}
        return {'availability': 'pending', 'availability_label': '尚未接入',
                'availability_note': '已登记参考入口；公告栏目及采集规则尚待适配验证，当前不自动提供资讯。', 'issues': []}
    issues = public_issues(source)
    failed = source.get('status') == '异常'
    if issues or failed or source.get('status') == '部分可用':
        if not issues:
            label, note = ISSUES['read_incomplete']
            issues = [{'code': 'read_incomplete', 'label': label, 'note': note}]
        return {'availability': 'unavailable' if failed else 'partial',
                'availability_label': issues[0]['label'],
                'availability_note': '已启用采集；' + issues[0]['note'], 'issues': issues}
    return {'availability': 'active', 'availability_label': '已启用采集',
            'availability_note': '已纳入自动采集计划，按计划检查公开公告。', 'issues': []}


def public_health(source, requires_authorization=False):
    """Keep availability/filter semantics and expose a safe actual-run state."""
    health = _availability_health(source, requires_authorization)
    status = source.get('status')
    if not source.get('enabled'):
        state = ('paused' if source.get('adapter_available') or status in ('已停用', '管理员停用')
                 else 'failure')
    elif status == '待采集':
        # Re-enabling a source may leave an old error in storage. A scheduled
        # source is pending until a new run supplies the actual result.
        state = 'pending'
    elif health['availability'] in ('partial', 'unavailable'):
        state = 'failure'
    elif status == '正常':
        state = 'success'
    else:
        state = 'pending'
    return {**health, 'collection_state': state}
