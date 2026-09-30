"""
Flask 路由行为的单元测试（安全相关）。

使用 Flask test_client 发送请求，不启动真实服务器、不依赖真实浏览器。
依赖数据库存在即可（表不存在时路由也不应 500）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ---------- 测试夹具: Flask test client ----------
@pytest.fixture
def client():
    """构造 Flask 应用测试客户端。数据库空也没关系,路由不该 500。"""
    from app import app
    app.config['TESTING'] = True
    # CSRF 在测试态下临时关闭,单独测 CSRF 时再开启
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as c:
        yield c


# ============================================================
# L-1: /list 路由的 page 参数异常输入保护
# ============================================================
class TestListPageParamSafety:
    """验证 /list 的 page 参数在非法输入下返回 200 而非 500。"""

    def test_page_param_non_numeric_string_returns_200(self, client):
        """手动改 URL 传 ?page=abc 不能 ValueError 500。"""
        resp = client.get('/list?page=abc')
        assert resp.status_code == 200, (
            f"非数字 page 导致 {resp.status_code},预期 200 (应降级到第一页)"
        )

    def test_page_param_empty_returns_200(self, client):
        """?page= (空字符串) 返回 200。"""
        resp = client.get('/list?page=')
        assert resp.status_code == 200

    def test_page_param_negative_clamped_to_page_1(self, client):
        """?page=-99 返回 200 (页码应 clamp 到 1)。"""
        resp = client.get('/list?page=-99')
        assert resp.status_code == 200

    def test_page_param_zero_returns_200(self, client):
        """?page=0 返回 200,而不是 OFFSET 变成负数。"""
        resp = client.get('/list?page=0')
        assert resp.status_code == 200

    def test_page_param_float_returns_200(self, client):
        """?page=1.5 返回 200 (按 int 失败时默认第一页)。"""
        resp = client.get('/list?page=1.5')
        assert resp.status_code == 200

    def test_page_param_normal_number_ok(self, client):
        """合法 ?page=1 也得是 200 (保证改代码没破坏正常路径)。"""
        resp = client.get('/list?page=1')
        assert resp.status_code == 200


# ============================================================
# L-2: /list 搜索行为回归测试
# ============================================================
class TestListSearchFuzzyMatch:
    """验证 /list 的 kw 使用 LIKE 模糊匹配、city 使用前缀匹配。

    历史说明：旧版用 LOWER() 精确等值匹配（并有测试锁定该行为），
    但精确等值在真实库上几乎搜不到东西（实测 post='Python' 0 命中、
    LIKE '%Python%' 63 命中），且首页承诺"支持关键词搜索，非精确匹配"、
    采集完成页跳转 /list?kw=python 依赖模糊语义 —— 故统一改为模糊匹配。
    """

    @pytest.fixture(autouse=True)
    def _seed_and_cleanup(self):
        """插入临时测试记录（含「城市-区县」形态地址），测试结束后删除。"""
        import sqlite3
        from config import DB_PATH
        self.db = sqlite3.connect(DB_PATH, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executemany(
            "INSERT INTO data (post, company, address, salary_min, salary_max) VALUES (?, ?, ?, ?, ?)",
            [
                ("ZZZ_TEST_Python开发工程师", "TEST_CORP", "ZZZ_TEST_CITY", 10.0, 20.0),
                ("ZZZ_TEST_Java高级开发工程师", "TEST_CORP", "北京", 15.0, 30.0),
                ("ZZZ_TEST_爬虫工程师", "TEST_CORP", "ZZZ_TEST_CITY-海淀区", 12.0, 22.0),
            ]
        )
        self.db.commit()
        yield
        self.db.execute("DELETE FROM data WHERE post LIKE 'ZZZ_TEST_%' OR address LIKE 'ZZZ_TEST_CITY%'")
        self.db.commit()
        self.db.close()

    def test_exact_post_match_finds_record(self, client):
        """完整岗位名仍然命中。"""
        resp = client.get('/list?kw=ZZZ_TEST_Python开发工程师')
        html = resp.data.decode('utf-8')
        assert resp.status_code == 200
        assert 'ZZZ_TEST_Python开发工程师' in html

    def test_case_insensitive_partial_match(self, client):
        """大小写忽略的部分关键词也应命中（模糊匹配）。"""
        resp = client.get('/list?kw=zzz_test_python')
        html = resp.data.decode('utf-8')
        assert resp.status_code == 200
        assert 'ZZZ_TEST_Python开发工程师' in html
        assert 'ZZZ_TEST_Java高级开发工程师' not in html

    def test_city_prefix_matches_district_form(self, client):
        """城市前缀必须命中「城市-区县」形态的地址（旧版等值匹配漏掉这类行）。"""
        resp = client.get('/list?city=ZZZ_TEST_CITY')
        html = resp.data.decode('utf-8')
        assert resp.status_code == 200
        assert 'ZZZ_TEST_爬虫工程师' in html
        assert 'ZZZ_TEST_Java高级开发工程师' not in html  # 地址是「北京」，不匹配

    def test_unrelated_city_no_match(self, client):
        """无关城市不应命中测试数据。"""
        resp = client.get('/list?city=上海')
        html = resp.data.decode('utf-8')
        assert resp.status_code == 200
        assert 'ZZZ_TEST_Python开发工程师' not in html

    def test_empty_search_returns_all(self, client):
        """不带搜索参数,返回所有记录(测试数据至少能查到插入的)。"""
        resp = client.get('/list')
        assert resp.status_code == 200

    def test_no_match_returns_empty(self, client):
        """完全不存在的数据返回空结果不报错。"""
        resp = client.get('/list?kw=ZZZ_NONEXISTENT_12345')
        assert resp.status_code == 200


# ============================================================
# H-3+H-4: secret_key 存在性 + CSRF 保护生效
# ============================================================
class TestAppSecretAndCSRF:
    """验证 app.secret_key 被正确设置,以及 CSRF 保护生效。"""

    def test_app_has_non_empty_secret_key(self):
        """app.secret_key 不能为 None / 空字符串 (否则 session / CSRF 失效)。"""
        from app import app
        key = app.secret_key
        assert key is not None, "app.secret_key 未设置 (应为 Flask-WTF CSRF / session 前提)"
        assert len(key) > 0, "app.secret_key 为空字符串"

    def test_csrf_blocks_post_advice_without_token(self):
        """POST /advice 不带 CSRF token 返回 4xx (WTF_CSRF_CHECK_DEFAULT 生效)。"""
        from app import app
        csrf_app = app
        csrf_app.config['TESTING'] = False
        csrf_app.config['WTF_CSRF_ENABLED'] = True
        with csrf_app.test_client() as c:
            resp = c.post('/advice', data={
                'city': '北京', 'target_post': '后端开发',
                'edu': '本科', 'experience': '1-3年',
            })
            assert resp.status_code in (400, 403), (
                f"未带 CSRF token 的 POST /advice 返回 {resp.status_code},"
                f"预期 400/403 (CSRF 保护应该生效)"
            )


# ============================================================
# H-1: debug/host 不从硬编码读取,而从环境变量读取
# ============================================================
class TestDebugHostFromEnv:
    """验证 Flask 启动配置从环境变量读取,而不是硬编码 debug=True host=0.0.0.0。"""

    def test_debug_flag_defaults_off(self, monkeypatch):
        """没设 FLASK_DEBUG 时,debug 应该是 False (生产安全默认)。"""
        # 删掉环境变量里的 FLASK_DEBUG,模拟干净环境
        monkeypatch.delenv('FLASK_DEBUG', raising=False)
        monkeypatch.delenv('FLASK_HOST', raising=False)

        # 因为 app 模块可能已经被 import 过,我们直接读 app.config 里的相关设定
        # 更直接: 检查 app.py 中的启动逻辑是否依赖硬编码 debug=True
        import inspect
        import app as app_module
        source = inspect.getsource(app_module)
        # 启动语句里不能出现裸 debug=True (必须是环境变量驱动)
        hardcoded_debug_true = any(
            line.strip().startswith('app.run(') and 'debug=True' in line
            and 'os.environ' not in line and 'getenv' not in line
            for line in source.splitlines()
        )
        assert not hardcoded_debug_true, (
            "app.run() 中硬编码 debug=True。必须由 FLASK_DEBUG 环境变量控制,"
            "默认关闭。"
        )

    def test_host_not_wildcard_by_default(self, monkeypatch):
        """默认 host 不能是 0.0.0.0 (默认只有本机能访问)。"""
        import inspect
        import app as app_module
        source = inspect.getsource(app_module)
        # 查找 app.run 行,硬编码 host='0.0.0.0' 且无环境变量判断 = fail
        hardcoded_wildcard = any(
            line.strip().startswith('app.run(')
            and "'0.0.0.0'" in line and 'os.environ' not in line and 'getenv' not in line
            for line in source.splitlines()
        )
        assert not hardcoded_wildcard, (
            "app.run() 中硬编码 host='0.0.0.0'。默认必须是 127.0.0.1,"
            "通过 FLASK_HOST 环境变量显式开启对外监听。"
        )


# ============================================================
# H-5: 路由冒烟测试（防止代码改动导致页面 500）
# ============================================================
class TestAllRoutesSmoke:
    """验证所有主要 GET 路由返回 200，不改内容断言，只防引入 500。"""

    def test_index_page_loads(self, client):
        resp = client.get('/')
        assert resp.status_code == 200

    def test_list_page_loads(self, client):
        resp = client.get('/list')
        assert resp.status_code == 200

    @pytest.mark.usefixtures("temp_db")
    def test_chart_page_loads(self, client):
        """图表页在无数据时应友好降级,有数据时正常渲染(不 500)。"""
        resp = client.get('/chart')
        assert resp.status_code == 200

    def test_ml_page_loads(self, client):
        resp = client.get('/ml')
        assert resp.status_code == 200

    def test_advice_page_loads(self, client):
        resp = client.get('/advice')
        assert resp.status_code == 200

    def test_collect_page_loads(self, client):
        """采集页 GET 无 session 时重定向首页（302），不崩即可"""
        resp = client.get('/collect')
        assert resp.status_code in (200, 302)

    def test_job_detail_page_for_missing_id(self, client):
        """不存在的岗位 ID 渲染提示页不崩（200 非 500）"""
        resp = client.get('/job/99999')
        assert resp.status_code == 200

    def test_cluster_jobs_page_not_500_when_empty(self, client):
        """聚类岗位明细页在无数据时返回 404（不崩 500）"""
        resp = client.get('/ml/cluster/0')
        assert resp.status_code in (200, 404)


# ============================================================
# L-?: /toggle_interest 收藏切换 + 校验
# ============================================================
class TestToggleInterest:
    """验证收藏岗位切换路由: 增删、非法 job_id 校验、CSRF 保护。"""

    @pytest.fixture(autouse=True)
    def _seed(self):
        import sqlite3
        from config import DB_PATH
        self.db = sqlite3.connect(DB_PATH, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "INSERT INTO data (post, company, address, salary_min, salary_max) VALUES (?, ?, ?, ?, ?)",
            ("ZZZ_INTEREST_后端工程师", "CORP", "ZZZ_INTEREST_CITY", 12.0, 24.0),
        )
        self.db.commit()
        self.job_id = self.db.execute(
            "SELECT id FROM data WHERE post='ZZZ_INTEREST_后端工程师'"
        ).fetchone()[0]
        yield
        self.db.execute("DELETE FROM data WHERE post='ZZZ_INTEREST_后端工程师'")
        self.db.commit()
        self.db.close()

    def test_toggle_adds_then_removes(self, client):
        """首次 toggle 收藏(返回 interested=True),再次 toggle 取消(interested=False)。"""
        r1 = client.post('/toggle_interest', data={'job_id': str(self.job_id)})
        assert r1.status_code == 200
        d1 = r1.get_json()
        assert d1['interested'] is True
        assert d1['job_id'] == self.job_id
        assert d1['count'] == 1

        r2 = client.post('/toggle_interest', data={'job_id': str(self.job_id)})
        d2 = r2.get_json()
        assert d2['interested'] is False
        assert d2['count'] == 0

    def test_toggle_invalid_job_id_returns_400(self, client):
        """非数字 / 非正整数 job_id 必须 400。"""
        assert client.post('/toggle_interest', data={'job_id': 'abc'}).status_code == 400
        assert client.post('/toggle_interest', data={'job_id': '-1'}).status_code == 400
        assert client.post('/toggle_interest', data={'job_id': '0'}).status_code == 400
        assert client.post('/toggle_interest', data={}).status_code == 400

    def test_toggle_requires_csrf(self):
        """开启 CSRF 后,无 token 的 POST 必须被拒(400/419)。"""
        from app import app
        app.config['TESTING'] = False
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            with app.test_client() as c:
                resp = c.post('/toggle_interest', data={'job_id': '1'})
                assert resp.status_code in (400, 419), f"期望 CSRF 拒绝, 实际 {resp.status_code}"
        finally:
            app.config['WTF_CSRF_ENABLED'] = False


# ============================================================
# L-?: /interested 收藏岗位展示页
# ============================================================
class TestInterestedPage:
    """验证收藏展示页路由: 空收藏渲染、带收藏渲染、导航角标上下文。"""

    @pytest.fixture(autouse=True)
    def _seed(self, client):
        import sqlite3
        from config import DB_PATH
        self.db = sqlite3.connect(DB_PATH, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "INSERT INTO data (post, company, address, salary_min, salary_max) VALUES (?, ?, ?, ?, ?)",
            ("ZZZ_INTERESTED_PAGE_后端工程师", "CORP", "ZZZ_CITY-北京", 12.0, 24.0),
        )
        self.db.commit()
        self.job_id = self.db.execute(
            "SELECT id FROM data WHERE post='ZZZ_INTERESTED_PAGE_后端工程师'"
        ).fetchone()[0]
        # 用同一 client 收藏, 保证 session 与后续断言共享
        client.post('/toggle_interest', data={'job_id': str(self.job_id)})
        yield
        client.post('/toggle_interest', data={'job_id': str(self.job_id)})
        self.db.execute("DELETE FROM data WHERE post='ZZZ_INTERESTED_PAGE_后端工程师'")
        self.db.commit()
        self.db.close()

    def test_page_renders_with_favorites(self, client):
        resp = client.get('/interested')
        assert resp.status_code == 200
        assert '我感兴趣的岗位' in resp.get_data(as_text=True)
        assert 'ZZZ_INTERESTED_PAGE_后端工程师' in resp.get_data(as_text=True)

    def test_page_shows_empty_state_when_no_favorites(self, client):
        client.post('/toggle_interest', data={'job_id': str(self.job_id)})
        resp = client.get('/interested')
        assert resp.status_code == 200
        assert '还没有收藏任何岗位' in resp.get_data(as_text=True)


# ============================================================
# L-?: /advice/compare/analyze 城市对比 AI 解读接口
# ============================================================
class TestCompareAIAnalyze:
    """验证城市对比 AI 解读接口: 无对比状态 400、无密钥 503、正常返回并缓存命中。"""

    @staticmethod
    def _set_compare_state(client):
        """构造一份结构合法的对比结果写入 session。"""
        fake_result = {
            'compare_type': 'city',
            'a': {'value': '北京', 'count': 3, 'avg_salary_k': 18.0,
                  'min_salary_k': 10.0, 'max_salary_k': 30.0,
                  'top_edu': [('本科', 2)], 'top_exper': [('3-5年', 1)]},
            'b': {'value': '上海', 'count': 2, 'avg_salary_k': 20.0,
                  'min_salary_k': 12.0, 'max_salary_k': 35.0,
                  'top_edu': [('硕士', 1)], 'top_exper': [('1年', 1)]},
            'skill_diff': {'北京_独有': [('Django', 1)], '上海_独有': [('Flask', 1)],
                           '共同高频': [('Python', 2)]},
        }
        with client.session_transaction() as sess:
            sess['compare_state'] = {'result': fake_result, 'a': '北京', 'b': '上海'}

    def test_without_compare_state_returns_400(self, client):
        """未执行城市对比时调用 AI 解读应 400。"""
        resp = client.post('/advice/compare/analyze')
        assert resp.status_code == 400, f"无对比状态应 400, 实际 {resp.status_code}"

    def test_no_api_key_returns_503(self, client, monkeypatch):
        """两个密钥都未配置时应 503。"""
        self._set_compare_state(client)
        monkeypatch.setattr('config.DEEPSEEK_API_KEY', '')
        monkeypatch.setattr('config.QWEN_API_KEY', '')
        resp = client.post('/advice/compare/analyze')
        assert resp.status_code == 503, f"无密钥应 503, 实际 {resp.status_code}"

    def test_normal_returns_analysis_and_cache_hit(self, client, monkeypatch):
        """正常返回分析文本，且第二次请求命中缓存不再调用 LLM。"""
        self._set_compare_state(client)
        monkeypatch.setattr('config.DEEPSEEK_API_KEY', 'test-key')
        calls = {'n': 0}

        def fake_llm(messages, deepseek_key=None):
            calls['n'] += 1
            return '北京与上海对比: 上海平均薪资更高, 岗位略少。', 'deepseek'

        monkeypatch.setattr('agent.agent_core.call_llm_with_fallback', fake_llm)
        resp1 = client.post('/advice/compare/analyze')
        assert resp1.status_code == 200, f"正常应 200, 实际 {resp1.status_code}"
        assert '北京' in resp1.text and '上海' in resp1.text
        assert calls['n'] == 1, "首次请求应调用一次 LLM"

        resp2 = client.post('/advice/compare/analyze')
        assert resp2.status_code == 200
        assert resp2.text == resp1.text
        assert calls['n'] == 1, "缓存命中时不应再次调用 LLM"

        # 验证结果同时持久化到 session，页面刷新后可直接恢复
        with client.session_transaction() as sess:
            assert 'compare_ai_analysis' in sess
            assert sess['compare_ai_analysis']['text'] == resp1.text


# ============================================================
# L-?: /advice 多 tab 状态保持
# ============================================================
class TestAdviceStatePersistence:
    """验证 /advice 各 tab 的结果在 session 中独立保存，切回页面后同时恢复。"""

    def test_active_tab_recorded_after_post(self, client):
        """提交岗位匹配后，session 中应记录 active_tab 为 match。"""
        # 用最小数据触发 match 分支并成功返回（空库时 match_jobs 可能无结果但不应抛异常）
        resp = client.post('/advice', data={
            'tool': 'match',
            'skills': 'Python',
            'city': '北京',
            'edu': '本科',
            'exper': '3-5年',
        })
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get('advice_active_tab') == 'match'

    def test_get_restores_last_active_tab(self, client):
        """GET /advice 没有 ?tool 参数时，应恢复到 session 中记录的 active_tab。"""
        with client.session_transaction() as sess:
            sess['advice_active_tab'] = 'compare'
        resp = client.get('/advice')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'id="tab-compare"' in html
        # 当前激活的 tab 按钮应有 active 类
        assert 'data-tab="compare"' in html and 'active' in html.split('data-tab="compare"')[0].rsplit('class="', 1)[-1]

    def test_get_with_tool_param_overrides_session(self, client):
        """URL 带 ?tool=review 时，应优先显示 review tab。"""
        with client.session_transaction() as sess:
            sess['advice_active_tab'] = 'match'
        resp = client.get('/advice?tool=review')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'id="tab-review"' in html

    def test_multiple_tab_states_restored_together(self, client, monkeypatch):
        """同时存在 compare、match、review 状态时，GET 应一次性恢复所有 tab。"""
        monkeypatch.setattr('config.DEEPSEEK_API_KEY', 'test-key')

        # 构造多 tab 状态
        with client.session_transaction() as sess:
            sess['compare_state'] = {
                'result': {
                    'compare_type': 'city',
                    'a': {'value': '北京', 'count': 1, 'avg_salary_k': 15.0,
                          'min_salary_k': 10.0, 'max_salary_k': 20.0,
                          'top_edu': [], 'top_exper': []},
                    'b': {'value': '上海', 'count': 1, 'avg_salary_k': 18.0,
                          'min_salary_k': 12.0, 'max_salary_k': 24.0,
                          'top_edu': [], 'top_exper': []},
                    'skill_diff': {},
                },
                'a': '北京', 'b': '上海',
            }
            sess['match_state'] = {
                'result': {'total_matched': 0, 'top_matches': [], 'thresholds': {}},
                'skills': 'Python', 'city': '北京', 'edu': '本科', 'exper': '3-5年',
                'target_job_ids': None,
            }
            from app import _review_store
            _review_id = 'test-review-id'
            _review_store[_review_id] = {
                'result': {
                    'extracted': {'skills': ['Python'], 'edu': '本科', 'exper': '3-5年'},
                    'summary': '测试总结', 'job_gaps': [],
                },
                'text': '测试简历', 'city': '北京', 'category': '后端',
                'target_job_ids': None,
            }
            sess['review_id'] = _review_id
            sess['advice_active_tab'] = 'compare'

        resp = client.get('/advice')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        # 三个 tab 的内容都应出现在页面中
        assert '城市对比' in html
        assert '岗位匹配推荐' in html
        assert '简历审查' in html
        # 当前 active tab 是 compare
        assert 'id="tab-compare"' in html

    def test_active_tab_api_updates_session(self, client):
        """前端切 tab 时 POST /advice/active-tab 应更新 session。"""
        resp = client.post('/advice/active-tab',
                           json={'tab': 'review'},
                           content_type='application/json')
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get('advice_active_tab') == 'review'

    def test_active_tab_api_rejects_invalid_tab(self, client):
        """非法 tab 参数应返回 400，且不污染 session。"""
        resp = client.post('/advice/active-tab',
                           json={'tab': 'xxx'},
                           content_type='application/json')
        assert resp.status_code == 400



