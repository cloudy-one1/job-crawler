"""
技能供需热力图 — 哪些技能在哪些城市最值钱？

从 data 表的 keywords 列提取技能标签，按 (技能, 城市) 交叉统计平均薪资
和岗位数量，输出 ECharts 热力图所需的矩阵数据。

当 keywords 列缺失/为空时（兼容历史数据），自动 fallback 到从职位标题
+ JD 正文中正则提取技能关键词。

数据不足的格子（count < 3）不纳入热力图，避免偶然性误导。
"""
import sqlite3
import re
import config
import logging
import numpy as np

_logger = logging.getLogger('modeling.skill_heatmap')

# 同义词归一化（与 wordcloud_gen.py 共享逻辑）
TERM_NORMALIZE = {
    'css3': 'CSS', 'css': 'CSS',
    'vuejs': 'Vue', 'vue': 'Vue',
    'reactjs': 'React', 'react': 'React',
    'angularjs': 'Angular', 'angular': 'Angular',
    'nodejs': 'Node.js', 'node': 'Node.js',
    'k8s': 'Kubernetes',
    'machinelearning': '机器学习',
    'deeplearning': '深度学习',
    'nlp': 'NLP',
    '大模型': 'LLM', 'llm': 'LLM',
    'golang': 'Go', 'go': 'Go',
    'rustlang': 'Rust', 'rust': 'Rust',
    'cpp': 'C++', 'c++': 'C++',
    'csharp': 'C#', 'c#': 'C#',
    'typescript': 'TypeScript',
    'javascript': 'JavaScript', 'js': 'JavaScript',
    'html5': 'HTML5',
    'scikitlearn': 'Scikit-learn',
    'pytorch': 'PyTorch',
    'tensorflow': 'TensorFlow',
    'postgresql': 'PostgreSQL',
    'postgres': 'PostgreSQL',
    'mysql': 'MySQL',
    'mongodb': 'MongoDB',
    'redis': 'Redis',
    'linux': 'Linux',
    'docker': 'Docker',
    'kubernetes': 'Kubernetes',
    'git': 'Git',
    'jenkins': 'Jenkins',
    'nginx': 'Nginx',
    'springboot': 'Spring Boot',
    'spring': 'Spring',
    'django': 'Django',
    'flask': 'Flask',
    'fastapi': 'FastAPI',
    'java': 'Java',
    'sql': 'SQL',
    'oracle': 'Oracle',
    'python': 'Python',
    '微服务': '微服务',
    '分布式': '分布式',
    '高并发': '高并发',
}

# 非技术标签（应从技能热力图中排除）
NON_TECH_TAGS = {
    '五险一金', '周末双休', '带薪年假', '定期体检', '年终奖金',
    '绩效奖金', '餐饮补贴', '交通补贴', '通讯补贴', '节日福利',
    '全勤奖', '包吃', '包住', '弹性工作', '股票期权',
    '专业培训', '员工旅游', '补充公积金', '补充医疗保险',
    '高温补贴', '取暖补贴', '加班补贴', '住房补贴',
    '做五休二', '免费班车', '下午茶', '年度旅游', '出国机会',
    '生育保险', '工伤保险', '失业保险',
}


_SKILL_PATTERNS = re.compile(
    r'\b(?:Python|Java|JavaScript|TypeScript|Go|Rust|C\+\+|C#|PHP|Ruby|Swift|Kotlin|'
    r'SQL|MySQL|PostgreSQL|MongoDB|Redis|Elasticsearch|Oracle|Docker|Kubernetes|K8s|'
    r'Linux|AWS|Azure|GCP|Git|Jenkins|CI/CD|DevOps|Ansible|Terraform|Nginx|Apache|'
    r'React|Vue|Angular|Node\.js|Django|Flask|Spring|Spring\s*Boot|FastAPI|Express|'
    r'TensorFlow|PyTorch|Scikit-learn|Pandas|NumPy|Spark|Hadoop|Kafka|RabbitMQ|'
    r'GraphQL|REST|gRPC|Webpack|Vite|CSS|HTML|Sass|Tailwind|Bootstrap|'
    r'机器学习|深度学习|自然语言处理|计算机视觉|数据分析|数据挖掘|大数据|'
    r'微服务|分布式|高并发|系统设计|架构设计)\b',
    re.IGNORECASE
)


def _extract_skills_from_text(text):
    """从职位标题/JD 正文中正则提取技能关键词（fallback 用）。"""
    if not text:
        return []
    seen = set()
    skills = []
    for m in _SKILL_PATTERNS.finditer(text):
        sk = m.group().strip()
        sk_lower = sk.lower()
        if sk_lower not in seen:
            seen.add(sk_lower)
            normalized = TERM_NORMALIZE.get(sk_lower, sk)
            skills.append(normalized)
    return skills


def _extract_skills(row):
    """从 keywords 字段提取技能标签列表（标准化后）。

    若 keywords 为空，则 fallback 从 post + content 提取。
    兼容旧调用：row 也可以直接是 keywords 字符串。
    """
    if row is None:
        return []
    if isinstance(row, str):
        keywords_str = row or ''
        post = ''
        content = ''
    else:
        keywords_str = row['keywords'] or ''
        post = row['post'] or ''
        content = row['content'] or ''

    skills = []
    if keywords_str.strip():
        for tag in keywords_str.split():
            tag_lower = tag.strip().lower()
            if not tag_lower or tag_lower in NON_TECH_TAGS:
                continue
            normalized = TERM_NORMALIZE.get(tag_lower, tag.strip())
            skills.append(normalized)
    if not skills:
        # 兼容旧数据：从标题 + JD 中提取
        text = f"{post} {content}"
        skills = _extract_skills_from_text(text)
    return skills


def compute_skill_heatmap(min_count=3, top_skills=20):
    """计算技能×城市薪资热力图矩阵。

    Args:
        min_count: 每个 (技能, 城市) 格子最少岗位数，低于此数不纳入
        top_skills: 取热度最高的前 N 个技能

    Returns:
        dict: {
            skills: [str], cities: [str],
            salary_matrix: [[float]], count_matrix: [[int]],
            total_rows: int
        }
        或 {'error': str, 'total_rows': 0} — 数据不足
    """
    try:
        db = sqlite3.connect(config.DB_PATH)
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        cursor.execute(
            "SELECT address, salary_min, salary_max, keywords, post, content FROM data"
        )
        rows = cursor.fetchall()
        db.close()
    except sqlite3.Error as e:
        _logger.warning('compute_skill_heatmap 读取数据库失败: %s', e)
        return {'error': '数据库读取失败', 'total_rows': 0}

    if not rows:
        return {'error': '数据库中没有岗位数据，请先采集', 'total_rows': 0}

    # 聚合：(skill, city) → [salaries]
    skill_city_salaries = {}
    for row in rows:
        addr = row['address'] or ''
        city = addr.split('-')[0].strip() if addr else '未知'
        if not city:
            continue
        smin = row['salary_min']
        smax = row['salary_max']
        if not smin or not smax or (smin + smax) <= 0:
            continue
        avg_sal = (smin + smax) / 2

        skills = _extract_skills(row)
        for sk in skills:
            key = (sk, city)
            if key not in skill_city_salaries:
                skill_city_salaries[key] = []
            skill_city_salaries[key].append(avg_sal)

    if not skill_city_salaries:
        return {'error': '未提取到有效技能标签', 'total_rows': len(rows)}

    # 筛选：格子 count >= min_count
    valid_cells = {}
    for (sk, city), salaries in skill_city_salaries.items():
        if len(salaries) >= min_count:
            valid_cells[(sk, city)] = salaries

    if not valid_cells:
        return {
            'error': f'没有满足 ≥{min_count} 个岗位的技能-城市组合',
            'total_rows': len(rows),
        }

    # 提取 top_skills 和 top_cities
    skill_total = {}
    city_total = {}
    for (sk, city), salaries in valid_cells.items():
        skill_total[sk] = skill_total.get(sk, 0) + len(salaries)
        city_total[city] = city_total.get(city, 0) + len(salaries)

    skills = sorted(skill_total, key=skill_total.get, reverse=True)[:top_skills]
    cities = sorted(city_total, key=city_total.get, reverse=True)[:10]

    # 构建矩阵
    salary_matrix = []
    count_matrix = []
    for sk in skills:
        s_row = []
        c_row = []
        for ct in cities:
            salaries = valid_cells.get((sk, ct), [])
            if salaries:
                s_row.append(round(float(np.mean(salaries)), 1))
                c_row.append(len(salaries))
            else:
                s_row.append(None)
                c_row.append(0)
        salary_matrix.append(s_row)
        count_matrix.append(c_row)

    return {
        'skills': skills,
        'cities': cities,
        'salary_matrix': salary_matrix,
        'count_matrix': count_matrix,
        'total_rows': len(rows),
    }


if __name__ == '__main__':
    import json
    result = compute_skill_heatmap()
    print(json.dumps(result, ensure_ascii=False, indent=2))
