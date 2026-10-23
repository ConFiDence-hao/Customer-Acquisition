"""Robots groups with wildcard/end-anchor rules and longest-match precedence."""
from urllib.parse import quote, urlsplit
import re


_UNRESERVED = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')


def _normalize_path(value: str, *, pattern: bool = False) -> str:
    """Compare UTF-8 octets without turning reserved escapes into delimiters.

    In particular, %2F is distinct from '/', and %2A/%24 in rules are literal
    characters, never the REP wildcard or end anchor. Hex digits are canonical.
    """
    def escape(match):
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else '%' + match.group(1).upper()

    value = re.sub(r'%([0-9a-fA-F]{2})', escape, value)
    value = ''.join(char if ord(char) < 128 else quote(char, safe='') for char in value)
    if pattern:
        end = value.endswith('$')
        value = value.removesuffix('$').replace('$', '%24') + ('$' if end else '')
    else:
        value = value.replace('*', '%2A').replace('$', '%24')
    return value


class PublicRobots:
    def __init__(self,text: str):
        self.groups=[]
        agents=[];rules=[];seen_directive=False
        for line in text.splitlines():
            key,sep,value=line.partition('#')[0].partition(':')
            if not sep:continue
            key=key.strip().lower();value=value.strip()
            if key=='user-agent':
                if seen_directive:
                    self.groups.append((agents,rules));agents=[];rules=[];seen_directive=False
                if value:agents.append(value.lower())
            elif key in ('allow','disallow') and agents:
                # An empty directive has no path restriction, but still closes
                # this group's agent lines before the next User-agent record.
                seen_directive=True
                if value:rules.append((key=='allow',_normalize_path(value,pattern=True)))
        if agents:self.groups.append((agents,rules))

    def can_fetch(self,user_agent: str,url: str) -> bool:
        agent=user_agent.lower()
        scored=[(max((0 if a=='*' else len(a) for a in names if a=='*' or a in agent),default=-1),rules)
                for names,rules in self.groups]
        score=max((s for s,_ in scored),default=-1)
        path=urlsplit(url).path or '/'
        if urlsplit(url).query:path+='?'+urlsplit(url).query
        path=_normalize_path(path)
        matches=[]
        for specificity,rules in scored:
            if specificity<0 or specificity!=score:continue
            for allow,pattern in rules:
                end=pattern.endswith('$')
                expr='^'+'.*'.join(re.escape(x) for x in pattern.removesuffix('$').split('*'))+('$' if end else '')
                if re.search(expr,path):matches.append((len(pattern.replace('*','').removesuffix('$')),allow))
        return not matches or max(matches)[1]
