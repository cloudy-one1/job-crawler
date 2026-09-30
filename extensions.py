"""
Flask 扩展实例集中地。

csrf / limiter 在此创建(不绑定 app),由 app.py 通过 init_app() 装配。
独立成模块的原因:routes/ 各蓝图模块需要 @csrf.exempt、@limiter.limit 装饰器,
若扩展在 app.py 里创建,蓝图 import app.py 就会形成循环导入。
"""
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import os

# 启用 Flask-WTF CSRF 保护。所有 POST 表单必须带 {{ csrf_token() }} 隐藏字段,
# 否则返回 400 Bad Request (防止跨站请求伪造)。
csrf = CSRFProtect()

# Flask-Limiter 速率限制: 全局限流 + 对危险路由(/collect)单独收紧。
# 默认内存存储在多进程部署(如 gunicorn 多 worker)下各进程独立计数,限流会变相失效;
# 多 worker 部署时通过 RATELIMIT_STORAGE_URI 指向共享后端(如 redis://localhost:6379/0)。
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'),
)
