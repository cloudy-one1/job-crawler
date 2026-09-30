"""
job-crawler Web 服务入口（装配层）。

本文件只负责:
- 日志初始化
- 创建 Flask app、注入 secret、装配扩展(CSRF / 限流)
- 注册 routes/ 下的五个蓝图
- 启动时的数据库初始化与 WAL 开启

业务逻辑的归属:
- 路由        → routes/（pages / chart / ml / advice / collect 五个蓝图）
- 缓存与模型状态 → services/cache.py
- 数据库连接   → services/db.py
- LLM 分析    → services/ai.py
- 扩展实例     → extensions.py

数据流:
    data/ 采集清洗 → data.db
        → analysis/ 描述性统计
        → modeling/ 聚类与薪资建模
        → agent/    LLM 求职建议
        → templates/ 渲染展示

分层硬约束: 依赖只能自上而下(data → analysis → modeling → agent),
下层模块禁止引用上层。

运行: python app.py
访问: http://127.0.0.1:5000
"""
import os
import sqlite3
import logging
from logging.handlers import RotatingFileHandler

from flask import Flask, session

import config

# --- 日志系统 ----------------------------------------------------------------
# 同时输出到旋转日志文件(每5MB切一个,保留3个备份)和控制台
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(_log_dir, exist_ok=True)

_logger = logging.getLogger('job_analysis')
_logger.setLevel(logging.INFO)

_file_handler = RotatingFileHandler(
    os.path.join(_log_dir, 'app.log'), maxBytes=5 * 1024 * 1024, backupCount=3,
    encoding='utf-8'
)
_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
))
_logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
_logger.addHandler(_console_handler)

# 消掉 jieba → pkg_resources 的弃用警告
import warnings
warnings.filterwarnings('ignore', message='pkg_resources is deprecated', category=UserWarning)

from extensions import csrf, limiter
from services.db import close_db, init_db
# 以下重导出仅供既有测试与外部引用保持兼容,新代码请直接从 services.cache 引用
from services.cache import (  # noqa: F401
    _review_store, _conversations, _joblib_load_fresh,
)

app = Flask(__name__)

# --- 安全基础配置 ------------------------------------------------------------
# secret_key 用于 Flask session 签名与 Flask-WTF CSRF token 校验。
# 生产环境必须通过 FLASK_SECRET 环境变量注入固定值(否则重启后所有 session/token 失效)。
app.secret_key = os.environ.get('FLASK_SECRET') or os.urandom(32)

# --- 启动时数据库与 WAL 初始化 ------------------------------------------------
init_db()

# SQLite WAL 模式: 设一次即持久化到数据库文件,后续所有连接自动受益(并发读不阻塞写)
try:
    if os.path.exists(config.DB_PATH):
        _wal_conn = sqlite3.connect(config.DB_PATH)
        _wal_conn.execute("PRAGMA journal_mode=WAL")
        _wal_conn.close()
except Exception:
    pass

# --- 蓝图注册 ------------------------------------------------------------
from routes.pages import pages_bp
from routes.chart import chart_bp
from routes.ml import ml_bp
from routes.advice import advice_bp
from routes.collect import collect_bp

app.register_blueprint(pages_bp)
app.register_blueprint(chart_bp)
app.register_blueprint(ml_bp)
app.register_blueprint(advice_bp)
app.register_blueprint(collect_bp)

csrf.init_app(app)
limiter.init_app(app)


@app.teardown_appcontext
def _close_db(exception=None):
    """请求结束时关闭数据库连接,避免连接泄漏。"""
    close_db(exception)


# --- 模板全局变量注入 ------------------------------------------------------------
@app.context_processor
def inject_globals():
    """向所有模板注入全局变量,避免每个路由手动传参。"""
    return {
        'collect_token_required': bool(config.COLLECT_TOKEN),
        'interested_count': len(session.get('interested_jobs', [])),
    }


if __name__ == '__main__':
    # --- 启动配置 ---------------------------------------------------------------
    # debug 默认关闭，避免 Flask 强制开启 reloader（reloader fork 子进程后，
    # 父进程的 print 输出丢失，且子进程启动失败时伪装为"正常退出"→ERR_CONNECTION_REFUSED）。
    # 如需热更新: 在项目根目录 touch 一个 .debug 文件即可（不建议日常使用）。
    # 局域网访问: $env:FLASK_HOST="0.0.0.0"
    _debug_flag = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.debug')
    debug = os.path.exists(_debug_flag)
    if debug:
        print("[WARN] 检测到 .debug 文件，已启用 debug 模式（含 reloader）", flush=True)
    host = os.environ.get('FLASK_HOST', '127.0.0.1')

    # SO_REUSEADDR: 允许端口立即重用，避免 Windows 上重启 Flask 时
    # 因 TIME_WAIT 导致 "Address already in use" 需要手动杀进程
    import socketserver
    socketserver.TCPServer.allow_reuse_address = True

    # threaded=True: 允许并发处理请求,避免/collect 阻塞其他页面浏览
    try:
        port = int(os.environ.get('FLASK_PORT', '5000'))
    except ValueError:
        port = 5000
    print(f"\n  -> 本地访问: http://127.0.0.1:{port}", flush=True)
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.254.254.254', 1))
        lan_ip = s.getsockname()[0]
        s.close()
        print(f"  -> 局域网访问: http://{lan_ip}:{port}    (同局域网设备可用)\n", flush=True)
    except Exception:
        print(flush=True)
    app.run(debug=debug, host=host, port=port, threaded=True, use_reloader=debug)
