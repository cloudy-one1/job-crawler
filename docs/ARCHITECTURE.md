# 架构说明

job-crawler 采用**单向四层架构**：`data → analysis → modeling → agent`，`app.py` 作为展示层调用上层。

**硬约束：依赖只能自上而下，下层模块禁止引用上层。** 这条约束保证了统计口径可独立复用（例如 `modeling/salary_predict.py` 直接复用 `analysis/jobtitle.classify()`，而不需要把逻辑复制一遍）。

```
templates/ + app.py     展示层：路由、渲染、安全、缓存
        ↓
agent/                  Agent 层：LLM 调用与 10 个工具函数
        ↓
modeling/               建模层：聚类、分类、相似度、曲线
        ↓
analysis/               分析层：描述性统计与口径定义
        ↓
data/                   数据层：采集、解析、清洗
        ↓
config.py + data.db     基础设施
```

## 分层职责

| 层 | 目录 | 职责 | 关键模块 |
|----|------|------|----------|
| 数据层 | `data/` | 采集与清洗，产出标准化记录 | `python_job_scraper.py`、`salary_parser.py`、`exper_parser.py` |
| 分析层 | `analysis/` | 描述性统计，定义统一口径（城市提取、职位分类） | `xinzi.py`、`xueli.py`、`jinyan.py`、`region.py`、`cross.py`、`jobtitle.py`、`wordcloud_gen.py` |
| 建模层 | `modeling/` | 机器学习与多维分析 | `job_clustering.py`、`salary_classifier.py`、`skill_heatmap.py`、`job_similarity.py`、`salary_curve.py`、`edu_premium.py`、`salary_predict.py` |
| Agent 层 | `agent/` | LLM 调用封装与工具函数 | `agent_core.py`、`agent_tools.py`、`resume_parser.py` |
| 展示层 | `app.py`、`templates/`、`static/` | 路由、页面、图表、主题、动效 | — |

## 数据模型

数据落在项目根目录的 `data.db`（SQLite），核心表为 `data`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `post` | TEXT | 职位名称 |
| `company` | TEXT | 公司名称 |
| `address` | TEXT | 城市/地区原始文本 |
| `salary_min` / `salary_max` | REAL | 薪资区间，单位**千元/月**（由 `salary_parser` 归一化） |
| `dateT` | TEXT | 发布日期 |
| `edu` | TEXT | 学历要求 |
| `exper` | TEXT | 经验要求原始文本（51job `workYearString`），统计时按 `data/exper_parser.py` 归一为 5 档 |
| `content` | TEXT | 岗位描述原文 |
| `keywords` | TEXT | 51job 官方 jobTags，词云与技能分析的输入 |
| `job_url` | TEXT | 原文链接 |

应用启动时 `init_db()` 会自动建表并为旧库补齐 `keywords`、`job_url` 字段，无需手动迁移。

## 数据流

```
1. 采集   /collect 或命令行  →  data/ 解析清洗  →  整表替换写入 data.db
2. 统计   路由首次访问        →  analysis/*     →  ECharts 所需 JSON
3. 建模   /ml 首次访问        →  modeling/*     →  joblib 持久化到 cache/
4. 解读   用户点击 AI 按钮    →  agent/*        →  服务端缓存后返回
```

采集采用**整表替换**语义：命令行入口与 `/collect` 入口行为一致，避免两条入口产出不同版本的数据。

## 关键设计决策

**1. 重型依赖懒加载**

`sklearn` / `jieba` / `pandas` 的导入开销在秒级。这些模块在路由首次访问时才 import，应用启动保持在亚秒级。新增功能时不要把重型库提到模块顶层。

**2. 两级缓存**

- 数据级：聚类模型 joblib 落盘到 `cache/`，重启免重训
- 结果级：AI 解读结果按 TTL 缓存（图表 5 分钟、建模 2 分钟），避免重复调用 LLM

所有模块级缓存变量集中声明在 `app.py` 的「模块级状态」区块，禁止散落到各处。

**3. 预热机制**

首页静默调用 `/api/warmup` 预计算图表与模型，用户后续点开页面即秒开。该接口为纯前端 AJAX，豁免 CSRF。

**4. 数据库连接复用**

连接挂在 Flask `g` 对象上，请求生命周期内复用，`teardown_appcontext` 自动关闭，避免连接泄漏。启动时开启 WAL 模式，读写并发不互相阻塞。

**5. 安全边界**

| 机制 | 实现 |
|------|------|
| CSRF | Flask-WTF 全站校验，仅 `/api/warmup`、`/advice/compare/analyze`、`/advice/active-tab` 三个纯前端 AJAX 接口豁免 |
| 限流 | Flask-Limiter：全局 200/天、50/小时，`/collect` 单独 5/小时 |
| 输入校验 | URL 数值参数做正整数校验，越界返回 400；分页链接统一走 `url_for()` 编码 |
| Agent 工具调用 | 工具参数由代码预定义后直接调用，不经 LLM 动态传参 |
| 信息脱敏 | 前端异常提示不暴露内部路径与堆栈 |

**6. 容错设计**

空数据库首次启动不会崩溃，所有页面给出「请先采集数据」的友好提示，无需预先准备数据。

**7. 字段口径统一在数据层定义**

`data/salary_parser.py` 归一薪资，`data/exper_parser.py` 归一经验。库里存原始文本（51job 的措辞会变），分析层与建模层共用同一份映射，不要在下层再抄一份。

`exper_parser` 取文本中的最低年限归档：51job 同时使用「3-5年」区间式与「3年及以上」下限式两种写法，按下限归一才能让它们在同一档比较。

**8. 前端动效层不引入构建流程**

`static/fx.js` + `static/fx-motion.css` 以零依赖 vanilla 方式实现全站交互动效（滚动入场、磁吸按钮、粒子场、ECharts 入场、解码式 AI 正文），缓动令牌统一为 `cubic-bezier(.22, 1, .36, 1)`。三条硬约束：

- 动效类一律由 JS 注入，禁用 JS 时内容照常完整可见；`prefers-reduced-motion: reduce` 下全部降级为静态终态。
- JS 只写 `--fx-*` 自定义属性，transform 的组合权留在样式层，避免与米色 / 深色两套主题的 hover 规则互相覆盖。
- 每个模块独立 try/catch，任一模块抛错不得影响页面内容与其余模块。

新增页面只需继承 `base.html` 即自动获得动效；需要粒子场的页面放 `<canvas data-fx-field>`，需要逐字解码的标题加 `data-fx-decode`。

## 扩展指引

**新增一个统计维度**：在 `analysis/` 新增函数 → 在 `app.py` 的图表路由中注册 → 在 `templates/h.html` 增加区段 → 补测试。

**新增一个 Agent 工具**：在 `agent/agent_tools.py` 实现函数并注册进 `TOOLS` 表 → 参数由调用方显式传入 → 补测试。注意工具函数应基于 `analysis/` / `modeling/` 的既有口径，不要另起炉灶。

**新增一个页面**：`templates/` 下新建模板继承 `base.html` → `app.py` 增加路由 → 若为 POST 表单必须加 CSRF token。
