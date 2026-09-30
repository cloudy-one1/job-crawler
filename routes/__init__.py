"""展示层路由蓝图。

每个模块一个 Blueprint,app.py 统一注册;路由不注册 url_prefix,
保持与拆分前完全相同的 URL 路径。注意:蓝图端点带命名空间
(如 url_for('pages.job_detail')),模板中的裸端点已同步迁移。
"""
