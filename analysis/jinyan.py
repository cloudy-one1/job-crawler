"""
工作经验要求分布统计。

统计 data 表中每个不同 exper 值的出现次数,返回 [[标签, 数量], ...]
按频率从大到小排序。空值合并为 "经验不限"。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import config
from pandas import DataFrame


def get_exper():
    db = sqlite3.connect(config.DB_PATH)
    try:
        cursor = db.cursor()
        cursor.execute("select exper from data")
        return cursor.fetchall()
    except Exception as e:
        print('query failed:', e)
        return []
    finally:
        db.close()


def jinyanfun():
    rows = get_exper()
    # 空库 / 全部为空经验时直接返回空列表（DataFrame([]) 取列 0 会 KeyError，导致 /chart 500）
    if not rows:
        return []
    data = [r[0] if r[0] else '经验不限' for r in rows]

    counts = DataFrame(data)[0].value_counts()
    list_all = []
    for exper, count in zip(counts.index, counts):
        list_all.append([exper, int(count)])
    return list_all


if __name__ == '__main__':
    print(jinyanfun())
