# 桌面版架构

版本：2026.10.0。桌面与原有 Streamlit 入口并存，不共享用户数据库。

```text
run_desktop.py → desktop/main.py（单实例、应用生命周期）
                     ↓
             desktop/window.py / dialogs.py（五页界面）
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
- 稳定任务 ID 关联进度、提醒与专注记录。调整保留已开始或手动修改的未来任务，剩余任务重新安排；版本号防止覆盖预览期间的新记录。
- 恢复前检查 SQLite 完整性、版本和关键字段，建立恢复点，再替换数据库。计划 JSON 导入会创建新 ID，避免覆盖已有记录。

## 可复现检查

安装开发依赖后运行 `python -m pytest -q`。Qt 测试不调用付费服务；网关测试使用本机 HTTP Mock 验证真实 SDK 序列化。

`scripts/build_desktop.py` 生成目录程序、便携 ZIP、安装包、依赖清单和 SHA-256；检查构建期间源码没有变化。`scripts/verify_desktop.py` 移除外部 Python 环境并检查冻结启动、重启和单实例。`scripts/verify_installer.py` 使用独立目录检查按用户安装、覆盖安装及卸载保留数据；检测到已有正式安装时会拒绝覆盖。

构建采用目录模式，以便替换 Qt 动态库。已有开发环境的 ICU DLL 不应遮盖 Windows 系统 ICU；此冲突已纳入打包过滤。干净 Windows 电脑、真实服务商和操作系统通知的人工验收边界见 [验收记录](desktop-verification.md)。
