# 桌面版架构

版本：2026.10.4 本机测试版，尚未正式发布。桌面与原有 Streamlit 入口并存，不共享用户数据库。

```text
run_desktop.py → desktop/main.py（单实例、应用生命周期）
                     ↓
             desktop/window.py / dialogs.py（五页界面）
               ├─ guide.py → 同版本Markdown（离线独立使用指南）
               ├─ planning_ui.py → planning.py（草稿状态、有界问答、概略、修改提案）
               ├─ workers.py → gateway.py → 用户选择的模型服务
               │                   └─ Pydantic + 时间约束校验
               ├─ credentials.py → Windows 凭据管理器 / 内存
               ├─ review.py → 延期与难度规则 → 调整预览
               ├─ focus.py → 单调时钟、暂停、幂等累计
               └─ store.py → 本机 SQLite、每日备份、恢复点
```

## 数据边界

- 普通资料存入 `%LOCALAPPDATA%\LearningCompass`。安装目录只包含程序与动态运行库；升级不迁移或清空学习资料。
- 模型连接只保存服务地址、模型和非敏感选项。密钥按连接 ID 保存在 Windows 凭据管理器；不可用时回退到内存。不读取源码目录的 `.env`。
- 每次后台请求持有启动时的配置快照，后续切换不改动进行中的请求。取消后不应用结果；服务端已收到的请求仍可能计费。
- AI 结果只有通过本地结构、周数、学习日和时长校验后才能保存；最多一次修正，SDK 隐式重试关闭。
- 创建流程依次经过资料、最多两轮追问、可展开概略、每日任务预览与保存。各阶段使用结构化返回；只有明确确认才进入下一生成阶段或写入正式计划，未完成内容保存在草稿表。
- 稳定任务 ID 关联进度、提醒与专注记录。局部调整不增删任务，只允许修改白名单范围；已开始、有记录、锁定及已复盘任务受保护。改期与保留任务合并检查每日／每周容量；应用前重新核对计划revision。
- 计划需求与修改意见保存在plan_briefs，完整修改前后快照在plan_versions。仅没有后续变更的最近AI调整可撤销；大改创建关联副本，新任务ID与旧进度分离。
- 数据库schema2；旧库与旧备份先复制到临时库迁移，完整校验后原子替换，升级前另建备份。计划JSON3继续接受1／2，导入创建新ID；整库备份保留草稿与完整版本历史。
- 草稿连接被删除或改址时必须显式重绑定，避免新Key发送到旧地址。Key不进入草稿、需求、版本、日志或备份。

## 可复现检查

安装开发依赖后运行 `python -m pytest -q`。Qt 测试不调用付费服务；网关测试使用本机 HTTP Mock 验证真实 SDK 序列化。

`scripts/build_desktop.py --local-test` 只生成本机目录程序，不生成ZIP或安装包；不带此参数才进行正式打包。构建检查源码摘要未变化。`scripts/verify_desktop.py` 移除外部Python环境并检查冻结启动、重启、单实例和同版本离线指南。`scripts/verify_desktop_migration.py` 对指定学习库的只读副本迁移并比对原字段摘要，不访问凭据。`scripts/verify_installer.py` 仅用于正式安装包的独立QA目录验证，不作为本轮测试补丁的发布步骤。

构建采用目录模式，以便替换 Qt 动态库。已有开发环境的 ICU DLL 不应遮盖 Windows 系统 ICU；此冲突已纳入打包过滤。干净 Windows 电脑、真实服务商和操作系统通知的人工验收边界见 [验收记录](desktop-verification.md)。
