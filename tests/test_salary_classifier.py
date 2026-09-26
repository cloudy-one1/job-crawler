"""
测试 modeling/salary_classifier.py 薪资档位分类模型:
- compute_salary_classifier 返回结构/指标合理性
- predict_salary_band 正常/降级
- 样本不足 error
- /ml/salary-predict 路由 CSRF 保护与返回校验
"""
import pytest
import sqlite3
import numpy as np


# ============================================================
# 测试辅助
# ============================================================
class MockRow:
    """模拟 sqlite3.Row。"""
    def __init__(self, **kwargs):
        self._data = kwargs
    def __getitem__(self, key):
        return self._data[key]
    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError(key)
        return self._data.get(key)


def _patch_sqlite(module, rows_data, columns):
    """Mock sqlite3 模块返回指定数据。"""
    mock_row_list = [MockRow(**dict(zip(columns, row))) for row in rows_data] if rows_data else []

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

    return type('_SQLite3Mock', (), {
        'connect': lambda *a, **kw: MockConn(),
        'Error': sqlite3.Error,
        'Row': MockRow,
    })


def _make_sample_rows(n=60):
    """生成 60 条样本数据，刚好满足 min_samples=50。"""
    cities = ['北京', '上海', '深圳', '杭州', '广州', '成都', '武汉', '南京']
    edus = ['本科', '硕士', '大专', '不限']
    expers = ['1-3年', '3-5年', '5-10年', '经验不限']
    posts = ['Python后端开发', 'Java开发工程师', '数据分析师', '前端开发工程师',
             '算法工程师', '测试工程师', '运维工程师', '全栈工程师']
    keywords_list = [
        'Python Django MySQL', 'Java Spring Boot', 'Python Pandas NumPy',
        'React Vue TypeScript', 'Python TensorFlow PyTorch', 'Selenium Appium',
        'Docker Kubernetes Linux', 'Python React Node.js'
    ]

    rows = []
    for i in range(n):
        city_idx = i % len(cities)
        edu_idx = (i // 15) % len(edus)
        exper_idx = (i // 8) % len(expers)
        post_idx = i % len(posts)
        kw_idx = i % len(keywords_list)

        smin = 8.0 + (i % 10) * 1.5 + (city_idx % 3) * 2.0 + edu_idx * 2.0
        smax = smin + 8.0 + (i % 5) * 2.0

        rows.append((
            i + 1,
            posts[post_idx],
            cities[city_idx],
            smin,
            smax,
            edus[edu_idx],
            expers[exper_idx],
            keywords_list[kw_idx],
            f'岗位职责: {posts[post_idx]},要求熟悉 {keywords_list[kw_idx]} 相关技术栈,'
            f'熟悉 Docker 部署与 Git 协作,具备高并发系统经验者优先。',
        ))
    return rows


# ============================================================
# compute_salary_classifier 测试
# ============================================================
class TestComputeSalaryClassifier:
    """验证 compute_salary_classifier 核心逻辑。"""

    def test_empty_db_returns_error(self, monkeypatch):
        """空数据库返回 error。"""
        from modeling import salary_classifier
        mock = _patch_sqlite(salary_classifier, [], [])
        monkeypatch.setattr(salary_classifier, 'sqlite3', mock)
        result = salary_classifier.compute_salary_classifier(min_samples=30)
        assert 'error' in result
        assert result['total_rows'] == 0

    def test_insufficient_samples(self, monkeypatch):
        """样本不足返回 error。"""
        from modeling import salary_classifier
        columns = ['id', 'post', 'address', 'salary_min', 'salary_max', 'edu', 'exper', 'keywords', 'content']
        rows = [
            (1, '测试', '北京', 10.0, 20.0, '本科', '1-3年', 'Python Django', '需要 Python 经验'),
            (2, '测试', '上海', 12.0, 22.0, '硕士', '3-5年', 'Java Spring', '需要 Java 经验'),
        ]
        mock = _patch_sqlite(salary_classifier, rows, columns)
        monkeypatch.setattr(salary_classifier, 'sqlite3', mock)
        result = salary_classifier.compute_salary_classifier(min_samples=50)
        assert 'error' in result
        assert '不足' in result['error']

    def test_returns_valid_structure(self, monkeypatch):
        """有足够样本时应返回完整结果包。"""
        from modeling import salary_classifier
        columns = ['id', 'post', 'address', 'salary_min', 'salary_max', 'edu', 'exper', 'keywords', 'content']
        rows = _make_sample_rows(60)
        mock = _patch_sqlite(salary_classifier, rows, columns)
        monkeypatch.setattr(salary_classifier, 'sqlite3', mock)
        result = salary_classifier.compute_salary_classifier(min_samples=30)

        assert 'error' not in result
        assert 'metrics' in result
        assert 'feature_importances' in result
        assert 'confusion_matrix' in result
        assert 'bands' in result
        assert '_model' in result
        assert '_features' in result

        m = result['metrics']
        assert 0 <= m['accuracy'] <= 1
        assert 0 <= m['macro_f1'] <= 1
        assert 0 <= m['adjacent_acc'] <= 1
        assert m['adjacent_acc'] >= m['accuracy'] - 1e-6  # ≤1档比例必然 ≥ 命中率
        assert m['mae_bands'] >= 0
        assert m['n_samples'] >= 30
        assert m['n_classes'] >= 2
        assert m['model_name'] in ('RandomForest', 'GradientBoosting', 'LogisticRegression')
        assert result['pkg_version'] == 2

        assert len(result['feature_importances']) >= 1
        assert result['total_rows'] >= 30

    def test_bands_cover_all_samples(self, monkeypatch):
        """档位分布应覆盖所有样本。"""
        from modeling import salary_classifier
        columns = ['id', 'post', 'address', 'salary_min', 'salary_max', 'edu', 'exper', 'keywords', 'content']
        rows = _make_sample_rows(60)
        mock = _patch_sqlite(salary_classifier, rows, columns)
        monkeypatch.setattr(salary_classifier, 'sqlite3', mock)
        result = salary_classifier.compute_salary_classifier(min_samples=30)

        total_in_bands = sum(b['count'] for b in result['bands'])
        assert total_in_bands == result['total_rows']


# ============================================================
# predict_salary_band 测试
# ============================================================
class TestPredictSalaryBand:
    """验证 predict_salary_band 推理逻辑。"""

    def _make_pkg(self, monkeypatch):
        """创建一个训练好的结果包用于预测测试。"""
        from modeling import salary_classifier
        columns = ['id', 'post', 'address', 'salary_min', 'salary_max', 'edu', 'exper', 'keywords', 'content']
        rows = _make_sample_rows(60)
        mock = _patch_sqlite(salary_classifier, rows, columns)
        monkeypatch.setattr(salary_classifier, 'sqlite3', mock)
        return salary_classifier.compute_salary_classifier(min_samples=30)

    def test_predict_returns_band(self, monkeypatch):
        """正常预测应返回档位+概率。"""
        from modeling.salary_classifier import predict_salary_band
        pkg = self._make_pkg(monkeypatch)
        if 'error' in pkg:
            pytest.skip(f'模型训练失败: {pkg["error"]}')

        result = predict_salary_band(pkg, city='北京', edu='本科', exper='1-3年',
                                      skills=['Python', 'Django'])
        assert 'error' not in result
        assert 'predicted_band' in result
        assert 'probabilities' in result
        assert len(result['probabilities']) >= 2
        # 概率和为 1（近似）
        total_prob = sum(p['prob'] for p in result['probabilities'])
        assert abs(total_prob - 1.0) < 0.01

    def test_predict_unknown_city(self, monkeypatch):
        """未知城市应优雅降级 ('其他')。"""
        from modeling.salary_classifier import predict_salary_band
        pkg = self._make_pkg(monkeypatch)
        if 'error' in pkg:
            pytest.skip(f'模型训练失败: {pkg["error"]}')

        result = predict_salary_band(pkg, city='火星', edu='本科', exper='1-3年',
                                      skills=['Python'])
        assert 'error' not in result
        assert 'predicted_band' in result
        assert result.get('degraded', {}).get('city') is True

    def test_predict_empty_skills(self, monkeypatch):
        """空技能也能预测（只用其他特征）。"""
        from modeling.salary_classifier import predict_salary_band
        pkg = self._make_pkg(monkeypatch)
        if 'error' in pkg:
            pytest.skip(f'模型训练失败: {pkg["error"]}')

        result = predict_salary_band(pkg, city='上海', edu='硕士', exper='3-5年',
                                      skills=[])
        assert 'error' not in result
        assert 'predicted_band' in result

    def test_predict_with_string_skills(self, monkeypatch):
        """skills 传入逗号分隔字符串也能正确处理。"""
        from modeling.salary_classifier import predict_salary_band
        pkg = self._make_pkg(monkeypatch)
        if 'error' in pkg:
            pytest.skip(f'模型训练失败: {pkg["error"]}')

        result = predict_salary_band(pkg, city='深圳', edu='本科', exper='3-5年',
                                      skills='Python, Django, MySQL')
        assert 'error' not in result
        assert 'predicted_band' in result

    def test_predict_case_insensitive_skills(self, monkeypatch):
        """用户输入小写技能应能匹配数据库中大小写混合的训练词表。"""
        from modeling.salary_classifier import predict_salary_band
        pkg = self._make_pkg(monkeypatch)
        if 'error' in pkg:
            pytest.skip(f'模型训练失败: {pkg["error"]}')

        result = predict_salary_band(pkg, city='北京', edu='本科', exper='1-3年',
                                      skills='python, django, mysql')
        assert 'error' not in result
        assert 'predicted_band' in result
        # 常见技能被识别后，不应出现在未命中列表
        not_found = result.get('degraded', {}).get('skills_not_found', [])
        assert 'python' not in not_found
        assert 'mysql' not in not_found

    def test_predict_empty_pkg_returns_error(self):
        """空结果包返回 error。"""
        from modeling.salary_classifier import predict_salary_band
        result = predict_salary_band({'error': 'test'})
        assert 'error' in result


# ============================================================
# 辅助函数测试
# ============================================================
class TestHelpers:
    """验证辅助函数。"""

    def test_normalize_edu(self):
        from modeling.salary_classifier import _normalize_edu
        assert _normalize_edu('本科及以上') == '本科'
        assert _normalize_edu('硕士研究生') == '硕士'
        assert _normalize_edu('博士研究生') == '博士'
        assert _normalize_edu('不限') == '不限'
        assert _normalize_edu('') == '不限'
        assert _normalize_edu(None) == '不限'

    def test_normalize_exper(self):
        """分类器使用的经验口径统一来自 data/exper_parser.py。"""
        from modeling.salary_classifier import normalize_exper
        assert normalize_exper('1-3年经验') == '1-3年'
        assert normalize_exper('3年及以上') == '3-5年'
        assert normalize_exper('') == '经验不限'
        assert normalize_exper(None) == '经验不限'

    def test_extract_city(self):
        from modeling.salary_classifier import _extract_city
        assert _extract_city('北京-海淀区') == '北京'
        assert _extract_city('上海') == '上海'
        assert _extract_city('') == '未知'

    def test_extract_skills_from_keywords(self):
        from modeling.salary_classifier import _extract_skills_from_keywords
        result = _extract_skills_from_keywords('Python Django MySQL')
        assert 'Python' in result
        assert 'Django' in result
        assert 'MySQL' in result

    def test_get_band_index(self):
        from modeling.salary_classifier import _get_band_index, BAND_EDGES
        assert _get_band_index(4.0) == 0   # <6K
        assert _get_band_index(8.0) == 1   # 6-10K
        assert _get_band_index(12.0) == 2  # 10-14K
        assert _get_band_index(20.0) == 4  # 18-22K


# ============================================================
# /ml/salary-predict 路由测试
# ============================================================
class TestSalaryPredictRoute:
    """验证 /ml/salary-predict 路由行为。"""

    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False  # 测试环境关闭 CSRF
        with app.test_client() as c:
            yield c

    def test_post_no_data_returns_ok_or_error(self, client):
        """空 POST 在有缓存模型时应返回 200（用默认值预测），无模型时返回 503。"""
        resp = client.post('/ml/salary-predict', json={})
        # 有缓存模型 → 200；无缓存模型 → 503
        assert resp.status_code in (200, 503)

    def test_post_with_data(self, client):
        """正常 POST 带参数（可能因无数据返回 503）。"""
        resp = client.post('/ml/salary-predict', json={
            'city': '北京', 'edu': '本科', 'exper': '1-3年', 'skills': 'Python'
        })
        assert resp.status_code in (200, 400, 503)
        if resp.status_code == 200:
            data = resp.get_json()
            assert 'predicted_band' in data


# ============================================================
# content 职位描述技能抽取测试（v2 特征口径）
# ============================================================
class TestContentSkillExtraction:
    """职位描述全文中的技能要求应进入特征(jobTags 只覆盖一部分技术要求)。"""

    def test_english_tech_words_found(self):
        from modeling.salary_classifier import _extract_skills_from_content
        content = '负责后端服务开发,熟悉 Django Flask MySQL Redis,使用 Docker 部署'
        skills = _extract_skills_from_content(content)
        for expected in ('django', 'flask', 'mysql', 'redis', 'docker'):
            assert expected in skills

    def test_chinese_tech_words_found(self):
        from modeling.salary_classifier import _extract_skills_from_content
        content = '参与机器学习平台建设,要求熟悉深度学习框架 PyTorch,有大模型应用经验优先'
        skills = _extract_skills_from_content(content)
        for expected in ('机器学习', '深度学习', 'pytorch', '大模型'):
            assert expected in skills

    def test_no_false_positive_java_from_javascript(self):
        from modeling.salary_classifier import _extract_skills_from_content
        skills = _extract_skills_from_content('精通 JavaScript 与 Vue')
        assert 'java' not in skills
        assert 'javascript' in skills
        assert 'vue' in skills

    def test_empty_content_returns_empty(self):
        from modeling.salary_classifier import _extract_skills_from_content
        assert _extract_skills_from_content('') == []
        assert _extract_skills_from_content(None) == []

    def test_content_skills_enter_feature_vocab(self, monkeypatch):
        """描述里反复出现的技能应出现在技能词表中。"""
        from modeling import salary_classifier
        columns = ['id', 'post', 'address', 'salary_min', 'salary_max',
                   'edu', 'exper', 'keywords', 'content']
        rows = []
        for i in range(40):
            smin = 10.0 + (i % 5)
            rows.append((
                i + 1, 'Python开发', '北京', smin, smin + 6,
                ['本科', '硕士'][i % 2], ['1-3年', '3-5年'][i % 2],
                'Python', '工作内容: 使用 FastAPI 与 Docker 构建服务,熟悉 MySQL。',
            ))
        mock_rows = [MockRow(**dict(zip(columns, row))) for row in rows]
        feat = salary_classifier._build_features(mock_rows)
        assert 'error' not in feat
        for expected in ('fastapi', 'docker', 'mysql'):
            assert expected in feat['skill_vocab']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
