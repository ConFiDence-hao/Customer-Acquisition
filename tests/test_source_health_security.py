"""Public health classifies safe evidence without publishing private diagnostics."""
import json

import pytest

from app import source_health


@pytest.mark.parametrize('message', ['安全认证', 'IP存在访问异常', 'IP异常'])
def test_ip_security_diagnostics_use_fixed_public_messages(message):
    source = {
        'enabled': True, 'status': '部分可用',
        'last_error': message + ' private-diagnostic-token https://private.example/key?token=secret-token',
    }
    health = source_health.public_health(source)

    assert [issue['code'] for issue in health['issues']] == ['ip_security_check']
    assert health['availability'] == 'partial'
    assert health['availability_label'] == 'IP安全认证'
    rendered = json.dumps(health, ensure_ascii=False)
    assert all(secret not in rendered for secret in (
        'private-diagnostic-token', 'private.example', 'secret-token',
    ))


@pytest.mark.parametrize('message', ['访问验证', '人机验证', '浏览器验证', '验证码'])
def test_generic_verification_keeps_its_existing_browser_category(message):
    assert [issue['code'] for issue in source_health.public_issues({'last_error': message})] == ['browser_verification']


@pytest.mark.parametrize('issues,message', [
    (['browser_verification', 'login_required', 'ip_security_check'], ''),
    (['login_required', {'code': 'ip_security_check', 'note': 'private-note'}, 'browser_verification'], ''),
    (['browser_verification', 'login_required'], 'IP存在访问异常 private-raw-token'),
    (['login_required'], '浏览器组件未安装；访问验证；安全认证 private-raw-token'),
])
def test_ip_security_category_precedes_all_other_issues(issues, message):
    source = {'enabled': True, 'status': '部分可用', 'issues': issues, 'last_error': message}
    health = source_health.public_health(source)

    assert health['issues'][0]['code'] == 'ip_security_check'
    assert health['availability_label'] == 'IP安全认证'
    assert sum(issue['code'] == 'ip_security_check' for issue in health['issues']) == 1
    assert {'login_required', 'browser_verification'}.issubset({issue['code'] for issue in health['issues']})
    rendered = json.dumps(health, ensure_ascii=False)
    assert 'private-note' not in rendered and 'private-raw-token' not in rendered


@pytest.mark.parametrize('message', [
    '原正文为空', '正文为空', '正文结构变化', '详情结构变化',
    '正文或详情结构变化', '公告详情页结构发生变化',
])
def test_missing_or_changed_detail_body_is_classified(message):
    assert [issue['code'] for issue in source_health.public_issues({'last_error': message})] == ['detail_missing']


@pytest.mark.parametrize('message', [
    '列表结构变化', '公告列表结构发生变化', '列表结构变化，详情候选保留',
])
def test_list_structure_change_does_not_claim_detail_body_is_missing(message):
    assert source_health.public_issues({'last_error': message}) == []


@pytest.mark.parametrize('url', [
    'https://private.example/安全认证?token=secret-token',
    'https://private.example/IP异常?token=secret-token',
    'https://private.example/正文结构变化?token=secret-token',
    'HTTPS://private.example/验证码?token=secret-token',
])
def test_keywords_inside_private_urls_are_not_diagnostic_evidence(url):
    source = {'enabled': True, 'status': '部分可用', 'last_error': '读取未完成 ' + url}

    assert source_health.public_issues(source) == []
    health = source_health.public_health(source)
    assert [issue['code'] for issue in health['issues']] == ['read_incomplete']
    assert 'private.example' not in json.dumps(health)
    assert 'secret-token' not in json.dumps(health)


def test_structured_codes_remain_authoritative_and_ignore_untrusted_fields():
    source = {
        'enabled': True, 'status': '部分可用',
        'issues': [
            {'code': 'login_required', 'label': 'secret-label', 'note': 'secret-note',
             'url': 'https://private.example/secret-token'},
            'detail_missing', {'code': 'login_required'}, {'code': 'private-unknown-code'}, None,
        ],
        'last_error': '访问验证 private-diagnostic-token',
    }

    health = source_health.public_health(source)
    assert [issue['code'] for issue in health['issues']] == [
        'login_required', 'detail_missing', 'browser_verification',
    ]
    assert health['availability_label'] == '需要登录'
    assert all(set(issue) == {'code', 'label', 'note'} for issue in health['issues'])
    rendered = json.dumps(health, ensure_ascii=False)
    assert all(secret not in rendered for secret in (
        'secret-label', 'secret-note', 'private.example', 'secret-token',
        'private-unknown-code', 'private-diagnostic-token',
    ))


def test_unknown_error_and_codes_keep_the_safe_fallback():
    source = {
        'enabled': True, 'status': '部分可用',
        'last_error': 'unknown private-diagnostic-token',
        'issues': [{'code': 'unknown-code', 'note': 'private-note'}],
    }
    health = source_health.public_health(source)

    assert [issue['code'] for issue in health['issues']] == ['read_incomplete']
    assert health['availability_label'] == '读取未完成'
    rendered = json.dumps(health, ensure_ascii=False)
    assert 'private-diagnostic-token' not in rendered and 'private-note' not in rendered


def test_scanned_pdf_page_without_ocr_text_is_an_image_body_issue():
    issues = source_health.public_issues({'last_error': '第3页：PDF扫描页OCR未识别出文字；第2页：PDF扫描页OCR未识别出文字'})
    assert [issue['code'] for issue in issues] == ['image_body']
