"""
Agent 工具函数单元测试 — compare_jobs 城市/类别并排对比。

使用临时 SQLite 数据库注入,不依赖真实 data.db。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import tempfile
import sqlite3


@pytest.fixture
def temp_db(monkeypatch):
    """创建含测试数据的临时 SQLite 数据库，注入到 agent_tools 中。"""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data (
            id INTEGER PRIMARY KEY,
            post TEXT, address TEXT, salary_min REAL, salary_max REAL,
            edu TEXT, exper TEXT, content TEXT DEFAULT '', job_url TEXT DEFAULT ''
        )
    """)
    test_data = [
        ('Python后端开发', '北京-海淀区', 15, 25, '本科', '3-5年', 'Python Django Flask MySQL'),
        ('Java后端开发', '北京-朝阳区', 12, 20, '本科', '1-3年', 'Java Spring Boot Docker Redis'),
        ('Python后端开发', '上海-浦东新区', 18, 30, '硕士', '3-5年', 'Python FastAPI Docker Kubernetes AWS'),
        ('前端开发工程师', '上海-徐汇区', 10, 18, '大专', '1-3年', 'React Vue TypeScript Webpack'),
        ('数据爬虫工程师', '北京-海淀区', 12, 22, '本科', '1-3年', 'Python Scrapy Scrapyd MongoDB Docker'),
        ('Python爬虫工程师', '深圳-南山区', 15, 25, '本科', '3-5年', 'Python Scrapy Redis Docker K8s'),
        ('Java后端开发', '上海-浦东新区', 15, 25, '本科', '3-5年', 'Java Spring Cloud Docker K8s MySQL'),
        ('运维工程师', '深圳-福田区', 8, 15, '大专', '经验不限', 'Linux Docker Kubernetes Jenkins Ansible'),
        ('测试工程师', '上海-静安区', 10, 18, '本科', '1-3年', 'Selenium Python 自动化测试 接口测试'),
        ('Web前端开发', '北京-海淀区', 12, 22, '本科', '3-5年', 'React Vue TypeScript CSS Node.js'),
    ]
    conn.executemany(
        "INSERT INTO data (post, address, salary_min, salary_max, edu, exper, content, job_url)"
        " VALUES (?,?,?,?,?,?,?, 'https://jobs.51job.com/test/1.html')",
        test_data
    )
    conn.commit()
    conn.close()

    import config
    monkeypatch.setattr(config, 'DB_PATH', path)

    # 强制 agent_tools 的 _connect 使用新路径 (config.DB_PATH 在 _connect 调用时解析)
    from agent import agent_tools
    # 无需特殊处理: _connect() 内部是 sqlite3.connect(config.DB_PATH),monkeypatch 后生效

    yield path
    os.unlink(path)


# ============================================================
# compare_jobs — 城市/类别并排对比
# ============================================================
class TestCompareJobs:
    """验证 compare_jobs 两种对比模式。"""

    def test_city_compare_structure(self, temp_db):
        """按城市对比返回正确结构。"""
        from agent.agent_tools import compare_jobs
        result = compare_jobs('city', '北京', '上海')

        assert result['compare_type'] == 'city'
        assert 'a' in result and 'b' in result
        # 北京: 4 条 (Python后端×1, Java后端×1, 爬虫×1, Web前端×1)
        assert result['a']['value'] == '北京'
        assert result['a']['count'] == 4
        assert result['a']['avg_salary_k'] > 0
        assert len(result['a']['top_edu']) > 0
        assert len(result['a']['top_exper']) > 0
        # 上海: 4 条 (Python后端×1, 前端×1, Java后端×1, 测试×1)
        assert result['b']['value'] == '上海'
        assert result['b']['count'] == 4

    def test_category_compare_structure(self, temp_db):
        """按职位类别对比返回正确结构。"""
        from agent.agent_tools import compare_jobs
        result = compare_jobs('category', '后端开发', 'Web/前端')

        assert result['compare_type'] == 'category'
        assert result['a']['value'] == '后端开发'
        assert result['a']['count'] > 0
        assert result['b']['value'] == 'Web/前端'
        assert result['b']['count'] >= 0  # 可能为 0

    def test_unknown_city_returns_zero_count(self, temp_db):
        """不存在的城市返回 0 计数。"""
        from agent.agent_tools import compare_jobs
        result = compare_jobs('city', '拉萨', '乌鲁木齐')

        assert result['a']['count'] == 0
        assert result['a']['avg_salary_k'] == 0
        assert result['b']['count'] == 0

    def test_unknown_category_returns_zero_count(self, temp_db):
        """不存在的类别返回 0 计数。"""
        from agent.agent_tools import compare_jobs
        result = compare_jobs('category', '培训讲师', '运营')

        assert result['a']['count'] == 0

    def test_same_city_both_sides(self, temp_db):
        """同城市对比两侧都应有数据。"""
        from agent.agent_tools import compare_jobs
        result = compare_jobs('city', '上海', '上海')
        assert result['a']['count'] == result['b']['count']
        assert result['a']['avg_salary_k'] == result['b']['avg_salary_k']

    def test_city_compare_skill_diff(self, temp_db):
        """城市对比应包含技能差异信息。"""
        from agent.agent_tools import compare_jobs
        result = compare_jobs('city', '北京', '上海')
        if result['a']['count'] > 0 and result['b']['count'] > 0:
            assert 'skill_diff' in result
            assert isinstance(result['skill_diff'], dict)

    def test_no_content_texts_leaked(self, temp_db):
        """结果中不应泄露原始 content_texts。"""
        from agent.agent_tools import compare_jobs
        result = compare_jobs('city', '北京', '上海')
        assert '_content_texts' not in result['a']
        assert '_content_texts' not in result['b']


# ============================================================
# query_jobs — 标题+描述联合搜索
# ============================================================
class TestQueryJobs:
    """验证 query_jobs 标题+描述联合搜索。"""

    def test_title_match(self, temp_db):
        """纯标题匹配仍正常工作。"""
        from agent.agent_tools import query_jobs
        result = query_jobs('爬虫')
        assert result['count'] >= 2
        assert result['keyword'] == '爬虫'
        assert result['avg_salary_k'] > 0

    def test_content_only_match(self, temp_db):
        """JD 中有但标题没有的关键词也能命中。"""
        from agent.agent_tools import query_jobs
        result = query_jobs('Kubernetes')
        assert result['count'] > 0
        assert result['keyword'] == 'Kubernetes'

    def test_no_match(self, temp_db):
        """不存在的关键词返回 0。"""
        from agent.agent_tools import query_jobs
        result = query_jobs('COBOL')
        assert result['count'] == 0
        assert 'message' in result


# ============================================================
# skill_demand_analysis — 技能市场需求分析
# ============================================================
class TestSkillDemandAnalysis:
    """验证 skill_demand_analysis 技能需求分析。"""

    def test_existing_skill(self, temp_db):
        """常见技能应返回统计信息。"""
        from agent.agent_tools import skill_demand_analysis
        result = skill_demand_analysis('Docker')
        assert result['count'] > 0
        assert result['skill'] == 'Docker'
        assert result['median_salary_k'] > 0
        assert isinstance(result['top_cities'], list)
        assert isinstance(result['top_co_skills'], list)
        assert isinstance(result['sample_jobs'], list)

    def test_unknown_skill(self, temp_db):
        """不存在的技能返回 0 计数和提示消息。"""
        from agent.agent_tools import skill_demand_analysis
        result = skill_demand_analysis('Fortran')
        assert result['count'] == 0
        assert 'message' in result

    def test_empty_skill(self, temp_db):
        """空技能名返回错误提示。"""
        from agent.agent_tools import skill_demand_analysis
        result = skill_demand_analysis('')
        assert result['count'] == 0
        assert 'message' in result

    def test_skill_salary_consistency(self, temp_db):
        """薪资统计应在合理范围内。"""
        from agent.agent_tools import skill_demand_analysis
        result = skill_demand_analysis('Python')
        if result['count'] > 0:
            assert result['avg_salary_k'] > 0
            k = result['median_salary_k']
            assert k <= result['avg_salary_k'] * 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


# ============================================================
# review_resume — 双 Agent 简历审查 + 规则引擎降级
# ============================================================
class TestReviewResume:
    """验证 review_resume 规则层 + 单次 LLM 调用(诊断+优化)与降级。"""

    SAMPLE_RESUME = (
        "我有3年Python开发经验，熟悉Django、Flask、MySQL。本科毕业于某大学，"
        "负责过Web后端项目，使用Redis做缓存。"
    )

    def test_rule_based_fields_present(self, temp_db, monkeypatch):
        """无 Key 时仅返回规则结果, 且 ai_available=False, 不抛 NameError(job_url 已解包)。"""
        import config
        monkeypatch.setattr(config, 'DEEPSEEK_API_KEY', '')
        monkeypatch.setattr(config, 'QWEN_API_KEY', '')
        from agent.agent_tools import review_resume
        result = review_resume(self.SAMPLE_RESUME)
        assert 'extracted' in result
        assert 'job_gaps' in result
        assert 'summary' in result
        assert result['ai_available'] is False
        assert result['critique'] == ''
        assert result['optimized_resume'] == ''
        # job_gaps 卡片应有 job_url (验证解包 bug 已修复)
        if result['job_gaps']:
            assert 'job_url' in result['job_gaps'][0]

    def test_single_call_normal(self, temp_db, monkeypatch):
        """配 Key 且 LLM 可用时, 单次调用产出诊断+优化两段, ai_available=True。"""
        import config
        import agent.agent_core
        monkeypatch.setattr(config, 'DEEPSEEK_API_KEY', 'dummy-key')
        monkeypatch.setattr(
            agent.agent_core, 'call_llm_with_fallback',
            lambda messages, deepseek_key='': (
                '## 简历诊断报告\n核心优势: Python 经验扎实\n\n'
                '## 优化版简历\n优化版个人摘要...',
                'deepseek'
            )
        )
        from agent.agent_tools import review_resume
        result = review_resume(self.SAMPLE_RESUME)
        assert result['ai_available'] is True
        assert result['provider'] == 'deepseek'
        assert '诊断' in result['critique']
        assert '优化' in result['optimized_resume']

    def test_llm_failure_degrades(self, temp_db, monkeypatch):
        """LLM 双模型均失败时, 优雅降级为规则结果, ai_available=False 但 job_gaps 仍在。"""
        import config
        import agent.agent_core
        monkeypatch.setattr(config, 'DEEPSEEK_API_KEY', 'dummy-key')
        monkeypatch.setattr(
            agent.agent_core, 'call_llm_with_fallback',
            lambda messages, deepseek_key='': (_ for _ in ()).throw(RuntimeError('all LLM down'))
        )
        from agent.agent_tools import review_resume
        result = review_resume(self.SAMPLE_RESUME)
        assert result['ai_available'] is False
        assert result['critique'] == ''
        assert result['optimized_resume'] == ''
        # 规则层结果必须保留
        assert len(result['job_gaps']) > 0
        assert result['extracted']['edu'] == '本科'

    def test_target_job_ids_restricts_gap_scope(self, temp_db, monkeypatch):
        """传入 target_job_ids=[4] 时, 只针对该岗位 JD 做 Gap 分析。"""
        import config
        monkeypatch.setattr(config, 'DEEPSEEK_API_KEY', '')
        monkeypatch.setattr(config, 'QWEN_API_KEY', '')
        from agent.agent_tools import review_resume
        # 前端岗位(id=4) JD 含 React/Vue/TypeScript, 简历没有 → 必产生 Gap
        result = review_resume(self.SAMPLE_RESUME, target_job_ids=[4])
        ids = [g['id'] for g in result['job_gaps']]
        assert all(i in (4,) for i in ids), f"结果含非目标岗位: {ids}"
        assert 4 in ids

    def test_target_job_ids_backward_compat(self, temp_db, monkeypatch):
        """不传 target_job_ids 时保持原全表兜底, 命中多个岗位。"""
        import config
        monkeypatch.setattr(config, 'DEEPSEEK_API_KEY', '')
        monkeypatch.setattr(config, 'QWEN_API_KEY', '')
        from agent.agent_tools import review_resume
        result = review_resume(self.SAMPLE_RESUME)
        ids = [g['id'] for g in result['job_gaps']]
        assert len(ids) >= 2, "全表兜底应命中多个岗位"

    def test_job_with_no_extractable_skills_does_not_crash(self, temp_db, monkeypatch):
        """目标岗位 JD 未提取到任何技能关键词时，skill_match_pct 为 None 不应导致排序报错。"""
        import config
        import sqlite3
        monkeypatch.setattr(config, 'DEEPSEEK_API_KEY', '')
        monkeypatch.setattr(config, 'QWEN_API_KEY', '')
        from agent.agent_tools import review_resume
        # 在临时 DB 插入一条无技能关键词但有学历/经验要求的岗位
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO data (post, address, salary_min, salary_max, edu, exper, content, job_url)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ('行政助理', '北京-朝阳区', 5, 8, '本科', '1-3年', '负责日常行政和文档管理', 'https://jobs.51job.com/test/11.html')
        )
        conn.commit()
        conn.close()
        result = review_resume('本科，1年工作经验，熟悉文档处理', target_city='北京')
        assert 'job_gaps' in result
        assert 'error' not in result



# ============================================================
# match_jobs — target_job_ids 收藏岗位匹配
# ============================================================
class TestMatchJobsTargetIds:
    """验证 match_jobs 的 target_job_ids 参数: 仅在收藏岗位集合内匹配。"""

    def test_target_job_ids_restricts_scope(self, temp_db):
        """传入 target_job_ids=[4] 时, 结果仅含该岗位。"""
        from agent.agent_tools import match_jobs
        result = match_jobs(skills='React', target_job_ids=[4])
        ids = [j['id'] for j in result['top_matches']]
        assert all(i == 4 for i in ids), f"结果含非目标岗位: {ids}"
        assert result['total_matched'] == 1

    def test_target_job_ids_no_match_when_missing(self, temp_db):
        """传入不存在的 id, 命中数为 0 (不报错)。"""
        from agent.agent_tools import match_jobs
        result = match_jobs(skills='React', target_job_ids=[999])
        assert result['total_matched'] == 0
        assert result['top_matches'] == []

    def test_backward_compat_full_table(self, temp_db):
        """不传 target_job_ids 时保持原全表匹配, 命中多个岗位。"""
        from agent.agent_tools import match_jobs
        result = match_jobs(skills='Python')
        ids = [j['id'] for j in result['top_matches']]
        assert len(ids) >= 2, "全表匹配应命中多个岗位"


# ============================================================
# _score_exper — 经验档位评分（统一走 data/exper_parser 口径）
# ============================================================
class TestScoreExper:
    """经验评分两侧都归一到 5 个有序档位再比较，
    「3年及以上」这类下限式写法与「3-5年」在同一口径下可比。"""

    def test_full_match_same_bucket(self):
        from agent.agent_tools import _score_exper
        score, _ = _score_exper('3-5年', '3-5年', 15)
        assert score == 15

    def test_lower_bound_style_normalized(self):
        """「3年及以上」归一后就是「3-5年」档，应给满分（旧版只能得'匹配度一般'）。"""
        from agent.agent_tools import _score_exper
        score, reason = _score_exper('3-5年', '3年及以上', 15)
        assert score == 15
        assert '经验匹配' in reason

    def test_canonical_bucket_1_3year(self):
        """'1-3年' 是规范档位（旧版 _EXPER_RANK 缺这一档，同样只能得'匹配度一般'）。"""
        from agent.agent_tools import _score_exper
        score, _ = _score_exper('1-3年', '1-3年', 15)
        assert score == 15

    def test_user_exceeds_requirement(self):
        from agent.agent_tools import _score_exper
        score, reason = _score_exper('5-10年', '1-3年', 15)
        assert 0 < score < 15
        assert '达标' in reason

    def test_user_under_requirement(self):
        from agent.agent_tools import _score_exper
        score, reason = _score_exper('1-3年', '5-10年', 15)
        assert 0 < score < 15
        assert '不足' in reason

    def test_no_experience_required_full_score(self):
        from agent.agent_tools import _score_exper
        score, _ = _score_exper('1-3年', '经验不限', 15)
        assert score == 15

    def test_chinese_skill_extraction_mid_sentence(self):
        """中文技能词紧邻其他汉字时也应被提取（\\b 词边界对中文失效的回归）。"""
        from agent.agent_tools import extract_skills
        skills = extract_skills('要求熟悉机器学习者优先，有大数据处理经验')
        assert '机器学习' in skills
        assert '大数据' in skills


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
