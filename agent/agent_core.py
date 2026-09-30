"""
轻量 Agent：预加载数据库概览数据注入 system prompt，单次 LLM 调用直接输出回答。

v2 变更（2026-07-06）：
  - 移除 ReAct 多轮循环（工具间无依赖链，多轮纯增延迟）
  - 移除 Critic Agent 审计（LLM 审 LLM 投入产出比低）
  - 改为预加载 overview → 单次调用，API 调用从 5 次 → 1 次
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
import time
import logging
import requests

import config as _config

_logger = logging.getLogger('job_analysis.agent')

DEEPSEEK_API_URL = 'https://api.deepseek.com/chat/completions'

# 复用 Session 减少 TCP 握手和连接重置概率
_session = None


def _get_session():
    """返回全局复用的 requests.Session，启用 keep-alive。"""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            'Connection': 'keep-alive',
            'Accept': 'application/json',
        })
    return _session


# ---- 常见技术关键词（用于从用户问题中提取查询关键词） ----
_TECH_KEYWORDS = [
    'Python', 'Java', 'JavaScript', 'TypeScript', 'Go', 'Rust', 'C++', 'C#', 'PHP', 'Ruby',
    'React', 'Vue', 'Angular', 'Node', 'Django', 'Flask', 'Spring', 'FastAPI',
    'Docker', 'Kubernetes', 'K8s', 'Linux', 'AWS', 'Azure', 'GCP',
    'MySQL', 'PostgreSQL', 'MongoDB', 'Redis', 'Elasticsearch', 'Oracle',
    'TensorFlow', 'PyTorch', 'Pandas', 'NumPy', 'Spark', 'Hadoop', 'Kafka',
    '爬虫', '前端', '后端', '全栈', '数据分析', '机器学习', '深度学习', '运维', '测试',
    '架构', '微服务', '大数据', 'NLP', '计算机视觉', '算法',
]


def _extract_keywords(question):
    """从用户问题中提取匹配的技术关键词，用于 query_jobs 预查询。"""
    found = []
    for kw in _TECH_KEYWORDS:
        if kw.lower() in question.lower():
            found.append(kw)
    return found


def _build_system_prompt(data_context):
    """构建包含预加载数据的 system prompt。"""
    total = data_context.get('total_jobs', 0)

    prompt = f"""You are a job-market assistant. You have been given a PRE-LOADED snapshot
of a local SQLite database containing {total} Python-related job postings crawled from 51job.

==== PRE-LOADED DATA — USE THESE EXACT NUMBERS FOR THE "基于数据库分析" SECTION ====

{json.dumps(data_context, ensure_ascii=False, indent=2)}

==== ANSWER STRUCTURE — YOU MUST FOLLOW THIS EXACT TWO-SECTION FORMAT ====

---
## 📊 基于数据库分析（来自本地招聘数据）

[Present ALL data-backed findings first. This section MUST:]
- Always open by stating the sample scope: "在本数据库的 {total} 条岗位中..."
- Every number, percentage, skill name, city name, salary figure MUST come
  directly from the PRE-LOADED DATA above — never fabricate.
- Structure by dimension: salary range → city distribution → education
  requirements → experience requirements → skill frequencies → trends.
- If no data is available for a given dimension, honestly state so
  (e.g. "当前数据库暂无该维度的数据") rather than filling in guesses.

---
## 💡 普适性建议（行业通用分析，非数据库推导）

[Add broader career advice only AFTER the database section above.]
- You MUST begin this section with a clear disclaimer, e.g.:
  "以下建议基于行业通用认知，并非直接来自本数据库的分析结果。"
- Cover: industry trends, learning paths, soft skills, interview tips,
  career development strategies, certification advice.
- NEVER include fabricated numbers, percentages, or specific data claims
  in this section — if you need data, it belongs in section 1.
- Keep this section concise (3-5 bullet points recommended).

==== HARD CONSTRAINTS ====

1. ALL factual claims (numbers, trends, skill requirements, salary ranges,
   educational requirements, city distributions) MUST be directly backed by
   the PRE-LOADED DATA above and placed in the "基于数据库分析" section.

2. All salary figures, company counts, skill frequencies, and statistical
   claims must be exact numbers from the pre-loaded data — never round, approximate,
   or invent them.

3. When drawing conclusions from the database, qualify with the sample scope
   (e.g. "在本数据库的{total}条岗位中"). Do NOT imply the sample represents the
   entire market.

4. The two sections MUST be visually distinct. Use the exact section headers
   shown above (## 📊 基于数据库分析 / ## 💡 普适性建议) so users can tell
   at a glance what is data-backed vs. general knowledge.

5. Answer in Chinese. Use the exact city/category names from the pre-loaded data.

Only output the Final Answer directly — no Thought/Action/Action Input needed."""

    return prompt


def _log_retry(provider, reason, attempt, max_retries, err):
    """统一的重试日志:最后一次标注"最后一次",其余附退避等待秒数。"""
    if attempt < max_retries:
        wait = 2 ** attempt
        _logger.warning('%s %s(第%d/%d次),%ds后重试: %s', provider, reason, attempt, max_retries, wait, err)
    else:
        _logger.warning('%s %s(第%d/%d次,最后一次): %s', provider, reason, attempt, max_retries, err)


def _post_chat_completion(url, api_key, model, messages, provider, max_retries=3):
    """统一的 chat/completions POST + 指数退避重试(连接类错误与限流均重试)。"""
    session = _get_session()
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.post(
                url,
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={'model': model, 'messages': messages, 'temperature': 0.3},
                timeout=(5, 30),
            )
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content']
        except requests.exceptions.ConnectionError as e:
            last_error = e
            _log_retry(provider, '连接被重置', attempt, max_retries, e)
        except requests.exceptions.RequestException as e:
            last_error = e
            status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            reason = '被限流(HTTP 429)' if status_code == 429 else 'API 失败'
            _log_retry(provider, reason, attempt, max_retries, e)
        if attempt < max_retries:
            time.sleep(2 ** attempt)
    raise last_error


def call_deepseek(messages, api_key, model='deepseek-chat', max_retries=3):
    """调用 DeepSeek API,带指数退避重试,应对网络抖动。"""
    return _post_chat_completion(
        DEEPSEEK_API_URL, api_key, model, messages, 'DeepSeek', max_retries)


def call_qwen(messages, api_key=None, model=None, max_retries=3):
    """调用通义千问 API,作为 DeepSeek 不可用时的 fallback。"""
    if api_key is None:
        api_key = getattr(_config, 'QWEN_API_KEY', '')
    if model is None:
        model = getattr(_config, 'QWEN_MODEL', 'qwen-plus')
    if not api_key:
        raise RuntimeError('QWEN_API_KEY 未配置,无法调用千问 API')

    qwen_url = getattr(_config, 'QWEN_API_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
    return _post_chat_completion(qwen_url, api_key, model, messages, '千问', max_retries)


def call_llm_with_fallback(messages, deepseek_key=None, deepseek_model='deepseek-chat'):
    """先尝试 DeepSeek，失败或无 key 时自动回退到千问。

    返回: (content, provider) 元组，provider 为 'deepseek' 或 'qwen'。
    """
    if deepseek_key is None:
        deepseek_key = getattr(_config, 'DEEPSEEK_API_KEY', '')

    if deepseek_key:
        try:
            result = call_deepseek(messages, deepseek_key, model=deepseek_model, max_retries=2)
            return result, 'deepseek'
        except Exception as e:
            _logger.warning('DeepSeek 调用失败,回退到千问: %s', e)
    else:
        _logger.info('DEEPSEEK_API_KEY 未配置,直接使用千问')

    try:
        result = call_qwen(messages, max_retries=2)
        return result, 'qwen'
    except Exception as e:
        _logger.error('千问也调用失败: %s', e)
        raise RuntimeError(f'所有 LLM API 均不可用 (DeepSeek + 千问): {e}')


def _extract_answer_text(text):
    """从 LLM 输出中清理出答案正文——定位第一个 Markdown 二级标题,丢弃前缀。"""
    if not text:
        return text
    m = re.search(r'(?m)^\s*##\s+', text)
    if m:
        text = text[m.start():]
    return text.strip()


def run_agent(question, api_key=None, llm_call=None):
    """预加载数据库概览数据 → 注入 system prompt → 单次 LLM 调用 → 返回答案。

    v2 变更: 移除 ReAct 多轮循环和 Critic 审计，改为一次调用。

    Returns:
        (answer, data_context)
        - answer: LLM 生成的回答（Markdown 文本）
        - data_context: 注入 system prompt 的数据库概览数据（dict）
    """
    if llm_call is None:
        if api_key is None:
            api_key = getattr(_config, 'DEEPSEEK_API_KEY', '')
        def _default_call(messages):
            result, _provider = call_llm_with_fallback(messages, deepseek_key=api_key)
            return result
        llm_call = _default_call

    # ---- 1. 预加载数据库概览数据 ----
    from agent.agent_tools import (
        category_overview, city_overview, edu_overview, exper_overview, query_jobs,
    )

    data_context = {
        'category_distribution': category_overview(),
        'city_distribution': city_overview(),
        'edu_distribution': edu_overview(),
        'exper_distribution': exper_overview(),
    }

    # 计算总岗位数
    total = sum(c['count'] for c in data_context['category_distribution'])
    data_context['total_jobs'] = total

    # ---- 2. 从问题中提取关键词做预查询 ----
    keywords = _extract_keywords(question)
    if keywords:
        main_kw = keywords[0]
        try:
            kw_result = query_jobs(main_kw)
            data_context['keyword_query'] = {
                'keyword': main_kw,
                'result': kw_result,
            }
        except Exception:
            data_context['keyword_query'] = {'keyword': main_kw, 'error': '查询失败'}

    # ---- 3. 构建 prompt + 单次调用 ----
    system_prompt = _build_system_prompt(data_context)
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': question},
    ]

    reply = llm_call(messages)
    _logger.debug('LLM 回答:\n%s', reply)

    answer = _extract_answer_text(reply)
    return answer, data_context


if __name__ == '__main__':
    api_key = getattr(_config, 'DEEPSEEK_API_KEY', '')
    qwen_key = getattr(_config, 'QWEN_API_KEY', '')
    if not api_key and not qwen_key:
        print('DEEPSEEK_API_KEY 和 QWEN_API_KEY 均未配置,请在 .env 文件或环境变量中至少设置一个。')
    else:
        question = input('请输入问题(例如: Python Web 开发岗位在本地区的就业形势如何?): ')
        answer, data_context = run_agent(question)
        print('=== 数据库概览（注入 LLM 上下文）===')
        print(f'总岗位数: {data_context["total_jobs"]}')
        print(f'城市分布前 5: {data_context["city_distribution"][:5]}')
        if data_context.get("keyword_query"):
            print(f'关键词预查询: {data_context["keyword_query"]["keyword"]}')
        print('=== 最终答案 ===')
        print(answer)
