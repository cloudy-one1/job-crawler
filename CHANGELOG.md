# 更新日志

本项目所有值得记录的变更都会写在这里，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **采集落库改造：整表替换 → 增量合并**。新增 `data/job_store.py` 作为全项目唯一落库口径：按 `job_url` upsert（重复采集刷新既有记录而非重复插入），同「职位+公司+城市」但不同 URL 的重复挂载收敛到已有行（存量库曾有 8 组此类重复），「面议」等无法解析薪资的记录直接排除（历史行为是落库为 (0,0)，曾拉偏均值）。每条记录新增 `collected_at` 时间戳，多次采集自然形成多期数据，为路线图「岗位时间序列 / 薪资趋势」打好口径。命令行采集与网页 /collect 共用同一实现；存量库一次性清理 3 条面议 + 8 条重复（960 → 949），旧库已备份至 `backups/data.db.pre-cleanup`。
- **职位描述（content）进入薪资分类器特征**。此前特征只有城市/学历/经验/岗位类别/jobTags 技能词，库里 949 条职位描述全文未被建模利用。新增 `CONTENT_SKILL_PATTERNS` 词典（50 条中英文正则）从描述抽取技能，与 jobTags 汇入同一词表做 one-hot；缓存结果包带 `pkg_version=2`，旧缓存自动识别重训。
- **薪资档位有序指标**。档位是有序标签，除 accuracy / macro-F1 外新增 `mae_bands`（预测平均偏几档）与 `adjacent_acc`（偏差 ≤1 档的比例），/ml 页指标卡同步展示；GradientBoosting 通过派生类补齐 class_weight='balanced' 语义（原生不支持该参数），三候选模型在不均衡分布下可比。
- **聚类簇判别性命名**。auto_label 由「簇中心 top3 词 '/' 连接」升级为 c-TF-IDF 思路：每簇平均 TF-IDF 减全局平均，取显著高于其他簇的词以「·」连接，避免各簇都有的通用词充当方向名；聚类缓存带 `label_version=2` 自动重算。
- 图表页薪资分布新增数据驱动的离群说明：最高薪资 ≥10 万/月时标注该岗位与公司，说明属可解释高阶岗位而非脏数据。
- 测试新增 `tests/test_job_store.py`（11 例：schema 迁移幂等、同 URL 更新、三元组收敛、面议排除、collected_at 写入/刷新等），分类器测试补 content 抽取与有序指标断言，用例 301 → 318。
- 测试夹具补充「X年及以上」「无需经验」「在校生/应届生」等线上真实写法（10 → 16 条），并新增 `tests/test_exper_parser.py` 固化库中出现过的全部 36 种经验写法的映射；用例 250 → 301。此前夹具只用旧口径，故 250 条全绿仍漏掉了这次的数据漂移。
- **前端交互与动效层**（参考 [ThreeUI](https://github.com/MengTo/threeui) 的动效语汇，按零依赖方式移植）：新增 `static/fx.js` + `static/fx-motion.css`，提供滚动入场（IntersectionObserver 同容器交错）、指针磁吸按钮与卡片跟随光斑 / 倾斜、点击涟漪、数字滚动、收藏角标弹跳、导航滚动抬升与顶部阅读进度、首页 Hero 数据粒子场（Canvas 2D，随主题重算配色、离屏与后台自动暂停）。
- 图表页与薪资洞察页的 ECharts 统一注入入场动画（880ms `quinticOut`，逐项 28ms 递增 delay），AI 解读面板改为解码式逐字呈现；智能助手页 tab 切换加入场面板动画，提交遮罩换成 Uplink 式进度条。缓动令牌统一为 `cubic-bezier(.22, 1, .36, 1)`。
- 动效全部为渐进增强：类名由 JS 注入，禁用 JS 或 `prefers-reduced-motion: reduce` 时内容照常完整可见；单个模块异常被 try/catch 隔离，不影响页面其余部分。顺带修复窄窗口下固定导航换行遮挡首屏的问题（按导航实际高度同步 `body` 上边距）。
- README 新增「界面预览」小节：`docs/images/` 下 5 张本地运行截图（首页、城市分布、高频热词、薪资预测模型、深色主题），目录结构同步补 `docs/images/` 一行。

### 修复

- **经验字段口径错配**：51job 现用「3年及以上」下限式写法，而 `analysis/cross.py`、`modeling/salary_curve.py`、`modeling/salary_classifier.py` 各自维护一份「1-3年」区间式映射表，新写法无法命中而统一兜底成「经验不限」——「薪资 × 经验」交叉图 849/960（88.4%）、分类器特征 551/960（57.4%）被错误归档。新增 `data/exper_parser.py` 作为全项目唯一口径（按文本最低年限归入 5 个有序档位），三处消费方改为共用，库里仍保留原始文本以便口径再变时无需重采。
- `modeling/salary_predict.py` 的经验筛选改用同一档位口径，按「3-5年」筛选现能命中写作「3年及以上」的岗位。
- 薪资档位分类器随新口径重训：test accuracy 0.422 → 0.438，macro-F1 0.361 → 0.386（同一数据、同一划分）。

## [1.0.0] - 2026-07-14

首个正式版本，覆盖「采集 → 清洗 → 分析 → 建模 → AI 建议 → 可视化」完整链路。

### 新增

**数据采集与清洗**

- 基于 Playwright + playwright-stealth 的 51job Python 岗位采集，支持多城市 × 5 页上限，随机延时与 UA 轮换对抗 WAF
- 薪资字符串解析器：支持 `1.5-2万/月`、`15-25万/年`、`300元/天` 等常见写法，统一归一化为千元数值

**描述性统计**

- 薪资分段（8 档）、学历分布、经验分布、城市分布统计
- 交叉分析：薪资 × 经验、薪资 × 学历
- 职位标题规则分类（40+ 行业、100+ 规则、5 层优先级）
- 技能词云：基于 51job 官方 jobTags，含同义词归一化

**机器学习建模**

- KMeans 岗位聚类：TF-IDF 技能向量 + 自动 k 选择 + 轮廓系数评估，模型 joblib 持久化
- 薪资档位分类模型：RandomForest / GradientBoosting / Logistic 三模型交叉验证选优
- 技能供需热力图、岗位相似度网络、经验-薪资成长曲线、学历溢价分析

**AI 能力**

- DeepSeek 主模型 + 通义千问 fallback，指数退避重试
- 10 个 Agent 工具：数据查询、技能需求、城市/学历/经验概览、城市对比、薪资预测、岗位匹配、简历审查
- 图表页 6 区段与建模页 5 维度的 AI 解读（服务端缓存）
- 简历解析（PDF / DOCX）与六维度岗位匹配推荐

**Web 与可视化**

- Flask 服务端渲染 10 个页面，ECharts 图表，首页静默预热机制
- 米色 / 深色双主题一键切换，ECharts 色板自适应
- 岗位收藏清单（session 存储，零数据库迁移）

### 安全

- 全站 CSRF 防护（Flask-WTF），仅 3 个纯前端 AJAX 接口豁免
- `/collect` 限流 5 次/小时，全局 50 次/小时
- 可选 `COLLECT_TOKEN` 采集口令
- `FLASK_SECRET` 环境变量注入，未设置时随机生成
- 数据库 WAL 模式；URL 参数校验与 `url_for()` 编码，杜绝拼接注入
- 前端异常信息脱敏，不泄露内部路径与堆栈

[1.0.0]: https://github.com/cloudy-one1/job-crawler/releases/tag/v1.0.0
