# 第一次公开部署：只开放免费样例

当前 GitHub 仓库：https://github.com/doubleadd111/ai-learning-compass

2026-09-26 已部署公开样例：https://ai-learning-compass-g4awbdngukgkydhe5rzcnp.streamlit.app/ 。首页截图与 HTTP 200 已确认。用户随后在公开页面完成每日记录、第一周复盘及计划调整，下载 JSON 后在新标签页导入，页面恢复“已完成 2/6 个学习日”。这是手动验收记录，不代表已完成跨浏览器、移动端或全量模型质量评测。以下步骤保留供重新部署或练习。

同日补充自动回归：用全新公开会话导入已复盘的计划，验证完成 2/6 天、延期任务、笔记和复盘均恢复；本机完整测试 39 项通过。文件上传控件的真实浏览器路径由上述手动验收覆盖。

本次先上线无需 API 的样例，不填写 `DEEPSEEK_API_KEY`、邀请码或数据库连接。公开访客可以完成每日记录、复盘和 JSON 导出导入；真实生成暂时关闭，不会产生模型调用费用。

1. 打开 [Streamlit Community Cloud](https://share.streamlit.io/)，用 GitHub 账号 `doubleadd111` 登录。如果页面要求连接 GitHub，按页面说明自行授权；不要把授权码或密钥发给他人。
2. 点击 **Create app**，选择从现有 GitHub 仓库部署。填写：

   | 字段 | 填写内容 |
   | --- | --- |
   | Repository | `doubleadd111/ai-learning-compass` |
   | Branch | `master` |
   | Main file path | `app.py` |
   | App URL | 可留空，让平台生成地址 |

3. 在 **Advanced settings** 中选择 Python 3.11 或 3.12。Secrets 可以留空，因为应用默认进入免费样例模式；若想写明运行模式，只填写 `APP_MODE = "demo"`。**不要把本机 `.env` 整份粘贴进去。**
4. 点击 **Deploy**，等待部署完成。打开平台生成的 `*.streamlit.app` 地址，确认首页出现两周 Python 样例，且“生成我的学习计划”按钮不可用（真实生成未开放）。
5. 将第一周三天标记为“已完成”或“延期”，完成一次复盘，下载 JSON，再尝试导入。检查无报错后，将实际公开链接加入 README 和简历。

如果部署失败，打开应用的 **Manage app → Logs**，记录最上面的错误信息；不要截图包含密钥的 Secrets 页面。

之后要开放受邀请码保护的真实生成，先按 README 准备 PostgreSQL 配额表，再在云端 Secrets 中配置 `DEEPSEEK_API_KEY`、`DEMO_INVITE_CODE`、`DEMO_DATABASE_URL` 和 `DEMO_DAILY_LIMIT`。没有这些配置时，免费样例仍可正常运行。
