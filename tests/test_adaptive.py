"""
data/adaptive.py(借鉴 Scrapling 设计的自适应采集组件)的单元测试。

全部离线运行: 不依赖 playwright, 不发起任何网络请求。
"""
import json
import random
import time
from urllib.parse import quote

from data.adaptive import (
    AdaptiveThrottle,
    ApiCapture,
    ProxyRotator,
    Signal,
    classify_api_result,
    extract_job_items,
    resolve_proxies,
)


# ============================================================
# classify_api_result — 封禁信号归类(借鉴 Scrapling 封禁检测)
# ============================================================
class TestClassifyApiResult:

    def test_page_dead_is_timeout(self):
        assert classify_api_result({'error': 'x'}, page_dead=True) is Signal.TIMEOUT

    def test_none_is_timeout(self):
        assert classify_api_result(None) is Signal.TIMEOUT

    def test_waf_text_is_blocked(self):
        """JS_FETCH_API 的 WAF 拦截错误文案必须识别为 BLOCKED。"""
        assert classify_api_result({'error': 'WAF拦截/返回HTML'}) is Signal.BLOCKED

    def test_html_text_is_blocked(self):
        assert classify_api_result({'error': '返回HTML'}) is Signal.BLOCKED

    def test_http_403_is_blocked(self):
        assert classify_api_result({'error': 'HTTP 403'}) is Signal.BLOCKED

    def test_http_412_is_blocked(self):
        assert classify_api_result({'error': 'HTTP 412'}) is Signal.BLOCKED

    def test_http_429_is_blocked(self):
        assert classify_api_result({'error': 'HTTP 429'}) is Signal.BLOCKED

    def test_fetch_timeout_is_timeout(self):
        assert classify_api_result({'error': '请求超时(20s)'}) is Signal.TIMEOUT

    def test_http_500_is_timeout(self):
        """5xx 是瞬时故障, 不算明确封禁。"""
        assert classify_api_result({'error': 'HTTP 500'}) is Signal.TIMEOUT

    def test_valid_data_is_ok(self):
        data = {'resultbody': {'job': {'items': [{'jobId': 1}]}}}
        assert classify_api_result(data) is Signal.OK

    def test_valid_but_no_items_is_empty(self):
        data = {'resultbody': {'job': {'items': []}}}
        assert classify_api_result(data) is Signal.EMPTY

    def test_non_dict_payload_is_empty(self):
        assert classify_api_result('garbage') is Signal.EMPTY

    def test_custom_get_items(self):
        assert classify_api_result({'rows': [1]}, get_items=lambda d: d['rows']) is Signal.OK


# ============================================================
# extract_job_items — 51job JSON 结构取数
# ============================================================
class TestExtractJobItems:

    def test_full_shape(self):
        items = [{'jobId': 1}, {'jobId': 2}]
        assert extract_job_items({'resultbody': {'job': {'items': items}}}) == items

    def test_missing_keys(self):
        assert extract_job_items({}) == []
        assert extract_job_items({'resultbody': {}}) == []
        assert extract_job_items({'resultbody': {'job': {}}}) == []

    def test_non_dict_input(self):
        assert extract_job_items(None) == []
        assert extract_job_items('str') == []
        assert extract_job_items(123) == []

    def test_items_not_list(self):
        assert extract_job_items({'resultbody': {'job': {'items': 'x'}}}) == []


# ============================================================
# AdaptiveThrottle — 自适应限速(借鉴 Scrapling AutoThrottle)
# ============================================================
class TestAdaptiveThrottle:

    def _throttle(self, **kw):
        kw.setdefault('rng', random.Random(42))
        return AdaptiveThrottle(**kw)

    def test_initial_delay_is_min(self):
        t = self._throttle(min_delay=0.1)
        assert t.current_delay == 0.1

    def test_next_delay_jitter_within_bounds(self):
        t = self._throttle(min_delay=1.0, max_delay=8.0)
        for _ in range(50):
            d = t.next_delay()
            assert 1.0 <= d <= 1.3

    def test_ok_decay_only_after_recovery_streak(self):
        """前 recovery_streak 次 OK 不回落, 之后才开始衰减。"""
        t = self._throttle(min_delay=0.5, initial=1.0, recovery_streak=3, decay=0.5)
        for _ in range(3):
            t.on_result(Signal.OK)
        assert t.current_delay == 1.0
        t.on_result(Signal.OK)
        assert t.current_delay == 0.5

    def test_ok_decay_floors_at_min(self):
        t = self._throttle(min_delay=0.5, decay=0.5)
        for _ in range(20):
            t.on_result(Signal.OK)
        assert t.current_delay >= 0.5

    def test_blocked_doubles_delay(self):
        t = self._throttle(min_delay=1.0, boost=2.0)
        t.on_result(Signal.BLOCKED)
        assert t.current_delay == 2.0

    def test_blocked_caps_at_max(self):
        t = self._throttle(min_delay=5.0, max_delay=8.0, boost=2.0)
        t.on_result(Signal.BLOCKED)
        assert t.current_delay == 8.0

    def test_timeout_boost_milder_than_blocked(self):
        t = self._throttle(min_delay=2.0, timeout_boost=1.5)
        t.on_result(Signal.TIMEOUT)
        assert t.current_delay == 3.0

    def test_empty_is_neutral(self):
        """EMPTY(正常翻到底)不该被惩罚。"""
        t = self._throttle(min_delay=1.0)
        t.on_result(Signal.EMPTY)
        assert t.current_delay == 1.0

    def test_streak_resets_after_boost(self):
        t = self._throttle(min_delay=0.5, initial=1.0, recovery_streak=3, decay=0.5)
        for _ in range(4):
            t.on_result(Signal.OK)
        assert t.current_delay == 0.5  # 连续4次健康, 已回落
        t.on_result(Signal.BLOCKED)
        for _ in range(3):
            t.on_result(Signal.OK)
        assert t.current_delay == 1.0  # 被拦截后健康计数清零, 连续次数不够还没回落
        t.on_result(Signal.OK)
        assert t.current_delay == 0.5

    def test_accepts_signal_string(self):
        t = self._throttle()
        t.on_result('ok')
        assert t.snapshot()['counts']['ok'] == 1

    def test_snapshot_shape(self):
        snap = self._throttle().snapshot()
        assert set(snap) == {'delay', 'streak', 'counts'}
        assert set(snap['counts']) == {'ok', 'empty', 'blocked', 'timeout'}


# ============================================================
# ProxyRotator / resolve_proxies — 代理轮换(借鉴 ProxyRotator)
# ============================================================
class TestProxyRotator:

    def test_round_robin_order(self):
        r = ProxyRotator(['http://p1', 'http://p2'])
        assert [r.next(), r.next(), r.next()] == ['http://p1', 'http://p2', 'http://p1']

    def test_empty_returns_none(self):
        assert ProxyRotator([]).next() is None
        assert ProxyRotator(None).next() is None

    def test_len(self):
        assert len(ProxyRotator(['a', 'b', 'c'])) == 3

    def test_whitespace_entries_filtered(self):
        assert len(ProxyRotator(['a', '  ', ''])) == 1


class TestResolveProxies:

    def test_comma_separated(self):
        env = {'COLLECT_PROXIES': 'http://a:1, http://b:2'}
        assert resolve_proxies(env) == ['http://a:1', 'http://b:2']

    def test_space_separated(self):
        env = {'COLLECT_PROXIES': 'http://a:1  http://b:2'}
        assert resolve_proxies(env) == ['http://a:1', 'http://b:2']

    def test_single_fallback(self):
        env = {'COLLECT_PROXY': 'http://a:1'}
        assert resolve_proxies(env) == ['http://a:1']

    def test_proxies_take_precedence(self):
        env = {'COLLECT_PROXIES': 'http://a:1', 'COLLECT_PROXY': 'http://b:1'}
        assert resolve_proxies(env) == ['http://a:1']

    def test_unset_returns_empty(self):
        assert resolve_proxies({}) == []


# ============================================================
# ApiCapture — 被动网络捕获(借鉴 record_requests)
# ============================================================
def _api_body(items):
    return json.dumps({'resultbody': {'job': {'items': items}}})


def _api_url(keyword='python', job_area='010000', page_num=1):
    return ('https://we.51job.com/api/job/search-pc?api_key=51job'
            f'&keyword={quote(keyword)}&jobArea={job_area}&pageNum={page_num}')


class _FakeResponse:
    """鸭子类型模拟 playwright Response, 只用到 url 与 text()。"""

    def __init__(self, url, text):
        self.url = url
        self._text = text

    def text(self):
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


def _params(keyword='python', job_area='010000', page_num=1):
    return {'keyword': keyword, 'jobArea': job_area, 'pageNum': page_num}


class TestApiCapture:

    def test_capture_and_find(self):
        cap = ApiCapture()
        cap.on_response(_FakeResponse(_api_url(), _api_body([{'jobId': 1}])))
        data = cap.find(_params())
        assert data['resultbody']['job']['items'] == [{'jobId': 1}]

    def test_no_match_returns_none(self):
        cap = ApiCapture()
        cap.on_response(_FakeResponse(_api_url(page_num=2), _api_body([1])))
        assert cap.find(_params(page_num=1)) is None

    def test_chinese_keyword_matches(self):
        """URLSearchParams 编码的中文关键词经 parse_qs 解码后必须能对上。"""
        cap = ApiCapture()
        cap.on_response(_FakeResponse(_api_url(keyword='机器学习'), _api_body([1])))
        assert cap.find(_params(keyword='机器学习')) is not None

    def test_latest_match_wins(self):
        cap = ApiCapture()
        cap.on_response(_FakeResponse(_api_url(), _api_body([{'v': 1}])))
        cap.on_response(_FakeResponse(_api_url(), _api_body([{'v': 2}])))
        assert cap.find(_params())['resultbody']['job']['items'] == [{'v': 2}]

    def test_html_body_ignored(self):
        cap = ApiCapture()
        cap.on_response(_FakeResponse(_api_url(), '<html>verify</html>'))
        assert cap.find(_params()) is None
        assert len(cap) == 0

    def test_bad_json_ignored(self):
        cap = ApiCapture()
        cap.on_response(_FakeResponse(_api_url(), 'not json'))
        assert cap.find(_params()) is None

    def test_other_urls_ignored(self):
        cap = ApiCapture()
        cap.on_response(_FakeResponse('https://we.51job.com/pc/search?x=1', _api_body([1])))
        assert len(cap) == 0

    def test_handler_never_raises(self):
        """响应体读取失败绝不能影响采集主流程。"""
        cap = ApiCapture()
        cap.on_response(_FakeResponse(_api_url(), RuntimeError('boom')))
        assert cap.find(_params()) is None

    def test_ttl_expiry(self):
        cap = ApiCapture(ttl=60.0)
        cap.on_response(_FakeResponse(_api_url(), _api_body([1])))
        future = time.time() + 61.0
        assert cap.find(_params(), now=future) is None

    def test_maxlen_eviction(self):
        cap = ApiCapture(max_items=1)
        cap.on_response(_FakeResponse(_api_url(page_num=1), _api_body([1])))
        cap.on_response(_FakeResponse(_api_url(page_num=2), _api_body([2])))
        assert cap.find(_params(page_num=1)) is None
        assert cap.find(_params(page_num=2)) is not None


# ============================================================
# _pick_proxy — 采集入口的代理轮换接线
# ============================================================
from data.python_job_scraper import _pick_proxy  # noqa: E402


class TestPickProxy:

    def test_rotates_across_calls(self):
        """同一进程内多次采集依次轮换出口。"""
        proxies = ['http://test-p1:1', 'http://test-p2:2']
        assert _pick_proxy(proxies) == 'http://test-p1:1'
        assert _pick_proxy(proxies) == 'http://test-p2:2'
        assert _pick_proxy(proxies) == 'http://test-p1:1'

    def test_empty_is_none(self):
        assert _pick_proxy([]) is None
        assert _pick_proxy(None) is None
