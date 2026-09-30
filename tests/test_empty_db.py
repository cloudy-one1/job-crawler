"""
空数据库行为回归测试。

README / ARCHITECTURE 承诺「空数据库首次启动不会崩溃」——但
`DataFrame([])[0]` 在 xueli / jinyan / region 上会抛 KeyError: 0，
导致空库访问 /chart 直接 500（修复前 320 个用例全绿也没接住：
conftest 的 temp_db 夹具只覆盖"有数据的库"）。

本文件用「建表后清空」的临时库把空库路径锁死。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3

import pytest


@pytest.fixture
def empty_db(temp_db):
    """基于含样例数据的 temp_db 清空全部行，得到真正的空库。"""
    conn = sqlite3.connect(temp_db)
    conn.execute("DELETE FROM data")
    conn.commit()
    conn.close()
    return temp_db


# ============================================================
# 统计层：空库必须返回空结构，而不是 KeyError
# ============================================================
class TestAnalysisFunctionsOnEmptyDb:

    def test_xueli_returns_empty_list(self, empty_db):
        from analysis.xueli import xuelifun
        assert xuelifun() == []

    def test_jinyan_returns_empty_list(self, empty_db):
        from analysis.jinyan import jinyanfun
        assert jinyanfun() == []

    def test_region_returns_empty_list(self, empty_db):
        from analysis.region import regionfun
        assert regionfun() == []

    def test_jobtitle_returns_empty_list(self, empty_db):
        from analysis.jobtitle import jobtitlefun
        assert jobtitlefun() == []

    def test_xinzi_returns_all_zero_buckets(self, empty_db):
        from analysis.xinzi import xinzi
        assert xinzi() == [0] * 8

    def test_cross_returns_zero_counts(self, empty_db):
        from analysis.cross import salary_vs_exper, salary_vs_edu
        assert sum(salary_vs_exper()['counts']) == 0
        assert sum(salary_vs_edu()['counts']) == 0


# ============================================================
# 路由层：空库下主要页面必须 200（容错承诺）
# ============================================================
class TestRoutesOnEmptyDb:
    """空库下主要 GET 路由返回 200；/chart 曾在此场景 500。"""

    @pytest.fixture
    def client(self, empty_db):
        from app import app
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        # 关键：清掉模块级缓存，否则会复用其他用例填充的图表/聚类缓存，
        # 空库路径根本不会被走到。缓存状态的唯一归属地是 services.cache
        from services import cache as cache_service
        cache_service._chart_data_cache = None
        cache_service._clustering_cache = None
        with app.test_client() as c:
            yield c
        # 用例结束后清缓存，避免污染后续测试
        cache_service._chart_data_cache = None

    def test_chart_returns_200(self, client):
        assert client.get('/chart').status_code == 200

    def test_chart_salary_returns_200(self, client):
        assert client.get('/chart/salary').status_code == 200

    def test_list_returns_200(self, client):
        assert client.get('/list').status_code == 200

    def test_index_returns_200(self, client):
        assert client.get('/').status_code == 200

    def test_ml_returns_200(self, client):
        assert client.get('/ml').status_code == 200

    def test_interested_returns_200(self, client):
        assert client.get('/interested').status_code == 200

    def test_advice_returns_200(self, client):
        assert client.get('/advice').status_code == 200
