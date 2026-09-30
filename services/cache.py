"""
缓存与模型状态收敛地（原 app.py「模块级状态」区块整体迁入）。

三层缓存:
- 数据级:   图表统计数据（TTL 5 分钟）、AI 分析结果（图表 5 分钟 / 建模 2 分钟 / 对比 5 分钟）
- 模型级:   聚类与薪资分类器懒加载结果 + joblib 磁盘持久化（带版本号/sklearn 版本双失效）
- 会话级:   Agent 对话与简历审查的服务端存储（带上限，防长驻进程内存无限增长）

所有懒加载/重训共用 _lazy_lock（RLock：_get_similarity 内部会再调 _get_clustering），
避免 threaded 模式下并发首访重复训练、并发写同一缓存文件。
"""
import logging
import os
import time
import threading
import warnings

import config

from services.db import _raw_connect, _has_data

_logger = logging.getLogger('job_analysis')

# services/cache.py 的上一层即项目根（cache/ 目录固定放项目根下）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =============================================================================
# 模块级状态 / 缓存变量（集中在此处，禁止分散定义）
# =============================================================================
_chart_analysis_cache = {}          # 图表 AI 分析结果缓存: {section: (timestamp, text)}
_ml_analysis_cache = {}             # /ml 页面 AI 分析结果缓存: {section: (timestamp, text)}
_compare_analysis_cache = {}        # /advice 城市对比 AI 解读缓存: {(a,b): (timestamp, text)}
_CHART_CACHE_TTL = 300              # AI 分析缓存有效期，秒（5 分钟）
_COMPARE_CACHE_TTL = 300            # 城市对比 AI 解读缓存有效期，秒（5 分钟）
_ML_CACHE_TTL = 120                 # /ml AI 分析缓存有效期，秒（2 分钟，保证实时性）
_chart_data_cache = None            # 图表数据缓存: (timestamp, data_dict)
_CHART_DATA_CACHE_TTL = 300         # 图表数据缓存有效期，秒（5 分钟）
_clustering_cache = None            # 聚类结果懒加载缓存（首次 /ml 访问时训练填充）
_skill_heatmap_cache = None         # 技能供需热力图缓存
_job_similarity_cache = None        # 岗位相似度网络缓存
_salary_curve_cache = None          # 薪资成长曲线缓存
_edu_premium_cache = None           # 学历溢价分析缓存
_Salary_classifier_cache = None     # 薪资档位分类模型缓存
_conversations = {}                 # Agent 对话持久化: {chat_uuid: {question, answer, trace}}
_review_store = {}                    # 简历审查结果持久化(体积大, 不进 cookie): {review_uuid: state}
_SERVER_STORE_MAX = 200            # 服务端会话存储上限(超出淘汰最早条目,防长驻进程内存无限增长)
_lazy_lock = threading.RLock()     # 懒加载/重训互斥锁(RLock: _get_similarity 内部会再调 _get_clustering)

CLUSTERING_LABEL_VERSION = 2  # 聚类结果包版本: 2 = c-TF-IDF 判别性命名
CLASSIFIER_PKG_VERSION = 2  # 分类器结果包口径版本: 2 = content 特征 + 有序指标(mae/相邻准确率)


def _store_put(store, key, value):
    """模块级会话存储写入;超过上限时按插入顺序淘汰最早条目。"""
    store[key] = value
    while len(store) > _SERVER_STORE_MAX:
        store.pop(next(iter(store)))


def _invalidate_chart_analysis_cache():
    """清空图表 AI 分析缓存 + 图表数据缓存 + /ml 分析缓存 + 城市对比 AI 解读缓存，采集新数据后必须调用。"""
    global _chart_data_cache
    _chart_analysis_cache.clear()
    _ml_analysis_cache.clear()
    _compare_analysis_cache.clear()
    _chart_data_cache = None
    _logger.debug('图表 AI 分析缓存 + 数据缓存 + /ml 分析缓存 + 城市对比 AI 解读缓存已清空')


def _salary_max_info():
    """当前库中薪资最高的岗位,用于薪资分布页的离群说明;空库返回 None。"""
    import sqlite3
    try:
        conn = _raw_connect()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT post, company, salary_max FROM data "
            "WHERE salary_max IS NOT NULL ORDER BY salary_max DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row or not row['salary_max']:
            return None
        return {
            'max_k': round(float(row['salary_max']), 1),
            'post': row['post'] or '未知岗位',
            'company': row['company'] or '未知公司',
            # 只有当最高薪明显高于主体分布(≥10 万/月)时才值得提示
            'is_outlier': float(row['salary_max']) >= 100,
        }
    except sqlite3.Error:
        return None


def _compute_chart_data():
    """计算图表所需全部统计数据（带 5 分钟缓存；加锁避免并发重复计算）。"""
    global _chart_data_cache
    now = time.time()
    if _chart_data_cache and now - _chart_data_cache[0] < _CHART_DATA_CACHE_TTL:
        return _chart_data_cache[1]
    with _lazy_lock:
        now = time.time()
        # 双重检查：等锁期间可能已被其他线程填充
        if _chart_data_cache and now - _chart_data_cache[0] < _CHART_DATA_CACHE_TTL:
            return _chart_data_cache[1]

        import analysis.xinzi as xinzi
        import analysis.xueli as xueli
        import analysis.jinyan as jinyan
        import analysis.region as region
        import analysis.cross as cross
        from analysis.wordcloud_gen import generate_wordcloud_data

        data = {
            'xz': xinzi.xinzi(),
            'xl': xueli.xuelifun(),
            'jy': jinyan.jinyanfun(),
            'city_data': region.regionfun(),
            'cross_exper': cross.salary_vs_exper(),
            'cross_edu': cross.salary_vs_edu(),
            'wc_data': generate_wordcloud_data(top_n=60),
            'salary_max_info': _salary_max_info(),
        }
        _logger.info('图表数据缓存已更新')
        _chart_data_cache = (now, data)
        return data


# 聚类结果懒加载设计说明：
# 不在启动时触发 sklearn/pandas 的首次 .pyc 编译，
# 等首个访问 /ml 的请求到来时才计算（避免误判启动卡死）。

def _joblib_load_fresh(path):
    """加载 joblib 缓存文件,并把"疑似过期"归约为一个布尔值。

    pkg_version / label_version 只覆盖自定义口径版本号,覆盖不到
    sklearn 自身的小版本升级(如 1.9.0 → 1.9.1):反序列化旧对象时
    sklearn 会发 InconsistentVersionWarning。这里捕获加载期间的该警告,
    一律视为旧缓存,调用方据此自动重训,启动日志不再带版本警告。

    Returns:
        (obj, stale): 加载成功且无版本警告 → (obj, False);
        加载失败或版本不一致 → (None 或 obj, True)。
    """
    import joblib as _joblib
    try:
        from sklearn.exceptions import InconsistentVersionWarning
        version_guard = True
    except ImportError:  # 极旧/极新 sklearn 无此警告类时退化为只做异常防护
        version_guard = False

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        try:
            obj = _joblib.load(path)
        except Exception as e:
            _logger.warning('缓存加载失败: %s', e)
            return None, True

    stale = version_guard and any(
        issubclass(w.category, InconsistentVersionWarning) for w in caught)
    return obj, stale


def _cluster_cache_has_job_ids(cluster):
    """检查聚类缓存是否包含方向岗位明细所需的 job_ids 字段。"""
    if not isinstance(cluster, dict) or 'clusters' not in cluster:
        return False
    return all('job_ids' in c for c in cluster['clusters'])


def _load_or_train_models():
    """尝试加载磁盘缓存的聚类模型;失败/不存在/格式过旧/版本不一致则重训并持久化。"""
    import joblib as _joblib
    import modeling.job_clustering as job_clustering

    _cache_dir = os.path.join(_PROJECT_ROOT, 'cache')
    os.makedirs(_cache_dir, exist_ok=True)
    _cluster_path = os.path.join(_cache_dir, 'clustering_result.joblib')

    if not _has_data():
        _logger.warning('数据库为空,跳过模型预计算。请先执行数据采集后再使用 /ml 和 Agent 功能。')
        return None

    # 尝试加载持久化聚类模型
    cluster = None
    if os.path.exists(_cluster_path):
        cluster, stale = _joblib_load_fresh(_cluster_path)
        # sklearn 版本不一致 / 缺 job_ids / 命名口径升级 → 一律重训
        if stale or not (_cluster_cache_has_job_ids(cluster)
                and cluster.get('label_version') == CLUSTERING_LABEL_VERSION):
            _logger.info('检测到旧版聚类缓存(sklearn版本不一致/缺 job_ids/命名口径旧),将重新训练')
            cluster = None
        else:
            _logger.info('从磁盘加载聚类结果 (k=%d)', cluster['k'])

    # 缺失则训练
    try:
        if cluster is None:
            cluster = job_clustering.run_clustering()
            _joblib.dump(cluster, _cluster_path)
            _logger.info('聚类模型已训练并保存到磁盘 (k=%d)', cluster['k'])
    except Exception as e:
        _logger.error('聚类训练失败: %s。部分功能可能不可用。', e)
        cluster = None

    return cluster


def _get_clustering():
    """获取聚类结果（首次调用时训练+缓存；加锁避免并发重复训练/并发写缓存文件）。"""
    global _clustering_cache
    if _clustering_cache is not None:
        return _clustering_cache
    with _lazy_lock:
        if _clustering_cache is not None:  # 等锁期间可能已被其他线程填充
            return _clustering_cache
        _logger.info('正在预计算职位聚类(只在第一次访问时跑一次)...')
        _clustering_cache = _load_or_train_models()
        if _clustering_cache is not None:
            _logger.info('预计算完成。')
        return _clustering_cache


def _get_skill_heatmap():
    """获取技能供需热力图数据（懒加载缓存 + 互斥）。"""
    global _skill_heatmap_cache
    if _skill_heatmap_cache is not None:
        return _skill_heatmap_cache
    with _lazy_lock:
        if _skill_heatmap_cache is not None:
            return _skill_heatmap_cache
        try:
            from modeling.skill_heatmap import compute_skill_heatmap
            _skill_heatmap_cache = compute_skill_heatmap()
        except Exception as e:
            _logger.warning('技能热力图计算失败: %s', e)
            _skill_heatmap_cache = {'error': str(e), 'total_rows': 0}
        return _skill_heatmap_cache


def _get_similarity():
    """获取岗位相似度网络数据（懒加载缓存 + 互斥；内部再调 _get_clustering，RLock 可重入）。"""
    global _job_similarity_cache
    if _job_similarity_cache is not None:
        return _job_similarity_cache
    with _lazy_lock:
        if _job_similarity_cache is not None:
            return _job_similarity_cache
        clustering = _get_clustering()
        if not clustering:
            _job_similarity_cache = {'error': '聚类模型未训练'}
            return _job_similarity_cache
        try:
            from modeling.job_similarity import compute_similarity_network
            _job_similarity_cache = compute_similarity_network(clustering)
        except Exception as e:
            _logger.warning('相似度网络计算失败: %s', e)
            _job_similarity_cache = {'error': str(e)}
        return _job_similarity_cache


def _get_salary_curve():
    """获取薪资成长曲线数据（懒加载缓存 + 互斥）。"""
    global _salary_curve_cache
    if _salary_curve_cache is not None:
        return _salary_curve_cache
    with _lazy_lock:
        if _salary_curve_cache is not None:
            return _salary_curve_cache
        try:
            from modeling.salary_curve import compute_salary_curve
            _salary_curve_cache = compute_salary_curve()
        except Exception as e:
            _logger.warning('薪资曲线计算失败: %s', e)
            _salary_curve_cache = {'error': str(e), 'total_rows': 0}
        return _salary_curve_cache


def _get_edu_premium():
    """获取学历溢价分析数据（懒加载缓存 + 互斥）。"""
    global _edu_premium_cache
    if _edu_premium_cache is not None:
        return _edu_premium_cache
    with _lazy_lock:
        if _edu_premium_cache is not None:
            return _edu_premium_cache
        try:
            from modeling.edu_premium import compute_edu_premium
            _edu_premium_cache = compute_edu_premium()
        except Exception as e:
            _logger.warning('学历溢价计算失败: %s', e)
            _edu_premium_cache = {'error': str(e), 'total_rows': 0}
        return _edu_premium_cache


def _get_salary_classifier():
    """获取薪资档位分类模型（懒加载+缓存+joblib持久化；加锁避免并发训练/并发写缓存）。"""
    global _Salary_classifier_cache
    if _Salary_classifier_cache is not None:
        return _Salary_classifier_cache
    with _lazy_lock:
        if _Salary_classifier_cache is not None:  # 等锁期间可能已被其他线程填充
            return _Salary_classifier_cache
        _cache_dir = os.path.join(_PROJECT_ROOT, 'cache')
        os.makedirs(_cache_dir, exist_ok=True)
        _classifier_path = os.path.join(_cache_dir, 'salary_classifier.joblib')

        # 尝试从磁盘加载
        try:
            if os.path.exists(_classifier_path):
                # pkg_version / sklearn 版本任一不一致都说明是旧缓存,强制重训
                pkg, stale = _joblib_load_fresh(_classifier_path)
                if not stale and isinstance(pkg, dict) and '_model' in pkg \
                        and pkg.get('pkg_version') == CLASSIFIER_PKG_VERSION:
                    _logger.info('从磁盘加载薪资分类器 (model=%s, n=%d)',
                                 pkg.get('metrics', {}).get('model_name', '?'),
                                 pkg.get('total_rows', 0))
                    _Salary_classifier_cache = pkg
                    return pkg
                _logger.info('检测到旧版薪资分类器缓存(sklearn版本不一致或口径旧),将重新训练')
        except Exception as e:
            _logger.warning('薪资分类器磁盘加载失败: %s', e)

        # 训练
        try:
            from modeling.salary_classifier import compute_salary_classifier
            import joblib as _joblib
            pkg = compute_salary_classifier(min_samples=50)
            if 'error' not in pkg and '_model' in pkg:
                _joblib.dump(pkg, _classifier_path)
                _logger.info('薪资分类器已训练并保存到磁盘')
            else:
                _logger.warning('薪资分类器训练未产生有效模型: %s', pkg.get('error', '未知'))
            _Salary_classifier_cache = pkg
            return pkg
        except Exception as e:
            _logger.warning('薪资分类器训练失败: %s', e)
            _Salary_classifier_cache = {'error': str(e), 'total_rows': 0}
            return _Salary_classifier_cache


def _invalidate_modeling_caches():
    """清空所有建模模块缓存（采集新数据/重训后调用）。"""
    global _skill_heatmap_cache, _job_similarity_cache, _salary_curve_cache, _edu_premium_cache, \
        _Salary_classifier_cache
    _skill_heatmap_cache = None
    _job_similarity_cache = None
    _salary_curve_cache = None
    _edu_premium_cache = None
    _Salary_classifier_cache = None
    _logger.debug('建模模块全部缓存已清空')


def _retrain_clustering_now():
    """采集新数据后同步重训聚类并持久化（原 /collect 路由内联逻辑迁入）。

    失败只告警：_clustering_cache 保持 None，后续访问由懒加载兜底重算。
    """
    global _clustering_cache
    import joblib as _joblib
    import modeling.job_clustering as job_clustering

    _cache_dir = os.path.join(_PROJECT_ROOT, 'cache')
    _cluster_path = os.path.join(_cache_dir, 'clustering_result.joblib')
    try:
        _clustering_cache = job_clustering.run_clustering()
        _joblib.dump(_clustering_cache, _cluster_path)
        _logger.info('采集后聚类模型已重新训练并持久化 (k=%d)', _clustering_cache['k'])
    except Exception as e:
        _logger.warning('采集后聚类模型重算失败: %s', e)
