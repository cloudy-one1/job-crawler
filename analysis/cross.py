"""
交叉分析:薪资 vs 经验、薪资 vs 学历的关联统计。

与 xinzi/xueli/jinyan 等单维度统计不同,这两个函数对两个维度做交叉聚合,
用于 /chart 页面渲染薪资与经验/学历的交叉分析热力图。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import config
from data.exper_parser import EXPER_BUCKETS, normalize_exper


def _get_rows():
    db = sqlite3.connect(config.DB_PATH)
    try:
        cursor = db.cursor()
        cursor.execute("SELECT salary_min, salary_max, edu, exper FROM data")
        return cursor.fetchall()
    except Exception:
        return []
    finally:
        db.close()


def salary_vs_exper():
    """返回按经验等级分组的薪资统计。

    返回: {
        'labels': ['经验不限', '1-3年', ...],       # x 轴
        'counts': [15, 23, ...],                   # 各经验等级职位数
        'avg_salaries': [8.5, 12.3, ...],          # 各经验等级平均薪资(千元)
    }
    """
    rows = _get_rows()
    exper_order = EXPER_BUCKETS
    exper_data = {e: {'salaries': [], 'count': 0} for e in exper_order}

    for smin, smax, edu, exper in rows:
        exp = normalize_exper(exper)
        exper_data[exp]['count'] += 1
        if smin or smax:
            exper_data[exp]['salaries'].append((smin + smax) / 2)

    result = {'labels': [], 'counts': [], 'avg_salaries': []}
    for e in exper_order:
        d = exper_data[e]
        result['labels'].append(e)
        result['counts'].append(d['count'])
        sl = d['salaries']
        result['avg_salaries'].append(round(sum(sl) / len(sl), 1) if sl else 0)

    return result


def salary_vs_edu():
    """返回按学历等级分组的薪资统计。

    返回: {
        'labels': ['不限', '大专', '本科', '硕士', '博士'],
        'counts': [...],
        'avg_salaries': [...],
    }
    """
    rows = _get_rows()
    edu_order = ['不限', '大专', '本科', '硕士', '博士']
    edu_data = {e: {'salaries': [], 'count': 0} for e in edu_order}

    for smin, smax, edu, exper in rows:
        e = edu or '不限'
        # 模糊归并: "不限" 类归一
        if e not in edu_data:
            if '大专' in e:
                e = '大专'
            elif '本科' in e:
                e = '本科'
            elif '硕士' in e or '研究生' in e:
                e = '硕士'
            elif '博士' in e:
                e = '博士'
            else:
                e = '不限'
        edu_data[e]['count'] += 1
        if smin or smax:
            edu_data[e]['salaries'].append((smin + smax) / 2)

    result = {'labels': [], 'counts': [], 'avg_salaries': []}
    for e in edu_order:
        d = edu_data[e]
        result['labels'].append(e)
        result['counts'].append(d['count'])
        sl = d['salaries']
        result['avg_salaries'].append(round(sum(sl) / len(sl), 1) if sl else 0)

    return result


if __name__ == '__main__':
    print('=== 薪资 vs 经验 ===')
    print(salary_vs_exper())
    print('\n=== 薪资 vs 学历 ===')
    print(salary_vs_edu())
