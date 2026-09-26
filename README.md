# 学习罗盘：自适应 Python 学习教练

面向零基础 Python 学习者：生成学习计划，记录每天是否完成、实际用时和难度；每周复盘后，根据延期任务与学习反馈调整后续计划。

**公开体验：**[打开无需 API 的两周 Python 样例](https://ai-learning-compass-g4awbdngukgkydhe5rzcnp.streamlit.app/)。真实生成暂未开放，页面中的生成按钮因此处于禁用状态。

目前本机模式可保存多份计划。公开演示模式有一份无需 API 的两周样例；真实生成需要服务器端邀请码和每日总额度。

## 功能一览

- **生成计划**：输入目标、基础、周数、每周天数、每天分钟数；DeepSeek 返回结构化 JSON，Pydantic 校验周次、任务数和时间上限，失败时修正重试一次。
- **每日记录**：保存完成情况、实际用时、难度与笔记；本机使用 SQLite，重启后仍能继续。
- **每周复盘**：必须先把本周任务标记为“已完成”或“延期”。延期任务优先进入下周；完成率低于 60% 或平均难度不低于 4 时，后续周次单日任务上限降低至原来的 80%（最低 15 分钟）。模型编写其余任务，程序再次校验上限。
- **导出与导入**：下载 JSON 保存计划和记录；文件不包含 API Key。公开演示模式中的个人计划仅保留在当前浏览器会话，建议离开前下载。
- **质量评测**：30 组 Python 入门场景；默认只检查评测输入，显式加 `--live` 才调用模型并生成报告。

## 在 Windows 本机运行

需要 Python 3.11 或更新版本。从项目目录执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

在 `.env` 中填写自己的 `DEEPSEEK_API_KEY`。不要把密钥发给别人或提交到 Git。然后启动：

```powershell
.\.venv\Scripts\streamlit.exe run app.py
```

浏览器打开终端显示的本机地址。PowerShell 窗口运行着 Streamlit 服务；按 `Ctrl+C` 停止。

计划与每日记录默认保存在 `.learning_compass/plans.sqlite3`，该目录已被 Git 忽略。可以设置 `STUDY_DB_PATH` 指定其他本机路径。

## 不使用 API 的公开样例模式

在 PowerShell 中运行：

```powershell
$env:APP_MODE = "demo"
.\.venv\Scripts\streamlit.exe run app.py
```

样例会直接出现。把第一周的三天标记为“已完成”或“延期”，点击“生成本周复盘并调整后续计划”即可看到规则调整。此流程不调用模型。关闭标签页或重启服务器后，样例记录可能消失；可先下载 JSON，之后再导入。

## 测试与评测

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m evaluation.run
```

第一条运行单元与 Streamlit 界面交互测试，不使用真实 API；第二条检查 30 组输入，也不使用真实 API。

想测试真实模型时，先估算自己的 API 预算，再明确指定场景数：

```powershell
.\.venv\Scripts\python.exe -m evaluation.run --live --limit 1
```

想先看覆盖不同限制的小样本，可免费预览：

```powershell
.\.venv\Scripts\python.exe -m evaluation.run --pilot
```

确认愿意支付 API 调用费用后，才加 `--live --pilot`：它会测 4 组代表性场景（短时、普通项目、每周集中一天、英语限制），每组格式不合规或连接失败时最多重试一次。评分方法见[质量评测说明](docs/evaluation-guide.md)。

真实评测将报告写到 `.learning_compass/evaluations/`：JSON 包含约束通过率、响应时间、token 用量和错误类别；CSV 留出任务可执行性、先修顺序的人工评分栏。最多可以运行 30 组。未经真实运行与人工评分，不应在简历上声称这些指标已达标。

仓库中保留了[一次真实冒烟评测](docs/evaluation-sample.json)：1 个场景约束校验通过，耗时 3.744 秒，输入 819 / 输出 686 token。这不是 30 组场景的总体结论，任务质量也尚未完成人工评分。

## 公开部署准备

推荐部署到 Streamlit Community Cloud，入口文件为 `app.py`，Python 版本选择 3.11 或更新版本。公开应用设置 `APP_MODE="demo"`；即使忘记设置，默认也是仅按会话保存的样例模式，不会启用本机 SQLite。未配置真实生成时，样例可以独立运行。

若要开放受限真实生成：

1. 准备托管 PostgreSQL，在其 SQL 控制台执行 [`deploy/demo_quota.sql`](deploy/demo_quota.sql)。
2. 在云平台的 **Secrets** 中设置 `DEEPSEEK_API_KEY`、`APP_MODE="demo"`、`DEMO_INVITE_CODE`、`DEMO_DATABASE_URL`、`DEMO_DAILY_LIMIT`。不要提交这些真实值。数据库连接必须使用 TLS；额度统计在 PostgreSQL 中原子更新，数据库不可用时不放行请求。
3. 先用少量邀请码测试额度达到上限时是否拒绝；公开页面只提供样例，把邀请码单独给需要试用真实生成的人。

参见 [Streamlit 部署说明](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)和[密钥管理](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)。云端数据库、邀请码和 API 调用可能产生费用，具体额度需以服务商当前规则为准。

第一次发布可按[逐项填写清单](docs/deploy-checklist.md)先上线无需 API 的公开样例，确认完整流程后再决定是否开放真实生成。

## 项目结构与学习点

| 位置 | 作用 | 对应知识 |
| --- | --- | --- |
| `app.py` | 页面、表单、每日记录与复盘入口 | Streamlit、会话状态 |
| `models.py`、`curriculum.py` | 计划数据结构和 Python 入门主题 | Pydantic、数据建模 |
| `planner.py`、`weekly_review.py` | 调用模型、校验、规则调整 | API、结构化输出、约束校验 |
| `progress_store.py`、`session_store.py` | 本机数据库与公开会话数据 | SQLite、持久化、隔离 |
| `plan_transfer.py`、`demo_quota.py` | 导入导出和公开额度控制 | JSON、输入校验、数据库原子更新 |
| `evaluation/`、`tests/` | 评测与自动测试 | Mock、回归测试、指标 |

架构与数据流见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。
录制公开演示时可参考[两分钟演示脚本](docs/demo-script.md)；当前尚无已发布视频。

## 常见问题

- **未找到 API Key**：检查 `.env` 是否和 `app.py` 同目录、变量名是否为 `DEEPSEEK_API_KEY`，保存后重启应用。
- **为什么公开样例不能随意真实生成**：每次真实生成消耗项目作者的 API 额度；公开样例无需调用模型。
- **为什么关闭网页后记录不见了**：公开模式只在当前会话保存数据，离开前下载 JSON；本机模式使用 SQLite，可以重启后继续。
- **计划不符合时长**：程序会重试并再次校验；仍失败会显示可理解的错误，不会把不合规计划保存下来。
