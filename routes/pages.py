"""基础页面路由：首页、数据列表、岗位详情、收藏清单、静默预热接口。"""
import json
import re

from flask import Blueprint, render_template, request

from extensions import csrf
from services.db import get_db, _load_interested_jobs
from services import cache as cache_service

pages_bp = Blueprint('pages', __name__)

PER_PAGE = 12


@pages_bp.route('/')
def index():
    from data.python_job_scraper import get_province_city_map
    city_map = get_province_city_map()
    return render_template('input.html', city_map_json=json.dumps(city_map, ensure_ascii=False))


@csrf.exempt
@pages_bp.route('/api/warmup', methods=['GET', 'POST'])
def api_warmup():
    """静默预热接口：首页加载后前端静默调用，提前计算图表数据 + 全部建模模块。
    此后用户点「图表分析」「薪资洞察」即可秒开，无需等待训练。"""
    # 图表数据（/chart 页面）
    try:
        cache_service._compute_chart_data()
    except Exception as e:
        cache_service._logger.warning('预热图表数据失败: %s', e)
    # 聚类模型（/ml 页面核心）
    try:
        cache_service._get_clustering()
    except Exception as e:
        cache_service._logger.warning('预热聚类模型失败: %s', e)
    # 技能热力图
    try:
        cache_service._get_skill_heatmap()
    except Exception as e:
        cache_service._logger.warning('预热技能热力图失败: %s', e)
    # 岗位相似度网络
    try:
        cache_service._get_similarity()
    except Exception as e:
        cache_service._logger.warning('预热相似度网络失败: %s', e)
    # 薪资成长曲线
    try:
        cache_service._get_salary_curve()
    except Exception as e:
        cache_service._logger.warning('预热薪资曲线失败: %s', e)
    # 学历溢价分析
    try:
        cache_service._get_edu_premium()
    except Exception as e:
        cache_service._logger.warning('预热学历溢价分析失败: %s', e)
    # 薪资档位分类模型
    try:
        cache_service._get_salary_classifier()
    except Exception as e:
        cache_service._logger.warning('预热薪资分类模型失败: %s', e)
    cache_service._logger.info('预热完成: 图表数据 + 聚类 + 热力图 + 相似度 + 薪资曲线 + 学历溢价 + 薪资分类器均已就绪')
    return '{"ok":true}', 200, {'Content-Type': 'application/json'}


@pages_bp.route('/list')
def list_data():
    try:
        page = int(request.args.get('page', 1))
    except (ValueError, TypeError):
        page = 1
    page = max(1, page)  # 避免页码为0或负数
    kw = request.args.get('kw', '').strip()
    city_raw = request.args.get('city', '').strip()
    cities = [c.strip() for c in re.split(r'[,，\s]+', city_raw) if c.strip()]

    conditions = []
    params = []
    if kw:
        # 关键词模糊匹配：精确等值在真实库上几乎搜不到东西
        # （实测 post='Python' 等值 0 命中，LIKE '%Python%' 63 命中）
        conditions.append("LOWER(post) LIKE LOWER(?)")
        params.append(f"%{kw}%")
    if cities:
        # 城市前缀匹配：address 同时存在「上海」与「上海-闵行区」两种形态，
        # 等值匹配会漏掉带区县的那一半
        city_conditions = " OR ".join(["LOWER(address) LIKE LOWER(?)"] * len(cities))
        conditions.append(f"({city_conditions})")
        params.extend([f"{c}%" for c in cities])
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        f"SELECT id, post, company, address, salary_min, salary_max, dateT FROM data "
        f"{where_clause} LIMIT ? OFFSET ?",
        (*params, PER_PAGE, (page - 1) * PER_PAGE)
    )
    rows = cursor.fetchall()
    cursor.execute(f"SELECT COUNT(*) FROM data {where_clause}", params)
    total = cursor.fetchone()[0]
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    return render_template(
        'data.html', rows=rows, kw=kw, city=city_raw, page=page,
        total=total, total_pages=total_pages
    )


@pages_bp.route('/job/<int:job_id>')
def job_detail(job_id):
    """岗位详情页面"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, post, company, address, salary_min, salary_max, dateT, edu, exper, content, job_url "
        "FROM data WHERE id = ?",
        (job_id,)
    )
    job = cursor.fetchone()

    if job is None:
        return render_template('job_detail.html', job=None, error='未找到该岗位信息')

    # 将 Row 对象转换为字典，方便模板访问
    job_dict = {
        'id': job['id'],
        'post': job['post'],
        'company': job['company'],
        'address': job['address'],
        'salary_min': job['salary_min'],
        'salary_max': job['salary_max'],
        'dateT': job['dateT'],
        'edu': job['edu'],
        'exper': job['exper'],
        'content': job['content'],
        'job_url': job['job_url'] or ''  # 兼容旧数据（NULL 回退为 ''）
    }

    return render_template('job_detail.html', job=job_dict, error=None)


@pages_bp.route('/interested')
def interested():
    """展示求职者收藏的「我感兴趣的岗位」清单 (session 存储, 零迁移)。"""
    jobs = _load_interested_jobs()
    return render_template('interested.html', jobs=jobs)
