"""
薪资成长曲线 — 经验 vs 薪资的趋势分析。

从数据库中按经验等级分组统计薪资分布，支持：
1. 整体曲线：所有岗位的经验-薪资趋势
2. 分城市曲线：各主要城市的经验-薪资趋势对比

返回 ECharts 折线图所需的数据。
"""
import sqlite3
import config
import logging
import numpy as np

from data.exper_parser import EXPER_ORDER, normalize_exper

_logger = logging.getLogger('modeling.salary_curve')


def _normalize_exper(exper_raw):
    """经验文本 → (排序键, 统一档位标签)，口径见 data/exper_parser.py。"""
    label = normalize_exper(exper_raw)
    return (EXPER_ORDER[label], label)


def compute_salary_curve(min_per_level=3):
    """计算经验-薪资曲线数据。

    Args:
        min_per_level: 每个经验等级最少岗位数

    Returns:
        dict: {
            overall: [{exper: str, count: int, median: float, mean: float, p25: float, p75: float}],
            by_city: {city_name: [{exper: str, count: int, median: float, ...}]},
            cities: [str],
            total_rows: int,
        }
    """
    try:
        db = sqlite3.connect(config.DB_PATH)
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        cursor.execute(
            "SELECT address, salary_min, salary_max, exper FROM data"
        )
        rows = cursor.fetchall()
        db.close()
    except sqlite3.Error as e:
        _logger.warning('compute_salary_curve 读取数据库失败: %s', e)
        return {'error': '数据库读取失败', 'total_rows': 0}

    if not rows:
        return {'error': '数据库中没有岗位数据，请先采集', 'total_rows': 0}

    # 聚合： exper_level → [salaries]（整体）
    exper_salaries = {}
    city_exper_salaries = {}  # city → exper_level → [salaries]

    for row in rows:
        addr = row['address'] or ''
        city = addr.split('-')[0].strip() if addr else '未知'
        smin = row['salary_min']
        smax = row['salary_max']
        if not smin or not smax or (smin + smax) <= 0:
            continue
        avg_sal = (smin + smax) / 2
        order, label = _normalize_exper(row['exper'])

        # 整体
        if label not in exper_salaries:
            exper_salaries[label] = []
        exper_salaries[label].append(avg_sal)

        # 分城市
        if city not in city_exper_salaries:
            city_exper_salaries[city] = {}
        if label not in city_exper_salaries[city]:
            city_exper_salaries[city][label] = []
        city_exper_salaries[city][label].append(avg_sal)

    if not exper_salaries:
        return {'error': '没有有效薪资数据', 'total_rows': len(rows)}

    # 构建整体曲线（按经验排序）
    sorted_exper = sorted(exper_salaries.keys(),
                         key=lambda e: EXPER_ORDER.get(e, 99))

    overall = []
    for exper in sorted_exper:
        salaries = exper_salaries[exper]
        if len(salaries) < min_per_level:
            continue
        arr = np.array(salaries)
        overall.append({
            'exper': exper,
            'count': len(salaries),
            'median': round(float(np.median(arr)), 1),
            'mean': round(float(np.mean(arr)), 1),
            'p25': round(float(np.percentile(arr, 25)), 1),
            'p75': round(float(np.percentile(arr, 75)), 1),
        })

    # 构建分城市曲线（只保留数据较多的城市）
    by_city = {}
    top_cities = sorted(city_exper_salaries.keys(),
                       key=lambda c: sum(len(v) for v in city_exper_salaries[c].values()),
                       reverse=True)[:6]

    for city in top_cities:
        city_data = city_exper_salaries[city]
        city_curve = []
        for exper in sorted_exper:
            if exper not in city_data:
                continue
            salaries = city_data[exper]
            if len(salaries) < min_per_level:
                continue
            arr = np.array(salaries)
            city_curve.append({
                'exper': exper,
                'count': len(salaries),
                'median': round(float(np.median(arr)), 1),
                'mean': round(float(np.mean(arr)), 1),
            })
        if len(city_curve) >= 2:  # 至少两个经验等级才有趋势意义
            by_city[city] = city_curve

    return {
        'overall': overall,
        'by_city': by_city,
        'cities': list(by_city.keys()),
        'total_rows': len(rows),
    }


if __name__ == '__main__':
    import json
    result = compute_salary_curve()
    print(json.dumps(result, ensure_ascii=False, indent=2))
