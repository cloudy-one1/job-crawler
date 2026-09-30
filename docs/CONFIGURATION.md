# 配置说明

所有配置项都通过环境变量读取，写在项目根目录的 `.env` 文件中（从 `.env.example` 复制而来，已被 git 忽略）。
`config.py` 启动时解析 `.env` 并写入 `os.environ`；**已在本机 export 过的同名环境变量优先级更高，不会被覆盖**。

```bash
cp .env.example .env
```

## 配置项一览

| 变量 | 默认值 | 是否必填 | 说明 |
|------|--------|----------|------|
| `DEEPSEEK_API_KEY` | 空 | 选填（AI 功能） | 主用大模型密钥，[申请地址](https://platform.deepseek.com/) |
| `QWEN_API_KEY` | 空 | 选填（AI 功能） | 通义千问密钥，DeepSeek 不可用时自动切换 |
| `QWEN_MODEL` | `qwen-plus` | 选填 | 通义千问模型名 |
| `QWEN_API_URL` | dashscope 兼容接口 | 选填 | 通义千问 API 地址 |
| `FLASK_SECRET` | 启动时随机生成 | 生产必填 | 签名 session 与 CSRF token，固定值请用 `python -c "import secrets; print(secrets.token_hex(32))"` 生成 |
| `FLASK_HOST` | `127.0.0.1` | 选填 | 绑定网卡，需要局域网访问时设 `0.0.0.0` |
| `FLASK_PORT` | `5000` | 选填 | 监听端口 |
| `DB_PATH` | 项目根目录 `data.db` | 选填 | SQLite 数据库路径。Docker 部署用它把数据库指到挂载目录（`docker-compose.yml` 已内置） |
| `RATELIMIT_STORAGE_URI` | `memory://` | 选填 | Flask-Limiter 限流计数存储。默认内存存储在**多进程部署**（gunicorn 多 worker）下各进程独立计数、限流变相失效，多 worker 时应指向共享后端如 `redis://localhost:6379/0`；容器默认单 worker，无需配置 |
| `COLLECT_TOKEN` | 空（不启用） | 选填 | 采集口令，设置后 `/collect` 表单必须携带同值字段 |

## 关于 AI 功能

除 AI 相关的 4 个能力（Agent 问答、图表解读、建模解读、简历审查）外，**其余功能全部离线可用**，不配置任何密钥也能正常采集、分析、建模和看图。未配置密钥时，AI 入口会给出提示而不是报错。

## 关于 Debug 模式

Debug 开关**不使用环境变量**，而是看项目根目录是否存在 `.debug` 文件：

```bash
touch .debug     # 开启 Debug + 自动重载
rm .debug        # 关闭（默认）
```

日常运行请保持关闭：Flask 的 reloader 会 fork 出子进程，导致 Playwright 长任务被打断、日志重复打印、端口残留。

## 数据库位置

数据库默认是项目根目录下的 `data.db`（SQLite），路径在 `config.py` 中定义，可用环境变量 `DB_PATH` 覆盖（Docker 部署即通过它指向挂载目录）。应用启动时会调用 `init_db()` 自动建表和补齐字段，无需手动执行迁移脚本。

```bash
sqlite3 data.db ".schema data"   # 查看表结构
```
