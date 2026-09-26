"""
薪资档位分类模型 — 以城市/学历/经验/岗位类别/技能词为特征，
预测岗位薪资所属档位（如「8-12K档」）。

流程：DB 读取 → 特征工程 → cross_val 选优(RF/GB/Logistic) →
joblib 持久化 → predict() 推理（含优雅降级）。

技能词有两个来源,统一汇入同一份词表做 one-hot:
- keywords 字段(51job 官方 jobTags, 逗号分隔)
- content 职位描述全文(CONTENT_SKILL_PATTERNS 词典抽取,
  覆盖 jobTags 没写到但描述里明确要求的技能)

档位是有序标签,评估除 accuracy/macro-F1 外,同时输出
mae_bands(档位平均绝对误差)与 adjacent_acc(误差≤1档的比例)。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import sqlite3
import logging
import numpy as np

import config
from analysis.jobtitle import classify as _classify
from data.exper_parser import EXPER_ORDER, normalize_exper

_logger = logging.getLogger('modeling.salary_classifier')

# ============================================================
# 薪资档位边界定义（单位：K，mid=(min+max)/2）
# ============================================================
# 档位与 xinzi.py 图表分段风格对齐，按数据分布自动调整
BAND_EDGES = [0, 6, 10, 14, 18, 22, 30]
BAND_LABELS = ['<6K', '6-10K', '10-14K', '14-18K', '18-22K', '22K+']

# ============================================================
# 技能词黑名单 — 过滤掉福利/要求/公司性质等非技能关键词
# ============================================================
SKILL_BLACKLIST = {
    # 福利待遇
    '五险一金', '六险一金', '五险', '三险', '绩效奖金', '年终奖金', '年终分红',
    '带薪年假', '带薪休假', '带薪病假', '周末双休', '弹性工作', '弹性工作制',
    '餐饮补贴', '餐补', '有餐补', '交通补贴', '交通补助', '通讯补贴',
    '定期体检', '定期团建', '员工旅游', '专业培训', '包吃', '包住', '包吃包住',
    '补充公积金', '补充医疗保险', '补充医疗', '出国机会',
    '股票期权', '期权', '全勤奖', '节日福利', '生日福利', '下午茶',
    '零食下午茶', '零食', '健身房', '高温补贴', '住房补贴', '房补',
    '团建', '旅游', '出差补贴', '项目奖金', '节假日福利', '团队氛围好',
    '人才推荐奖', '免费三餐', '大牛带队', '牛人带队',
    # 经验/年限要求（非技能）
    '无需经验', '经验不限', '在校生', '应届生', '应届',
    '3年及以上', '5年及以上', '1年及以上', '2年及以上',
    '4年及以上', '10年以上', '8年及以上', '6年及以上',
    '1-3年', '3-5年', '5-10年', '10年以上经验',
    '1年', '2年', '3年', '5年', '1年经验', '3年经验', '5年经验',
    'x年经验', '经验要求',
    # 学历标签（非技能，已作为独立序数特征）
    '本科', '大专', '硕士', '博士', '中专', '高中', '学历',
    '本科及以上', '硕士及以上', '大专及以上', '学历不限',
    '软件工程', '计算机科学', '计算机相关',
    # 公司性质
    '民营', '民营公司', '外资', '外企', '国企', '合资', '上市公司',
    '创业公司', '创业', '外资(欧美)', '外资(非欧美)',
    # 工作制度
    '做五休二', '朝九晚五', '朝九晚六', '双休', '单休', '大小周',
    # 通用软素质（太泛，无区分度）
    '良好沟通', '团队合作', '责任心强', '责任心', '抗压能力',
    '学习能力', '沟通能力', '团队协作',
    # 过于宽泛的领域词（无薪资区分度）
    '计算机', '互联网', '培训',
    # 杂项
    '年终奖', '年底双薪', '免费班车', '招若干人',
}

# 黑名单子串集 — 技能词中只要包含这些子串之一即过滤
# （用于匹配复合词如 "零食下午茶" 含 "下午茶"）
SKILL_BLACKLIST_SUBSTR = {
    '下午茶', '零食', '餐补', '补贴', '奖金', '福利',
    '五险', '公积金', '年假', '双休', '团建', '旅游',
}

# ============================================================
# 学历序数编码
# ============================================================
EDU_ORDER = {
    '不限': 0, '高中': 1, '中专': 2, '大专': 3,
    '本科': 4, '硕士': 5, '博士': 6,
}


def _normalize_edu(edu_raw):
    """归一化学历字符串到 EDU_ORDER 键。"""
    if not edu_raw:
        return '不限'
    edu_lower = edu_raw.strip()
    for key in ['博士', '硕士', '本科', '大专', '中专', '高中']:
        if key in edu_lower:
            return key
    return '不限'


def _extract_city(address):
    """从 address 字段拆出城市名。"""
    if not address:
        return '未知'
    return address.split('-')[0].strip()


def _extract_skills_from_keywords(keywords_str):
    """从 keywords 字段拆出技能词列表。"""
    if not keywords_str:
        return []
    parts = [k.strip() for k in keywords_str.replace(',', ' ').replace('，', ' ').split()]
    return [p for p in parts if len(p) >= 2]


# ============================================================
# 职位描述(content)技能词典 — 补足 jobTags 没覆盖的技术要求
# ============================================================
# 值为正则(大小写不敏感),键为归一化后的技能名(进 one-hot 词表)。
# 只收录"描述里出现即代表确实要求该技能"的词,避免泛词稀释特征。
CONTENT_SKILL_PATTERNS = {
    'sql': re.compile(r'\bsql\b', re.I),
    'mysql': re.compile(r'\bmysql\b', re.I),
    'postgresql': re.compile(r'postgres', re.I),
    'mongodb': re.compile(r'mongodb', re.I),
    'redis': re.compile(r'\bredis\b', re.I),
    'elasticsearch': re.compile(r'elastic|es\b', re.I),
    'linux': re.compile(r'\blinux\b', re.I),
    'docker': re.compile(r'docker|容器化', re.I),
    'kubernetes': re.compile(r'k8s|kubernetes', re.I),
    'git': re.compile(r'\bgit\b|gitlab|github', re.I),
    'django': re.compile(r'django', re.I),
    'flask': re.compile(r'flask', re.I),
    'fastapi': re.compile(r'fastapi', re.I),
    'django-rest': re.compile(r'django\s*rest|drf\b', re.I),
    'celery': re.compile(r'celery', re.I),
    'pandas': re.compile(r'pandas', re.I),
    'numpy': re.compile(r'numpy', re.I),
    'spark': re.compile(r'spark', re.I),
    'hadoop': re.compile(r'hadoop|hive|hbase', re.I),
    'flink': re.compile(r'flink', re.I),
    'kafka': re.compile(r'kafka', re.I),
    'rabbitmq': re.compile(r'rabbitmq|rocketmq|消息队列', re.I),
    'pytorch': re.compile(r'pytorch|torch\b', re.I),
    'tensorflow': re.compile(r'tensorflow|keras', re.I),
    'sklearn': re.compile(r'scikit-learn|sklearn', re.I),
    '机器学习': re.compile(r'机器学习|machine\s*learning', re.I),
    '深度学习': re.compile(r'深度学习|deep\s*learning', re.I),
    'nlp': re.compile(r'\bnlp\b|自然语言处理', re.I),
    'cv': re.compile(r'计算机视觉|\bcv\b|图像识别|opencv', re.I),
    '大模型': re.compile(r'大模型|大语言模型|\bllm\b|\bgpt\b|aigc|agent开发', re.I),
    '推荐系统': re.compile(r'推荐系统|推荐算法', re.I),
    '数据挖掘': re.compile(r'数据挖掘|data\s*mining', re.I),
    '数据仓库': re.compile(r'数仓|数据仓库|数据建模', re.I),
    'etl': re.compile(r'\betl\b', re.I),
    '爬虫': re.compile(r'爬虫|scrapy|采集程序', re.I),
    'java': re.compile(r'\bjava\b(?!script)', re.I),
    'spring': re.compile(r'spring', re.I),
    'golang': re.compile(r'golang|\bgo语言|\bgo\b(?!ogl)', re.I),
    'c++': re.compile(r'c\+\+', re.I),
    'c#': re.compile(r'c#|\.net\b', re.I),
    'php': re.compile(r'\bphp\b', re.I),
    'javascript': re.compile(r'javascript|typescript|\bjs\b', re.I),
    'vue': re.compile(r'\bvue\b', re.I),
    'react': re.compile(r'react', re.I),
    'nodejs': re.compile(r'node\.?js', re.I),
    '微服务': re.compile(r'微服务|spring\s*cloud|服务治理', re.I),
    'restful': re.compile(r'restful|rest\s*api|rest接口', re.I),
    '前端开发': re.compile(r'前端|web页面|h5', re.I),
    '自动化测试': re.compile(r'自动化测试|pytest|unittest|selenium', re.I),
    '性能优化': re.compile(r'性能优化|性能调优|高并发', re.I),
}


def _extract_skills_from_content(content):
    """从职位描述全文抽取技能词(归一化名,已是小写 canonical 形式)。"""
    if not content:
        return []
    text = content[:3000]  # 截断,防止超长描述拖慢训练
    found = []
    for name, pattern in CONTENT_SKILL_PATTERNS.items():
        if pattern.search(text):
            found.append(name)
    return found


def _get_band_index(salary_mid):
    """返回 salary_mid 所在的档位索引。"""
    for i in range(len(BAND_EDGES) - 1):
        if BAND_EDGES[i] <= salary_mid < BAND_EDGES[i + 1]:
            return i
    return len(BAND_EDGES) - 2  # 落入最后一档


# ============================================================
# 数据读取
# ============================================================
def _fetch_rows():
    """从 DB 读取建模所需字段(含 content 职位描述)。"""
    try:
        db = sqlite3.connect(config.DB_PATH)
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, post, address, salary_min, salary_max, edu, exper, keywords, content FROM data"
        )
        rows = cursor.fetchall()
        db.close()
        return rows
    except sqlite3.Error as e:
        _logger.warning('salary_classifier 读取数据库失败: %s', e)
        return []


# ============================================================
# 特征工程
# ============================================================
def _build_features(rows, skill_vocab=None):
    """从 DB 行构建特征矩阵与标签。

    Args:
        rows: DB 查询结果列表
        skill_vocab: 已知技能词表（训练时 None=从数据构建，推理时传入）

    Returns:
        dict: {
            X: np.ndarray (n_samples, n_features),
            y: np.ndarray (n_samples,) — 档位索引,
            feature_names: [str],
            city_vocab: {city: idx},
            edu_order: {edu_str: int},
            exper_order: {exper_str: int},
            category_vocab: {category: idx},
            skill_vocab: {skill: idx},
            band_labels: [str],
            total_rows: int,
            error: str or None,
        }
    """
    if not rows:
        return {'error': '数据库中没有岗位数据', 'total_rows': 0}

    # 收集有效样本
    samples = []
    for row in rows:
        smin = row['salary_min']
        smax = row['salary_max']
        if not smin or not smax or (smin + smax) <= 0:
            continue
        salary_mid = (smin + smax) / 2

        city = _extract_city(row['address'])
        edu = _normalize_edu(row['edu'])
        exper = normalize_exper(row['exper'])
        category = _classify(row['post'] or '')
        # 技能词 = jobTags 官方标签 ∪ 职位描述词典抽取,统一去重
        skills = list(dict.fromkeys(
            [s.lower() for s in _extract_skills_from_keywords(row['keywords'])]
            + _extract_skills_from_content(row['content'])
        ))
        band_idx = _get_band_index(salary_mid)

        samples.append({
            'city': city,
            'edu': edu,
            'exper': exper,
            'category': category,
            'skills': skills,
            'band_idx': band_idx,
            'salary_mid': salary_mid,
        })

    n_total = len(samples)
    if n_total < 30:
        return {'error': f'有效样本不足 (仅 {n_total} 个，需 ≥ 30)', 'total_rows': n_total}

    # 构建词表（训练时）或直接使用传入的词表（推理时）
    if skill_vocab is None:
        # 统计技能词频，取 Top 30（大小写不敏感，避免 Python/python 分裂）
        skill_counts = {}
        for s in samples:
            for sk in s['skills']:
                sk_norm = sk.lower()
                # 过滤非技能关键词（精确黑名单 + 子串黑名单）
                if sk_norm in SKILL_BLACKLIST or sk in SKILL_BLACKLIST:
                    continue
                if any(sub in sk_norm for sub in SKILL_BLACKLIST_SUBSTR):
                    continue
                skill_counts[sk_norm] = skill_counts.get(sk_norm, 0) + 1
        # 只保留出现 ≥ 3 次的真实技能词
        skill_vocab = {
            k: i for i, (k, v) in enumerate(
                sorted(
                    [(k, v) for k, v in skill_counts.items() if v >= 3],
                    key=lambda x: -x[1]
                )[:30]
            )
        }

    # 构建类别词表
    categories = sorted(set(s['category'] for s in samples))
    category_vocab = {c: i for i, c in enumerate(categories)}

    # 构建城市词表（只保留出现 ≥ 2 次的城市，其余归为「其他」）
    city_counts = {}
    for s in samples:
        city_counts[s['city']] = city_counts.get(s['city'], 0) + 1
    top_cities = sorted(
        [c for c, cnt in city_counts.items() if cnt >= 2],
        key=lambda c: -city_counts[c]
    )[:20]
    city_vocab = {c: i for i, c in enumerate(top_cities)}
    city_vocab['其他'] = len(city_vocab)

    # 构建特征矩阵
    n_city = len(city_vocab)
    n_edu = 1  # 序数，1 维
    n_exper = 1  # 序数，1 维
    n_category = len(category_vocab)
    n_skill = len(skill_vocab)
    n_features = n_city + n_edu + n_exper + n_category + n_skill

    X = np.zeros((n_total, n_features), dtype=np.float32)
    y = np.zeros(n_total, dtype=np.int32)

    for i, s in enumerate(samples):
        col = 0
        # 城市 one-hot
        city_idx = city_vocab.get(s['city'], city_vocab['其他'])
        X[i, col + city_idx] = 1.0
        col += n_city

        # 学历序数
        edu_num = EDU_ORDER.get(s['edu'], 0)
        max_edu = max(EDU_ORDER.values()) or 1
        X[i, col] = edu_num / max_edu
        col += n_edu

        # 经验序数
        exper_num = EXPER_ORDER.get(s['exper'], 0)
        max_exper = max(EXPER_ORDER.values()) or 1
        X[i, col] = exper_num / max_exper
        col += n_exper

        # 岗位类别 one-hot
        cat_idx = category_vocab.get(s['category'], 0)
        X[i, col + cat_idx] = 1.0
        col += n_category

        # 技能 one-hot（大小写归一化，与词表构建一致）
        for sk in s['skills']:
            sk_norm = sk.lower()
            if sk_norm in skill_vocab:
                X[i, col + skill_vocab[sk_norm]] = 1.0
        # col += n_skill  # 已到末尾

        y[i] = s['band_idx']

    # 构造特征名列表
    feature_names = []
    for c in sorted(city_vocab, key=lambda k: city_vocab[k]):
        feature_names.append(f'城市_{c}')
    feature_names.append('学历_序数')
    feature_names.append('经验_序数')
    for cat in sorted(category_vocab, key=lambda k: category_vocab[k]):
        feature_names.append(f'类别_{cat}')
    for sk in sorted(skill_vocab, key=lambda k: skill_vocab[k]):
        feature_names.append(f'技能_{sk}')

    return {
        'X': X,
        'y': y,
        'feature_names': feature_names,
        'city_vocab': city_vocab,
        'edu_order': EDU_ORDER,
        'exper_order': EXPER_ORDER,
        'category_vocab': category_vocab,
        'skill_vocab': skill_vocab,
        'band_labels': BAND_LABELS[:len(set(y))],  # 实际出现的档位数
        'total_rows': n_total,
        'samples': samples,
    }


# ============================================================
# 训练 + 评估
# ============================================================
# GradientBoostingClassifier 不支持 class_weight 参数,用派生类在 fit 时
# 注入 balanced 样本权重,保证三档候选模型在类别不均衡下可比。
# 必须定义在模块顶层: pkg['_model'] 会被 joblib 序列化,
# 反序列化要求类可按 modeling.salary_classifier._BalancedGB 导入。
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.utils.class_weight import compute_sample_weight


class _BalancedGB(GradientBoostingClassifier):
    """带 class_weight='balanced' 语义的 GradientBoosting(fit 时自动加权)。"""

    def fit(self, X, y, **kwargs):
        if 'sample_weight' not in kwargs:
            kwargs['sample_weight'] = compute_sample_weight('balanced', y)
        return super().fit(X, y, **kwargs)


def compute_salary_classifier(min_samples=50):
    """训练薪资档位分类模型并返回完整结果包。

    Args:
        min_samples: 最小样本数阈值

    Returns:
        dict: {
            bands: [{label, count, pct}],  # 各档位样本分布
            metrics: {accuracy, macro_f1, model_name, n_classes, n_samples},
            feature_importances: [{name, importance}],  # Top-15 降序
            confusion_matrix: [[int]],  # 混淆矩阵
            total_rows: int,
            band_labels: [str],
            # 以下用于持久化（不直接传给模板）
            _model: 拟合的 sklearn 模型对象,
            _features: 特征工程包 (city_vocab, skill_vocab, category_vocab, feature_names),
        }
        或 {'error': str, 'total_rows': int}
    """
    rows = _fetch_rows()
    if not rows:
        return {'error': '数据库中没有岗位数据，请先采集', 'total_rows': 0}

    feat = _build_features(rows)
    if feat.get('error'):
        return {'error': feat['error'], 'total_rows': feat.get('total_rows', 0)}

    X = feat['X']
    y = feat['y']
    n_total = feat['total_rows']

    if n_total < min_samples:
        return {'error': f'有效样本不足 (仅 {n_total} 个，需 ≥ {min_samples})', 'total_rows': n_total}

    # 检查类别数
    unique_labels = np.unique(y)
    n_classes = len(unique_labels)
    if n_classes < 2:
        return {'error': f'薪资档位只有 {n_classes} 个，无法做有意义的分类', 'total_rows': n_total}

    # 档位分布
    label_counts = []
    for i in range(len(BAND_LABELS)):
        cnt = int(np.sum(y == i))
        if cnt > 0:
            label_counts.append({
                'label': BAND_LABELS[i],
                'count': cnt,
                'pct': round(cnt / n_total * 100, 1),
            })

    # ---- 模型选优：RF / GB / Logistic 交叉验证 ----
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score, StratifiedKFold
    from sklearn.metrics import classification_report, confusion_matrix

    cv = StratifiedKFold(n_splits=min(5, n_classes), shuffle=True, random_state=42)

    candidates = {
        'RandomForest': RandomForestClassifier(
            n_estimators=120, max_depth=10, random_state=42, class_weight='balanced'
        ),
        'GradientBoosting': _BalancedGB(
            n_estimators=100, max_depth=4, random_state=42
        ),
        'LogisticRegression': LogisticRegression(
            max_iter=2000, class_weight='balanced', random_state=42
        ),
    }

    best_name = None
    best_score = -1
    best_model = None

    for name, model in candidates.items():
        try:
            scores = cross_val_score(model, X, y, cv=cv, scoring='f1_macro')
            avg = float(np.mean(scores))
            _logger.info('  %s CV macro-F1: %.3f (±%.3f)', name, avg, float(np.std(scores)))
            if avg > best_score:
                best_score = avg
                best_name = name
                best_model = model
        except Exception as e:
            _logger.warning('  %s CV 失败: %s', name, e)

    if best_model is None:
        return {'error': '所有候选模型交叉验证失败', 'total_rows': n_total}

    _logger.info('最佳模型: %s (CV macro-F1=%.3f)', best_name, best_score)

    # ---- 训练/测试集分离，诚实评估泛化能力 ----
    from sklearn.model_selection import train_test_split

    if n_total >= 100:
        # 样本量够大：80/20 分层划分
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        use_split = True
    else:
        # 样本量偏小：留一法交叉验证粗略估算
        _logger.info('  样本量 %d < 100，使用留一法交叉验证估算泛化指标', n_total)
        use_split = False

    if use_split:
        # ① 在训练集上拟合，在测试集上评估
        best_model.fit(X_train, y_train)
        y_pred = best_model.predict(X_test)
        y_eval = y_test
    else:
        # 留一法交叉验证评估(逐样本包外预测,可与全量 y 直接比较)
        from sklearn.model_selection import cross_val_predict
        _splits = min(5, min(int(np.bincount(y).min()), n_classes))
        if _splits < 2:
            _splits = 2
        y_pred = cross_val_predict(best_model, X, y, cv=_splits)
        y_eval = y

    # ② 评估指标(档位是有序标签,除分类指标外补有序误差:
    #    mae_bands = 平均偏了几档; adjacent_acc = 偏差不超过 1 档的比例)
    _abs_diff = np.abs(y_pred.astype(np.int32) - y_eval)
    report = classification_report(y_eval, y_pred, output_dict=True, zero_division=0)
    test_accuracy = float(report.get('accuracy', 0))
    test_macro_f1 = float(report.get('macro avg', {}).get('f1-score', 0))
    mae_bands = float(_abs_diff.mean())
    adjacent_acc = float(np.mean(_abs_diff <= 1))
    cm = confusion_matrix(y_eval, y_pred).tolist()

    # ③ 最终模型：全量重新拟合（用于实际部署推理）
    best_model.fit(X, y)

    # ---- 特征重要性（基于全量训练后的模型） ----
    if hasattr(best_model, 'feature_importances_'):
        importances = best_model.feature_importances_
    elif hasattr(best_model, 'coef_'):
        # Logistic 取系数绝对值均值
        importances = np.abs(best_model.coef_).mean(axis=0)
    else:
        importances = np.ones(len(feat['feature_names']))

    imp_pairs = sorted(
        zip(feat['feature_names'], importances),
        key=lambda x: -x[1]
    )[:15]
    feature_importances = [
        {'name': name, 'importance': round(float(imp), 4)}
        for name, imp in imp_pairs if imp > 0.001
    ]

    # 构造返回包
    result = {
        'pkg_version': 2,  # 口径版本: 2 = content 特征 + 有序指标;旧缓存据此自动重训
        'bands': label_counts,
        'metrics': {
            'accuracy': round(test_accuracy, 3),
            'macro_f1': round(test_macro_f1, 3),
            'cv_macro_f1': round(best_score, 3),  # 交叉验证得分（模型选优用）
            'mae_bands': round(mae_bands, 3),      # 有序误差: 平均偏几档
            'adjacent_acc': round(adjacent_acc, 3),  # 偏差 ≤ 1 档的比例
            'model_name': best_name,
            'n_classes': n_classes,
            'n_samples': n_total,
            'n_train': int(X_train.shape[0]) if use_split else n_total,
            'n_test': int(X_test.shape[0]) if use_split else n_total,
        },
        'feature_importances': feature_importances,
        'confusion_matrix': cm,
        'total_rows': n_total,
        'band_labels': [b['label'] for b in label_counts],
        'band_counts': [b['count'] for b in label_counts],
        # 持久化字段
        '_model': best_model,
        '_features': {
            'city_vocab': feat['city_vocab'],
            'skill_vocab': feat['skill_vocab'],
            'category_vocab': feat['category_vocab'],
            'feature_names': feat['feature_names'],
            'band_labels': BAND_LABELS,
            'band_edges': BAND_EDGES,
            'edu_order': EDU_ORDER,
            'exper_order': EXPER_ORDER,
        },
    }

    _logger.info(
        '薪资分类器训练完成: %s, test_accuracy=%.3f, test_macro_f1=%.3f, cv_macro_f1=%.3f, n=%d',
        best_name, test_accuracy, test_macro_f1, best_score, n_total
    )
    return result


# ============================================================
# 推理
# ============================================================
def predict_salary_band(pkg, city='', edu='', exper='', skills=None):
    """输入用户条件，返回预测档位 + 各档位概率分布。

    Args:
        pkg: compute_salary_classifier() 返回的结果包
        city: 城市名称
        edu: 学历字符串
        exper: 经验字符串
        skills: 技能词列表或逗号分隔字符串

    Returns:
        dict: {
            predicted_band: str,
            probabilities: [{band, prob}],
            input: {city, edu, exper, skills},
        }
        或 {'error': str}
    """
    if pkg.get('error') or '_model' not in pkg:
        return {'error': '模型尚未训练或训练失败，无法预测'}

    model = pkg['_model']
    feat_cfg = pkg.get('_features', {})

    if not feat_cfg:
        return {'error': '模型特征配置丢失'}

    # 归一化输入
    edu_norm = _normalize_edu(edu) if edu else '不限'
    exper_norm = normalize_exper(exper) if exper else '经验不限'

    # 技能处理
    if isinstance(skills, str):
        skills_list = [s.strip() for s in skills.replace(',', ' ').replace('，', ' ').split() if s.strip()]
    elif skills is None:
        skills_list = []
    else:
        skills_list = list(skills)

    # 城市映射
    city_vocab = feat_cfg['city_vocab']
    skill_vocab = feat_cfg['skill_vocab']
    category_vocab = feat_cfg['category_vocab']
    band_labels = feat_cfg.get('band_labels', BAND_LABELS)

    # 总特征维度 = city + 1(edu) + 1(exper) + category + skill
    n_city = len(city_vocab)
    n_category = len(category_vocab)
    n_skill = len(skill_vocab)
    n_features = n_city + 1 + 1 + n_category + n_skill

    X_input = np.zeros((1, n_features), dtype=np.float32)
    col = 0

    # 城市 one-hot
    city_idx = city_vocab.get(city, city_vocab.get('其他', 0))
    X_input[0, col + city_idx] = 1.0
    col += n_city

    # 学历序数
    edu_num = EDU_ORDER.get(edu_norm, 0)
    max_edu = max(EDU_ORDER.values()) or 1
    X_input[0, col] = edu_num / max_edu
    col += 1

    # 经验序数
    exper_num = EXPER_ORDER.get(exper_norm, 0)
    max_exper = max(EXPER_ORDER.values()) or 1
    X_input[0, col] = exper_num / max_exper
    col += 1

    # 类别 one-hot — 推理时无类别，取最常见的（第一个）
    X_input[0, col] = 1.0  # 默认分配第一个类别
    col += n_category

    # 技能 one-hot（大小写不敏感，兼容用户小写输入与数据库原始大小写）
    skill_lookup = {sk.lower(): sk for sk in skill_vocab}
    for sk in skills_list:
        sk_lower = sk.lower()
        if sk_lower in skill_lookup:
            X_input[0, col + skill_vocab[skill_lookup[sk_lower]]] = 1.0

    # 推理
    try:
        proba = model.predict_proba(X_input)[0]
        pred_idx = int(np.argmax(proba))
    except Exception as e:
        return {'error': f'推理失败: {str(e)}'}

    predicted_band = band_labels[pred_idx] if pred_idx < len(band_labels) else '未知'
    probabilities = [
        {
            'band': band_labels[i] if i < len(band_labels) else f'档位{i}',
            'prob': round(float(proba[i]), 4),
        }
        for i in range(len(proba))
    ]
    # 按概率降序
    probabilities.sort(key=lambda x: -x['prob'])

    return {
        'predicted_band': predicted_band,
        'probabilities': probabilities,
        'input': {
            'city': city or '未指定',
            'edu': edu or '未指定',
            'exper': exper or '未指定',
            'skills': skills_list,
        },
        'degraded': {
            'city': city not in city_vocab if city else False,
            'skills_not_found': [s for s in skills_list if s.lower() not in skill_lookup],
        },
    }


# ============================================================
# 自测入口
# ============================================================
if __name__ == '__main__':
    import json
    result = compute_salary_classifier(min_samples=30)
    if 'error' in result:
        print(f'错误: {result["error"]}')
    else:
        print(f'\n=== 模型: {result["metrics"]["model_name"]} ===')
        print(f'准确率: {result["metrics"]["accuracy"]:.3f}')
        print(f'Macro-F1: {result["metrics"]["macro_f1"]:.3f}')
        print(f'样本数: {result["metrics"]["n_samples"]}')
        print(f'档位数: {result["metrics"]["n_classes"]}')
        print('\n--- 档位分布 ---')
        for b in result['bands']:
            print(f'  {b["label"]}: {b["count"]} 岗 ({b["pct"]}%)')
        print('\n--- Top 10 特征重要性 ---')
        for f in result['feature_importances'][:10]:
            print(f'  {f["name"]}: {f["importance"]:.4f}')

        # 示例推理
        print('\n--- 示例推理 ---')
        pred = predict_salary_band(result, city='北京', edu='本科', exper='1-3年',
                                   skills=['Python', 'Django'])
        if 'error' not in pred:
            print(f'  预测档位: {pred["predicted_band"]}')
            for p in pred['probabilities']:
                bar = '█' * int(p['prob'] * 50)
                print(f'    {p["band"]}: {p["prob"]:.2%}  {bar}')
            if pred.get('degraded', {}).get('skills_not_found'):
                print(f'  未识别技能: {pred["degraded"]["skills_not_found"]}')
