"""
岗位数据入库统一入口(全项目唯一落库口径)。

之前 /collect 路由与命令行采集各自维护一份 INSERT + 「整表替换」逻辑,
行为容易漂移(命令行清空重写、网页保留最新 N 条,两套语义)。现在统一到这里:

- ensure_schema(conn): 建表 / 补列(collected_at) / job_url 唯一索引,幂等
- upsert_jobs(conn, jobs): 按 job_url 增量合并,不再清空旧数据;
  同「职位+公司+城市」视为同一岗位收敛(51job 常把同一岗位挂在多个 URL 下);
  「面议」等无法解析出薪资的记录直接排除,不再以 (0, 0) 落库。

每条记录带 collected_at(最后一次采集到该岗位的时间),为后续
「岗位时间序列 / 薪资趋势」路线图项留好口径:多次采集自然形成多期数据。
"""
import logging
import time

from data.salary_parser import parse_salary

_logger = logging.getLogger('data.job_store')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post TEXT, company TEXT, address TEXT,
    salary_min REAL, salary_max REAL,
    dateT TEXT, edu TEXT, exper TEXT, content TEXT,
    keywords TEXT, job_url TEXT, collected_at TEXT
)
"""


def ensure_schema(conn):
    """建表与迁移:补 keywords / job_url / collected_at 列,建 job_url 唯一索引。

    幂等,可在每次启动与采集前调用。
    唯一索引在存量库存在重复 job_url 时会创建失败,此时只打日志不阻塞
    ——入库去重由 upsert_jobs 的显式查询保证,索引只是兜底约束。
    """
    cur = conn.cursor()
    cur.execute(_SCHEMA)
    cur.execute("PRAGMA table_info(data)")
    cols = [r[1] for r in cur.fetchall()]
    if 'keywords' not in cols:
        cur.execute("ALTER TABLE data ADD COLUMN keywords TEXT")
    if 'job_url' not in cols:
        cur.execute("ALTER TABLE data ADD COLUMN job_url TEXT")
    if 'collected_at' not in cols:
        cur.execute("ALTER TABLE data ADD COLUMN collected_at TEXT")
    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_data_job_url ON data(job_url)")
    except Exception as e:
        _logger.warning('job_url 唯一索引创建失败(存在重复数据?): %s', e)
    conn.commit()


def upsert_jobs(conn, jobs):
    """把 scrape_jobs 产出的岗位列表增量写入 data 表(不清空旧数据)。

    匹配顺序:
    1. job_url 相同 → 视为同一岗位,刷新字段与 collected_at
    2. 「职位+公司+城市」相同(URL 不同) → 视为同一岗位的重复挂载,收敛到已有行
    3. 都未命中 → 新插入

    「面议」或无法解析出正薪资的记录被排除(历史行为是落库为 (0,0),
    会把均值与分箱拉偏)。

    Returns:
        dict: {'inserted': 新增数, 'updated': 刷新数, 'skipped': 排除数}
    """
    cur = conn.cursor()
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    inserted = updated = skipped = 0

    for j in jobs:
        smin, smax = parse_salary(j.get('salary_raw') or '')
        if smin <= 0 and smax <= 0:
            skipped += 1
            continue

        post = j.get('post') or ''
        company = j.get('company') or ''
        address = j.get('address') or ''
        url = j.get('job_url') or ''
        values = (post, company, address, smin, smax, j.get('dateT', ''),
                  j.get('edu', ''), j.get('exper', ''), j.get('content', ''),
                  j.get('keywords', ''), url, now)

        row = None
        if url:
            row = cur.execute(
                "SELECT id FROM data WHERE job_url = ?", (url,)).fetchone()
        if row is None and post and company:
            row = cur.execute(
                "SELECT id FROM data WHERE post = ? AND company = ? AND address = ?",
                (post, company, address)).fetchone()

        if row is not None:
            cur.execute(
                "UPDATE data SET post=?, company=?, address=?, salary_min=?, salary_max=?,"
                " dateT=?, edu=?, exper=?, content=?, keywords=?, job_url=?, collected_at=?"
                " WHERE id=?",
                values + (row[0],))
            updated += 1
        else:
            cur.execute(
                "INSERT INTO data (post, company, address, salary_min, salary_max,"
                " dateT, edu, exper, content, keywords, job_url, collected_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)
            inserted += 1

    conn.commit()
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped}
