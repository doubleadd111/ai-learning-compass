# 学习罗盘：AI 学习计划生成器

一个面向零基础学习者的中文 Web 应用。填写目标、基础和可投入时间后，它会生成一份包含每周里程碑、每日任务与可检查产出的学习计划。

> 首版只在本机运行，不保存计划、不需要登录，也不会上传文件。

## 你会学到什么

- **Python 项目结构**：把网页入口、数据模型、模型调用和测试拆分到不同文件。
- **大模型 API 调用**：通过 OpenAI 兼容的 Python SDK 调用 DeepSeek Responses API。
- **提示词设计**：将学习周期、可用时间和偏好写成模型必须遵守的约束。
- **结构化输出**：要求模型按 JSON Schema 返回数据，再用 Pydantic 验证，避免界面依赖一大段不稳定文本。
- **错误处理与测试**：API Key、网络和模型输出异常均给出可理解的提示；测试不调用真实 API。

## 准备环境（Windows）

1. 安装 [Python 3.11](https://www.python.org/downloads/)，安装时勾选 **Add Python to PATH**。
2. 在项目目录打开 PowerShell，创建并激活虚拟环境：

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. 安装依赖：

   ```powershell
   pip install -r requirements.txt
   ```

## 配置 DeepSeek API Key

1. 复制 `.env.example` 并重命名为 `.env`。
2. 打开 `.env`，填写你的 DeepSeek API Key：

   ```env
   DEEPSEEK_API_KEY=你的真实密钥
   ```

`.env` 已被 `.gitignore` 排除，**不要**把真实密钥放进代码或提交到 Git。

## 启动

```powershell
streamlit run app.py
```

浏览器会打开本地页面。填完表单后点击“生成我的学习计划”；生成结果底部的代码块可一键复制为 Markdown。

## 运行测试

```powershell
pytest
```

测试会验证表单边界、时间限制、JSON 解析，以及模型返回非 JSON 时自动修正重试一次的行为，不会消耗 API 额度。

## 项目结构

```text
app.py          # Streamlit 中文界面与 Markdown 导出
models.py       # 表单与学习计划的 Pydantic 数据模型
planner.py      # OpenAI Responses API、Schema 与二次校验
tests/          # 不调用真实 API 的单元测试
.env.example    # 密钥配置模板
```

## 常见问题

**提示“未找到 DeepSeek API Key”怎么办？** 检查 `.env` 是否和 `app.py` 位于同一目录，变量名必须为 `DEEPSEEK_API_KEY`；修改后重启 Streamlit。

**计划比我的时间长怎么办？** 应用会校验每项任务与每周总时长，并让模型自动修正一次。仍不合适时，把每天时长或偏好写得更具体，再重新生成。

**为什么要返回 JSON？** 界面需要知道“第几周、哪一天、花多久、完成什么”，结构化字段比从一整段自然语言中猜测更可靠。DeepSeek Responses API 支持 JSON Schema 格式输出。[官方文档](https://api-docs.deepseek.com/zh-cn/api/create-response/)
