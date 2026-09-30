"""
城市/地区分布统计。

从 address 字段按 '-' 分割提取城市,然后按城市统计出现次数。
返回 [[城市名, 数量], ...] 按频率从大到小排序。
用于 /chart 页面渲染地区饼图。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import config
from pandas import DataFrame


def extract_city(addr):
    """从地址字符串中提取城市名('-' 分割的第一段)。
    作为共享工具函数,被 regionfun 和 agent_tools.city_overview 共同调用,
    确保城市提取逻辑只有一处定义。
    """
    return addr.split('-')[0] if addr else 'Unknown'


def get_address():
    db = sqlite3.connect(config.DB_PATH)
    try:
        cursor = db.cursor()
        cursor.execute("select address from data")
        return cursor.fetchall()
    except Exception as e:
        print('query failed:', e)
        return []
    finally:
        db.close()


def regionfun():
    rows = get_address()
    # 空库时直接返回空列表（DataFrame([]) 取列 0 会 KeyError，导致 /chart 500）
    if not rows:
        return []
    city_list = [extract_city(row[0]) for row in rows]

    counts = DataFrame(city_list)[0].value_counts()

    list_all = []
    for city, count in zip(counts.index, counts):
        list_all.append([city, int(count)])
    return list_all


if __name__ == '__main__':
    print(regionfun())
