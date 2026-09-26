"""
职位标题无监督聚类。

处理流程:jieba 中文分词 -> TF-IDF 向量化 -> 使用轮廓系数选择 k 值的 KMeans 聚类 ->
输出每个聚类的高权重关键词摘要。

特意采用轻量化、纯 sklearn 实现。典型采集数据约 <1000 行,更复杂的模型反而容易过拟合。
主要耗时在 jieba 分词与轮廓系数计算,两者均在 Flask 启动时预计算并缓存,避免页面刷新时重复运算。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import config
import jieba
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score


def get_posts():
    """返回 (id, post) 元组列表，id 用于关联薪资等字段。"""
    try:
        db = sqlite3.connect(config.DB_PATH)
        cursor = db.cursor()
        cursor.execute("SELECT id, post FROM data")
        rows = [(r[0], r[1]) for r in cursor.fetchall() if r[1]]
        db.close()
        return rows
    except sqlite3.Error as e:
        import logging
        logging.getLogger('modeling').warning('get_posts 读取数据库失败: %s', e)
        return []


def _cluster_salary_stats(posts_data, labels, k):
    """为每个聚类计算薪资统计(均薪、薪资范围)。"""
    ids = [p[0] for p in posts_data]
    if not ids:
        return []
    try:
        db = sqlite3.connect(config.DB_PATH)
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        placeholders = ','.join(['?'] * len(ids))
        if not placeholders:
            db.close()
            return []
        cursor.execute(
            f"SELECT id, salary_min, salary_max FROM data WHERE id IN ({placeholders})",
            ids
        )
        db_salaries = {}
        for row in cursor.fetchall():
            sid, smin, smax = row['id'], row['salary_min'], row['salary_max']
            if smin is not None and smax is not None and (smin + smax) > 0:
                db_salaries[sid] = (smin + smax) / 2
        db.close()
    except sqlite3.Error:
        try:
            db.close()
        except Exception:
            pass
        return [{'avg_salary': 0, 'min_salary': 0, 'max_salary': 0, 'salary_count': 0} for _ in range(k)]

    stats = []
    for i in range(k):
        idx = np.where(labels == i)[0]
        cluster_salaries = []
        for j in idx:
            sid = posts_data[j][0]
            if sid in db_salaries:
                cluster_salaries.append(db_salaries[sid])
        if cluster_salaries:
            stats.append({
                'avg_salary': round(float(np.mean(cluster_salaries)), 1),
                'min_salary': round(float(min(cluster_salaries)), 1),
                'max_salary': round(float(max(cluster_salaries)), 1),
                'salary_count': len(cluster_salaries),
            })
        else:
            stats.append({'avg_salary': 0, 'min_salary': 0, 'max_salary': 0, 'salary_count': 0})
    return stats


import re

# 几乎每个职位都会出现的高频词,对分类无贡献。
# 同时包含标点符号,由下方正则表达式过滤。
STOPWORDS = {
    # 核心职位词（几乎每个岗位都有）
    'python', 'java', '工程师', '开发', '软件', '程序员', '研发',
    '(', ')', '（', '）', '/', '、', '-', '－',
    '岗位', '招聘', '需求', '相关', '方向', '人员', '职位',
    # 级别/头衔
    '高级', '中级', '初级', '资深', '主管', '经理', '总监', '架构师', '专家', '顾问', '助理', '实习', '实习生',
    '中高级', '初中高', '中', '初', '高',
    # 学历/经验
    '本科', '大专', '硕士', '博士', '学历', '经验', '应届', '应届生', '毕业生', '不限',
    '本科及以上', '大专及以上', '统招', '全日制', '及以上',
    # 语言/非技能
    '粤语', '普通话', '英语', '日语', '韩语', '汉语', '双语',
    # 城市/地区（标题里偶尔出现）
    '北京', '上海', '广州', '深圳', '杭州', '南京', '苏州', '成都', '武汉', '西安', '重庆', '天津',
    '长沙', '合肥', '厦门', '福州', '郑州', '济南', '青岛', '大连', '沈阳', '无锡', '宁波', '东莞', '珠海', '佛山',
    '朝阳', '海淀', '浦东', '天河', '南山', '福田', '宝安', '高新', '高新园', '高新园区',
    # 国家/区域/地理泛词（不应成为方向标签）
    '中国', '国内', '全国', '全球', '国际', '海外', '国外', '境外', '境内', '本土', '大中华区',
    '华东', '华南', '华北', '华中', '西南', '西北', '东北', '长三角', '珠三角', '京津冀', '粤港澳',
    # 行业/业务泛词（不应单独作为技术方向）
    '金融', '财经', '财务', '经济', '制造', '生产', '产业', '资本', '赋能', '应用', '系统', 'evb',
    # 公司/业务泛词
    '公司', '科技', '集团', '有限', '企业', '信息', '软件', '互联网', '通信', '网络', '国企', '央企', '总部', '海外',
    # 具体公司名（不应成为方向标签）
    '阿里', '阿里巴巴', '腾讯', '百度', '字节', '字节跳动', '美团', '京东', '滴滴', '华为', '小米', '网易',
    '新浪', '搜狐', '快手', '拼多多', '小红书', '哔哩哔哩', 'bilibili', '大疆', '联想', '用友', '金蝶',
    '平安', '招商', '中信', '运通', '美国运通',
    # 金融细分行业（不应单独成为技术方向）
    '银行', '证券', '保险',
    # 招聘场景/非技能词
    '面试', '现场', '客户', '业务', '平台', '服务', '支持', '急聘', '诚聘', '诚招', '高薪', '招聘中',
    # 过于宽泛的技术泛词
    '技术', 'it',
    # 招聘无意义词
    '职位', '描述', '要求', '工作', '负责', '提供', '福利', '待遇', '薪资', '面议', '全职',
    '行业', '技术员', '出差', '办公', '环境', '交通', '便利', '团队', '氛围', '培训', '晋升',
    '优秀', '良好', '具备', '熟悉', '能力', '了解', '优先', '使用', '熟练', '基础', '学习',
    '具有', '专业', '扎实', '精通', '掌握', '善于', '至少', '能够', '独立', '完成', '编写',
    '进行', '实现', '参与', '推动', '提升', '优化', '维护', '管理', '理解', '深入', '热爱',
    '项目', '熟练掌握', '精神',
    # 福利/补贴
    '五险', '一金', '五险一金', '补贴', '带薪', '年假', '奖金', '年终', '绩效', '公积金', '商业', '保险',
    '定期', '体检', '专业培训', '员工', '旅游', '周末', '双休', '单休', '大小周', '做五休二', '做六休一',
    '餐补', '房补', '交通补贴', '通讯补贴', '节日', '生日', '弹性', '股票', '期权', '零食', '下午茶', '年度', '调薪',
    '六险', '二金', '三金', '包吃', '包住', '免费', '班车', '全勤', '全勤奖', '工龄', '工龄奖', '项目奖金', '季度',
    '有餐', '有餐补', '有住', '话补', '高温', '高温补贴', '无需', '经验不限', '无需经验', '接受',
    '节假日', '法定', '假期', '劳动', '社保', '合同', '签订', '医疗', '养老', '失业', '工伤', '生育', '缴纳',
    '住房', '住房补贴', '午餐', '晚餐', '完善', '丰厚', '保障', '按照国家', '规定', '享受', '标准', '国家',
    '出国', '机会', '外派', '驻外', '广告',
}
PUNCT_PATTERN = re.compile(r'^[\W_]+$')
CN_SINGLE_CHAR_PATTERN = re.compile(r'^[\u4e00-\u9fa5]$')


def tokenize(text):
    text_lower = text.lower()
    words = jieba.cut(text_lower)
    result = []
    for w in words:
        w = w.strip()
        if not w or w in STOPWORDS:
            continue
        if PUNCT_PATTERN.match(w) or CN_SINGLE_CHAR_PATTERN.match(w):
            continue
        result.append(w)
    return result


def choose_best_k(X, k_range=None):
    """遍历候选 k 值,选择轮廓系数最高的一个。

    当样本数过小或每次聚类都退化为单簇时,回退到 k=1(无有意义的聚类)。
    这两种情况在小范围采集运行中常出现,必须妥善处理以免程序崩溃。
    """
    n_samples = X.shape[0]
    max_k = min(11, n_samples - 1)

    if max_k < 2:
        return 1, {1: 0.0}

    if k_range is None:
        k_range = range(min(4, max_k), max_k + 1)

    scores = {}
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=42, n_init='auto')
        labels = km.fit_predict(X)
        n_distinct = len(set(labels))
        if n_distinct < 2 or n_distinct > n_samples - 1:
            continue
        scores[k] = silhouette_score(X, labels)

    if not scores:
        return 1, {1: 0.0}

    best_k = max(scores, key=scores.get)
    return best_k, scores


def run_clustering(k=None):
    posts_data = get_posts()  # [(id, post), ...]
    titles = [p[1] for p in posts_data]

    # 数据不足时返回空结果，避免 TF-IDF 向量化器崩溃
    if not titles:
        return {'k': 0, 'k_scores': None, 'clusters': [], 'total_jobs': 0}

    # 正常大小数据使用 min_df=2 过滤稀有词;当数据集很小时(例如非常新、非常窄范围的一次采集),
    # 放宽为 min_df=1,保证向量化器仍能输出有效结果。
    min_df = 2 if len(titles) >= 20 else 1
    vectorizer = TfidfVectorizer(tokenizer=tokenize, min_df=min_df)
    X = vectorizer.fit_transform(titles)
    feature_names = vectorizer.get_feature_names_out()

    if k is None:
        k, k_scores = choose_best_k(X)
    else:
        k_scores = None

    km = KMeans(n_clusters=k, random_state=42, n_init='auto')
    labels = km.fit_predict(X)

    # 计算每个簇的薪资统计
    salary_stats = _cluster_salary_stats(posts_data, labels, k)

    # 判别性命名(c-TF-IDF 思路): 每簇平均 TF-IDF 向量减去全局平均,
    # 取"该簇显著高于其他簇"的词做名字 —— 直接用簇中心 top 词容易选到
    # 各簇都有的通用词(如"开发"),区分度不足。
    global_mean = np.asarray(X.mean(axis=0)).ravel()

    result = []
    for i in range(k):
        idx = np.where(labels == i)[0]
        count = len(idx)
        center = km.cluster_centers_[i]
        top_idx = center.argsort()[-5:][::-1]
        top_keywords = [feature_names[j] for j in top_idx]

        cluster_mean = np.asarray(X[idx].mean(axis=0)).ravel()
        distinct = cluster_mean - global_mean
        distinct_order = distinct.argsort()[::-1]
        discriminative = [
            feature_names[j] for j in distinct_order[:6] if distinct[j] > 0
        ][:3]
        if not discriminative:
            # 该簇与全局几乎无差异时退回簇中心 top 词
            discriminative = top_keywords[:3]
        auto_label = '·'.join(discriminative)

        result.append({
            'cluster_id': i,
            'auto_label': auto_label,
            'count': int(count),
            'top_keywords': top_keywords,
            'job_ids': [int(posts_data[j][0]) for j in idx],
            **salary_stats[i],
        })
    result.sort(key=lambda x: -x['count'])
    total_jobs = len(titles)
    return {'k': k, 'k_scores': k_scores, 'clusters': result,
            'total_jobs': total_jobs,
            'label_version': 2}  # 2 = c-TF-IDF 判别性命名;旧缓存据此自动重算


if __name__ == '__main__':
    output = run_clustering()
    print(f"自动选择的聚类数 k = {output['k']}")
    if output['k_scores']:
        print('每个候选 k 的轮廓系数:')
        for k, score in output['k_scores'].items():
            print(f'  k={k}: {score:.3f}')
    print('\n聚类结果(按规模从大到小):')
    for c in output['clusters']:
        print(f"  聚类 {c['cluster_id']} [标签: {c['auto_label']}] "
              f"数量={c['count']} 关键词={c['top_keywords']}")
