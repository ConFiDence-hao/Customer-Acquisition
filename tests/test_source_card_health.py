"""Compact source status is derived from actual run state, not enablement."""
import pytest

from app.source_health import public_health


@pytest.mark.parametrize('source,state', [
    ({'enabled': True, 'status': '正常'}, 'success'),
    ({'enabled': True, 'status': '部分可用'}, 'failure'),
    ({'enabled': True, 'status': '异常'}, 'failure'),
    ({'enabled': True, 'status': '待采集'}, 'pending'),
    ({'enabled': True}, 'pending'),
    ({'enabled': False, 'adapter_available': False, 'status': '未接入'}, 'failure'),
    ({'enabled': False, 'adapter_available': True}, 'paused'),
    ({'enabled': False, 'status': '管理员停用'}, 'paused'),
    ({'enabled': False, 'status': '已停用'}, 'paused'),
    ({'enabled': False, 'status': '正常', 'adapter_available': True}, 'paused'),
    ({'enabled': True, 'status': '异常', 'last_success': '2026-10-01'}, 'failure'),
    ({'enabled': True, 'status': '待采集', 'last_error': '旧OCR识别失败'}, 'pending'),
])
def test_public_health_adds_safe_run_state_without_changing_availability_semantics(source, state):
    health = public_health(source)
    assert health['collection_state'] == state
    assert {'availability', 'availability_label', 'availability_note', 'issues'} <= health.keys()


def test_collection_state_does_not_publish_raw_status_or_private_config():
    health = public_health({'enabled': True, 'status': 'private-marker',
                            'url': 'https://example.com/?token=secret', 'api_key': 'secret'})
    assert health['collection_state'] == 'pending'
    assert all(secret not in str(health) for secret in ('private-marker', 'token=secret', 'api_key'))
