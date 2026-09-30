"""薪资洞察路由：/ml 页面、AI 解读、方向岗位明细、薪资档位预测接口。"""
import json as _json_mod
import sqlite3

from flask import Blueprint, abort, render_template, request

import config
from services.ai import _llm_analyze
from services import cache as cache_service

ml_bp = Blueprint('ml', __name__)


@ml_bp.route('/ml')
def ml_page():
    clustering = cache_service._get_clustering()
    total_jobs = clustering.get('total_jobs', 0) if clustering else 0

    # 懒加载四个新功能的数据
    heatmap = cache_service._get_skill_heatmap() if clustering else {'error': '请先采集数据'}
    similarity = cache_service._get_similarity() if clustering else {'error': '请先采集数据'}
    salary_curve = cache_service._get_salary_curve() if clustering else {'error': '请先采集数据'}
    edu_premium = cache_service._get_edu_premium() if clustering else {'error': '请先采集数据'}
    salary_classifier = cache_service._get_salary_classifier() if clustering else {'error': '请先采集数据'}

    # 提取城市词表供模板下拉菜单
    city_vocab = None
    if salary_classifier and not salary_classifier.get('error'):
        feat = salary_classifier.get('_features', {})
        city_vocab = feat.get('city_vocab', {})

    return render_template(
        'ml.html',
        clustering=clustering,
        total_jobs=total_jobs,
        heatmap=heatmap,
        similarity=similarity,
        salary_curve=salary_curve,
        edu_premium=edu_premium,
        salary_classifier=salary_classifier,
        city_vocab=city_vocab,
    )


@ml_bp.route('/ml/analyze', methods=['POST'])
def ml_analyze():
    """/ml 页面各图表 AI 解读接口（带 2 分钟服务端缓存，支持 fresh 参数强制刷新）。"""
    data = request.get_json(silent=True) or {}
    section = data.get('section', 'cluster')
    force_fresh = data.get('fresh', False)
    valid_sections = {'cluster', 'heatmap', 'similarity', 'salary_curve', 'edu_premium', 'salary_predict'}
    if section not in valid_sections:
        return _json_mod.dumps({'error': '无效的分析类型'}), 400

    # 获取各模块数据
    clustering = cache_service._get_clustering()
    if not clustering:
        return _json_mod.dumps({'error': '请先采集数据'}), 503

    heatmap = cache_service._get_skill_heatmap()
    similarity = cache_service._get_similarity()
    salary_curve = cache_service._get_salary_curve()
    edu_premium = cache_service._get_edu_premium()
    salary_classifier = cache_service._get_salary_classifier()

    # 组装各 section 的数据描述与指令
    section_map = {}

    # --- cluster ---
    clusters_desc_lines = []
    for c in clustering.get('clusters', []):
        kw_str = '、'.join(c.get('top_keywords', [])[:6])
        clusters_desc_lines.append(
            f"【{c.get('auto_label','')}】{c.get('count',0)}岗,"
            f"均薪{c.get('avg_salary',0):.1f}K,"
            f"区间{c.get('min_salary',0)}-{c.get('max_salary',0)}K,"
            f"关键词:{kw_str}"
        )
    cluster_desc = '\n'.join(clusters_desc_lines)
    section_map['cluster'] = (
        f'聚类将{clustering.get("total_jobs",0)}个岗位分为{clustering.get("k",0)}个方向:\n{cluster_desc}',
        '请分析各方向的岗位规模差异、薪资区间特征以及关键词反映的技术方向差异。'
        '指出规模最大/薪资最高的方向,并给 Python 开发者选择方向的具体建议。'
        '用中文,200-350字,直接说结论,不要问候语。'
    )

    # --- heatmap ---
    heatmap_desc = ''
    if heatmap and not heatmap.get('error'):
        skills = heatmap.get('skills', [])
        cities = heatmap.get('cities', [])
        matrix = heatmap.get('salary_matrix', [])
        top_skills = skills[:8]
        top_cities = cities[:6]
        highlights = []
        for i, skill in enumerate(top_skills):
            for j, city in enumerate(top_cities):
                if i < len(matrix) and j < len(matrix[i]) and matrix[i][j] is not None:
                    if matrix[i][j] >= 20:
                        highlights.append(f'{city}·{skill}={matrix[i][j]}K')
        heatmap_desc = f'技能: {",".join(top_skills)}; 城市: {",".join(top_cities)}; '
        heatmap_desc += f'高薪亮点: {"; ".join(highlights[:15]) if highlights else "各城市技能薪资大致在10-30K区间"}'
        section_map['heatmap'] = (
            heatmap_desc,
            '请根据热力图数据,分析不同技能在各城市的薪资冷热分布:哪些技能在多个城市都是高薪(通用高价值技能),'
            '哪些技能只在特定城市突出(地域性技能)。'
            '指出求职者如果想靠某项技能拿到更高薪资,应该重点关注哪些城市,以及哪些技能组合覆盖面最广。'
            '用中文,180-300字,直接说结论,不要问候语。'
        )

    # --- similarity ---
    sim_desc = ''
    if similarity and not similarity.get('error'):
        nodes = similarity.get('nodes', [])
        links = similarity.get('links', [])
        top_links = sorted(links, key=lambda x: x.get('value', 0), reverse=True)[:5]
        node_names = [n.get('name', '') for n in nodes[:10]]
        sim_desc = f'方向节点: {",".join(node_names)}; '
        sim_desc += '高相似度关系: ' + '; '.join([
            f'{lk.get("source","")}↔{lk.get("target","")}=({(lk.get("value",0)*100):.0f}%)'
            for lk in top_links
        ]) if top_links else '暂无显著相似关系'
        section_map['similarity'] = (
            sim_desc,
            '请分析岗位方向之间的可迁移性:哪些方向技能重叠度高、转方向容易;哪些方向相对独立。'
            '给出一条具体的转方向建议。'
            '用中文,180-300字,直接说结论,不要问候语。'
        )

    # --- salary_curve ---
    curve_desc = ''
    if salary_curve and not salary_curve.get('error'):
        overall = salary_curve.get('overall', [])
        curve_lines = [
            f'{d.get("exper","")}段 中位{d.get("median",0)}K 均值{d.get("mean",0)}K({d.get("count",0)}岗)'
            for d in overall
        ]
        curve_desc = '经验-薪资趋势: ' + '; '.join(curve_lines)
        if len(overall) >= 2:
            max_growth, max_idx = 0, 0
            for i in range(1, len(overall)):
                g = overall[i].get('median', 0) - overall[i-1].get('median', 0)
                if g > max_growth:
                    max_growth, max_idx = g, i
            # 所有段中位数都在下降时 max_idx 保持 0，此时不存在"跃升阶段"，跳过
            if max_growth > 0 and max_idx >= 1:
                curve_desc += f'\n薪资跃升最快阶段: {overall[max_idx-1].get("exper","")}→{overall[max_idx].get("exper","")}(+{max_growth}K)'
        section_map['salary_curve'] = (
            curve_desc,
            '请分析薪资随经验的成长规律:哪个阶段增幅最大、天花板在哪。'
            '给求职者关于经验积累与薪资预期的建议。'
            '用中文,180-300字,直接说结论,不要问候语。'
        )

    # --- edu_premium ---
    edu_desc = ''
    if edu_premium and not edu_premium.get('error'):
        overall_edu = edu_premium.get('overall', {})
        edu_lines = []
        for level in ['博士', '硕士', '本科', '大专', '高中']:
            if level in overall_edu:
                d = overall_edu[level]
                edu_lines.append(f'{level} 中位{d.get("median",0)}K 均值{d.get("avg_salary",0)}K({d.get("count",0)}岗)')
        edu_desc = '各学历整体薪资: ' + '; '.join(edu_lines)
        premiums = edu_premium.get('premiums', [])
        if premiums:
            top_p = []
            for p in premiums[:5]:
                for prem in p.get('premiums', [])[:3]:
                    top_p.append(
                        f'{p.get("city","")}-{p.get("category","")}: '
                        f'{prem.get("from","")}→{prem.get("to","")}溢价{prem.get("premium_pct",0)}%'
                    )
            edu_desc += '\n典型溢价案例: ' + '; '.join(top_p[:8])
        section_map['edu_premium'] = (
            edu_desc,
            '请分析学历对薪资的真实影响:高学历溢价是否显著、哪些方向学历溢价最高。'
            '给不同学历背景的求职者针对性建议。'
            '用中文,180-300字,直接说结论,不要问候语。'
        )

    # --- salary_predict ---
    sc_desc = ''
    if salary_classifier and not salary_classifier.get('error'):
        m = salary_classifier.get('metrics', {})
        sc_desc = (
            f'模型: {m.get("model_name","?")}, '
            f'准确率: {m.get("accuracy",0):.1%}, '
            f'Macro-F1: {m.get("macro_f1",0):.3f}, '
            f'训练样本: {m.get("n_samples",0)}个, '
            f'薪资档位: {m.get("n_classes",0)}个'
        )
        bands = salary_classifier.get('bands', [])
        if bands:
            sc_desc += '\n档位分布: ' + '; '.join(
                f'{b["label"]}={b["count"]}岗({b["pct"]}%)' for b in bands
            )
        top_feat = salary_classifier.get('feature_importances', [])[:5]
        if top_feat:
            sc_desc += '\nTop5 特征: ' + '; '.join(
                f'{f["name"]}({f["importance"]:.3f})' for f in top_feat
            )
        section_map['salary_predict'] = (
            sc_desc,
            '请解读薪资分类模型的结果:分析哪些特征对薪资档位影响最大,'
            '各薪资档位的分布特征,模型的可靠性(准确率/F1),'
            '并给出求职者关于城市/学历/经验/技能组合的薪资提升建议。'
            '用中文,180-300字,直接说结论,不要问候语。'
        )

    if section not in section_map:
        return _json_mod.dumps({'error': '数据不足,无法生成该分析'}), 503

    data_desc, instruction = section_map[section]
    system_prompt = ('你是招聘数据分析与建模解读助手。数据结论必须来自提供的真实采集数据;'
                     '可补充普适性建议但必须用**【普适性建议】**标注;数据不足时诚实说明;'
                     '不编造具体数字。输出纯文本中文。')
    return _llm_analyze(
        cache_service._ml_analysis_cache, cache_service._ML_CACHE_TTL, section,
        data_desc, instruction, system_prompt,
        intro='并建模后的', force_fresh=force_fresh,
    )


@ml_bp.route('/ml/cluster/<int:cluster_id>')
def cluster_jobs(cluster_id):
    """点击 /ml 页面的方向卡片后，展示该簇包含的所有岗位列表。"""
    clustering = cache_service._get_clustering()
    if not clustering:
        abort(404)

    cluster = None
    for c in clustering.get('clusters', []):
        if c.get('cluster_id') == cluster_id:
            cluster = c
            break
    if not cluster:
        abort(404)

    job_ids = cluster.get('job_ids', [])
    if not job_ids:
        rows = []
    else:
        db = sqlite3.connect(config.DB_PATH)
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        placeholders = ','.join('?' * len(job_ids))
        cursor.execute(
            f"SELECT id, post, company, address, salary_min, salary_max, dateT "
            f"FROM data WHERE id IN ({placeholders}) ORDER BY id DESC",
            job_ids,
        )

        rows = cursor.fetchall()
        db.close()

    return render_template(
        'cluster_jobs.html',
        cluster=cluster,
        rows=rows,
    )


@ml_bp.route('/ml/salary-predict', methods=['POST'])
def salary_predict_route():
    """薪资档位预测 AJAX 接口（受 CSRF 保护）。

    接收 JSON: {city, edu, exper, skills}
    返回 JSON: {predicted_band, probabilities, input}
    """
    data = request.get_json(silent=True) or {}
    city = (data.get('city') or '').strip()
    edu = (data.get('edu') or '').strip()
    exper = (data.get('exper') or '').strip()
    skills_raw = (data.get('skills') or '').strip()

    pkg = cache_service._get_salary_classifier()
    if pkg.get('error') or '_model' not in pkg:
        return _json_mod.dumps({'error': pkg.get('error', '模型尚未训练')}), 503

    from modeling.salary_classifier import predict_salary_band
    result = predict_salary_band(pkg, city=city, edu=edu, exper=exper,
                                  skills=skills_raw)
    if 'error' in result:
        return _json_mod.dumps({'error': result['error']}), 400

    return _json_mod.dumps(result, ensure_ascii=False), 200, {'Content-Type': 'application/json'}
