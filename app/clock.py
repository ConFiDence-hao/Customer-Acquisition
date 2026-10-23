"""Trusted UTC anchored to monotonic time; all business dates use Beijing time."""
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from statistics import median
from threading import Event, RLock, Thread
from time import monotonic as _monotonic

import httpx

UTC = timezone.utc
TZ = timezone(timedelta(hours=8))
PEERS = ('https://new.ebidding.cecep.cn/', 'https://www.hbzhan.com/', 'https://www.solidwaste.com.cn/')
INITIAL_SYNC_BUDGET = 8.0
SYNC_INTERVAL = 900.0
MAX_UNVERIFIED = 21600.0


class ClockUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TimeSample:
    peer: str
    utc: datetime
    received_monotonic: float


def parse_sample(*, peer, date, age, rtt, received_monotonic, status_code, redirected=False):
    if peer not in PEERS or not 200 <= status_code < 300 or redirected or not 0 <= rtt <= 2:
        raise ValueError('invalid time response')
    try:
        cache_age = int(age or '0')
        instant = parsedate_to_datetime(date)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('invalid time header') from None
    if not 0 <= cache_age <= 2 or instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ValueError('untrusted cached time')
    return TimeSample(peer, instant.astimezone(UTC) + timedelta(seconds=cache_age + rtt / 2), received_monotonic)


def fetch_sample(peer):
    # HEAD carries no business data; certificate validation and fixed destination stay enabled.
    with httpx.Client(timeout=2.0, follow_redirects=False, verify=True) as client:
        began = _monotonic()
        response = client.head(peer, headers={'Cache-Control': 'no-cache', 'User-Agent': 'TenderRadar-Clock/1.0'})
        received = _monotonic()
    return parse_sample(peer=peer, date=response.headers.get('date', ''), age=response.headers.get('age', '0'),
                        rtt=received - began, received_monotonic=received,
                        status_code=response.status_code, redirected=bool(response.history))


class TrustedClock:
    def __init__(self, *, monotonic=_monotonic, host_now=None, fetcher=fetch_sample):
        self.monotonic = monotonic
        self.host_now = host_now or (lambda: datetime.now(UTC))
        self.fetcher = fetcher
        self.lock = RLock()
        self.anchor = None
        self.anchor_tick = None
        self.verified_tick = None
        self.last_attempt = None
        self.correction = 0.0
        self.last_returned = None
        self.host_delta = None
        self.error = None
        self.agreeing_peers = []
        self.stop = Event()
        self.thread = None

    def _value(self, tick):
        if self.anchor is None:
            raise ClockUnavailable('北京时间尚未可信校准，业务操作暂不可用')
        elapsed = max(0.0, tick - self.anchor_tick)
        return self.anchor + timedelta(seconds=elapsed - min(self.correction, elapsed * 0.1))

    def utc_now(self):
        with self.lock:
            value = self._value(self.monotonic())
            # A verified correction is gradual; an anomalous monotonic reading never moves facts backward.
            value = max(value, self.last_returned) if self.last_returned else value
            self.last_returned = value
            return value

    def now(self):
        return self.utc_now().astimezone(TZ)

    def today(self):
        return self.now().date()

    def synchronize(self):
        pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='clock-peer')
        futures = [pool.submit(self.fetcher, peer) for peer in PEERS]
        samples = []
        try:
            finished, pending = wait(futures, timeout=INITIAL_SYNC_BUDGET)
            for future in finished:
                try:
                    sample = future.result()
                    if isinstance(sample, TimeSample) and sample.peer in PEERS:
                        samples.append(sample)
                except Exception:
                    pass
            for future in pending:
                future.cancel()
        finally:
            # Do not let a slow peer extend the first-sync budget; late results cannot install an anchor.
            pool.shutdown(wait=False, cancel_futures=True)
        tick = self.monotonic()
        values = sorted((sample.utc.timestamp() + max(0, tick - sample.received_monotonic), sample.peer) for sample in samples)
        groups = [[value for value in values if 0 <= value[0] - left[0] <= 3] for left in values]
        agreeing = max(groups, key=len, default=[])
        with self.lock:
            self.last_attempt = tick
            if len({peer for _, peer in agreeing}) < 2:
                self.error = '公开时间来源暂未取得一致样本'
                if self.anchor is None:
                    raise ClockUnavailable('北京时间首次校准失败，请检查公开时间来源网络后重试')
                return False
            verified = datetime.fromtimestamp(median(value for value, _ in agreeing), UTC)
            previous = self._value(tick) if self.anchor is not None else verified
            if self.last_returned:
                previous = max(previous, self.last_returned)
            self.anchor = max(previous, verified)
            self.correction = max(0.0, (previous - verified).total_seconds())
            self.anchor_tick = tick
            self.verified_tick = tick
            self.host_delta = (self.host_now() - verified).total_seconds()
            self.error = None
            self.agreeing_peers = [peer for _, peer in agreeing]
            return True

    def writable(self):
        with self.lock:
            return self.verified_tick is not None and 0 <= self.monotonic() - self.verified_tick <= MAX_UNVERIFIED

    def ensure_writable(self):
        if not self.writable():
            raise ClockUnavailable('北京时间超过六小时未核验，业务写入与新任务已暂停，请等待时间校准恢复')

    def status(self):
        with self.lock:
            if self.anchor is None:
                return {'state': 'unavailable', 'writable': False, 'current_time': None, 'timezone': 'Asia/Shanghai'}
            tick = self.monotonic()
            value = self.utc_now()
            writable = self.writable()
            return {'state': 'read_only' if not writable else 'degraded' if self.error else 'synced',
                    'writable': writable, 'current_time': value.astimezone(TZ).isoformat(),
                    'timezone': 'Asia/Shanghai', 'verified_seconds_ago': max(0, tick - self.verified_tick),
                    'correction_seconds': max(0.0, self.correction - max(0, tick - self.anchor_tick) * 0.1),
                    'host_offset_seconds': (self.host_now() - value).total_seconds(),
                    'peers': list(self.agreeing_peers), 'error': self.error}

    def refresh_if_due(self):
        with self.lock:
            tick = self.monotonic()
            due = self.verified_tick is None or tick < self.verified_tick or tick - self.verified_tick >= SYNC_INTERVAL
            if self.anchor is not None:
                due = due or abs((self.host_now() - self._value(tick)).total_seconds() - self.host_delta) > 5
            # A failed sync retries at most once per minute, even if the host was stepped.
            if self.error and self.last_attempt is not None and tick - self.last_attempt < 60:
                return False
        if not due:
            return False
        return self.synchronize()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        def run():
            while not self.stop.wait(1):
                try:
                    self.refresh_if_due()
                except ClockUnavailable:
                    pass
        self.thread = Thread(target=run, name='business-clock', daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=INITIAL_SYNC_BUDGET + 1)


CLOCK = TrustedClock()


def initialize():
    if CLOCK.anchor is None:
        CLOCK.synchronize()
    CLOCK.ensure_writable()
    CLOCK.start()


def utc_now(): return CLOCK.utc_now()
def now(): return CLOCK.now()
def today(): return CLOCK.today()
def status(): return CLOCK.status()
def writable(): return CLOCK.writable()
def ensure_writable(): return CLOCK.ensure_writable()
def shutdown(): CLOCK.shutdown()
