"""
经验字段口径测试 — data/exper_parser.py 是全项目唯一的归一入口。

覆盖两类线上写法：「1-3年」区间式与「3年及以上」下限式。
51job 曾整体切换到下限式写法，导致下游按精确匹配归档时大面积落入兜底档，
因此这里把库里真实出现过的全部写法固化成映射表，任何改口径的行为都会在此暴露。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.exper_parser import (  # noqa: E402
    EXPER_BUCKETS,
    EXPER_ORDER,
    normalize_exper,
    parse_min_years,
)

# 数据库 960 条记录中真实出现过的全部经验写法 → 应归入的档位
PRODUCTION_WORDING = {
    # 零经验/无年限表述
    '无需经验': '经验不限',
    '在校生/应届生': '经验不限',
    '经验不限': '经验不限',
    '': '经验不限',
    # 下限式（当前 51job 主力写法，占比最高）
    '1年及以上': '1-3年',
    '2年及以上': '1-3年',
    '3年及以上': '3-5年',
    '4年及以上': '3-5年',
    '5年及以上': '5-10年',
    '6年及以上': '5-10年',
    '7年及以上': '5-10年',
    '8年及以上': '5-10年',
    '10年及以上': '10年以上',
    # 区间式
    '1-2年': '1-3年',
    '1-3年': '1-3年',
    '1-5年': '1-3年',
    '1-7年': '1-3年',
    '1-10年': '1-3年',
    '2-3年': '1-3年',
    '2-4年': '1-3年',
    '2-5年': '1-3年',
    '2-6年': '1-3年',
    '2-10年': '1-3年',
    '3-4年': '3-5年',
    '3-5年': '3-5年',
    '3-6年': '3-5年',
    '3-7年': '3-5年',
    '3-8年': '3-5年',
    '3-10年': '3-5年',
    '5-7年': '5-10年',
    '5-8年': '5-10年',
    '5-10年': '5-10年',
    # 单值式
    '1年': '1-3年',
    '2年': '1-3年',
    '3年': '3-5年',
    '5年': '5-10年',
    '8年': '5-10年',
    '10年': '10年以上',
}


class TestParseMinYears:
    """年限解析：区间取下限，下限式取该值，无数字取 0。"""

    def test_range_uses_lower_bound(self):
        assert parse_min_years('3-5年') == 3
        assert parse_min_years('1-10年') == 1

    def test_at_least_uses_threshold(self):
        assert parse_min_years('3年及以上') == 3

    def test_plain_and_none(self):
        assert parse_min_years('5年') == 5
        assert parse_min_years('') == 0
        assert parse_min_years(None) == 0

    def test_text_without_years_is_zero(self):
        assert parse_min_years('无需经验') == 0
        assert parse_min_years('在校生/应届生') == 0


class TestNormalizeExper:
    """归一到 5 个有序档位。"""

    @pytest.mark.parametrize('raw,expected', sorted(PRODUCTION_WORDING.items()))
    def test_production_wording(self, raw, expected):
        assert normalize_exper(raw) == expected, f'{raw!r} 归档错误'

    def test_all_production_wording_is_covered(self):
        """库里 960 条记录的每一种经验写法都必须在映射表中。"""
        import sqlite3
        import config
        if not os.path.exists(config.DB_PATH):
            pytest.skip('本地无 data.db，跳过真实数据口径覆盖检查')
        conn = sqlite3.connect(config.DB_PATH)
        try:
            raws = {r[0] or '' for r in conn.execute('SELECT DISTINCT exper FROM data')}
        finally:
            conn.close()
        assert raws <= set(PRODUCTION_WORDING), f'出现未登记的经验写法: {raws - set(PRODUCTION_WORDING)}'

    def test_bucket_vocabulary(self):
        assert EXPER_BUCKETS == ['经验不限', '1-3年', '3-5年', '5-10年', '10年以上']
        assert EXPER_ORDER == {b: i for i, b in enumerate(EXPER_BUCKETS)}

    def test_result_always_in_buckets(self):
        assert normalize_exper('随便写的') in EXPER_BUCKETS

    def test_ordering_is_monotonic_in_years(self):
        orders = [EXPER_ORDER[normalize_exper(f'{y}年及以上')] for y in (1, 3, 5, 10)]
        assert orders == sorted(orders) and len(set(orders)) == len(orders)


class TestBucketConsumers:
    """下游消费方必须共用同一口径 —— 曾经的 P1 回归点。

    直接跑在 temp_db 上（夹具同时含区间式与下限式两种写法）。
    """

    def test_cross_chart_keeps_lower_bound_out_of_default(self, temp_db):
        """「3年及以上」不得被兜底进'经验不限'（旧实现把 849/960 条堆进该档）。"""
        from analysis.cross import salary_vs_exper
        result = salary_vs_exper()
        counts = dict(zip(result['labels'], result['counts']))

        assert result['labels'] == EXPER_BUCKETS
        assert counts['经验不限'] == 3      # 仅 无需经验 / 在校生应届生 / 经验不限
        assert counts['3-5年'] == 6         # 含写作「3年及以上」的那条
        assert counts['5-10年'] == 1        # 来自「5年及以上」
        assert sum(result['counts']) == 16

    def test_salary_curve_yields_only_standard_levels(self, temp_db):
        from modeling.salary_curve import compute_salary_curve
        result = compute_salary_curve(min_per_level=1)

        labels = [item['exper'] for item in result['overall']]
        assert labels == ['经验不限', '1-3年', '3-5年', '5-10年']
        assert sum(item['count'] for item in result['overall']) == 16

    def test_salary_predict_filter_matches_lower_bound_wording(self, temp_db):
        """按'3-5年'筛选时，库里写作'3年及以上'的岗位也必须命中。"""
        from modeling.salary_predict import lookup_salary_range
        result = lookup_salary_range(exper='3-5年')

        assert result['count'] == 6

    def test_classifier_shares_the_same_normalizer(self):
        """分类器不得再自带一份经验映射表。"""
        import modeling.salary_classifier as sc
        from data import exper_parser
        assert sc.normalize_exper is exper_parser.normalize_exper
        assert sc.EXPER_ORDER is exper_parser.EXPER_ORDER


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
