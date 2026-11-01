"""自适应采集组件(借鉴 Scrapling 的设计思想自研实现,不引入其依赖)。

参考项目: https://github.com/D4Vinci/Scrapling (BSD-3-Clause)。
借鉴了它的四个核心设计,并结合本项目"51job 官方 JSON API + Playwright"
的采集方式重新实现(未复制其代码):

1. AdaptiveThrottle  <- 借鉴 AutoThrottle:
   不再使用固定随机延迟,而是根据每次请求的结果信号动态调节页间等待——
   健康时向目标下限缓慢衰减,被限流/封禁时按倍数上调并设上限,恢复后回落。
2. Signal / classify_api_result <- 借鉴"封禁响应自动检测":
   把"是否被反爬"从散落的异常处理提升为一等信号(OK/EMPTY/BLOCKED/TIMEOUT),
   限速器与重试策略消费同一份信号。
3. ApiCapture <- 借鉴被动网络捕获(record_requests=True):
   监听页面自身发出的 search-pc API 响应并按请求参数检索,命中时取数
   完全不需要向页面注入脚本;注入 fetch 降级为回退路径。
4. ProxyRotator / resolve_proxies <- 借鉴 ProxyRotator:
   从环境变量读取代理列表,在多次采集之间轮换出口 IP(轮询策略)。

本模块只依赖标准库,不依赖 playwright,全部逻辑可离线单元测试。
"""
import json
import os
import re
import time
import random
from collections import deque
from enum import Enum
from urllib.parse import parse_qs, urlparse


class Signal(str, Enum):
    """单次请求结果的封禁信号分类(借鉴 Scrapling 的封禁检测)。"""
    OK = 'ok'            # 正常拿到数据
    EMPTY = 'empty'      # 响应成功但没有数据(通常是该城市/页数翻到底,属正常情况)
    BLOCKED = 'blocked'  # 明确被反爬拦截(WAF 挑战页/403/412/429)
    TIMEOUT = 'timeout'  # 超时/断连等瞬时故障


def extract_job_items(data):
    """按 51job 搜索 API 的 JSON 结构取出职位列表,取不到返回空列表。"""
    if not isinstance(data, dict):
        return []
    resultbody = data.get('resultbody')
    if not isinstance(resultbody, dict):
        return []
    job = resultbody.get('job')
    if not isinstance(job, dict):
        return []
    items = job.get('items')
    return items if isinstance(items, list) else []


def classify_api_result(data, page_dead=False, get_items=extract_job_items):
    """把一次 API 调用的结果归类为封禁信号。

    参数:
        data: JS_FETCH_API / page.evaluate 返回的结果(dict/None)
        page_dead: 页面是否已僵死/断连(此时结果不可信,一律按超时处理)
        get_items: 从响应中取职位列表的函数,默认按 51job 结构取

    归类规则:
        page_dead / data 为 None            -> TIMEOUT(瞬时故障)
        error 文案含 WAF/HTML/403/412/429    -> BLOCKED(明确被拦)
        其他 error(HTTP 5xx/请求超时等)     -> TIMEOUT(可重试的瞬时故障)
        正常 JSON 且有数据                   -> OK
        正常 JSON 但没数据                   -> EMPTY(正常翻到底,不该被惩罚)
    """
    if page_dead or data is None:
        return Signal.TIMEOUT
    if isinstance(data, dict) and 'error' in data:
        msg = str(data['error']).lower()
        if 'waf' in msg or 'html' in msg or any(c in msg for c in ('403', '412', '429')):
            return Signal.BLOCKED
        return Signal.TIMEOUT
    return Signal.OK if get_items(data) else Signal.EMPTY


class AdaptiveThrottle:
    """页间自适应限速(借鉴 Scrapling AutoThrottle)。

    调节规则:
        OK      -> 连续超过 recovery_streak 次后,延迟按 decay 比例向 min 衰减
        BLOCKED -> 延迟按 boost 倍数上调(默认翻倍),健康计数清零
        TIMEOUT -> 延迟按 timeout_boost 上调(默认 x1.5,比封禁温和)
        EMPTY   -> 不调节(正常翻到底不该被惩罚),但清零健康计数

    延迟始终夹在 [min_delay, max_delay] 区间内;每次实际等待在
    [当前延迟, 当前延迟xjitter] 区间随机抖动,避免请求节奏过于规律。
    """

    def __init__(self, min_delay=0.1, max_delay=8.0, initial=None,
                 boost=2.0, timeout_boost=1.5, decay=0.85,
                 recovery_streak=3, jitter=1.3, rng=None):
        self.min_delay = float(min_delay)
        self.max_delay = float(max_delay)
        self.boost = float(boost)
        self.timeout_boost = float(timeout_boost)
        self.decay = float(decay)
        self.recovery_streak = int(recovery_streak)
        self.jitter = float(jitter)
        self._rng = rng if rng is not None else random
        self._delay = float(initial) if initial is not None else self.min_delay
        self._streak = 0
        self._counts = {s: 0 for s in Signal}

    @property
    def current_delay(self):
        """当前基础延迟(不含抖动),供日志与测试观察。"""
        return self._delay

    def next_delay(self):
        """取本次请求应等待的秒数: [delay, delay*jitter] 随机抖动。"""
        return self._rng.uniform(self._delay, self._delay * self.jitter)

    def on_result(self, signal):
        """回灌一次请求结果信号,更新内部延迟状态,返回当前延迟。"""
        signal = Signal(signal)
        self._counts[signal] += 1
        if signal is Signal.OK:
            self._streak += 1
            if self._streak > self.recovery_streak:
                self._delay = max(self.min_delay, self._delay * self.decay)
        elif signal is Signal.EMPTY:
            self._streak = 0  # 正常翻到底: 不惩罚,但也不再加速
        elif signal is Signal.TIMEOUT:
            self._delay = min(self.max_delay, self._delay * self.timeout_boost)
            self._streak = 0
        else:  # Signal.BLOCKED
            self._delay = min(self.max_delay, self._delay * self.boost)
            self._streak = 0
        return self._delay

    def snapshot(self):
        """返回内部状态快照,用于日志与测试断言。"""
        return {
            'delay': self._delay,
            'streak': self._streak,
            'counts': {s.value: n for s, n in self._counts.items()},
        }


class ApiCapture:
    """被动捕获 51job 搜索 API 的响应(借鉴 Scrapling record_requests)。

    注册到 page.on('response', capture.on_response) 后,页面(官方前端或
    注入的 fetch)发出的 search-pc 响应都会按请求参数留存;每次取数前先
    find(params) 检索,命中即可完全不经注入拿到数据。

    设计约束:
        on_response 永不抛异常——事件回调里任何错误都不能影响采集主流程;
        只读响应体,不改变页面任何行为;
        用 deque 限量留存,避免长时间采集内存膨胀。
    """

    API_MARKER = '/api/job/search-pc'

    def __init__(self, max_items=32, ttl=120.0):
        self._items = deque(maxlen=max_items)
        self._ttl = float(ttl)

    def on_response(self, response):
        """page.on('response') 回调: 留存一条 search-pc JSON 响应。"""
        try:
            url = response.url
            if self.API_MARKER not in url:
                return
            body = response.text()
            if not body or body.lstrip().startswith('<'):
                return  # WAF 挑战页返回 HTML, 不留存
            data = json.loads(body)
            if not isinstance(data, dict):
                return
            self._items.append({'url': url, 'data': data, 'ts': time.time()})
        except Exception:
            pass  # 任何解析失败都静默放弃, 不影响采集

    def find(self, params, now=None):
        """按 (keyword, jobArea, pageNum) 检索最近一条匹配且未过期的响应。

        未命中返回 None, 调用方回退到注入 fetch 路径。
        """
        if now is None:
            now = time.time()
        want = {
            'keyword': str(params.get('keyword', '')),
            'jobArea': str(params.get('jobArea', '')),
            'pageNum': str(params.get('pageNum', '')),
        }
        for item in reversed(self._items):  # 从最近往前找
            if now - item['ts'] > self._ttl:
                continue
            query = parse_qs(urlparse(item['url']).query)
            got = {k: query.get(k, [''])[0] for k in want}
            if got == want:
                return item['data']
        return None

    def __len__(self):
        return len(self._items)


class ProxyRotator:
    """代理轮换(借鉴 Scrapling ProxyRotator, 简化为轮询策略)。

    本项目只做"能力接口": 支持在多次采集之间依次轮换出口 IP,
    不内置代理池来源;代理由环境变量 COLLECT_PROXIES / COLLECT_PROXY
    或调用方显式传入。
    """

    def __init__(self, proxies):
        self._proxies = [str(p).strip() for p in (proxies or []) if str(p).strip()]
        self._index = 0

    def next(self):
        """返回下一个代理地址,列表为空时返回 None。"""
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return proxy

    def __len__(self):
        return len(self._proxies)


def resolve_proxies(env=None):
    """从环境变量解析代理列表。

    COLLECT_PROXIES 优先(逗号/空白分隔多个代理), 其次 COLLECT_PROXY(单个)。
    都未配置返回空列表(= 直连, 与旧行为一致)。
    .env 由 config.py 在应用启动时写进 os.environ, 因此网页端/命令行入口
    都会自然读到。
    """
    env = os.environ if env is None else env
    raw = env.get('COLLECT_PROXIES') or env.get('COLLECT_PROXY') or ''
    return [s for s in re.split(r'[,\s]+', raw.strip()) if s]
