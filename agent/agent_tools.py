"""
暴露给 Agent 的工具函数。每个函数都通过 TOOLS 注册表按名称被调用。

约束:
* 仅查询本地 SQLite data 表或缓存模型,不碰网络。
* 返回 JSON 友好的数据(列表 / 字典 / 数字 / 字符串)。
* 不得编造数据库中不存在的数值。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import re
import json
import logging
from collections import Counter
import config
from analysis.jobtitle import classify
from analysis.xueli import xuelifun
from analysis.jinyan import jinyanfun
from analysis.region import extract_city
from data.exper_parser import EXPER_ORDER, normalize_exper
from modeling.salary_predict import lookup_salary_range as _lookup_salary_range

_logger = logging.getLogger('job_analysis.agent_tools')


# 已废弃: set_model_result (薪资预测已替换为纯 DB 查询)

# 常见技术技能关键词(用于 extract_skills + skill_demand_analysis)。
# 英文词保留 \b 词边界防误切；中文词不能套 \b——中文汉字本身就是 \w，
# "熟悉机器学习"这类紧邻写法在 \b 下永远匹配不到。
_SKILL_PATTERNS = re.compile(
    r'\b(?:Python|Java|JavaScript|TypeScript|Go|Rust|C\+\+|C#|PHP|Ruby|Swift|Kotlin|'
    r'SQL|MySQL|PostgreSQL|MongoDB|Redis|Elasticsearch|Oracle|Docker|Kubernetes|K8s|'
    r'Linux|AWS|Azure|GCP|Git|Jenkins|CI/CD|DevOps|Ansible|Terraform|Nginx|Apache|'
    r'React|Vue|Angular|Node\.js|Django|Flask|Spring|Spring\s*Boot|FastAPI|Express|'
    r'TensorFlow|PyTorch|Scikit-learn|Pandas|NumPy|Spark|Hadoop|Kafka|RabbitMQ|'
    r'GraphQL|REST|gRPC|Webpack|Vite|CSS|HTML|Sass|Tailwind|Bootstrap)\b'
    r'|机器学习|深度学习|自然语言处理|计算机视觉|数据分析|数据挖掘|大数据'
    r'|微服务|分布式|高并发|系统设计|架构设计',
    re.IGNORECASE
)


def extract_skills(text: str) -> list:
    """从文本中提取技术技能关键词,去重后返回列表。"""
    if not text:
        return []
    seen = set()
    result = []
    for m in _SKILL_PATTERNS.finditer(text):
        skill = m.group().strip()
        if skill.lower() not in seen:
            seen.add(skill.lower())
            result.append(skill)
    return result


def edu_overview() -> list:
    """所有职位的学历分布统计。"""
    return [{'edu': e, 'count': c} for e, c in xuelifun()]


def exper_overview() -> list:
    """所有职位的工作经验要求分布统计。"""
    return [{'exper': e, 'count': c} for e, c in jinyanfun()]


def predict_salary(city: str, category: str, edu: str = '不限', exper: str = '经验不限') -> dict:
    """基于城市 + 职位类别 + 学历 + 经验,从数据库真实岗位查询薪资参考范围(非模型预测)。"""
    lookup = _lookup_salary_range(city, category, edu, exper)
    if 'message' in lookup:
        return {
            'city': city, 'category': category,
            'edu': edu, 'exper': exper,
            'lookup_count': lookup['count'],
            'note': lookup['message'],
        }
    return {
        'city': city, 'category': category,
        'edu': edu, 'exper': exper,
        'lookup_count': lookup['count'],
        'salary_median_k': lookup['median'],
        'salary_mean_k': lookup['mean'],
        'salary_range_k': f"{lookup['min']}-{lookup['max']}",
        'salary_p25_k': lookup['p25'],
        'salary_p75_k': lookup['p75'],
        'note': f"基于数据库中 {lookup['count']} 个匹配岗位的真实薪资统计(中位数 {lookup['median']}K)。数据来源可靠,非模型预测。",
    }


# ———— 学历排序表（经验档位统一走 data/exper_parser 的 EXPER_ORDER，不再另立口径） ————
_EDU_RANK = {'高中': 1, '中专': 2, '大专': 3, '本科': 4, '硕士': 5, '博士': 6}

# ———— 1-5 分制阈值标签 (career-ops-cn) ————
_SCORE_THRESHOLDS = [
    (4.5, '强烈推荐', 'strong', '#F59E0B'),
    (4.0, '推荐', 'good', '#38A169'),
    (3.5, '可考虑', 'fair', '#718096'),
    (3.0, '勉强匹配', 'weak', '#A0AEC0'),
    (0.0, '不建议', 'poor', '#CBD5E0'),
]


def _normalize_score(raw: float, max_raw: float) -> tuple:
    """将原始分按实际数据最高分归一化为 1-5 分 + 推荐标签。

    使用实际最高分做基准 (而非理论满分 100)，确保分布合理。

    Returns:
        (normalized_score: float, label: str, label_class: str, label_color: str)
    """
    if max_raw <= 0:
        return 1.0, '不建议', 'poor', '#CBD5E0'
    norm = round(1 + (raw / max_raw) * 4, 1)
    norm = max(1.0, min(5.0, norm))
    for threshold, label, cls, color in _SCORE_THRESHOLDS:
        if norm >= threshold:
            return norm, label, cls, color
    return norm, '不建议', 'poor', '#CBD5E0'


def _score_skill(user_skills: list, job_text: str, max_points: float = 35) -> tuple:
    """技能匹配评分 (ai-job-search 相关性权重思想)。

    每位用户技能:
      - 出现在标题/内容中: +1.0 基础分
      - 出现在标题中 (核心技能): +0.5 加成
      - 出现频次 >= 3: +0.5 加成
    每人技能最多贡献 2.0 分; 总分 = 累计 / (n*2) * max_points

    Returns:
        (score: float, matched: list, reasons: str)
    """
    if not user_skills:
        return max_points * 0.3, [], '未提供技能'
    total_earned = 0.0
    matched = []
    title_lower = job_text.split('\n')[0].lower() if job_text else ''
    for skill in user_skills:
        skill_lower = skill.lower()
        earned = 0.0
        if skill_lower in job_text.lower():
            earned += 1.0
            matched.append(skill)
            if skill_lower in title_lower:
                earned += 0.5
            if job_text.lower().count(skill_lower) >= 3:
                earned += 0.5
        total_earned += min(earned, 2.0)
    max_possible = len(user_skills) * 2.0
    score = (total_earned / max_possible) * max_points if max_possible > 0 else 0
    if matched:
        reason = f"技能命中 {len(matched)}/{len(user_skills)}: {', '.join(matched)}"
    else:
        reason = f"技能未命中 ({len(user_skills)}项)"
    return round(score, 1), matched, reason


def _score_city(city: str, addr: str, max_points: float = 15) -> tuple:
    """城市匹配评分。精确匹配得满分，部分匹配得半。

    Returns:
        (score: float, reason: str)
    """
    if not city:
        return max_points * 0.5, '未限定城市'
    if not addr:
        return 0, '地址未知'
    if city in addr:
        return max_points, f'城市匹配: {city}'
    # 模糊匹配: 检查城市首字是否在地址中
    if city[0] in addr:
        return max_points * 0.5, f'城市部分匹配: {city}'
    return 0, f'城市不匹配(期望{city})'


def _score_edu(user_edu: str, job_edu_text: str, max_points: float = 15) -> tuple:
    """学历匹配评分。

    Returns:
        (score: float, reason: str)
    """
    if not user_edu:
        return max_points * 0.5, '未限定学历'
    if not job_edu_text:
        return max_points * 0.6, '岗位未设学历要求'
    edu_rank = _EDU_RANK
    user_rank = edu_rank.get(user_edu, 0)
    job_rank = 0
    for key, rank in edu_rank.items():
        if key in job_edu_text:
            job_rank = max(job_rank, rank)
    if user_edu in job_edu_text:
        return max_points, '学历完全匹配'
    if user_rank >= job_rank > 0:
        return max_points * 0.7, f'学历达标: 要求{job_edu_text}, 你是{user_edu}'
    if job_rank > user_rank > 0:
        return max_points * 0.3, f'学历不达标: 要求{job_edu_text}, 你是{user_edu}'
    return max_points * 0.5, '学历匹配度一般'


def _score_exper(user_exper: str, job_exper_text: str, max_points: float = 15) -> tuple:
    """经验匹配评分（两侧都归一到 data/exper_parser 的 5 个有序档位再比较，
    「3年及以上」「1-3年经验」等写法与下拉选项在同一口径下可比）。

    Returns:
        (score: float, reason: str)
    """
    if not user_exper:
        return max_points * 0.5, '未限定经验'
    if not job_exper_text or job_exper_text == '经验不限':
        return max_points, '岗位经验不限'
    user_level = EXPER_ORDER.get(normalize_exper(user_exper), 0)
    job_level = EXPER_ORDER.get(normalize_exper(job_exper_text), 0)
    if user_level == job_level:
        return max_points, f'经验匹配: {job_exper_text}'
    if user_level > job_level:
        return max_points * 0.7, f'经验达标: 要求{job_exper_text}, 你有{user_exper}'
    return max_points * 0.35, f'经验不足: 要求{job_exper_text}, 你有{user_exper}'


def _score_salary(smin: float, smax: float, median_all: float, max_points: float = 10) -> tuple:
    """薪资水平评分 (百分位)。

    Returns:
        (score: float, reason: str)
    """
    avg_sal = round((smin + smax) / 2, 1) if (smin or smax) else 0
    if avg_sal <= 0 or median_all <= 0:
        return max_points * 0.3, '薪资数据不完整'
    percentile = avg_sal / median_all
    score = min(percentile * max_points * 0.5, max_points)
    if percentile >= 1.5:
        return round(score, 1), f'薪资领先(>中位数50%)'
    if percentile >= 1.2:
        return round(score, 1), '薪资高于中位数'
    if percentile >= 0.8:
        return round(score, 1), '薪资处于中位水平'
    return round(score * 0.6, 1), '薪资偏低'


def _score_authenticity(post: str, smin: float, smax: float,
                        edu_text: str, content: str, max_points: float = 10) -> tuple:
    """岗位真实性检测 (career-ops-cn Block G — 幽灵岗检测)。

    JD 内容完整度、标题合理性、薪资/学历是否填写。

    Returns:
        (score: float, reason: str)
    """
    points = 0.0
    flags = []
    # LD 描述丰富度
    content_len = len(content or '')
    if content_len >= 300:
        points += 4
        flags.append('JD详实')
    elif content_len >= 100:
        points += 2.5
        flags.append('JD较简')
    else:
        points += 1
        flags.append('JD过短')
    # 薪资完整
    if smin > 0 and smax > 0 and smax >= smin:
        points += 3
    elif smin > 0 or smax > 0:
        points += 1.5
        flags.append('薪资不完整')
    else:
        flags.append('未标注薪资')
    # 标题合理性
    if 4 < len(post) < 60:
        points += 2
    else:
        points += 0.5
    # 学历要求是否明确
    if edu_text and edu_text != '不限':
        points += 1
    return round(min(points, max_points), 1), ' · '.join(flags) if flags else '信息完整'


def _gap_analysis(user_skills: list, job_text: str, matched: list) -> list:
    """Gap 分析: 提取 JD 中用户缺失的高频技能。

    Args:
        user_skills: 用户已填技能(小写)
        matched: 已命中的技能
        job_text: 岗位文本

    Returns:
        list: 建议补充的技能 (最多 5 项)
    """
    matched_lower = {m.lower() for m in matched}
    jd_skills = extract_skills(job_text)
    if not jd_skills:
        return []
    freq = Counter(s.lower() for s in jd_skills)
    missing = [(s, c) for s, c in freq.most_common()
               if s not in matched_lower and s not in {u.lower() for u in user_skills}]
    return [s for s, _ in missing[:5]]


def match_jobs(skills: str = '', city: str = '', edu: str = '', exper: str = '',
               target_job_ids: list = None) -> dict:
    """岗位匹配推荐 v2 — 参考 career-ops-cn / Job-Application-Agent / ai-job-search / JobSpy。

    六维度透明评分 (权重固定, 规则驱动, 不依赖 LLM):
      技能匹配    35% — 相关性加权 (ai-job-search 思路)
      城市匹配    15% — 精确/部分/不匹配三档
      学历匹配    15% — 等级达标/完全匹配/不达标
      经验匹配    15% — 等级达标/完全匹配/不达标
      薪资水平    10% — 中位数百分位
      岗位真实性  10% — JD完整度 + 标题合理性 + 薪资明确性 (career-ops-cn Block G)

    输出:
      - 1-5 分制 + 推荐标签 (强烈推荐/推荐/可考虑/勉强匹配/不建议)
      - 阈值分布摘要 (各档位数量)
      - 每个岗位含: 六维度分解 + Gap 缺失技能 + Critic 校验理由

    参数:
        skills: 技能，逗号/顿号分隔，如 'Python,Django,Docker'
        city:   期望城市，如 '北京'
        edu:    学历，如 '本科'、'大专'、'硕士'
        exper:  经验，如 '1年'、'3-5年'、'应届'
        target_job_ids: 可选, 仅在这些岗位 id 集合内做匹配评分 (默认 None=全表匹配)
    """
    user_skills = [s.strip().lower()
                   for s in skills.replace('、', ',').replace('，', ',').split(',')
                   if s.strip()] if skills else []

    db = _connect()
    cursor = db.cursor()
    if target_job_ids:
        ids = [int(x) for x in target_job_ids if str(x).isdecimal() and int(x) > 0]
        placeholders = ','.join('?' * len(ids)) if ids else 'NULL'
        cursor.execute(
            f"SELECT id, post, address, salary_min, salary_max, edu, exper, content, job_url "
            f"FROM data WHERE id IN ({placeholders})",
            ids,
        )
    else:
        cursor.execute(
            "SELECT id, post, address, salary_min, salary_max, edu, exper, content, job_url FROM data"
        )
    rows = cursor.fetchall()
    db.close()

    # 全局薪资中位数
    all_salaries = [(r[3] + r[4]) / 2 for r in rows if r[3] or r[4]]
    median_all = sorted(all_salaries)[len(all_salaries) // 2] if all_salaries else 0

    results = []
    for job_id, post, addr, smin, smax, edu_text, exper_text, content, job_url in rows:
        job_text = f"{post} {content or ''}"
        breakdown = {}
        reasons = []

        # 1) 技能匹配 (35%)
        skill_score, matched, skill_reason = _score_skill(user_skills, job_text, 35)
        breakdown['skill'] = skill_score
        reasons.append(skill_reason)

        # 2) 城市匹配 (15%)
        city_score, city_reason = _score_city(city, addr or '', 15)
        breakdown['city'] = city_score
        reasons.append(city_reason)

        # 3) 学历匹配 (15%)
        edu_score, edu_reason = _score_edu(edu, edu_text or '', 15)
        breakdown['edu'] = edu_score
        reasons.append(edu_reason)

        # 4) 经验匹配 (15%)
        exper_score, exper_reason = _score_exper(exper, exper_text or '', 15)
        breakdown['exper'] = exper_score
        reasons.append(exper_reason)

        # 5) 薪资水平 (10%)
        salary_score, salary_reason = _score_salary(smin, smax, median_all, 10)
        breakdown['salary'] = salary_score
        reasons.append(salary_reason)

        # 6) 岗位真实性 (10%)
        auth_score, auth_reason = _score_authenticity(post, smin, smax,
                                                       edu_text or '', content or '', 10)
        breakdown['authenticity'] = auth_score
        reasons.append(auth_reason)

        # 汇总
        raw_score = sum(breakdown.values())
        avg_sal = round((smin + smax) / 2, 1) if (smin or smax) else 0
        results.append({
            'id': job_id,
            'title': post,
            'city': addr.split('-')[0] if addr else '未知',
            'address': addr or '未知',
            'salary_k': avg_sal,
            'salary_min_k': smin or 0,
            'salary_max_k': smax or 0,
            'edu': edu_text or '不限',
            'exper': exper_text or '经验不限',
            'job_url': job_url or '',
            'score_raw': round(raw_score, 1),         # 原始分
            'breakdown': breakdown,
            '_matched': matched,
            '_job_text': job_text,
            '_reasons': reasons,
            '_gap_skills': _gap_analysis(user_skills, job_text, matched),
        })

    # 取实际最高原始分做归一化基准
    all_raw = [r['score_raw'] for r in results]
    max_raw = max(all_raw) if all_raw else 1

    # 归一化 + 阈值标签 + Critic 校验
    for r in results:
        norm_score, label, label_class, label_color = _normalize_score(r['score_raw'], max_raw)
        verified_reasons = _critic_verify(r.pop('_reasons'), r.pop('_job_text'), r['breakdown'])
        r['score'] = norm_score
        r['label'] = label
        r['label_class'] = label_class
        r['label_color'] = label_color
        r['reasons'] = verified_reasons
        r['gap_skills'] = r.pop('_gap_skills')
        r.pop('_matched', None)

    results.sort(key=lambda x: -x['score_raw'])

    # 阈值分布统计
    threshold_counts = {'strong': 0, 'good': 0, 'fair': 0, 'weak': 0, 'poor': 0}
    valid_results = [r for r in results if r['score_raw'] > 10]
    for r in valid_results:
        cls = r['label_class']
        if cls in threshold_counts:
            threshold_counts[cls] += 1

    top = valid_results[:10]

    return {
        'skills': user_skills,
        'city': city,
        'edu': edu,
        'exper': exper,
        'total_matched': len(valid_results),
        'thresholds': threshold_counts,
        'top_matches': top,
    }


def _critic_verify(reasons: list, job_text: str, breakdown: dict) -> list:
    """Critic 校验 (Job-Application-Agent): 验证每条 reason 在数据中有依据。

    - 技能命中: 校验技能关键词确实在 job_text 中
    - 城市匹配/不匹配: 通过
    - 学历/经验匹配: 通过 (基于 breakdown 计算)
    - JD详实/过短: 校验 content 长度
    """
    verified = []
    for r in reasons:
        if '技能' in r and ('命中' in r or '未命中' in r):
            # 数据已在 _score_skill 中校验，直接保留
            verified.append(r)
        elif '薪资' in r:
            if breakdown.get('salary', 0) > 0:
                verified.append(r)
        elif 'JD' in r:
            if len(job_text or '') >= 100:
                verified.append(r)
            else:
                verified.append('JD内容较少')
        elif '信息完整' in r:
            verified.append(r)
        else:
            # 其他 reason 直接通过
            verified.append(r)
    return verified


def _connect():
    return sqlite3.connect(config.DB_PATH)


def query_jobs(keyword: str) -> dict:
    """按职位名称或JD描述关键词模糊搜索(标题+描述联合搜索),返回数量、薪资统计、城市排行。"""
    db = _connect()
    cursor = db.cursor()
    like_pattern = f'%{keyword}%'
    cursor.execute(
        "SELECT post, address, salary_min, salary_max FROM data "
        "WHERE post LIKE ? OR (content IS NOT NULL AND content LIKE ?)",
        (like_pattern, like_pattern)
    )
    rows = cursor.fetchall()
    db.close()

    if not rows:
        return {'keyword': keyword, 'count': 0, 'message': '未找到匹配职位'}

    salaries = [(r[2] + r[3]) / 2 for r in rows if r[2] or r[3]]
    cities = {}
    for r in rows:
        city = r[1].split('-')[0] if r[1] else '未知'
        cities[city] = cities.get(city, 0) + 1
    top_cities = sorted(cities.items(), key=lambda x: -x[1])[:3]

    return {
        'keyword': keyword,
        'count': len(rows),
        'avg_salary_k': round(sum(salaries) / len(salaries), 1) if salaries else 0,
        'min_salary_k': round(min(salaries), 1) if salaries else 0,
        'max_salary_k': round(max(salaries), 1) if salaries else 0,
        'top_cities': top_cities,
    }


def skill_demand_analysis(skill: str) -> dict:
    """统计某个技能/技术在岗位JD中的市场需求情况。

    搜索所有岗位的 content(JD正文)字段,统计包含该技能的岗位数量、
    薪资中位数、城市分布和常见共现技能。

    参数:
        skill: 技能关键词,如 'Docker'、'Python'、'Kubernetes'、'Spring Boot'。
    """
    if not skill or not skill.strip():
        return {'skill': skill, 'count': 0, 'message': '请提供有效的技能关键词'}

    db = _connect()
    cursor = db.cursor()
    like_pattern = f'%{skill}%'
    cursor.execute(
        "SELECT post, address, salary_min, salary_max, content FROM data "
        "WHERE content IS NOT NULL AND content LIKE ?",
        (like_pattern,)
    )
    rows = cursor.fetchall()
    db.close()

    if not rows:
        return {'skill': skill, 'count': 0, 'message': f'未找到包含"{skill}"的岗位JD'}

    salaries = sorted([(r[2] + r[3]) / 2
                       for r in rows if r[2] or r[3]])
    n = len(salaries)
    median = salaries[n // 2] if n > 0 else 0

    cities = Counter()
    for r in rows:
        city = r[1].split('-')[0] if r[1] else '未知'
        cities[city] += 1

    # 提取共现高频技能
    co_freq = Counter()
    for r in rows:
        if not r[4]:
            continue
        for s in extract_skills(r[4]):
            if s.lower() != skill.lower():
                co_freq[s] += 1
    top_co = [(s, c) for s, c in co_freq.most_common(8) if c >= 2]

    return {
        'skill': skill,
        'count': len(rows),
        'median_salary_k': round(median, 1),
        'avg_salary_k': round(sum(salaries) / len(salaries), 1) if salaries else 0,
        'top_cities': cities.most_common(5),
        'top_co_skills': top_co,
        'sample_jobs': [r[0] for r in rows[:5]],
    }


def category_overview() -> list:
    """按规则分类后的职位类别分布与各类别平均薪资。"""
    db = _connect()
    cursor = db.cursor()
    cursor.execute("SELECT post, salary_min, salary_max FROM data")
    rows = cursor.fetchall()
    db.close()

    cat_data = {}
    for post, smin, smax in rows:
        cat = classify(post)
        cat_data.setdefault(cat, []).append((smin + smax) / 2 if (smin or smax) else 0)

    result = []
    for cat, salaries in sorted(cat_data.items(), key=lambda x: -len(x[1])):
        valid = [s for s in salaries if s > 0]
        avg = round(sum(valid) / len(valid), 1) if valid else 0
        result.append({'category': cat, 'count': len(salaries), 'avg_salary_k': avg})
    return result


def city_overview() -> list:
    """各城市职位数量与平均薪资。"""
    db = _connect()
    cursor = db.cursor()
    cursor.execute("SELECT address, salary_min, salary_max FROM data")
    rows = cursor.fetchall()
    db.close()

    city_data = {}
    for addr, smin, smax in rows:
        city = extract_city(addr)
        city_data.setdefault(city, []).append((smin + smax) / 2 if (smin or smax) else 0)

    result = []
    for city, salaries in sorted(city_data.items(), key=lambda x: -len(x[1])):
        valid = [s for s in salaries if s > 0]
        avg = round(sum(valid) / len(valid), 1) if valid else 0
        result.append({'city': city, 'count': len(salaries), 'avg_salary_k': avg})
    return result


def compare_jobs(dim_type: str, a: str, b: str) -> dict:
    """并排对比两个城市或两个职位类别的招聘数据(含技能差异)。

    参数:
        dim_type: 'city' 按城市对比, 'category' 按类别对比。
        a, b: 要对比的两个值,如 a='北京' b='上海',或 a='后端开发' b='Web/前端'。
    """
    db = _connect()
    cursor = db.cursor()
    cursor.execute("SELECT post, address, salary_min, salary_max, edu, exper, content FROM data")
    rows = cursor.fetchall()
    db.close()

    def _side(val):
        acc = {'count': 0, 'salaries': [], 'edu': Counter(), 'exper': Counter(),
               'content_texts': []}
        for post, addr, smin, smax, edu, exper, content in rows:
            if dim_type == 'city':
                match = val in (addr or '')
            else:
                match = classify(post) == val
            if match:
                acc['count'] += 1
                if smin or smax:
                    acc['salaries'].append((smin + smax) / 2)
                if edu:
                    acc['edu'][edu] += 1
                if exper:
                    acc['exper'][exper] += 1
                if content:
                    acc['content_texts'].append(content)
        sal_list = acc['salaries']
        return {
            'value': val,
            'count': acc['count'],
            'avg_salary_k': round(sum(sal_list) / len(sal_list), 1) if sal_list else 0,
            'min_salary_k': round(min(sal_list), 1) if sal_list else 0,
            'max_salary_k': round(max(sal_list), 1) if sal_list else 0,
            'top_edu': acc['edu'].most_common(3),
            'top_exper': acc['exper'].most_common(3),
            '_content_texts': acc['content_texts'],
        }

    side_a = _side(a)
    side_b = _side(b)

    # ---- 技能差异分析 ----
    def _top_skills(content_texts, n=8):
        if not content_texts:
            return []
        skill_freq = Counter()
        for ct in content_texts:
            for s in extract_skills(ct):
                skill_freq[s] += 1
        return skill_freq.most_common(n)

    skills_a = _top_skills(side_a.get('_content_texts', []))
    skills_b = _top_skills(side_b.get('_content_texts', []))

    set_a = {s for s, _ in skills_a} if skills_a else set()
    set_b = {s for s, _ in skills_b} if skills_b else set()
    only_a = [(s, c) for s, c in skills_a if s not in set_b][:5]
    only_b = [(s, c) for s, c in skills_b if s not in set_a][:5]
    common = [(s, c) for s, c in skills_a if s in set_b][:5]

    side_a.pop('_content_texts', None)
    side_b.pop('_content_texts', None)

    result = {
        'compare_type': dim_type,
        'a': side_a,
        'b': side_b,
    }
    if only_a or only_b or common:
        result['skill_diff'] = {
            f'{a}_独有': only_a,
            f'{b}_独有': only_b,
            '共同高频': common,
        }
    return result



def _split_resume_analysis(text):
    """将单次 LLM 返回的「诊断报告 + 优化简历」两段文本拆分为 critique 与 optimized_resume。

    定位以「优化」开头的 Markdown 标题(## / #)作为分割点;若缺失则整段作为诊断。
    """
    if not text:
        return '', ''
    m = re.search(r'(?m)^\s*#{1,3}\s*优化', text)
    if m:
        critique = text[:m.start()].strip()
        optimized = text[m.start():].strip()
        return critique, optimized
    return text.strip(), ''


def review_resume(resume_text: str, target_city: str = '', target_category: str = '',
                  target_job_ids: list = None) -> dict:
    """简历审查与优化 — 参考 Job-Application-Agent 的简历-岗位对比模式。

    1. 从简历文本中结构化提取技能、学历、经验年限
    2. 在数据库中检索匹配岗位
    3. 逐岗位做 Gap 分析: 技能缺失、学历差距、经验差距
    4. 输出逐岗位的结构化优化建议

    Args:
        resume_text: 简历全文(自由文本)
        target_city: 可选,期望工作城市
        target_category: 可选,目标职位类别(如'后端开发')
        target_job_ids: 可选,仅针对这些具体岗位 id 的 JD 做 Gap 分析(优先级最高)

    Returns:
        dict: 含 extracted(提取画像), job_gaps(逐岗位Gap), summary(总建议)
    """
    if not resume_text or not resume_text.strip():
        return {'error': '请提供简历文本', 'extracted': {}, 'job_gaps': [], 'summary': ''}

    text_lower = resume_text.lower()

    # ---- 1. 结构化提取 ----
    extracted_skills = extract_skills(resume_text)
    # 学历提取
    extracted_edu = ''
    for e in ['博士', '硕士', '本科', '大专', '中专', '高中']:
        if e in resume_text:
            extracted_edu = e
            break
    # 经验年限提取
    extracted_exper = ''
    exper_patterns = [
        (r'(\d+)\s*年.*?(?:工作|开发|编程|项目).*?经验', lambda m: f'{m.group(1)}年'),
        (r'(\d+)\s*[-~至到]\s*(\d+)\s*年.*?(?:工作|开发).*?经验', lambda m: f'{m.group(1)}-{m.group(2)}年'),
        (r'(?:工作|开发)(?:经验|年限)[:：\s]*(\d+[-~至到]?\d*)\s*年', lambda m: f'{m.group(1)}年'),
        (r'应届|实习|在校', lambda m: '应届'),
    ]
    for pat, fmt in exper_patterns:
        m = re.search(pat, resume_text)
        if m:
            extracted_exper = fmt(m)
            break

    # ---- 2. 检索匹配岗位 ----
    db = _connect()
    cursor = db.cursor()
    query_sql = "SELECT id, post, address, salary_min, salary_max, edu, exper, content, job_url FROM data WHERE 1=1"
    params = []
    if target_job_ids:
        ids = [int(x) for x in target_job_ids if str(x).isdecimal() and int(x) > 0]
        placeholders = ','.join('?' * len(ids)) if ids else 'NULL'
        query_sql += f" AND id IN ({placeholders})"
        params.extend(ids)
    elif target_city:
        query_sql += " AND address LIKE ?"
        params.append(f'%{target_city}%')
    if target_category:
        cat_like = f'%{target_category}%'
        query_sql += " AND post LIKE ?"
        params.append(cat_like)
    cursor.execute(query_sql, params)
    rows = cursor.fetchall()
    db.close()

    if not rows:
        return {
            'extracted': {'skills': extracted_skills, 'edu': extracted_edu,
                          'exper': extracted_exper},
            'job_gaps': [],
            'summary': '数据库中暂无匹配岗位,请尝试放宽筛选条件。',
        }

    # ---- 3. 逐岗位 Gap 分析 ----
    gap_results = []
    for job_id, post, addr, smin, smax, edu_text, exper_text, content, job_url in rows:
        job_text = f"{post} {content or ''}"
        avg_sal = round((smin + smax) / 2, 1) if (smin or smax) else 0

        # 技能 Gap
        jd_skills = set(s.lower() for s in extract_skills(job_text))
        user_skills_set = set(s.lower() for s in extracted_skills)
        matched = jd_skills & user_skills_set
        missing = jd_skills - user_skills_set
        if jd_skills:
            skill_match_pct = round(len(matched) / len(jd_skills) * 100, 1)
        else:
            skill_match_pct = None  # JD 未提取到技能关键词，不做百分比比较

        # 学历 Gap
        edu_gap = ''
        if extracted_edu and edu_text:
            user_edu_rank = _EDU_RANK.get(extracted_edu, 0)
            job_edu_rank = 0
            for key, rank in _EDU_RANK.items():
                if key in edu_text:
                    job_edu_rank = max(job_edu_rank, rank)
            if user_edu_rank < job_edu_rank:
                edu_gap = f'学历偏低: 岗位要求{edu_text}, 你为{extracted_edu}'
            elif user_edu_rank == job_edu_rank:
                edu_gap = f'学历匹配: {edu_text}'
            else:
                edu_gap = f'学历超出: 岗位要求{edu_text}, 你为{extracted_edu}(加分项)'

        # 经验 Gap
        exper_gap = ''
        if extracted_exper and exper_text and exper_text != '经验不限':
            if extracted_exper in exper_text:
                exper_gap = f'经验匹配: {exper_text}'
            else:
                exper_gap = f'需确认: 岗位要求{exper_text}, 你为{extracted_exper}'

        # 仅保留有意义的 Gap 岗位
        if missing or edu_gap or exper_gap:
            # 生成优化建议
            suggestions = []
            if missing:
                # extract_skills 每篇 JD 已去重，缺失技能之间没有频率可排；
                # 取字母序前 5 保证输出稳定，不再伪装"按 JD 频率排优先级"
                priority_missing = sorted(missing)[:5]
                suggestions.append({
                    'type': 'skill',
                    'title': '建议补充技能',
                    'detail': f"该岗位JD要求但你简历未体现: {', '.join(priority_missing)}。"
                              f"建议在简历项目经历中自然融入这些关键词。",
                    'items': priority_missing,
                })
            if '学历偏低' in edu_gap:
                suggestions.append({
                    'type': 'edu',
                    'title': '学历有差距',
                    'detail': f"岗位要求{edu_text},可通过相关认证或突出项目经验来弥补。",
                    'items': [edu_text],
                })
            elif '学历超出' in edu_gap:
                suggestions.append({
                    'type': 'edu_plus',
                    'title': '学历优势',
                    'detail': f"你的{extracted_edu}学历高于岗位要求的{edu_text},这是加分项,简历中可突出学术/项目成果。",
                    'items': [extracted_edu],
                })

            gap_results.append({
                'id': job_id,
                'title': post,
                'city': addr.split('-')[0] if addr else '未知',
                'salary_k': avg_sal,
                'edu_require': edu_text or '不限',
                'exper_require': exper_text or '经验不限',
                'job_url': job_url or '',
                'skill_match_pct': skill_match_pct,
                'matched_skills': sorted(matched),
                'missing_skills': sorted(missing),
                'edu_gap': edu_gap,
                'exper_gap': exper_gap,
                'suggestions': suggestions,
            })

    # 按技能匹配百分比排序（None 视为 0）
    gap_results.sort(key=lambda x: (-(x['skill_match_pct'] if x['skill_match_pct'] is not None else 0), x['id']))

    gap_results = gap_results[:10]

    # ---- 4. 总建议 ----
    all_missing = Counter()
    for g in gap_results:
        for s in g['missing_skills']:
            all_missing[s] += 1
    top_missing = [s for s, _ in all_missing.most_common(8)]

    summary_parts = [f"从简历中提取到 {len(extracted_skills)} 项技能、学历 {extracted_edu or '未识别'}、经验 {extracted_exper or '未识别'}。"]
    if gap_results:
        summary_parts.append(f"对比了 {len(gap_results)} 个匹配岗位。")
    if top_missing:
        summary_parts.append(f"最常缺失的技能: {', '.join(top_missing[:6])}。")
    summary_parts.append('以上建议基于数据库中的真实岗位JD生成,建议优先补充高频缺失技能。')

    # ---- 5. 单次 LLM 深度分析 (诊断 + 优化合并为一次调用) ----
    # v3 变更: 移除 Critic→Optimizer 双 Agent 链式调用(原两次 API 调用),
    # 改为一次 API 调用同时产出「诊断报告」与「优化简历」两段。
    # 理由: 各功能间无链式依赖, 多轮调用仅增延迟与成本 (遵循"只调一次 API"决策)。
    critique = ''
    optimized_resume = ''
    ai_available = False
    provider = ''
    try:
        from agent.agent_core import call_llm_with_fallback

        deepseek_key = getattr(config, 'DEEPSEEK_API_KEY', '')
        qwen_key = getattr(config, 'QWEN_API_KEY', '')
        if not deepseek_key and not qwen_key:
            _logger.info('简历 AI 分析跳过: DEEPSEEK_API_KEY / QWEN_API_KEY 均未配置')
        else:
            # 仅取前 5 个最相关 Gap 岗位喂给 LLM, 控制 token 成本
            top_gaps = []
            for g in gap_results[:5]:
                top_gaps.append({
                    'title': g['title'],
                    'city': g['city'],
                    'salary_k': g['salary_k'],
                    'edu_require': g['edu_require'],
                    'exper_require': g['exper_require'],
                    'skill_match_pct': g['skill_match_pct'],
                    'matched_skills': g['matched_skills'],
                    'missing_skills': g['missing_skills'],
                    'edu_gap': g['edu_gap'],
                    'exper_gap': g['exper_gap'],
                })

            llm_context = {
                'extracted': {'skills': extracted_skills, 'edu': extracted_edu,
                              'exper': extracted_exper},
                'target_city': target_city,
                'target_category': target_category,
                'top_missing_skills': top_missing,
                'top_gaps': top_gaps,
            }

            messages = [
                {'role': 'system', 'content': (
                    '你是资深技术招聘顾问兼简历优化专家。基于候选人简历画像与真实岗位JD的 Gap 数据,'
                    '一次性输出两部分内容,使用以下两个固定 Markdown 标题严格分段:\n'
                    '## 简历诊断报告\n(核心优势 / 关键短板 / ATS 关键词缺口 / 优先级改进路线)\n'
                    '## 优化版简历\n(优化版个人摘要 / 技能板块重排 / 项目经历 STAR 改写 / 投递建议,可直接复制使用)\n'
                    '必须保留候选人真实经历,仅做关键词融入与表述强化,'
                    '不得虚构技能或经历。用中文 Markdown 输出。'
                )},
                {'role': 'user', 'content': (
                    f'# 简历提取画像\n{json.dumps(llm_context["extracted"], ensure_ascii=False, indent=2)}\n'
                    f'# 目标方向\n城市: {target_city or "不限"}  类别: {target_category or "不限"}\n'
                    f'# 高频缺失技能\n{", ".join(top_missing)}\n'
                    f'# 最相关 Gap 岗位(前5)\n{json.dumps(llm_context["top_gaps"], ensure_ascii=False, indent=2)}\n\n'
                    '请严格按系统指令的两个标题分段输出「简历诊断报告」与「优化版简历」。'
                )},
            ]
            analysis_text, provider = call_llm_with_fallback(messages, deepseek_key=deepseek_key)
            critique, optimized_resume = _split_resume_analysis(analysis_text)
            ai_available = True
    except Exception as e:
        _logger.warning('简历双 Agent 分析失败,降级为规则结果: %s', e)
        ai_available = False

    return {
        'extracted': {
            'skills': extracted_skills,
            'edu': extracted_edu,
            'exper': extracted_exper,
        },
        'job_gaps': gap_results,
        'summary': ' '.join(summary_parts),
        'top_missing_skills': top_missing,
        'critique': critique,
        'optimized_resume': optimized_resume,
        'ai_available': ai_available,
        'provider': provider,
    }


# 工具注册表,agent 循环通过该表解析工具名称并构建系统提示中的工具列表
TOOLS = {
    'query_jobs': {
        'func': query_jobs,
        'description': '按职位名称或JD描述关键词模糊搜索(标题+描述联合搜索),返回数量和薪资统计。参数: keyword (字符串),例如 query_jobs("Docker")。',
    },
    'skill_demand_analysis': {
        'func': skill_demand_analysis,
        'description': '统计某个技能/技术在岗位JD中的市场需求:出现频率、薪资中位数、城市分布、共现技能。参数: skill (字符串),例如 skill_demand_analysis("Docker")。',
    },
    'category_overview': {
        'func': category_overview,
        'description': '返回按规则分类后的职位类别分布与各类别平均薪资。无参数。',
    },
    'city_overview': {
        'func': city_overview,
        'description': '各城市职位数量与平均薪资。无参数。',
    },
    'edu_overview': {
        'func': edu_overview,
        'description': '返回所有职位的学历分布统计。无参数。',
    },
    'exper_overview': {
        'func': exper_overview,
        'description': '返回所有职位的工作经验要求分布统计。无参数。',
    },
    'predict_salary': {
        'func': predict_salary,
        'description': '基于数据库真实岗位统计，查询指定条件的薪资范围（中位数/均值/分位数等）。参数: city (字符串), category (字符串), edu (可选字符串), exper (可选字符串)。',
    },
    'compare_jobs': {
        'func': compare_jobs,
        'description': '并排对比两个城市或两类岗位的薪资、学历经验、技能差异(含各自独有技能和共同高频技能)。参数: dim_type ("city" 或 "category"), a (第一个值), b (第二个值)。例如 compare_jobs("city","北京","上海") 或 compare_jobs("category","后端开发","Web/前端")。',
    },
    'match_jobs': {
        'func': match_jobs,
        'description': '岗位匹配推荐:根据用户技能、期望城市、学历、经验,六维度综合评分(技能35%+城市15%+学历15%+经验15%+薪资10%+岗位真实性10%),输出1-5分制+推荐等级+Gap缺失技能。参数: skills (逗号分隔技能), city (可选), edu (可选学历), exper (可选经验), target_job_ids (可选,仅在这些岗位id内匹配,默认全表)。例如 match_jobs("Python,Django","北京","本科","1-3年")。',
    },
    'review_resume': {
        'func': review_resume,
        'description': '简历审查与优化:结构化提取简历中的技能、学历、经验,与数据库中匹配岗位做差距分析,输出逐岗位的技能缺失、学历/经验差距及简历优化建议。参数: resume_text (简历全文,字符串), target_city (可选,期望城市), target_category (可选,目标职位类别), target_job_ids (可选,仅针对这些具体岗位id的JD做Gap分析,优先级最高)。例如 review_resume("掌握Python和Django...","北京","后端开发")。',
    },

}


if __name__ == '__main__':
    print('=== match_jobs("Python,Django", "北京", "本科", "1-3年") ===')
    print(match_jobs('Python,Django', '北京', '本科', '1-3年'))
    print('\n=== query_jobs("爬虫") ===')
    print(query_jobs('爬虫'))
    print('\n=== skill_demand_analysis("Python") ===')
    print(skill_demand_analysis('Python'))
    print('\n=== category_overview() (前5项) ===')
    for item in category_overview()[:5]:
        print(item)
    print('\n=== city_overview() ===')
    for item in city_overview():
        print(item)
    print('\n=== predict_salary("北京", "爬虫/采集", "本科", "3-5年") ===')
    print(predict_salary('北京', '爬虫/采集', '本科', '3-5年'))
    print('\n=== edu_overview() ===')
    print(edu_overview())
    print('\n=== exper_overview() ===')
    print(exper_overview())
