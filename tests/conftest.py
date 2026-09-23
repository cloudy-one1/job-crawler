"""
共享测试夹具。

测试套件必须自给自足，不依赖本地 data.db：
- temp_db: 创建含样例数据的临时 SQLite 库，并把 config.DB_PATH 指向它。

表结构与 app.init_db() 的完整 schema 保持一致（含 company/dateT/keywords），
保证 analysis / modeling / agent 各函数的查询不会因缺列或空表而失败。

exper 取值必须覆盖 51job 线上真实的两类写法（「1-3年」区间式与「3年及以上」下限式），
否则口径回归只能在库里发现，测试全绿也接不住。
"""
import os
import sqlite3
import tempfile

import pytest

_SCHEMA = """
CREATE TABLE data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post TEXT, company TEXT, address TEXT,
    salary_min REAL, salary_max REAL,
    dateT TEXT, edu TEXT, exper TEXT, content TEXT,
    keywords TEXT, job_url TEXT
)
"""

_ROWS = [
    ('Python后端开发', '云帆科技', '北京-海淀区', 15.0, 25.0, '2026-07-01', '本科', '3-5年',
     '负责后端服务开发与维护,需要熟悉 Python Django Flask MySQL', 'Python,Django,Flask,MySQL',
     'https://jobs.51job.com/test/1.html'),
    ('Java后端开发', '星河软件', '北京-朝阳区', 12.0, 20.0, '2026-07-01', '本科', '1-3年',
     '参与核心业务系统开发,Java Spring Boot Docker Redis', 'Java,Spring Boot,Docker,Redis',
     'https://jobs.51job.com/test/2.html'),
    ('Python后端开发', '数联网络', '上海-浦东新区', 18.0, 30.0, '2026-07-02', '硕士', '3-5年',
     '高并发服务设计与实现,Python FastAPI Docker Kubernetes', 'Python,FastAPI,Docker,Kubernetes',
     'https://jobs.51job.com/test/3.html'),
    ('前端开发工程师', '灵犀互动', '上海-徐汇区', 10.0, 18.0, '2026-07-02', '大专', '1-3年',
     '负责 Web 前端页面开发,React Vue TypeScript Webpack', 'React,Vue,TypeScript,Webpack',
     'https://jobs.51job.com/test/4.html'),
    ('数据爬虫工程师', '云帆科技', '北京-海淀区', 12.0, 22.0, '2026-07-03', '本科', '1-3年',
     '负责数据采集与清洗,Python Scrapy Scrapyd MongoDB', 'Python,Scrapy,MongoDB',
     'https://jobs.51job.com/test/5.html'),
    ('Python爬虫工程师', '深蓝数据', '深圳-南山区', 15.0, 25.0, '2026-07-03', '本科', '3-5年',
     '分布式爬虫架构设计,Python Scrapy Redis Docker', 'Python,Scrapy,Redis,Docker',
     'https://jobs.51job.com/test/6.html'),
    ('Java后端开发', '数联网络', '上海-浦东新区', 15.0, 25.0, '2026-07-04', '本科', '3-5年',
     '微服务体系升级改造,Java Spring Cloud Docker MySQL', 'Java,Spring Cloud,Docker,MySQL',
     'https://jobs.51job.com/test/7.html'),
    ('运维工程师', '星河软件', '深圳-福田区', 8.0, 15.0, '2026-07-04', '大专', '经验不限',
     '容器化平台运维,Linux Docker Kubernetes Jenkins', 'Linux,Docker,Kubernetes,Jenkins',
     'https://jobs.51job.com/test/8.html'),
    ('测试工程师', '灵犀互动', '上海-静安区', 10.0, 18.0, '2026-07-05', '本科', '1-3年',
     '接口与自动化测试体系建设,Selenium Python', 'Selenium,Python,自动化测试',
     'https://jobs.51job.com/test/9.html'),
    ('Web前端开发', '云帆科技', '北京-海淀区', 12.0, 22.0, '2026-07-05', '本科', '3-5年',
     '中后台前端组件库建设,React Vue TypeScript CSS Node.js', 'React,Vue,TypeScript,CSS',
     'https://jobs.51job.com/test/10.html'),
    # 以下为 51job 的「下限式」经验写法，口径回归用例依赖它们
    ('Python后端开发', '江畔信息', '杭州-滨江区', 18.0, 30.0, '2026-07-06', '本科', '3年及以上',
     '服务端接口设计与性能优化,Python FastAPI MySQL Redis', 'Python,FastAPI,MySQL,Redis',
     'https://jobs.51job.com/test/11.html'),
    ('推荐算法工程师', '极光智能', '北京-朝阳区', 25.0, 45.0, '2026-07-06', '硕士', '5年及以上',
     '推荐系统建模与大规模特征工程,Python PyTorch Spark', 'Python,PyTorch,Spark',
     'https://jobs.51job.com/test/12.html'),
    ('数据分析师', '云帆科技', '上海-浦东新区', 11.0, 18.0, '2026-07-07', '本科', '1年及以上',
     '业务指标体系与取数看板建设,SQL Python Excel', 'SQL,Python,Excel',
     'https://jobs.51job.com/test/13.html'),
    ('运维工程师', '深蓝数据', '深圳-南山区', 9.0, 14.0, '2026-07-07', '大专', '无需经验',
     '基础监控告警值守与发布配合,Linux Shell Docker', 'Linux,Shell,Docker',
     'https://jobs.51job.com/test/14.html'),
    ('Java后端开发', '星河软件', '成都-高新区', 10.0, 16.0, '2026-07-08', '本科', '在校生/应届生',
     '校招储备轮岗培养,Java Spring Boot MySQL', 'Java,Spring Boot,MySQL',
     'https://jobs.51job.com/test/15.html'),
    ('前端开发工程师', '灵犀互动', '广州-天河区', 14.0, 22.0, '2026-07-08', '本科', '2年及以上',
     '活动页与组件库迭代开发,React TypeScript Webpack', 'React,TypeScript,Webpack',
     'https://jobs.51job.com/test/16.html'),
]


@pytest.fixture
def temp_db(monkeypatch):
    """含样例数据的临时库；需要空库行为的用例可自行 DELETE FROM data。"""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute(_SCHEMA)
        conn.executemany(
            "INSERT INTO data (post, company, address, salary_min, salary_max,"
            " dateT, edu, exper, content, keywords, job_url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            _ROWS,
        )
        conn.commit()
        conn.close()

        import config
        monkeypatch.setattr(config, 'DB_PATH', path)
        yield path
    finally:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except PermissionError:
            # Windows 下个别连接释放晚于 teardown，删除失败时留给系统临时目录回收
            pass
