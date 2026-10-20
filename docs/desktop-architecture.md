# 桌面版架构

版本：2026.10.11 公开试用版。桌面与原有 Streamlit 入口并存，不共享用户数据库。升级说明与累计功能见[发布说明](releases/2026.10.11.md)。

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
- 计划需求与修改意见保存在plan_briefs，完整修改前后快照在plan_versions。近7天（168小时）可预览调整前/后状态并追加回溯事件，只回放安排/设置白名单，保留当前学习记录；事务重检全局指纹，回溯前另建备份。大改创建关联副本，新任务ID与旧进度分离。
- 当前数据库schema5，包含plan_schedules、pause_history及独立手动任务；旧schema1–4及旧备份先复制到临时库迁移，完整校验后原子替换，升级前另建备份。计划JSON5继续接受旧格式，导入创建新ID；整库备份保留独立任务、草稿、预算与完整版本历史。
- scheduling.py提供纯本地排期预览与预算报告，store.write在事务内对比全局日期负荷，新增超载先回滚抛出BudgetExceeded/PlanCapacityExceeded；UI以来源指纹和负荷摘要作单次人工确认重试，其他计划或预算变化后授权失效。AI/自动排期的单计划硬上限不因此放宽。
- 排期元数据区分逻辑学习周数和实际日历结束日。补排仅改变日期，保留任务ID/记录；暂停为起日闭、恢复日开区间，到期恢复持久化一次并跳过暂停期提醒。预览全局指纹包含计划revision、归档、排期和预算，不涉及Key。
- 草稿连接被删除或改址时必须显式重绑定，避免新Key发送到旧地址。Key不进入草稿、需求、版本、日志或备份。

## 可复现检查

安装开发依赖后运行 `python -m pytest -q`。Qt 测试不调用付费服务；网关测试使用本机 HTTP Mock 验证真实 SDK 序列化。

`scripts/build_desktop.py --local-test` 只生成本机目录程序，不生成ZIP或安装包；不带此参数才进行正式打包。构建检查源码摘要未变化。`scripts/verify_desktop.py` 移除外部Python环境并检查冻结启动、重启、单实例、指南生命周期、删除保护及回溯切回。`scripts/verify_desktop_migration.py` 对指定学习库的只读副本迁移并比对原字段摘要，不访问凭据。`scripts/verify_installer.py` 以独立AppId和QA目录检查安装/覆盖升级/卸载，不能操作用户正式安装。

构建采用目录模式，以便替换 Qt 动态库。已有开发环境的 ICU DLL 不应遮盖 Windows 系统 ICU；此冲突已纳入打包过滤。干净 Windows 电脑、真实服务商和操作系统通知的人工验收边界见 [验收记录](desktop-verification.md)。
# 同版本本机补丁：手动任务与明确改期（2026-10-19）

- schema5沿用规范tasks表和其focus_sessions/notifications外键，允许NULL plan_id。manual=1的任务使用week=0、day=NULL，payload保留DailyTask(day=1)，不混入严格生成计划的逻辑学习日。独立任务不是隐藏的伪计划。
- schema1–4先备份，临时副本升级；SQLite tasks重建在暂时禁用外键的独立事务中完成，恢复外键后校验再原子替换。保留ID/记录，对已有专注且未开始任务修正状态，更新所属计划revision阻止旧预览覆盖。
- 手动任务共用任务视图/状态/提醒/专注；加入计划的手动任务参与实际日期容量与全局预算，但不参与AI改写、生成周数/天数校验或逻辑周复盘。JSON5携带manual，旧格式规范化为manual=0。
- 暂停默认只更改可用状态与历史，不重排任务；改期另由exact_date提案驱动，支持明确选择与日期。人工日期变更保护历史完成/复盘及关联计时，保留已有学习记录和AI锁；自动/AI保护独立执行。
- 全局预览指纹包含规范任务状态及内容的哈希，覆盖独立任务的变化，不输出原文。状态提示用轻量圆角专注浮窗，专注提交后原位更新，避免清空表格选中行。
- 人工指定日期与手动任务写入可启用单次PlanCapacityExceeded确认：事务先回滚，来源指纹和负荷签名一致才重试；新导入计划按稳定元数据签名，既有计划按ID。自动排期/AI仍严格校验。未新增持久超限授权或数据库版本。
- 指南打开期间为无父QWidget顶层单例，弹窗来源临时transient owner解决模态遮挡，来源关闭/隐藏后解除；关闭时WA_DeleteOnClose并清空应用/来源别名，仅保留阅读状态，避免隐藏HWND被来源销毁后复用幽灵对象。原生WM_CLOSE、来源DeferredDelete及exec真实按钮链路纳入guide_check冻结自检。专注浮窗不激活、不接收鼠标，退出前移除全应用过滤器。
- confirmations.py提供统一标题/可滚动内容/固定操作区，CapacityConfirmDialog直接读取异常结构化负荷，不依赖QMessageBox详情按钮。localization.py统一Qt标准按钮中文；UI约定见ui-conventions.md，颜色只取主题令牌，不增加依赖或数据库版本。
