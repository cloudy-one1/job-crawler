"""
词云生成模块 — 从 51job 职位标签（keywords / jobTags）中统计高频热词。

- 只从 keywords（51job jobTags）提取，这是平台官方标注的结构化标签（技能/福利/经验/学历等），已按空格分隔为完整标签
- 严格按 keywords 原样统计（高频热词板块，不局限于技能词），仅做同义词归一化（js->JavaScript 等）与最小清洗
- 不套用为职位描述(content)设计的 STOP_WORDS，避免误杀 java/c++/ai/计算机 等真实标签
- echarts-wordcloud 前端圆形布局渲染（maskImage 中国地图轮廓已由 ensure_china_mask 生成，但未启用）
- mask 图片自动从 GeoJSON 生成（首次运行时）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from collections import Counter

_logger = logging.getLogger('job_analysis')


# ========== 技术词同义词归一化 ==========
TERM_NORMALIZE = {
    'css3': 'CSS', 'css': 'CSS', 'scss': 'CSS', 'sass': 'CSS',
    'javascript': 'JavaScript', 'js': 'JavaScript', 'ecmascript': 'JavaScript',
    'typescript': 'TypeScript', 'ts': 'TypeScript',
    'vue': 'Vue', 'vuejs': 'Vue', 'vue2': 'Vue', 'vue3': 'Vue',
    'react': 'React', 'reactjs': 'React', 'react.js': 'React',
    'angular': 'Angular', 'angularjs': 'Angular',
    'html': 'HTML', 'html5': 'HTML',
    'web': 'Web', 'webpack': 'Webpack',
    'node': 'Node.js', 'nodejs': 'Node.js', 'node.js': 'Node.js',
    'python': 'Python', 'java': 'Java', 'golang': 'Go', 'go': 'Go',
    'c#': 'C#', 'c++': 'C++', 'c': 'C', 'rust': 'Rust',
    'php': 'PHP', 'ruby': 'Ruby', 'swift': 'Swift', 'kotlin': 'Kotlin',
    'spring': 'Spring', 'springboot': 'SpringBoot', 'springmvc': 'Spring MVC',
    'springcloud': 'SpringCloud', 'mybatis': 'MyBatis',
    'django': 'Django', 'flask': 'Flask', 'fastapi': 'FastAPI',
    'mysql': 'MySQL', 'postgresql': 'PostgreSQL', 'mongodb': 'MongoDB',
    'redis': 'Redis', 'elasticsearch': 'Elasticsearch',
    'oracle': 'Oracle', 'sqlite': 'SQLite',
    'docker': 'Docker', 'kubernetes': 'Kubernetes', 'k8s': 'Kubernetes',
    'jenkins': 'Jenkins', 'gitlab': 'GitLab', 'git': 'Git',
    'nginx': 'Nginx', 'tomcat': 'Tomcat', 'linux': 'Linux', 'unix': 'Unix',
    'aws': 'AWS', 'azure': 'Azure',
    'tensorflow': 'TensorFlow', 'pytorch': 'PyTorch',
    'opencv': 'OpenCV', 'pandas': 'Pandas', 'numpy': 'NumPy',
    'hadoop': 'Hadoop', 'spark': 'Spark', 'kafka': 'Kafka',
    'flink': 'Flink', 'hive': 'Hive',
    'tcp': 'TCP', 'http': 'HTTP', 'https': 'HTTPS', 'api': 'API',
    'restful': 'RESTful', 'rest': 'REST',
    'json': 'JSON', 'xml': 'XML', 'yaml': 'YAML',
    'mq': 'MQ', 'rabbitmq': 'RabbitMQ',
    'memcached': 'Memcached', 'memcache': 'Memcached',
    'maven': 'Maven', 'gradle': 'Gradle', 'npm': 'NPM', 'yarn': 'Yarn',
    'es6': 'ES6', 'es7': 'ES7',
    'scikit': 'scikit-learn', 'sklearn': 'scikit-learn',
    'matlab': 'MATLAB', 'labview': 'LabVIEW',
    'solidworks': 'SolidWorks', 'autocad': 'AutoCAD', 'cad': 'CAD',
    'plc': 'PLC', 'stm32': 'STM32', 'arm': 'ARM', 'fpga': 'FPGA',
    'ros': 'ROS', 'slam': 'SLAM',
    '大模型': 'LLM', 'llm': 'LLM', 'langchain': 'LangChain',
    'ai': 'AI', '人工智能': 'AI',
    'sql': 'SQL', 'it': 'IT',
    '安卓': 'Android', 'android': 'Android',
    'oa': 'OA', 'mes': 'MES', 'plm': 'PLM', 'erp': 'ERP',
    'jquery': 'jQuery', 'jqueryui': 'jQuery UI',
    'bootstrap': 'Bootstrap',
    'hibernate': 'Hibernate', 'struts': 'Struts',
    '微服务': '微服务', '微服务架构': '微服务',
    '全栈': '全栈',
    'devops': 'DevOps', 'cicd': 'CI/CD',
    'prometheus': 'Prometheus', 'grafana': 'Grafana',
    'vuex': 'Vuex', 'pinia': 'Pinia',
    'redux': 'Redux', 'mobx': 'Mobx',
    'sass': 'Sass',
    'less': 'Less',
    'webgl': 'WebGL',
    '小程序': '小程序',
    'uniapp': 'UniApp', 'uni-app': 'UniApp',
}


# 说明：曾在此维护过一套 STOP_WORDS/WHITELIST 停用词表，但本模块的统计
# 只认 51job 官方 jobTags（结构化标签，无需分词清洗），两表从未被引用，已删除。
# 若未来要统计职位描述长文本，再从 git 历史找回该表。


def _connect_db():
    """获取数据库连接"""
    import sqlite3
    from config import DB_PATH
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def ensure_china_mask(mask_path=None):
    """
    确保中国地图 mask 图片存在。不存在时从 GeoJSON 自动生成。

    mask 格式: RGBA PNG，中国地图区域为白色+透明(alpha=0)，
    背景为黑色+不透明(alpha=255)。这样无论 wordcloud2 看 alpha
    还是亮度，文字都会填充在地图形状内。

    返回:
        str: mask 图片路径，失败返回 None
    """
    if mask_path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        mask_path = os.path.join(base, 'static', 'china_mask.png')

    if os.path.exists(mask_path):
        return mask_path

    geojson_path = os.path.join(os.path.dirname(mask_path), 'china_geo.json')
    if not os.path.exists(geojson_path):
        _logger.warning('china_geo.json 不存在，无法生成 mask')
        return None

    try:
        import json
        from PIL import Image, ImageDraw

        with open(geojson_path, 'r', encoding='utf-8') as f:
            geo = json.load(f)

        all_lons, all_lats = [], []
        polygons = []   # [(外环点列, [内环点列, ...]), ...]

        for feat in geo.get('features', []):
            geom = feat.get('geometry')
            if not geom:
                continue
            gtype = geom.get('type')
            coords = geom.get('coordinates', [])
            if not coords:
                continue

            if gtype == 'Polygon':
                # 第一个 ring 是外环，其余是内环(洞)
                poly_rings = []
                for ring in coords:
                    pts = [(p[0], p[1]) for p in ring if len(p) >= 2]
                    if len(pts) >= 3:
                        poly_rings.append(pts)
                if poly_rings:
                    polygons.append(poly_rings)
                    for ring in poly_rings:
                        for p in ring:
                            all_lons.append(p[0])
                            all_lats.append(p[1])

            elif gtype == 'MultiPolygon':
                for poly in coords:
                    poly_rings = []
                    for ring in poly:
                        pts = [(p[0], p[1]) for p in ring if len(p) >= 2]
                        if len(pts) >= 3:
                            poly_rings.append(pts)
                    if poly_rings:
                        polygons.append(poly_rings)
                        for ring in poly_rings:
                            for p in ring:
                                all_lons.append(p[0])
                                all_lats.append(p[1])

        if not polygons:
            _logger.warning('GeoJSON 中无有效多边形')
            return None

        # 画布尺寸 — 与中国地图经纬度比例匹配，避免拉伸
        W, H = 1200, 900
        pad = 20

        min_lon, max_lon = min(all_lons), max(all_lons)
        min_lat, max_lat = min(all_lats), max(all_lats)

        lon_scale = (W - 2 * pad) / (max_lon - min_lon)
        lat_scale = (H - 2 * pad) / (max_lat - min_lat)
        # 保持等比例，取较小缩放因子
        scale = min(lon_scale, lat_scale)

        # 计算居中偏移
        data_w = (max_lon - min_lon) * scale
        data_h = (max_lat - min_lat) * scale
        off_x = (W - data_w) / 2
        off_y = (H - data_h) / 2

        def _proj(lon, lat):
            x = off_x + (lon - min_lon) * scale
            # 纬度北高南低，图片 y 轴南高北低，需要翻转
            y = H - (off_y + (lat - min_lat) * scale)
            return x, y

        # 创建 RGBA 图片: 背景黑色+不透明, 地图白色+透明
        img = Image.new('RGBA', (W, H), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        for poly_rings in polygons:
            if not poly_rings:
                continue
            outer = [_proj(p[0], p[1]) for p in poly_rings[0]]
            # PIL ImageDraw.polygon 不支持带洞的多边形，
            # 这里只画外环（中国省界密集，忽略湖泊/洞对整体轮廓影响极小）
            if len(outer) >= 3:
                # 白色 + alpha=0（透明，表示可放文字的区域）
                draw.polygon(outer, fill=(255, 255, 255, 0))

        os.makedirs(os.path.dirname(mask_path), exist_ok=True)
        img.save(mask_path, 'PNG')
        _logger.info(f'中国地图 mask 已生成: {mask_path} ({W}x{H})')
        return mask_path

    except Exception as e:
        _logger.warning(f'生成中国地图 mask 失败: {e}')
        return None


def generate_wordcloud_data(top_n=60):
    """
    仅从数据库 keywords（51job jobTags）中提取标签，严格按原样统计高频热词。

    - keywords：来自 51job 的职位标签(jobTags)，直接按空格拆分，
      严格按平台原始标签统计（高频热词板块，含技能/福利/经验/学历等全部标签）。
    - 不使用 post 或 content，避免职位描述长文本分词噪音；jobTags 已是结构化标签。
    - 仅做同义词归一化（js->JavaScript, c++->C++, ai->AI ...），不再套用 STOP_WORDS，
      以免误杀 java/c++/ai/计算机 等真实出现的标签。

    返回:
        dict: {
            'success': bool,
            'total_jobs': int,
            'words': [(word, count), ...],
        }
    """
    try:
        db = _connect_db()
        cursor = db.cursor()
        cursor.execute("SELECT keywords FROM data")
        rows = cursor.fetchall()
        db.close()
    except Exception as e:
        _logger.error("词云数据查询失败: %s", e)
        return {'success': False, 'total_jobs': 0, 'words': [], 'error': str(e)}

    if not rows:
        return {'success': True, 'total_jobs': 0, 'words': []}

    # keywords 是 51job 平台官方标注的结构化标签(jobTags), 已按空格分隔为完整标签,
    # 不存在长文本分词噪音. 因此严格按原样统计(高频热词含技能/福利/经验/学历等全部标签),
    # 仅做同义词归一化(js->JavaScript, c++->C++, ai->AI ...)与最小清洗,
    # 不套用为职位描述(content)设计的 STOP_WORDS, 避免误杀 java/c++/ai 等真实标签.
    counter = Counter()
    for r in rows:
        kw_str = (r[0] or '').strip()
        if not kw_str:
            continue
        for w in kw_str.split():
            w = w.strip()
            if len(w) < 2 or w.isdigit():
                continue
            # 同义词归一化: js->JavaScript, java->Java, c++->C++, ai->AI 等
            w = TERM_NORMALIZE.get(w, w)
            counter[w] += 1

    top_words = counter.most_common(top_n)
    return {
        'success': True,
        'total_jobs': len(rows),
        'words': top_words,
    }



# ---------- 命令行测试 ----------
if __name__ == '__main__':
    ensure_china_mask()
    data = generate_wordcloud_data(top_n=30)
    print(f"总职位数: {data['total_jobs']}")
    print(f"Top 30 关键词:")
    for w, c in data['words']:
        print(f"  {w:20s} {c}")
