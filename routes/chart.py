"""图表分析路由：/chart 页面 + AI 解读接口。"""
from flask import Blueprint, render_template, request

from services.ai import _llm_analyze
from services import cache as cache_service

chart_bp = Blueprint('chart', __name__)

VALID_SECTIONS = {'city', 'salary', 'xueli', 'jinyan', 'wordcloud', 'cross'}


@chart_bp.route('/chart')
@chart_bp.route('/chart/<section>')
def chart(section='city'):
    if section not in VALID_SECTIONS:
        section = 'city'
    data = cache_service._compute_chart_data()
    return render_template('h.html', section=section, **data)


@chart_bp.route('/chart/analyze', methods=['POST'])
def chart_analyze():
    """DeepSeek 数据驱动图表分析接口（带 5 分钟服务端缓存）。
    首次调用后相同 section 的结果被缓存，后续请求直接返回，实现秒开。
    采集新数据后缓存由 /collect 主动清空。"""
    import json as _json
    data = request.get_json(silent=True) or {}
    section = data.get('section', 'city')
    if section not in VALID_SECTIONS:
        return _json.dumps({'error': '无效的分析类型'}), 400

    # 获取所有数据(复用图表数据缓存)
    try:
        chart_data = cache_service._compute_chart_data()
        xz_val = chart_data['xz']
        xl_val = chart_data['xl']
        jy_val = chart_data['jy']
        city_val = chart_data['city_data']
        cross_exper = chart_data['cross_exper']
        cross_edu = chart_data['cross_edu']
        wc_val = chart_data['wc_data']
    except Exception as e:
        cache_service._logger.warning('图表数据查询失败: %s', e)
        return _json.dumps({'error': '数据查询失败,请稍后重试'}), 500

    # 拼装面向 DeepSeek 的数据描述
    labels_xz = ['<5k', '5-8k', '8-11k', '11-14k', '14-17k', '17-20k', '20-23k', '23k+']
    total_jobs = sum(xz_val)
    salary_desc = '、'.join([f'{labels_xz[i]} {xz_val[i]}个' for i in range(len(xz_val))])
    city_desc_lines = [f'{c[0]} {c[1]}个' for c in city_val[:10]]
    xl_desc = '、'.join([f'{l[0]} {l[1]}个' for l in xl_val])
    jy_desc = '、'.join([f'{j[0]} {j[1]}个' for j in jy_val])
    wc_top_desc = '、'.join([f'{w[0]}({w[1]}次)' for w in (wc_val.get('words', []) or [])[:10]])

    exper_desc = ''
    if cross_exper and cross_exper.get('labels'):
        exper_desc = '、'.join([
            f'{cross_exper["labels"][i]} 平均{cross_exper["avg_salaries"][i]}k({cross_exper["counts"][i]}个)'
            for i in range(len(cross_exper['labels']))
        ])
    edu_desc = ''
    if cross_edu and cross_edu.get('labels'):
        edu_desc = '、'.join([
            f'{cross_edu["labels"][i]} 平均{cross_edu["avg_salaries"][i]}k({cross_edu["counts"][i]}个)'
            for i in range(len(cross_edu['labels']))
        ])

    section_map = {
        'city': (f'当前共有{total_jobs}个职位,分布在前10的城市为:{city_desc_lines}',
                 '请你只针对城市分布给出直观分析,指出岗位集中趋势、核心城市及求职建议。用中文,150-300字,直接说结论,不要问候语。'),
        'salary': (f'薪资分布(共{total_jobs}个有薪资的职位): {salary_desc}',
                   '请你只针对薪资分布给出直观分析,指出主力薪资区间、高薪与低薪占比、以及薪资结构特征。用中文,150-300字,直接说结论,不要问候语。'),
        'xueli': (f'学历分布: {xl_desc}',
                  '请你只针对学历要求分布给出直观分析,指出市场主流的学历门槛、各学历占比态势。用中文,150-300字,直接说结论,不要问候语。'),
        'jinyan': (f'经验要求分布: {jy_desc}',
                   '请你只针对经验要求分布给出直观分析,指出市场最需求的年资段。用中文,150-300字,直接说结论,不要问候语。'),
        'wordcloud': (f'岗位描述高频技能词TOP10: {wc_top_desc}',
                      '请你只针对这些高频技能词给出直观分析,指出当前市场对Python开发者的核心技能要求方向。用中文,150-300字,直接说结论,不要问候语。'),
        'cross': (f'交叉分析:\n1) 经验 vs 平均薪资: {exper_desc}\n2) 学历 vs 平均薪资: {edu_desc}',
                  '请分三段输出,每段加粗标题:\n'
                  '1) 【经验 vs 薪资 独立分析】只针对"薪资 vs 经验等级"图进行分析,指出各经验段的平均薪资趋势、职位数量分布特征、哪个经验段薪资溢价最高。\n'
                  '2) 【学历 vs 薪资 独立分析】只针对"薪资 vs 学历"图进行分析,指出各学历层次的平均薪资差异、学历溢价效应。\n'
                  '3) 【综合分析】对比两段分析,指出经验与学历对薪资的影响哪个更大,并给出求职者针对性的职业规划建议。\n'
                  '用中文,每段150-200字,直接说结论,不要问候语。'),
    }

    data_desc, instruction = section_map[section]
    system_prompt = ('你是招聘数据分析助手。数据结论必须来自提供的真实采集数据;'
                     '可补充普适性建议但必须用**【普适性建议】**标注;数据不足时诚实说明;'
                     '不编造具体数字。输出纯文本中文。')
    return _llm_analyze(
        cache_service._chart_analysis_cache, cache_service._CHART_CACHE_TTL, section,
        data_desc, instruction, system_prompt,
    )
