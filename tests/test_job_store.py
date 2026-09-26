"""
测试 data/job_store.py —— 全项目唯一落库口径:
- ensure_schema: 建表/补列(collected_at)/job_url 唯一索引,幂等
- upsert_jobs: 按 job_url 增量合并、同「职位+公司+城市」收敛、面议排除
- collected_at: 新插入写入,重复采集刷新

历史背景(为什么必须有这层测试): 旧实现「命令行清空重写、网页保留最新 N 条」
两套语义并存,且同一岗位挂在多个 URL 下时会产生重复行。
"""
import sqlite3

import pytest

from data.job_store import ensure_schema, upsert_jobs


def _job(url, post='Python后端开发', company='云帆科技', address='北京-海淀区',
         salary_raw='1.5-2万/月', **overrides):
    j = {
        'post': post, 'company': company, 'address': address,
        'salary_raw': salary_raw, 'dateT': '2026-09-26', 'edu': '本科',
        'exper': '3年及以上', 'content': '熟悉 Python Django', 'keywords': 'Python,Django',
        'job_url': url,
    }
    j.update(overrides)
    return j


@pytest.fixture
def db():
    """内存库,已确保 schema。"""
    conn = sqlite3.connect(':memory:')
    ensure_schema(conn)
    yield conn
    conn.close()


def _rows(conn):
    return conn.execute(
        "SELECT post, company, address, salary_min, salary_max, job_url, collected_at "
        "FROM data ORDER BY id").fetchall()


class TestEnsureSchema:
    def test_fresh_db_creates_all_columns(self):
        conn = sqlite3.connect(':memory:')
        ensure_schema(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(data)").fetchall()]
        for col in ('keywords', 'job_url', 'collected_at'):
            assert col in cols
        conn.close()

    def test_migrates_legacy_table_keeping_data(self):
        """旧库(无 collected_at 列)迁移后原数据保留、新列补齐。"""
        conn = sqlite3.connect(':memory:')
        conn.execute(
            "CREATE TABLE data (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " post TEXT, company TEXT, address TEXT, salary_min REAL, salary_max REAL,"
            " dateT TEXT, edu TEXT, exper TEXT, content TEXT, keywords TEXT, job_url TEXT)")
        conn.execute(
            "INSERT INTO data (post, company, address, salary_min, salary_max, job_url)"
            " VALUES ('旧岗位', '旧公司', '北京', 10, 20, 'https://old/1')")
        conn.commit()

        ensure_schema(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(data)").fetchall()]
        assert 'collected_at' in cols
        row = conn.execute("SELECT post, job_url FROM data").fetchone()
        assert row == ('旧岗位', 'https://old/1')
        conn.close()

    def test_idempotent(self, db):
        ensure_schema(db)
        ensure_schema(db)
        idx = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_data_job_url'"
        ).fetchone()
        assert idx is not None


class TestUpsertJobs:
    def test_inserts_new_jobs(self, db):
        stats = upsert_jobs(db, [_job('https://x/1'), _job('https://x/2', post='Java后端开发')])
        assert stats == {'inserted': 2, 'updated': 0, 'skipped': 0}
        assert len(_rows(db)) == 2

    def test_same_url_updates_not_duplicates(self, db):
        upsert_jobs(db, [_job('https://x/1', salary_raw='1.5-2万/月')])
        stats = upsert_jobs(db, [_job('https://x/1', salary_raw='2-3万/月')])
        assert stats == {'inserted': 0, 'updated': 1, 'skipped': 0}
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0][3] == 20.0 and rows[0][4] == 30.0  # 2万, 3万

    def test_same_post_company_city_collapses(self, db):
        """同「职位+公司+城市」不同 URL: 51job 常见重复挂载,必须收敛。"""
        upsert_jobs(db, [_job('https://x/a')])
        stats = upsert_jobs(db, [_job('https://x/b')])
        assert stats['updated'] == 1
        assert stats['inserted'] == 0
        assert len(_rows(db)) == 1

    def test_mianyi_salary_skipped(self, db):
        """面议/无法解析薪资的记录不得以 (0,0) 落库。"""
        stats = upsert_jobs(db, [
            _job('https://x/1', salary_raw='面议'),
            _job('https://x/2', salary_raw=''),
            _job('https://x/3', salary_raw='1万以上/月'),
        ])
        assert stats['skipped'] == 2
        assert stats['inserted'] == 1
        assert all(r[3] > 0 and r[4] > 0 for r in _rows(db))

    def test_collected_at_written_on_insert_and_refreshed_on_update(self, db):
        upsert_jobs(db, [_job('https://x/1')])
        first = _rows(db)[0][6]
        assert first  # 非空时间戳
        # 模拟稍后再采到同一岗位
        import data.job_store as js
        original = js.time.strftime
        js.time.strftime = lambda *a, **kw: '2099-01-01 00:00:00'
        try:
            upsert_jobs(db, [_job('https://x/1')])
        finally:
            js.time.strftime = original
        assert _rows(db)[0][6] == '2099-01-01 00:00:00'

    def test_mixed_batch_counts(self, db):
        upsert_jobs(db, [_job('https://x/1'), _job('https://x/2', post='Java后端开发')])
        stats = upsert_jobs(db, [
            _job('https://x/1'),               # 更新(同URL)
            _job('https://x/3', post='数据工程师'),  # 新增(三元组不同)
            _job('https://x/4', post='算法工程师', company='星河软件'),  # 新增
            _job('https://x/5', post='Java后端开发'),  # 更新(与x/2同三元组,异URL)
            _job('https://x/6', salary_raw='面议'),  # 排除
        ])
        assert stats == {'inserted': 2, 'updated': 2, 'skipped': 1}

    def test_empty_list_is_noop(self, db):
        assert upsert_jobs(db, []) == {'inserted': 0, 'updated': 0, 'skipped': 0}

    def test_distinct_jobs_with_different_triples_coexist(self, db):
        upsert_jobs(db, [
            _job('https://x/1', post='A岗', company='甲公司', address='北京'),
            _job('https://x/2', post='B岗', company='甲公司', address='北京'),
            _job('https://x/3', post='A岗', company='乙公司', address='北京'),
        ])
        assert len(_rows(db)) == 3


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
