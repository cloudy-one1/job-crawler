"""
数据库连接管理与启动初始化。

连接挂在 Flask `g` 对象上,请求生命周期内复用,teardown 时自动关闭;
非请求上下文(启动预计算等)用 raw_connect() 提供独立连接,调用方自行关闭。
"""
import logging
import sqlite3

from flask import g

import config

_logger = logging.getLogger('job_analysis')


def init_db():
    """初始化数据库(建表/补列/唯一索引),schema 统一由 data.job_store 维护。"""
    try:
        from data.job_store import ensure_schema as _ensure_schema
        db = sqlite3.connect(config.DB_PATH)
        try:
            _ensure_schema(db)
        finally:
            db.close()
        _logger.info('数据库初始化完成')
    except Exception as e:
        _logger.error('数据库初始化失败: %s', e)


def get_db():
    """获取当前请求上下文中的 SQLite 连接;不存在则创建。
    连接在整个请求生命周期内复用,teardown 时自动关闭。"""
    if 'db' not in g:
        g.db = sqlite3.connect(config.DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(exception=None):
    """请求结束时关闭数据库连接,避免连接泄漏(由 app.py 注册 teardown)。"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def _raw_connect():
    """为非请求上下文(启动预计算等)提供独立连接,调用方自行关闭。"""
    return sqlite3.connect(config.DB_PATH)


def _has_data():
    """检查数据库中是否有可用的招聘数据。"""
    try:
        db = _raw_connect()
        count = db.cursor().execute("SELECT COUNT(*) FROM data").fetchone()[0]
        db.close()
        return count > 0
    except Exception as e:
        _logger.warning('数据库读取失败: %s', e)
        return False


def _load_interested_jobs():
    """从 session 读取收藏岗位 id, 按 id 查询标题/城市/薪资, 保持收藏顺序返回。"""
    from flask import session

    ids = session.get('interested_jobs', [])
    if not ids:
        return []
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    placeholders = ','.join('?' * len(ids))
    cur = db.execute(
        f"SELECT id, post, address, salary_min, salary_max FROM data WHERE id IN ({placeholders})",
        ids,
    )
    rows = cur.fetchall()
    db.close()
    by_id = {r['id']: r for r in rows}
    result = []
    for i in ids:
        r = by_id.get(i)
        if not r:
            continue
        avg = round((r['salary_min'] + r['salary_max']) / 2, 1) if (r['salary_min'] or r['salary_max']) else 0
        result.append({
            'id': r['id'],
            'title': r['post'],
            'city': r['address'].split('-')[0] if r['address'] else '未知',
            'salary_k': avg,
        })
    return result
