"""数据采集路由：/collect 实时采集（单独限流）+ /toggle_interest 收藏切换。"""
import re as _re

from flask import Blueprint, jsonify, redirect, render_template, request, session

import config
from data.python_job_scraper import scrape_jobs
from data.job_store import upsert_jobs
from extensions import limiter
from services.db import get_db, _load_interested_jobs
from services import cache as cache_service

collect_bp = Blueprint('collect', __name__)


@collect_bp.route('/collect', methods=['GET', 'POST'])
@limiter.limit("5 per hour")  # 采集接口单独限流,防止滥用
def collect():
    """
    用户指定关键词+城市,触发一次真实的51job实时采集(Playwright+stealth),
    采集结果写入data表,供后续所有分析(图表/聚类/预测/Agent)直接使用。

    表单本身在首页(input.html),这里只处理提交;GET请求优先从 session 恢复
    上次采集结果（若有），否则重定向回首页。

    注意: 这是同步阻塞调用,一次采集通常耗时5~10秒(过WAF)+每页约0.3秒,
    城市和页数设了上限,避免单次请求耗时过长。
    """
    if request.method != 'POST':
        # 支持清除: /collect?clear_collect=1
        if request.args.get('clear_collect') == '1':
            session.pop('collect_state', None)
            return redirect('/')
        # 尝试从 session 恢复上次采集结果
        collect_state = session.get('collect_state')
        if collect_state:
            return render_template('collect.html', restored=True, **collect_state)
        return redirect('/')

    # --- 采集口令校验 --------------------------------------------------------
    # 若 .env 中设置了 COLLECT_TOKEN,则要求表单中提交匹配的 token 字段,
    # 否则拒绝采集 —— 防止演示/教学场景下被人误操作清空数据。
    if config.COLLECT_TOKEN:
        form_token = request.form.get('token', '')
        if form_token != config.COLLECT_TOKEN:
            return render_template(
                'collect.html',
                error='采集口令不正确,请联系管理员获取',
                keyword=request.form.get('kw', '').strip(),
                city=request.form.get('city', '').strip(),
            ), 403

    keyword = request.form.get('kw', '').strip()
    city_raw = request.form.get('city', '').strip()
    # 同时支持中文逗号"，"、英文逗号","、空格分隔,
    # 之前只认英文逗号,用户用中文输入法打的全角逗号"，"不会被拆开,
    # 导致整段文字被当成一个城市名传进去(实测踩到的真实bug)
    cities = [c.strip() for c in _re.split(r'[,，\s]+', city_raw) if c.strip()]
    try:
        pages = int(request.form.get('pages', 2))
    except ValueError:
        pages = 2
    pages = max(1, min(pages, 5))  # 安全上限,防止单次请求耗时过长

    sort_type = request.form.get('sort_type', '0')
    if sort_type not in ('0', '1'):
        sort_type = '0'

    if not keyword:
        return render_template('collect.html', error='请输入采集关键词')

    sort_label = {'0': '综合排序', '1': '最新发布'}.get(sort_type, '未知')
    cache_service._logger.info('采集开始: 关键词=%s, 城市=%s, 页数=%d, 排序=%s',
                               keyword, cities, pages, sort_label)

    # 提前打开DB连接,用于增量保存回调(每城市采完就写,防Ctrl+C丢数据)
    db = get_db()
    incremental_stats = {'inserted': 0, 'updated': 0, 'skipped': 0}

    def save_callback(city, city_jobs):
        """每采集完一个城市就增量入库(upsert,不覆盖历史数据)"""
        stats = upsert_jobs(db, city_jobs)
        for k in incremental_stats:
            incremental_stats[k] += stats[k]

    try:
        jobs, pages_collected = scrape_jobs(
            keyword, cities, pages_per_city=pages, sort_type=sort_type,
            save_callback=save_callback
        )
    except Exception as e:
        cache_service._logger.error('采集异常: %s', e)
        # 即使异常中断,已采集的数据已通过save_callback写入
        if incremental_stats['inserted'] + incremental_stats['updated'] > 0:
            cache_service._logger.info('中断前已增量入库 %d 条数据',
                                       incremental_stats['inserted'] + incremental_stats['updated'])
        return render_template('collect.html', error='采集过程发生错误,请稍后重试',
                                keyword=keyword, city=city_raw)

    success = incremental_stats['inserted'] + incremental_stats['updated']
    cache_service._logger.info('采集完成: 新增 %d, 更新 %d, 排除(面议/重复解析失败) %d',
                               incremental_stats['inserted'], incremental_stats['updated'],
                               incremental_stats['skipped'])

    if success == 0:
        return render_template(
            'collect.html',
            error='没有采集到任何数据,可能是WAF拦截了这次请求,或者关键词/城市没有匹配结果,换个关键词或稍后再试',
            keyword=keyword, city=city_raw,
        )

    # 数据变了，图表 AI 缓存 + 模型缓存都要失效
    cache_service._invalidate_chart_analysis_cache()
    cache_service._invalidate_modeling_caches()

    # 数据变了,聚类模型也要跟着重新算一遍并持久化
    cache_service._retrain_clustering_now()

    # 清理 salary classifier 缓存文件，下一次访问 /ml 时自动重训
    import os
    _classifier_cache_path = os.path.join(cache_service._PROJECT_ROOT, 'cache', 'salary_classifier.joblib')
    try:
        if os.path.exists(_classifier_cache_path):
            os.remove(_classifier_cache_path)
    except Exception:
        pass

    # 保存采集结果到 session，跨页面导航后可恢复
    session['collect_state'] = {
        'success_count': success,
        'total_count': len(jobs),
        'inserted_count': incremental_stats['inserted'],
        'updated_count': incremental_stats['updated'],
        'skipped_count': incremental_stats['skipped'],
        'keyword': keyword,
        'city': city_raw,
        'pages_per_city': pages,
        'pages_collected': pages_collected,
    }

    return render_template(
        'collect.html', success_count=success, total_count=len(jobs),
        inserted_count=incremental_stats['inserted'],
        updated_count=incremental_stats['updated'],
        skipped_count=incremental_stats['skipped'],
        keyword=keyword, city=city_raw,
        pages_per_city=pages, pages_collected=pages_collected,
    )


@collect_bp.route('/toggle_interest', methods=['POST'])
def toggle_interest():
    """AJAX 切换岗位收藏状态, 仅读写 session, 返回 JSON。

    受全局 CSRFProtect 保护, 调用方必须在 X-CSRFToken 头或 csrf_token 表单字段携带 token。
    """
    raw = (request.form.get('job_id') or '').strip()
    if not raw.isdecimal():
        return jsonify({'error': 'invalid job_id'}), 400
    job_id = int(raw)
    if job_id <= 0:
        return jsonify({'error': 'invalid job_id'}), 400

    lst = session.get('interested_jobs', [])
    if job_id in lst:
        lst = [x for x in lst if x != job_id]
        interested = False
    else:
        lst = lst + [job_id]
        interested = True
    session['interested_jobs'] = lst
    return jsonify({'interested': interested, 'count': len(lst), 'job_id': job_id})
