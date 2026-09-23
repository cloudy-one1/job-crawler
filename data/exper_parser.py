"""
工作经验字段统一口径。

51job 的 workYearString 同时存在「1-3年」区间式与「3年及以上」下限式两类写法，
数据库按原始文本留存（口径再次变化时无需重新采集），所有下游统计与建模在读侧
调用本模块归一，避免各处映射表漂移。

归一规则：取文本中的最低年限，映射到 5 个有序档位。
    0（无需经验 / 在校生 / 应届生 / 空值 / 无数字）→ 经验不限
    1–2 → 1-3年
    3–4 → 3-5年
    5–9 → 5-10年
    ≥10 → 10年以上
"""
import re

EXPER_BUCKETS = ['经验不限', '1-3年', '3-5年', '5-10年', '10年以上']
EXPER_ORDER = {label: idx for idx, label in enumerate(EXPER_BUCKETS)}

_YEARS_RE = re.compile(r'(\d+)')


def parse_min_years(exper_raw):
    """从经验文本解析最低年限，无法解析（含"无需经验""应届生"等）返回 0。"""
    if not exper_raw:
        return 0
    match = _YEARS_RE.search(str(exper_raw))
    return int(match.group(1)) if match else 0


def normalize_exper(exper_raw):
    """原始经验文本 → EXPER_BUCKETS 中的统一档位标签。"""
    years = parse_min_years(exper_raw)
    if years <= 0:
        return '经验不限'
    if years <= 2:
        return '1-3年'
    if years <= 4:
        return '3-5年'
    if years <= 9:
        return '5-10年'
    return '10年以上'
