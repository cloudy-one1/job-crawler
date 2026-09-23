# 更新日志

本项目所有值得记录的变更都会写在这里，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **前端交互与动效层**（参考 [ThreeUI](https://github.com/MengTo/threeui) 的动效语汇，按零依赖方式移植）：新增 `static/fx.js` + `static/fx-motion.css`，提供滚动入场（IntersectionObserver 同容器交错）、指针磁吸按钮与卡片跟随光斑 / 倾斜、点击涟漪、数字滚动、收藏角标弹跳、导航滚动抬升与顶部阅读进度、首页 Hero 数据粒子场（Canvas 2D，随主题重算配色、离屏与后台自动暂停）。
- 图表页与薪资洞察页的 ECharts 统一注入入场动画（880ms `quinticOut`，逐项 28ms 递增 delay），AI 解读面板改为解码式逐字呈现；智能助手页 tab 切换加入场面板动画，提交遮罩换成 Uplink 式进度条。缓动令牌统一为 `cubic-bezier(.22, 1, .36, 1)`。
- 动效全部为渐进增强：类名由 JS 注入，禁用 JS 或 `prefers-reduced-motion: reduce` 时内容照常完整可见；单个模块异常被 try/catch 隔离，不影响页面其余部分。顺带修复窄窗口下固定导航换行遮挡首屏的问题（按导航实际高度同步 `body` 上边距）。

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
