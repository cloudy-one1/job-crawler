"""
薪资参考查询 — 纯统计，零模型。

基于数据库中的真实岗位数据，按 (城市, 职位类别, 学历, 经验) 条件筛选，
返回描述性统计（中位数、均值、范围、分位数），不做任何 ML 预测。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import config
from analysis.jobtitle import classify
from data.exper_parser import normalize_exper
import numpy as np


def get_rows():
    try:
        db = sqlite3.connect(config.DB_PATH)
        cursor = db.cursor()
        cursor.execute("SELECT post, address, salary_min, salary_max, edu, exper FROM data")
        rows = cursor.fetchall()
        db.close()
        return rows
    except sqlite3.Error:
        return []


def _contains_word(needle, haystack):
    """双向包含匹配，但要求 needle 至少 2 个字符，防止单字误匹配。"""
    if not needle or not haystack:
        return False
    if len(needle) < 2:
        return needle == haystack
    return needle in haystack or haystack in needle


def lookup_salary_range(city='', category='', edu='', exper=''):
    """数据库驱动的薪资参考查询 — 零模型，纯统计。

    从数据库中筛选匹配 (城市 + 职位类别 + 学历 + 经验) 的岗位，
    返回真实薪资的描述性统计（中位数、均值、范围、分位数）。

    Args:
        city: 城市名称（如"北京"、"上海"，模糊匹配）
        category: 职位类别（如"后端开发"、"Web/前端"，双向模糊匹配）
        edu: 学历过滤（空或"不限"表示不过滤）
        exper: 经验过滤（空或"经验不限"表示不过滤）

    Returns:
        dict: {
            count, median, mean, min, max, p25, p75  — 薪资统计，单位K
            或 {count, message} — 数据不足时
        }
    """
    try:
        rows = get_rows()
    except Exception:
        return {'count': 0, 'message': '数据库读取失败，请稍后重试'}
    matched = []
    for post, addr, smin, smax, edu_val, exper_val in rows:
        if not smin and not smax:
            continue
        # 城市过滤（模糊：输入"北京"能匹配"北京-海淀区"，但"海"不会误匹配"上海"）
        if city:
            addr_city = addr.split('-')[0] if addr else ''
            if not _contains_word(city, addr_city):
                continue
        # 类别过滤（双向模糊：输入"后端"能匹配"后端开发"）
        if category:
            cat = classify(post)
            if not _contains_word(category, cat):
                continue
        # 学历过滤（要求至少2字符匹配，防单字误匹配）
        if edu and edu != '不限':
            if not edu_val or not _contains_word(edu, edu_val):
                continue
        # 经验过滤（统一到 data/exper_parser.py 的档位口径）
        if exper and exper != '经验不限':
            if normalize_exper(exper_val) != normalize_exper(exper):
                continue
        matched.append((smin + smax) / 2)

    if len(matched) < 3:
        msg = '匹配岗位数量不足 (需要≥3)，请放宽条件' if matched else '未找到匹配岗位'
        return {'count': len(matched), 'message': msg}

    arr = np.array(matched)
    return {
        'count': len(matched),
        'median': round(float(np.median(arr)), 1),
        'mean': round(float(np.mean(arr)), 1),
        'min': round(float(np.min(arr)), 1),
        'max': round(float(np.max(arr)), 1),
        'p25': round(float(np.percentile(arr, 25)), 1),
        'p75': round(float(np.percentile(arr, 75)), 1),
    }


if __name__ == '__main__':
    print('=== 薪资参考查询（数据库驱动，零模型）===\n')
    for city, cat in [('北京', '后端开发'), ('上海', 'Web/前端'), ('深圳', '数据')]:
        r = lookup_salary_range(city, cat)
        if 'message' in r:
            print(f'  {city} + {cat}: {r["message"]}')
        else:
            print(f'  {city} + {cat}: '
                  f'中位数={r["median"]}K  均值={r["mean"]}K  '
                  f'范围 {r["min"]}-{r["max"]}K  (共{r["count"]}个岗位)')
