"""Searchable, fixed view/form routes. Search results never execute mutations."""
FEATURES=(
    ('today','今日与本周安排','点击日期卡片查看当天任务与预算。','今天 本周 日期 日程 每日 安排 待办'),
    ('library','计划库','查看计划、独立任务和已完成任务。','计划 任务箱 历史 完成 归档'),
    ('week','周视图','查看七日周历和明细表。','周历 星期 任务明细 日历'),
    ('focus','专注计时','打开专注页，不自动开始计时。','番茄钟 计时器 倒计时 学习时间'),
    ('month','月视图与拖动改期','打开月历，拖动后仍需预览确认。','月历 月份 排期 拖动 日期'),
    ('manual','手动添加任务','打开添加任务表单，确认保存前不创建任务。','新建 手动任务 独立任务 添加'),
    ('capture','快捷录入','先记下一件事，稍后再安排。','快速 记录 收集 ctrl+shift+n'),
    ('inbox','待安排收集箱','整理尚未安排日期的事项。','收件箱 草稿 快速记 录入'),
    ('batch','批量管理任务','打开选择窗口，批量改期、分组或完成前需预览。','多选 批量 日期 完成 删除 分组'),
    ('schedule','调整排期','选择任务，预览补排或改到指定一天。','改期 补排 顺延 调整 日期 时间'),
    ('overdue','安排逾期任务','打开逾期任务的补排窗口。','逾期 延期 过期 未完成 重新安排'),
    ('pause','计划暂停与恢复','到计划库选中计划，再从任务安排操作。','暂停 恢复 学习 停止 继续'),
    ('steps','任务分组与执行步骤','到计划库选择任务，再整理分组和步骤。','阶段 子任务 清单 分组 步骤 拆分'),
    ('dependencies','设置前置任务','到任务详情的整理菜单设置前置关系。','依赖 前置 顺序 先做 后做'),
    ('resources','任务资料与成果关联','到任务详情的资料与成果页添加引用。','关联 文件 链接 附件 教材 网址 成果 产出'),
    ('review','每周复盘','到计划库选择计划，核对记录后生成复盘。','复盘 总结 周回顾 反馈'),
    ('history','调整历史与回溯','到计划库选择计划，查看近7天的调整。','撤销 历史 回滚 回溯 版本 git'),
    ('statistics','学习统计','按任务安排日期查看进度与累计用时。','统计 图表 预计 实际 分钟'),
    ('actual','实际学习日历','按真实学习日期查看记录和趋势。','实际 日历 学习时长 连续 每日记录'),
    ('recurring','周期任务与复习','打开有限周期任务及间隔复习工具。','重复 周期 间隔复习 记忆 复习'),
    ('reminders','提醒中心','查看待办、逾期和错过提醒。','通知 稍后 静音 提醒'),
    ('trash','回收站','查看误删任务和计划；恢复前仍需确认。','误删 恢复 删除 找回 彻底删除'),
    ('backups','备份管理','查看备份类型、版本、大小与恢复差异。','备份 自动 手动 还原 恢复 数据库'),
    ('cleanup','备份空间与清理','打开清理预览窗口，不自动删除备份。','清理 删除 空间 磁盘 容量 保留规则'),
    ('drafts','继续计划草稿','查看尚未完成的计划草稿。','草稿 继续 需求 历史'),
    ('models','模型连接设置','打开模型配置页，不测试连接或调用模型。','模型 api key 密钥 服务商 DeepSeek 连接'),
    ('appearance','外观与提醒设置','查看主题、托盘、开机启动与快捷键设置。','主题 浅色 深色 提醒 快捷键 开机启动 托盘'),
    ('budget','学习时间预算','查看周一至周日的跨计划总预算。','预算 时间 每日 容量 超限 上限'),
    ('data','数据导入与备份设置','打开JSON及旧版数据导入入口，不自动导入。','导入 导出 json sqlite 迁移 数据目录'),
    ('guide','使用指南','打开离线指南，可搜索使用方法。','帮助 说明 使用教程 f1 指南'),
)


def open_feature(host,key):
    """Only explicit routes; never getattr or execute a query/result string."""
    navigation={'today':0,'library':1,'week':2,'focus':3}
    if key in navigation:host.navigate(navigation[key]);host.reveal();return
    settings={'models':0,'appearance':1,'budget':2,'data':3}
    if key in settings:host.navigate(4);host.settings_tabs.setCurrentIndex(settings[key]);host.reveal();return
    routes={'month':host.open_month,'manual':host.add_manual_task,'capture':host.quick_capture,'inbox':host.open_capture_inbox,
        'batch':lambda:host.open_batch(host.today_tree),'schedule':host.open_schedule,'overdue':lambda:host.open_schedule(overdue=True),
        'statistics':host.open_statistics,'actual':host.open_actual_statistics,'recurring':host.open_recurring,
        'reminders':host.open_reminders,'trash':host.open_trash,'backups':host.open_backups,'drafts':host.resume_draft}
    if key in routes:routes[key]();return
    if key=='cleanup':
        from desktop.backup_space_ui import CleanupDialog
        host.tools.open('backup_cleanup',lambda:CleanupDialog(host));return
    if key=='guide':
        from desktop.guide import open_guide
        open_guide(host);return
    hints={
        'pause':'选中一份计划 → 任务安排 → 暂停计划／恢复学习。',
        'steps':'选中计划或独立任务箱 → 任务安排 → 任务分组与执行步骤。',
        'dependencies':'双击一项任务 → 整理任务 → 设置前置任务。',
        'resources':'双击一项任务 → 资料与成果 → 添加关联。',
        'review':'选中计划 → AI辅助 → 生成本周复盘；本次只定位，不发出请求。',
        'history':'选中计划 → 历史与数据 → 调整历史与回溯。',
    }
    if key not in hints:raise ValueError('功能入口已不存在，请刷新搜索结果')
    host.navigate(1);host.notice.setText(hints[key]);host.reveal()
