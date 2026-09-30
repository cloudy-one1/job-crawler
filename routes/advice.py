"""智能助手路由：/advice 四个 tab（Agent 问答 / 城市对比 / 岗位匹配 / 简历审查）及其子接口。"""
import json as _json_mod
import time
import uuid

from flask import Blueprint, redirect, render_template, request, session, url_for

import config
from extensions import csrf
from services.ai import _llm_analyze
from services.db import _raw_connect, _load_interested_jobs
from services import cache as cache_service

advice_bp = Blueprint('advice', __name__)


def _render_advice(active_tab, **overrides):
    """统一渲染 advice.html：从 session/服务端存储恢复四个 tab 的持久化状态，
    保证任意 tab 提交生成后，其余 tab 的内容依旧保留（修复「切换 tab 内容被重置」）。
    overrides 用于覆盖刚计算出的当前 tab 结果（POST 提交时使用）。
    """
    # 从 address 字段提取唯一城市列表（供城市对比下拉选择）
    _db = _raw_connect()
    try:
        _addr_rows = _db.execute(
            "SELECT DISTINCT address FROM data WHERE address IS NOT NULL AND address != ''"
        ).fetchall()
        _city_set = set()
        for (_addr,) in _addr_rows:
            _c = _addr.strip().split('-')[0].strip()
            if _c:
                _city_set.add(_c)
        available_cities = sorted(_city_set)
    finally:
        _db.close()

    interested_jobs = _load_interested_jobs()
    review_preselect_id = None
    tp = request.args.get('target_job_id', '').strip()
    if tp.isdecimal():
        tid = int(tp)
        if tid in session.get('interested_jobs', []):
            review_preselect_id = tid

    chat_id = session.get('chat_id')
    agent_state = cache_service._conversations.get(chat_id) if chat_id else None
    compare_state = session.get('compare_state')
    match_state = session.get('match_state')
    review_state = cache_service._review_store.get(session.get('review_id')) if session.get('review_id') else None
    compare_ai = session.get('compare_ai_analysis')
    if compare_ai and (time.time() - compare_ai.get('ts', 0)) >= cache_service._COMPARE_CACHE_TTL:
        compare_ai = None
        session.pop('compare_ai_analysis', None)

    ctx = dict(
        active_tab=active_tab,
        interested_jobs=interested_jobs,
        available_cities=available_cities,
        review_preselect_id=review_preselect_id,
        question=agent_state.get('question') if agent_state else None,
        answer=agent_state.get('answer') if agent_state else None,
        data_context=agent_state.get('data_context') if agent_state else None,
        restored_agent=bool(agent_state),
        compare_result=compare_state.get('result') if compare_state else None,
        compare_a=compare_state.get('a') if compare_state else None,
        compare_b=compare_state.get('b') if compare_state else None,
        compare_ai_analysis=compare_ai.get('text') if compare_ai else None,
        restored_compare=bool(compare_state),
        match_result=match_state.get('result') if match_state else None,
        match_skills=match_state.get('skills') if match_state else None,
        match_city=match_state.get('city') if match_state else None,
        match_edu=match_state.get('edu') if match_state else None,
        match_exper=match_state.get('exper') if match_state else None,
        match_interested=bool(match_state.get('target_job_ids')) if match_state else False,
        match_interested_ids=match_state.get('target_job_ids') if match_state else None,
        restored_match=bool(match_state),
        review_result=review_state.get('result') if review_state else None,
        review_text=review_state.get('text') if review_state else None,
        review_city=review_state.get('city') if review_state else None,
        review_category=review_state.get('category') if review_state else None,
        review_interested_ids=review_state.get('target_job_ids') if review_state else None,
        restored_review=bool(review_state),
    )
    ctx.update(overrides)
    return render_template('advice.html', **ctx)


@advice_bp.route('/advice', methods=['GET', 'POST'])
def advice():
    if request.method != 'POST':
        # GET: 支持清除操作
        tool = request.args.get('tool', '').strip()
        if tool not in ('agent', 'compare', 'match', 'review'):
            tool = session.get('advice_active_tab', 'agent')
        if tool not in ('agent', 'compare', 'match', 'review'):
            tool = 'agent'

        # 清除 Agent 对话: /advice?clear_agent=1
        if request.args.get('clear_agent') == '1':
            chat_id = session.pop('chat_id', None)
            if chat_id:
                cache_service._conversations.pop(chat_id, None)
            session.pop('advice_active_tab', None)
            return redirect(url_for('advice.advice'))

        # 清除对比结果: /advice?clear_compare=1
        if request.args.get('clear_compare') == '1':
            session.pop('compare_state', None)
            session.pop('compare_ai_analysis', None)
            cache_service._compare_analysis_cache.clear()
            return redirect(url_for('advice.advice'))

        # 清除匹配结果: /advice?clear_match=1
        if request.args.get('clear_match') == '1':
            session.pop('match_state', None)
            return redirect(url_for('advice.advice'))
        # 清除简历审查结果: /advice?clear_review=1
        if request.args.get('clear_review') == '1':
            cache_service._review_store.pop(session.pop('review_id', None), None)
            return redirect(url_for('advice.advice'))

        # 从 session/服务端存储恢复所有 tab 的持久化状态并渲染
        return _render_advice(tool)

    tool = request.form.get('tool', 'agent')

    # --- 综合 Agent 模式（默认） ---
    if tool == 'agent':
        question = request.form.get('question', '').strip()
        if not question:
            return _render_advice('agent', error='请输入你的问题')
        api_key = getattr(config, 'DEEPSEEK_API_KEY', '')
        qwen_key = getattr(config, 'QWEN_API_KEY', '')
        if not api_key and not qwen_key:
            return _render_advice('agent', error='请先在.env配置DEEPSEEK_API_KEY或QWEN_API_KEY',
                                   question=question)
        try:
            from agent.agent_core import run_agent
            answer, data_context = run_agent(question)
        except Exception:
            return _render_advice('agent', error='Agent调用失败,请稍后重试',
                                   question=question)

        # 保存 Agent 对话到服务端会话存储（带上限，防长驻进程内存无限增长）
        chat_id = str(uuid.uuid4())
        cache_service._store_put(cache_service._conversations, chat_id,
                                 {'question': question, 'answer': answer, 'data_context': data_context})
        session['chat_id'] = chat_id
        session['advice_active_tab'] = 'agent'
        # 四个 tab 相互独立，各自保留已生成的内容，不再互相清空

        return _render_advice('agent', question=question, answer=answer,
                               data_context=data_context)

    # --- 城市对比工具 ---
    if tool == 'compare':
        a = request.form.get('a', '').strip()
        b = request.form.get('b', '').strip()
        if not a or not b:
            return _render_advice('compare', compare_error='请输入两个要对比的城市',
                                   compare_a=a, compare_b=b)
        try:
            from agent.agent_tools import compare_jobs
            compare_result = compare_jobs('city', a, b)
        except Exception:
            return _render_advice('compare', compare_error='对比查询失败,请稍后重试',
                                   compare_a=a, compare_b=b)

        # 保存对比结果到 session
        session['compare_state'] = {'result': compare_result, 'a': a, 'b': b}
        session['advice_active_tab'] = 'compare'
        # 重新对比 → 旧 AI 解读失效
        cache_service._compare_analysis_cache.clear()
        session.pop('compare_ai_analysis', None)

        return _render_advice('compare', compare_result=compare_result,
                               compare_a=a, compare_b=b)

    # --- 岗位匹配推荐工具 ---
    if tool == 'match':
        skills = request.form.get('skills', '').strip()
        city = request.form.get('city', '').strip()
        edu = request.form.get('edu', '').strip()
        exper = request.form.get('exper', '').strip()
        interested_jobs = _load_interested_jobs()
        # 仅在我感兴趣的岗位中匹配（可选开关，默认不勾选 = 原全库匹配）
        use_interested = bool(request.form.get('match_interested'))
        target_job_ids = None
        if use_interested and session.get('interested_jobs'):
            target_job_ids = [int(x) for x in session.get('interested_jobs')]
        if not skills:
            return _render_advice('match', match_error='请至少输入一个技能关键词',
                                   match_skills=skills, match_city=city,
                                   match_edu=edu, match_exper=exper,
                                   match_interested=use_interested,
                                   interested_jobs=interested_jobs)
        try:
            from agent.agent_tools import match_jobs
            match_result = match_jobs(skills=skills, city=city, edu=edu, exper=exper,
                                      target_job_ids=target_job_ids)
        except Exception:
            return _render_advice('match', match_error='匹配查询失败,请稍后重试',
                                   match_skills=skills, match_city=city,
                                   match_edu=edu, match_exper=exper,
                                   match_interested=use_interested,
                                   interested_jobs=interested_jobs)

        # 保存匹配结果到 session
        session['match_state'] = {
            'result': match_result,
            'skills': skills,
            'city': city,
            'edu': edu,
            'exper': exper,
            'target_job_ids': target_job_ids,
        }
        session['advice_active_tab'] = 'match'

        return _render_advice('match', match_result=match_result,
                               match_skills=skills, match_city=city,
                               match_edu=edu, match_exper=exper,
                               match_interested=use_interested,
                               match_interested_ids=target_job_ids,
                               interested_jobs=interested_jobs)

    # --- 简历审查与优化工具 ---
    if tool == 'review':
        resume_text = request.form.get('resume_text', '').strip()
        target_city = request.form.get('target_city', '').strip()
        target_category = request.form.get('target_category', '').strip()
        interested_jobs = _load_interested_jobs()

        # 针对收藏岗位做审查（可选）：勾选的岗位 id 列表
        raw_ids = request.form.getlist('target_job_ids')
        target_job_ids = [int(x) for x in raw_ids
                          if x.isdecimal() and int(x) > 0] or None

        # 如果上传了文件，优先从文件提取文本
        uploaded_name = ''
        if 'resume_file' in request.files:
            file = request.files['resume_file']
            if file.filename:
                uploaded_name = file.filename
                try:
                    from agent.resume_parser import extract_text
                    file_bytes = file.read()
                    extracted = extract_text(file_bytes, file.filename)
                    if extracted:
                        resume_text = extracted.strip()
                except Exception:
                    cache_service._logger.warning('文件解析失败, 回退到手动输入')

        if not resume_text:
            hint = f'请粘贴简历文本或上传PDF/Word文件'
            if uploaded_name:
                hint = f'无法从 "{uploaded_name}" 提取文本(请确认文件非空或尝试粘贴文本)'
            return _render_advice('review', review_error=hint,
                                   review_text='', review_city=target_city,
                                   review_category=target_category,
                                   interested_jobs=interested_jobs,
                                   review_interested_ids=target_job_ids)
        try:
            from agent.agent_tools import review_resume
            review_result = review_resume(resume_text, target_city=target_city,
                                          target_category=target_category,
                                          target_job_ids=target_job_ids)
        except Exception:
            return _render_advice('review', review_error='简历分析失败,请稍后重试',
                                   review_text=resume_text, review_city=target_city,
                                   review_category=target_category,
                                   interested_jobs=interested_jobs,
                                   review_interested_ids=target_job_ids)

        review_id = str(uuid.uuid4())
        cache_service._store_put(cache_service._review_store, review_id, {
            'result': review_result,
            'text': resume_text,
            'city': target_city,
            'category': target_category,
            'target_job_ids': target_job_ids,
        })
        session['review_id'] = review_id
        session['advice_active_tab'] = 'review'

        return _render_advice('review', review_result=review_result,
                               review_text=resume_text, review_city=target_city,
                               review_category=target_category,
                               interested_jobs=interested_jobs,
                               review_interested_ids=target_job_ids)

    # 未知工具类型，回退到 Agent
    return _render_advice('agent', error='未知工具类型')


@csrf.exempt
@advice_bp.route('/advice/compare/analyze', methods=['POST'])
def advice_compare_analyze():
    """城市对比 AI 解读接口（基于真实对比数据调用 LLM，带 5 分钟服务端缓存）。"""
    compare_state = session.get('compare_state')
    if not compare_state or 'result' not in compare_state:
        return _json_mod.dumps({'error': '请先完成城市对比再生成 AI 解读'}), 400

    result = compare_state['result']
    a = compare_state.get('a', '')
    b = compare_state.get('b', '')
    cache_key = (a, b)

    side_a = result.get('a', {})
    side_b = result.get('b', {})

    def _fmt_side(s):
        top_edu = '、'.join(f'{e}({c})' for e, c in s.get('top_edu', [])) or '无数据'
        top_exper = '、'.join(f'{e}({c})' for e, c in s.get('top_exper', [])) or '无数据'
        return (f"职位数量={s.get('count', 0)}个, 平均薪资={s.get('avg_salary_k', 0)}K, "
                f"区间={s.get('min_salary_k', 0)}-{s.get('max_salary_k', 0)}K, "
                f"学历要求Top3={top_edu}, 经验要求Top3={top_exper}")

    skill_diff = result.get('skill_diff', {})
    if skill_diff:
        skill_lines = [f'{k}: {"、".join(f"{sk}({c})" for sk, c in v) if v else "无"}'
                       for k, v in skill_diff.items()]
        skill_desc = '\n'.join(skill_lines)
    else:
        skill_desc = '两城技能数据不足,无法对比技能差异'

    data_desc = (f"城市A【{a}】: {_fmt_side(side_a)}\n"
                 f"城市B【{b}】: {_fmt_side(side_b)}\n"
                 f"技能差异:\n{skill_desc}")
    instruction = ('请基于上述两城真实对比数据,重点分析:'
                   '(1)两城薪资水平与岗位数量的差距及可能原因;'
                   '(2)学历/经验要求的差异,反映的产业成熟度或人才结构差异;'
                   '(3)技能偏好的差异,反映的产业侧重;'
                   '(4)给求职者一个明确的「选城」或「准备方向」建议。'
                   '用中文,180-320字,直接说结论,不要问候语。')

    system_prompt = ('你是招聘数据分析与城市对比解读助手。数据结论必须来自提供的真实采集数据;'
                     '可补充普适性建议但必须用**【普适性建议】**标注;数据不足时诚实说明;'
                     '不编造具体数字。输出纯文本中文。')

    def _on_success(analysis):
        session['compare_ai_analysis'] = {'text': analysis, 'ts': time.time()}
        cache_service._logger.info('城市对比 AI 解读 %s vs %s', a, b)

    return _llm_analyze(
        cache_service._compare_analysis_cache, cache_service._COMPARE_CACHE_TTL, cache_key,
        data_desc, instruction, system_prompt,
        intro='并对比后的', on_success=_on_success,
    )


@csrf.exempt
@advice_bp.route('/advice/active-tab', methods=['POST'])
def advice_active_tab():
    """前端切换 advice tab 时异步记录当前 active_tab，保证顶部导航切回后仍定位到该 tab。"""
    data = request.get_json(silent=True) or {}
    tab = (data.get('tab') or '').strip()
    if tab in ('agent', 'compare', 'match', 'review'):
        session['advice_active_tab'] = tab
        return _json_mod.dumps({'ok': True})
    return _json_mod.dumps({'error': 'invalid tab'}), 400
