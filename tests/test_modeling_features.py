"""
测试 modelling/ 新增的四个深度分析模块:
- skill_heatmap.py    (技能供需热力图)
- job_similarity.py   (岗位相似度网络)
- salary_curve.py     (薪资成长曲线)
- edu_premium.py      (学历溢价分析)
"""
import pytest
import sqlite3
import numpy as np


# ============================================================
# 测试辅助：模拟 sqlite3.Row 行为
# ============================================================
class MockRow:
    """模拟 sqlite3.Row 的 dict + attribute 双重访问方式。"""
    def __init__(self, **kwargs):
        self._data = kwargs
    def __getitem__(self, key):
        return self._data[key]
    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError(key)
        return self._data.get(key)


def _mock_rows(rows_data, columns):
    """将 [(val1, val2, ...), ...] 转为 MockRow 列表。
    rows_data: [[col1_val, col2_val, ...], ...]
    columns: ['col_name1', 'col_name2', ...]
    """
    return [MockRow(**dict(zip(columns, row))) for row in rows_data]


def _patch_sqlite(module, mock_rows_data, columns):
    """用 monkeypatch 替换目标 module 的 sqlite3 模块，让 db 查询返回 mock_rows_data。"""
    mock_row_list = _mock_rows(mock_rows_data, columns) if mock_rows_data else []

    class MockCursor:
        def execute(self, q, *args):
            pass
        def fetchall(self):
            return mock_row_list
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    class MockConn:
        row_factory = None
        def cursor(self):
            return MockCursor()
        def close(self):
            pass

    mock = type('_SQLite3Mock', (), {
        'connect': lambda *a, **kw: MockConn(),
        'Error': sqlite3.Error,
        'Row': MockRow,
    })
    return mock


# ============================================================
# skill_heatmap 测试
# ============================================================
class TestSkillHeatmap:
    """验证 compute_skill_heatmap 的核心逻辑。"""

    def test_empty_db_returns_error(self, monkeypatch):
        """空数据库返回 error 信息。"""
        from modeling import skill_heatmap
        mock = _patch_sqlite(skill_heatmap, [], [])
        monkeypatch.setattr(skill_heatmap, 'sqlite3', mock)
        result = skill_heatmap.compute_skill_heatmap()
        assert 'error' in result
        assert result['total_rows'] == 0

    def test_no_valid_keywords(self, monkeypatch):
        """关键词列全为非技术标签且无 post/content 技能时返回 error。"""
        from modeling import skill_heatmap
        rows_data = [
            ('北京', 15.0, 25.0, '五险一金 周末双休', '测试岗位', '负责测试'),
            ('上海', 12.0, 20.0, '带薪年假 年终奖金', '测试岗位', '负责测试'),
        ]
        mock = _patch_sqlite(skill_heatmap, rows_data,
                            ['address', 'salary_min', 'salary_max', 'keywords', 'post', 'content'])
        monkeypatch.setattr(skill_heatmap, 'sqlite3', mock)
        result = skill_heatmap.compute_skill_heatmap()
        assert 'error' in result

    def test_valid_keywords_produces_matrix(self, monkeypatch):
        """有效技术标签应生成矩阵。"""
        from modeling import skill_heatmap
        rows_data = []
        for i in range(5):
            rows_data.append(('北京', 15.0, 25.0, 'Python Django', 'Python后端', 'Python开发'))
            rows_data.append(('上海', 12.0, 20.0, 'Python Django', 'Python后端', 'Python开发'))
            rows_data.append(('深圳', 14.0, 24.0, 'Python Django', 'Python后端', 'Python开发'))
            rows_data.append(('北京', 18.0, 30.0, 'Java Spring', 'Java后端', 'Java开发'))
            rows_data.append(('上海', 16.0, 26.0, 'Java Spring', 'Java后端', 'Java开发'))
            rows_data.append(('深圳', 13.0, 22.0, 'Java Spring', 'Java后端', 'Java开发'))
        mock = _patch_sqlite(skill_heatmap, rows_data,
                            ['address', 'salary_min', 'salary_max', 'keywords', 'post', 'content'])
        monkeypatch.setattr(skill_heatmap, 'sqlite3', mock)
        result = skill_heatmap.compute_skill_heatmap(min_count=3)
        assert 'skills' in result
        assert 'cities' in result
        assert 'salary_matrix' in result
        assert len(result['skills']) >= 2
        assert len(result['cities']) >= 2

    def test__extract_skills_filters_non_tech(self):
        """_extract_skills 应过滤掉非技术标签。"""
        from modeling.skill_heatmap import _extract_skills
        result = _extract_skills('Python 五险一金 周末双休 Django')
        assert 'Python' in result or 'Django' in result
        for tag in result:
            assert tag not in ('五险一金', '周末双休')

    def test__extract_skills_normalizes(self):
        """_extract_skills 应做同义词归一化。"""
        from modeling.skill_heatmap import _extract_skills
        result = _extract_skills('k8s vuejs machinelearning')
        assert 'Kubernetes' in result
        assert 'Vue' in result
        assert '机器学习' in result

    def test__extract_skills_empty(self):
        """空字符串 / None 返回空列表。"""
        from modeling.skill_heatmap import _extract_skills
        assert _extract_skills('') == []
        assert _extract_skills(None) == []


# ============================================================
# job_similarity 测试
# ============================================================
class TestJobSimilarity:
    """验证 compute_similarity_network 的核心逻辑。"""

    def test_empty_clustering_returns_error(self):
        """聚类为空时返回 error。"""
        from modeling import job_similarity
        result = job_similarity.compute_similarity_network(None)
        assert 'error' in result

    def test_single_cluster_returns_error(self):
        """只有一个簇时返回 error。"""
        from modeling import job_similarity
        clustering = {
            'k': 1,
            'clusters': [{
                'cluster_id': 0, 'auto_label': '后端开发',
                'top_keywords': ['Python', 'Django', 'Flask'],
                'count': 10, 'avg_salary': 20.0,
            }]
        }
        result = job_similarity.compute_similarity_network(clustering)
        assert 'error' in result

    def test_two_clusters_similar(self):
        """两个有重叠关键词的簇应产生一条连线。"""
        from modeling import job_similarity
        clustering = {
            'k': 2,
            'clusters': [
                {'cluster_id': 0, 'auto_label': '后端开发',
                 'top_keywords': ['Python', 'Django', 'Flask', 'MySQL'],
                 'count': 10, 'avg_salary': 20.0},
                {'cluster_id': 1, 'auto_label': '数据/AI',
                 'top_keywords': ['Python', 'TensorFlow', 'PyTorch', 'MySQL'],
                 'count': 8, 'avg_salary': 25.0},
            ]
        }
        result = job_similarity.compute_similarity_network(clustering)
        assert 'nodes' in result
        assert len(result['nodes']) == 2
        assert len(result['links']) >= 1
        assert result['links'][0]['value'] > 0
        assert result['nodes'][0]['size'] == 10
        assert result['nodes'][0]['avg_salary'] == 20.0

    def test_two_clusters_dissimilar(self):
        """两个完全不重叠的簇，相似度为 0 被过滤。"""
        from modeling import job_similarity
        clustering = {
            'k': 2,
            'clusters': [
                {'cluster_id': 0, 'auto_label': '后端开发',
                 'top_keywords': ['Python', 'Django'],
                 'count': 10, 'avg_salary': 20.0},
                {'cluster_id': 1, 'auto_label': '前端开发',
                 'top_keywords': ['React', 'Vue'],
                 'count': 8, 'avg_salary': 18.0},
            ]
        }
        result = job_similarity.compute_similarity_network(clustering)
        assert 'nodes' in result
        assert len(result['nodes']) == 2
        assert len(result['links']) == 0


# ============================================================
# salary_curve 测试
# ============================================================
class TestSalaryCurve:
    """验证 compute_salary_curve 的核心逻辑。"""

    def test_empty_db_returns_error(self, monkeypatch):
        """空数据库返回 error。"""
        from modeling import salary_curve
        mock = _patch_sqlite(salary_curve, [], [])
        monkeypatch.setattr(salary_curve, 'sqlite3', mock)
        result = salary_curve.compute_salary_curve()
        assert 'error' in result

    def test_normalize_exper(self):
        """_normalize_exper 应正确排序。"""
        from modeling.salary_curve import _normalize_exper
        o1, _ = _normalize_exper('3-5年')
        o2, _ = _normalize_exper('1-3年')
        o3, _ = _normalize_exper('10年以上')
        assert o1 > o2
        assert o3 > o1
        o4, _ = _normalize_exper('经验不限')
        assert o4 < o2

    def test_normalize_exper_unparseable_defaults_to_不限(self):
        """无法解析出年限的写法并入'经验不限'，不再自成档位。"""
        from modeling.salary_curve import _normalize_exper
        assert _normalize_exper('随便写的') == (0, '经验不限')
        assert _normalize_exper('无需经验') == (0, '经验不限')
        assert _normalize_exper(None) == (0, '经验不限')

    def test_normalize_exper_lower_bound_wording(self):
        """51job 的「X年及以上」按下限归档，与区间式写法同档可比。"""
        from modeling.salary_curve import _normalize_exper
        assert _normalize_exper('3年及以上') == (2, '3-5年')
        assert _normalize_exper('5年及以上') == (3, '5-10年')
        assert _normalize_exper('1年及以上') == (1, '1-3年')

    def test_basic_curve_structure(self, monkeypatch):
        """有数据时应返回 overall 和 by_city。"""
        from modeling import salary_curve
        rows_data = [
            ('北京', 15.0, 25.0, '1-3年'), ('北京', 12.0, 20.0, '1-3年'),
            ('北京', 10.0, 18.0, '1-3年'), ('北京', 18.0, 30.0, '3-5年'),
            ('北京', 20.0, 35.0, '3-5年'), ('北京', 22.0, 38.0, '3-5年'),
            ('上海', 14.0, 24.0, '1-3年'), ('上海', 16.0, 26.0, '1-3年'),
            ('上海', 15.0, 25.0, '1-3年'), ('上海', 20.0, 32.0, '3-5年'),
            ('上海', 22.0, 34.0, '3-5年'), ('上海', 24.0, 36.0, '3-5年'),
        ]
        mock = _patch_sqlite(salary_curve, rows_data,
                            ['address', 'salary_min', 'salary_max', 'exper'])
        monkeypatch.setattr(salary_curve, 'sqlite3', mock)
        result = salary_curve.compute_salary_curve(min_per_level=3)
        assert 'overall' in result
        assert len(result['overall']) >= 2
        for item in result['overall']:
            assert 'exper' in item
            assert 'median' in item
            assert 'mean' in item
            assert item['count'] >= 3
        assert 'by_city' in result
        assert '北京' in result['by_city'] or '上海' in result['by_city']


# ============================================================
# edu_premium 测试
# ============================================================
class TestEduPremium:
    """验证 compute_edu_premium 的核心逻辑。"""

    def test_empty_db_returns_error(self, monkeypatch):
        """空数据库返回 error。"""
        from modeling import edu_premium
        mock = _patch_sqlite(edu_premium, [], [])
        monkeypatch.setattr(edu_premium, 'sqlite3', mock)
        result = edu_premium.compute_edu_premium()
        assert 'error' in result

    def test_normalize_edu(self):
        """_normalize_edu 应正确归一化学历。"""
        from modeling.edu_premium import _normalize_edu
        assert _normalize_edu('本科及以上') == '本科'
        assert _normalize_edu('硕士研究生') == '硕士'
        assert _normalize_edu('大学专科') == '大专'
        assert _normalize_edu('博士研究生') == '博士'
        assert _normalize_edu('不限') == '不限'
        assert _normalize_edu('') == '不限'

    def test_basic_premium_structure(self, monkeypatch):
        """有数据时应返回 premiums 和 overall。"""
        from modeling import edu_premium
        monkeypatch.setattr(edu_premium, 'classify', lambda p: '后端开发')

        rows_data = [
            ('Python后端开发', '北京', 15.0, 25.0, '本科'),
            ('Java后端开发', '北京', 12.0, 20.0, '本科'),
            ('Go后端开发', '北京', 14.0, 22.0, '本科'),
            ('Python后端开发', '北京', 20.0, 30.0, '硕士'),
            ('Java后端开发', '北京', 18.0, 28.0, '硕士'),
            ('Go后端开发', '北京', 19.0, 29.0, '硕士'),
            ('Python后端开发', '上海', 14.0, 24.0, '本科'),
            ('Java后端开发', '上海', 13.0, 21.0, '本科'),
            ('Go后端开发', '上海', 15.0, 23.0, '本科'),
            ('Python后端开发', '上海', 19.0, 29.0, '硕士'),
            ('Java后端开发', '上海', 18.0, 27.0, '硕士'),
            ('Go后端开发', '上海', 20.0, 30.0, '硕士'),
        ]
        mock = _patch_sqlite(edu_premium, rows_data,
                            ['post', 'address', 'salary_min', 'salary_max', 'edu'])
        monkeypatch.setattr(edu_premium, 'sqlite3', mock)
        result = edu_premium.compute_edu_premium(min_per_group=3)

        assert 'premiums' in result
        assert len(result['premiums']) >= 1
        for p in result['premiums']:
            for prem in p['premiums']:
                if prem['from'] == '本科' and prem['to'] == '硕士':
                    assert prem['premium_pct'] > 0
        assert 'overall' in result
        assert '本科' in result['overall']
        assert '硕士' in result['overall']

    def test_premium_negative_possible(self, monkeypatch):
        """当高学历样本均薪反而低时，溢价应为负。"""
        from modeling import edu_premium
        monkeypatch.setattr(edu_premium, 'classify', lambda p: '测试方向')

        rows_data = [
            ('测试职位', '测试城市', 10.0, 15.0, '本科'),
            ('测试职位', '测试城市', 11.0, 16.0, '本科'),
            ('测试职位', '测试城市', 10.5, 15.5, '本科'),
            ('测试职位', '测试城市', 8.0, 12.0, '硕士'),
            ('测试职位', '测试城市', 7.0, 11.0, '硕士'),
            ('测试职位', '测试城市', 9.0, 13.0, '硕士'),
        ]
        mock = _patch_sqlite(edu_premium, rows_data,
                            ['post', 'address', 'salary_min', 'salary_max', 'edu'])
        monkeypatch.setattr(edu_premium, 'sqlite3', mock)
        result = edu_premium.compute_edu_premium(min_per_group=3)
        if result.get('premiums'):
            for p in result['premiums']:
                for prem in p['premiums']:
                    if prem['from'] == '本科' and prem['to'] == '硕士':
                        assert prem['premium_pct'] < 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
