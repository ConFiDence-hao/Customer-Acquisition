"""REP path matching distinguishes reserved escapes from URI delimiters."""
import pytest

from app.robots_rules import PublicRobots


AGENT = 'TenderRadar/1.0 (+internal procurement research; low-rate public pages)'


def test_reserved_slash_allow_rule_cannot_override_a_literal_slash_disallow():
    robots = PublicRobots('User-agent: *\nDisallow: /private/data\nAllow: /private%2Fdata\n')
    assert not robots.can_fetch(AGENT, 'https://example.test/private/data')
    assert robots.can_fetch(AGENT, 'https://example.test/private%2Fdata')


@pytest.mark.parametrize('escaped', ['%2F', '%2f'])
def test_reserved_encoding_hex_case_is_normalized_without_decoding(escaped):
    robots = PublicRobots('User-agent: *\nDisallow: /a%2fb\nAllow: /a/b\n')
    assert not robots.can_fetch(AGENT, 'https://example.test/a'+escaped+'b')
    assert robots.can_fetch(AGENT, 'https://example.test/a/b')


@pytest.mark.parametrize('path', ['/private/~ABC', '/private/%7e%41%42%43'])
def test_only_unreserved_ascii_escapes_are_decoded(path):
    robots = PublicRobots('User-agent: *\nDisallow: /private/%7EABC\n')
    assert not robots.can_fetch(AGENT, 'https://example.test'+path)


@pytest.mark.parametrize('rule', ['/环境/公告', '/%E7%8E%AF%E5%A2%83/%E5%85%AC%E5%91%8A'])
@pytest.mark.parametrize('path', ['/环境/公告', '/%e7%8e%af%e5%a2%83/%e5%85%ac%e5%91%8a'])
def test_unicode_and_percent_encoded_utf8_octets_match(rule, path):
    robots = PublicRobots('User-agent: *\nDisallow: '+rule+'\n')
    assert not robots.can_fetch(AGENT, 'https://example.test'+path)


def test_percent_encoded_star_remains_literal_and_cannot_expand_a_rule():
    robots = PublicRobots('User-agent: *\nDisallow: /doc/literal%2A.txt\n')
    assert not robots.can_fetch(AGENT, 'https://example.test/doc/literal%2a.txt')
    assert not robots.can_fetch(AGENT, 'https://example.test/doc/literal*.txt')
    assert robots.can_fetch(AGENT, 'https://example.test/doc/literal-anything.txt')


def test_percent_encoded_dollar_is_not_an_end_anchor():
    robots = PublicRobots('User-agent: *\nDisallow: /doc/report%24\n')
    assert not robots.can_fetch(AGENT, 'https://example.test/doc/report%24')
    assert not robots.can_fetch(AGENT, 'https://example.test/doc/report$')
    assert robots.can_fetch(AGENT, 'https://example.test/doc/report')


def test_encoded_percent_does_not_trigger_a_second_decode():
    robots = PublicRobots('User-agent: *\nDisallow: /a%252Fb\nAllow: /a%2Fb\n')
    assert not robots.can_fetch(AGENT, 'https://example.test/a%252Fb')
    assert robots.can_fetch(AGENT, 'https://example.test/a%2Fb')


@pytest.mark.parametrize('directive', ['Disallow:', 'Allow:'])
def test_empty_directive_ends_agent_group_before_following_wildcard(directive):
    robots = PublicRobots('User-agent: TenderRadar\n'+directive+'\n\nUser-agent: *\nDisallow: /private\n')
    assert robots.can_fetch(AGENT, 'https://example.test/private')
    assert not robots.can_fetch('OtherBot/1.0', 'https://example.test/private')


def test_explicit_empty_final_group_does_not_inherit_wildcard_rules():
    robots = PublicRobots('User-agent: *\nDisallow: /private\nUser-agent: TenderRadar\n')
    assert robots.can_fetch(AGENT, 'https://example.test/private')
    assert not robots.can_fetch('OtherBot/1.0', 'https://example.test/private')


def test_consecutive_agents_share_rules_but_same_agent_groups_are_combined():
    robots = PublicRobots('User-agent: TenderRadar\nUser-agent: SecondBot\nDisallow: /one\nUser-agent: TenderRadar\nDisallow: /two\nAllow: /two/public\n')
    assert not robots.can_fetch(AGENT, 'https://example.test/one')
    assert not robots.can_fetch(AGENT, 'https://example.test/two')
    assert robots.can_fetch(AGENT, 'https://example.test/two/public')
    assert not robots.can_fetch('SecondBot/1.0', 'https://example.test/one')
    assert robots.can_fetch('SecondBot/1.0', 'https://example.test/two')


def test_unescaped_wildcard_and_end_anchor_keep_existing_semantics():
    robots = PublicRobots('User-agent: *\nDisallow: /private/*.do$\nAllow: /private/public.do$\n')
    assert not robots.can_fetch(AGENT, 'https://example.test/private/query.do')
    assert robots.can_fetch(AGENT, 'https://example.test/private/query.do?public=1')
    assert robots.can_fetch(AGENT, 'https://example.test/private/public.do')
