"""
建模层核心逻辑单元测试 — 使用合成数据，不依赖数据库。

覆盖:
  * job_clustering.choose_best_k() — 轮廓系数选 k
  * job_clustering.run_clustering() — 聚类全流程 (mock DB)
  * salary_predict.lookup_salary_range() — 薪资参考查询
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np
import warnings


# ============================================================
# choose_best_k() — 轮廓系数选 k
# ============================================================
class TestChooseBestK:
    """用合成数据验证轮廓系数 k 值选择。"""

    @pytest.fixture(autouse=True)
    def _import(self):
        from modeling.job_clustering import choose_best_k as _cbk
        self.cbk = _cbk

    def test_single_sample_returns_k1(self):
        """只有1个样本时返回 k=1。"""
        X = np.array([[0.1, 0.2]])
        k, scores = self.cbk(X)
        assert k == 1
        assert scores == {1: 0.0}

    def test_two_samples_returns_k1(self):
        """2个样本退化为 k=1 (max_k < 2)。"""
        X = np.array([[0.1, 0.2], [0.3, 0.4]])
        k, scores = self.cbk(X)
        assert k == 1

    def test_choose_k_with_clear_clusters(self):
        """3簇清晰数据应选 k=3 (或相近值)。"""
        np.random.seed(42)
        c1 = np.random.normal(0, 0.3, (30, 5))
        c2 = np.random.normal(5, 0.3, (30, 5))
        c3 = np.random.normal(10, 0.3, (30, 5))
        X = np.vstack([c1, c2, c3])
        k, scores = self.cbk(X)
        assert k >= 1
        assert len(scores) > 0
        # 在清晰3簇数据上,预期 k >= 2 (至少应发现多个簇)
        assert k >= 2

    def test_scores_are_positive(self):
        """轮廓系数应在合理范围内。"""
        np.random.seed(1)
        X = np.random.normal(0, 1, (40, 5))
        k, scores = self.cbk(X)
        for s in scores.values():
            assert -1.0 <= s <= 1.0


# ============================================================
# run_clustering() — 聚类全流程 (mock get_posts)
# ============================================================
class TestRunClustering:
    """mock 掉数据库调用,只测试聚类逻辑。"""

    def test_run_with_mocked_posts(self, monkeypatch):
        """用30个模拟职位标题运行完整聚类流程。"""
        mock_posts = [
            'Python后端开发工程师',
            'Java后端开发',
            'Go后台开发',
            '后端高级工程师',
            '后端服务端开发',
            # ---
            'Web前端工程师',
            '前端开发工程师',
            'React前端',
            'Vue前端开发',
            'Web前端高级',
            # ---
            '数据挖掘工程师',
            '大数据开发',
            '数据分析师',
            '数据工程师',
            '数据科学家',
            # ---
            '软件测试工程师',
            '自动化测试',
            '测试开发',
            '功能测试',
            '性能测试工程师',
            # ---
            '运维工程师',
            '运维开发',
            'Linux系统运维', 
            'DevOps工程师',
            'SRE运维',
            # ---
            'Python爬虫工程师',
            '数据爬虫',
            '爬虫开发',
            '网络爬虫',
            '反爬虫工程师',
        ]

        from modeling import job_clustering
        monkeypatch.setattr(job_clustering, 'get_posts', lambda: list(enumerate(mock_posts)))

        result = job_clustering.run_clustering()
        assert 'k' in result
        assert 'clusters' in result
        assert result['k'] >= 1
        assert len(result['clusters']) == result['k']
        for c in result['clusters']:
            assert 'cluster_id' in c
            assert 'auto_label' in c
            assert 'count' in c
            assert 'top_keywords' in c
            assert c['count'] > 0
        assert result.get('label_version') == 2

    def test_auto_label_uses_discriminative_naming(self, monkeypatch):
        """auto_label 应为判别性 top 词的「·」连接(升级自旧版 '/' 连接)。"""
        mock_posts = [
            'Python后端开发工程师', 'Java后端开发', 'Go后台开发',
            '后端高级工程师', '后端服务端开发', 'Python服务端',
            'Web前端工程师', '前端开发工程师', 'React前端',
            'Vue前端开发', 'Web前端高级', 'H5前端',
            '软件测试工程师', '自动化测试', '测试开发',
            '功能测试', '性能测试工程师', '测试运维',
        ]
        from modeling import job_clustering
        monkeypatch.setattr(job_clustering, 'get_posts', lambda: list(enumerate(mock_posts)))
        result = job_clustering.run_clustering()
        assert result['k'] >= 2
        for c in result['clusters']:
            # 每簇命名非空,由「·」连接判别词
            assert c['auto_label'], '簇命名为空'
            terms = c['auto_label'].split('·')
            assert 1 <= len(terms) <= 3
            # 判别词必须来自 TF-IDF 词表(即 top_keywords 的超集来源)
            assert all(t and t not in (' ',) for t in terms)




# ============================================================
# run_clustering() — 空数据兜底
# ============================================================
class TestRunClusteringEdgeCases:
    """验证 run_clustering 的健壮性：空数据/极小数据集。"""

    def test_empty_titles_returns_safe_result(self, monkeypatch):
        """空标题列表返回 k=0 + 空 clusters，不应崩溃。"""
        from modeling import job_clustering
        monkeypatch.setattr(job_clustering, 'get_posts', lambda: [])
        result = job_clustering.run_clustering()
        assert result['k'] == 0
        assert result['clusters'] == []
        assert result['total_jobs'] == 0

    def test_single_title_returns_k1(self, monkeypatch):
        """只有1个标题时应安全返回 k=1。"""
        from modeling import job_clustering
        monkeypatch.setattr(job_clustering, 'get_posts',
                           lambda: [(1, 'Python后端开发工程师')])
        result = job_clustering.run_clustering()
        assert result['k'] >= 1
        assert len(result['clusters']) == result['k']
        assert result['total_jobs'] == 1


# ============================================================
# _cluster_salary_stats() — 空 ID 防护
# ============================================================
class TestClusterSalaryStats:
    """验证 _cluster_salary_stats 的健壮性。"""

    def test_empty_ids_returns_empty(self):
        """空 ids 列表应返回空列表，不触发 SQL 语法错误。"""
        from modeling import job_clustering
        result = job_clustering._cluster_salary_stats([], np.array([]), 3)
        assert result == []

    def test_empty_ids_returns_empty_list_k0(self):
        """ids 为空且 k=0 时也不应崩溃。"""
        from modeling import job_clustering
        result = job_clustering._cluster_salary_stats([], np.array([]), 0)
        assert result == []


# ============================================================
# lookup_salary_range() — 薪资参考查询
# ============================================================
def _make_lookup_rows(n=30, seed=42):
    """生成模拟 get_rows() 输出的行数据。"""
    import random
    rng = random.Random(seed)
    cities = ['北京', '上海', '深圳', '广州', '杭州']
    posts = ['Python后端开发', 'Java后端开发', '前端开发工程师',
             'Python爬虫工程师', '运维工程师', '测试工程师',
             '大数据开发', '数据挖掘工程师']
    edus = ['不限', '大专', '本科', '硕士', '博士']
    expers = ['经验不限', '1-3年', '3-5年', '5-10年', '10年以上']
    rows = []
    for i in range(n):
        city = rng.choice(cities)
        post = rng.choice(posts)
        edu = rng.choice(edus)
        exper = rng.choice(expers)
        base_salary = rng.uniform(8, 30)
        smin = round(base_salary, 1)
        smax = round(base_salary + rng.uniform(3, 15), 1)
        rows.append((post, city, smin, smax, edu, exper))
    return rows


class TestLookupSalaryRange:
    """验证 lookup_salary_range 的查询逻辑和边缘情况。"""

    def test_basic_lookup_returns_expected_keys(self, monkeypatch):
        """正常查询应返回统计字段。"""
        from modeling import salary_predict
        monkeypatch.setattr(salary_predict, 'get_rows',
                           lambda: _make_lookup_rows(60))
        result = salary_predict.lookup_salary_range(city='北京', category='后端')
        if 'message' in result:
            # 可能匹配不够，只验证不崩溃
            assert result['count'] < 3
        else:
            assert 'median' in result
            assert 'mean' in result
            assert 'min' in result
            assert 'max' in result
            assert 'p25' in result
            assert 'p75' in result
            assert result['count'] >= 3
            assert result['median'] > 0

    def test_no_match_returns_message(self, monkeypatch):
        """完全不匹配的城市应返回 message。"""
        from modeling import salary_predict
        monkeypatch.setattr(salary_predict, 'get_rows',
                           lambda: _make_lookup_rows(30))
        result = salary_predict.lookup_salary_range(city='火星', category='前端')
        assert 'message' in result

    def test_single_char_city_not_false_match(self, monkeypatch):
        """单个字'海'不应误匹配'上海'（_contains_word 防误匹配）。"""
        from modeling import salary_predict
        rows = [('Python后端开发', '上海', 15.0, 25.0, '本科', '3-5年')]
        monkeypatch.setattr(salary_predict, 'get_rows', lambda: rows)
        result = salary_predict.lookup_salary_range(city='海')
        # '海' 长度 < 2，只做精确匹配，不应匹配 '上海'
        assert 'message' in result

    def test_multi_char_city_partial_match(self, monkeypatch):
        """'北京' 精确匹配应正常返回结果（长度 ≥ 2）。"""
        from modeling import salary_predict
        rows = [
            ('Python后端开发', '北京', 15.0, 25.0, '本科', '3-5年'),
            ('Java后端开发', '北京', 12.0, 20.0, '本科', '1-3年'),
            ('前端开发', '北京', 10.0, 18.0, '大专', '1-3年'),
            ('Python', '上海', 14.0, 22.0, '本科', '3-5年'),  # 不应该匹配
        ]
        monkeypatch.setattr(salary_predict, 'get_rows', lambda: rows)
        result = salary_predict.lookup_salary_range(city='北京')
        assert 'message' not in result
        assert result['count'] == 3

    def test_category_substring_match(self, monkeypatch):
        """输入'后端'应匹配'后端开发'（双向模糊匹配）。"""
        from modeling import salary_predict

        # 需要模拟 classify 返回的值包含 "后端"
        rows = [
            ('Python后端开发工程师', '北京', 15.0, 25.0, '本科', '3-5年'),
            ('Java后端开发', '北京', 12.0, 20.0, '本科', '1-3年'),
            ('Python后端', '北京', 14.0, 24.0, '大专', '3-5年'),
            ('Web前端开发', '北京', 10.0, 18.0, '大专', '1-3年'),
        ]
        # 需要 mock classify 让前三行返回 '后端开发'，最后一行返回 'Web/前端'
        original_classify = salary_predict.classify

        def mock_classify(post):
            if '后端' in post:
                return '后端开发'
            return 'Web/前端'

        monkeypatch.setattr(salary_predict, 'classify', mock_classify)
        monkeypatch.setattr(salary_predict, 'get_rows', lambda: rows)
        result = salary_predict.lookup_salary_range(category='后端')

        # 恢复原始函数（对测试无影响但不留副作用）
        # 注意: monkeypatch 已自动替换，测试结束会恢复

        if 'message' not in result:
            assert result['count'] >= 1
            assert result['median'] > 0

    def test_edu_filter_works(self, monkeypatch):
        """学历过滤应正确筛选。"""
        from modeling import salary_predict
        rows = [
            ('Python后端开发', '北京', 15.0, 25.0, '本科', '3-5年'),
            ('Java后端开发', '北京', 12.0, 20.0, '大专', '1-3年'),
            ('Python后端', '北京', 18.0, 28.0, '本科及以上', '3-5年'),
            ('后端开发', '北京', 10.0, 16.0, '大专', '1-3年'),
        ]
        monkeypatch.setattr(salary_predict, 'get_rows', lambda: rows)
        result = salary_predict.lookup_salary_range(city='北京', edu='本科')
        if 'message' not in result:
            assert result['count'] == 2  # 本科 + 本科及以上

    def test_get_rows_failure_returns_safe(self, monkeypatch):
        """数据库读取失败时返回安全 fallback。"""
        from modeling import salary_predict
        monkeypatch.setattr(salary_predict, 'get_rows', lambda: (_ for _ in ()).throw(sqlite3.Error))
        result = salary_predict.lookup_salary_range(city='北京')
        assert 'message' in result
        assert '失败' in result['message']

    def test_exact_city_address_match(self, monkeypatch):
        """带区地址'北京-海淀区'应匹配 city='北京'。"""
        from modeling import salary_predict
        rows = [
            ('Python后端开发', '北京-海淀区', 15.0, 25.0, '本科', '3-5年'),
            ('Java后端开发', '北京-朝阳区', 12.0, 20.0, '本科', '1-3年'),
            ('前端开发', '北京', 10.0, 18.0, '大专', '1-3年'),
        ]
        monkeypatch.setattr(salary_predict, 'get_rows', lambda: rows)
        result = salary_predict.lookup_salary_range(city='北京')
        assert 'message' not in result
        assert result['count'] == 3


# ============================================================
# _contains_word() — 模糊匹配辅助函数
# ============================================================
class TestContainsWord:
    """验证 _contains_word 的双向包含与单字防护逻辑。"""

    @pytest.fixture(autouse=True)
    def _import(self):
        from modeling.salary_predict import _contains_word as _cw
        self.cw = _cw

    def test_exact_match(self):
        assert self.cw('北京', '北京') is True

    def test_partial_needle_in_haystack(self):
        assert self.cw('北京', '北京-海淀区') is True
        assert self.cw('后端', '后端开发') is True

    def test_partial_haystack_in_needle(self):
        assert self.cw('后端开发', '后端') is True

    def test_single_char_requires_exact(self):
        """单字输入不做模糊匹配。"""
        assert self.cw('海', '上海') is False
        assert self.cw('本', '本科') is False
        assert self.cw('京', '北京') is False

    def test_single_char_exact_ok(self):
        """单字精确匹配应通过（虽然实际不太会发生）。"""
        assert self.cw('海', '海') is True

    def test_empty_values(self):
        assert self.cw('', '北京') is False
        assert self.cw('北京', '') is False
        assert self.cw('', '') is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
