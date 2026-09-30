# 招聘数据分析平台 Docker 镜像（纯 Web 服务，不含爬虫）
#
# 架构说明：
#   - 数据采集（Playwright Chromium）在宿主机本机运行 → 写入挂载目录中的 data.db
#   - Docker 容器只负责 Flask Web 展示 + ML 模型 + Agent 推理
#   - 两者通过 volume 挂载共享同一个数据库目录（含 SQLite 的 -wal/-shm 日志文件）
#
# 构建: docker build -t job-analysis .
# 运行: docker run -p 5000:5000 -v $(pwd)/db:/app/db -e DB_PATH=/app/db/data.db --env-file .env job-analysis

FROM python:3.11-slim

# 最小系统依赖（仅 SSL 证书）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先安装 Python 依赖（利用 Docker 缓存层）
# playwright 包仅安装 Python 绑定（import 不报错），不下载 Chromium 浏览器；
# gunicorn 仅容器内使用，本机开发仍直接 python app.py
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# 复制项目代码
COPY . .

# SQLite 数据库目录：宿主机采集后挂载整个目录（含 -wal/-shm 日志），不挂单文件
VOLUME ["/app/db"]

EXPOSE 5000

# 生产 WSGI 服务器。单 worker × 多线程：与开发服务器相同的并发语义，
# 且避免多 worker 下 Flask-Limiter 内存存储各进程独立计数（多 worker 需配
# RATELIMIT_STORAGE_URI 指向 Redis 等共享后端）。timeout 覆盖 /collect 长请求。
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "300", "--bind", "0.0.0.0:5000", "app:app"]
