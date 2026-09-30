"""
AI 分析公共逻辑（原 app.py _llm_analyze 迁入）。

供 routes/chart.py、routes/ml.py、routes/advice.py 三个解读接口共用:
缓存命中 → 拼 prompt → 双 Key 校验 → call_llm_with_fallback → 写缓存 → 统一异常兜底。
"""
import json
import logging
import time

import requests

import config

_logger = logging.getLogger('job_analysis')


def _llm_analyze(cache, ttl, cache_key, data_desc, instruction,
                 system_prompt, intro="", on_success=None, force_fresh=False):
    """公共 LLM 分析逻辑：缓存命中 → 拼 prompt → 双 Key 校验 →
    call_llm_with_fallback → 写缓存 → 统一异常兜底。

    成功返回纯文本分析字符串（端点可直接 return）；
    失败返回 (json_str, status_code) 元组。
    intro: 注入导语差异（'' / '并建模后的' / '并对比后的'）。
    on_success: 写缓存成功后回调（传入 analysis），如 compare 写 session。
    force_fresh: True 时跳过缓存命中判断（强制重新生成）。
    """
    # 缓存命中 → 直接返回（force_fresh 跳过）
    cached = cache.get(cache_key)
    if not force_fresh and cached and (time.time() - cached[0]) < ttl:
        _logger.debug('AI 分析缓存命中: %s', cache_key)
        return cached[1]

    prompt = f"""以下是当前招聘数据库中通过爬虫真实采集{intro}的数据。请严格据此给出分析。

【数据】
{data_desc}

【要求】
{instruction}

重要约束:
- 数据结论必须来自上述数据的具体数字,限定在"本数据库采集的岗位中"。
- 不得编造具体数字。数据不足时诚实说明"暂无相关数据"。
- 可适当补充普适性求职建议(非数据库内容),但必须用 **【普适性建议】** 前缀标注,与数据结论明确区分。"""

    api_key = getattr(config, 'DEEPSEEK_API_KEY', '')
    qwen_key = getattr(config, 'QWEN_API_KEY', '')
    if not api_key and not qwen_key:
        return json.dumps({'error': 'DEEPSEEK_API_KEY 和 QWEN_API_KEY 均未配置,无法生成分析'}), 503

    try:
        from agent.agent_core import call_llm_with_fallback
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt},
        ]
        analysis, _provider = call_llm_with_fallback(messages, deepseek_key=api_key)
        cache[cache_key] = (time.time(), analysis)
        if on_success:
            on_success(analysis)
        return analysis
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
        # 细节进日志,前端只拿友好提示,不暴露内部路径/接口地址
        _logger.warning('AI 分析生成失败 (HTTP %s): %s', status, e)
        msg = 'AI 服务暂时繁忙,请稍后再试' if status == 429 else 'AI 分析生成失败,请稍后再试'
        return json.dumps({'error': msg}), 503
    except Exception as e:
        _logger.warning('AI 分析生成失败: %s', e)
        return json.dumps({'error': 'AI 分析生成失败,请稍后再试'}), 500
