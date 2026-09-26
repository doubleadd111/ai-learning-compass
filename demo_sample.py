"""公开体验使用的固定样例，不消耗模型额度。"""

from models import DailyTask, LearningProfile, StudyPlan, WeeklyPlan


def sample_learning_plan() -> tuple[LearningProfile, StudyPlan]:
    profile = LearningProfile(
        goal="从零学习 Python，并完成一个命令行待办工具",
        current_level="零基础",
        plan_weeks=2,
        days_per_week=3,
        minutes_per_day=45,
        preferences="先动手，再补概念；每天保留一个可检查的文件",
    )
    plan = StudyPlan(
        title="两周 Python 起步样例",
        overview="从运行脚本开始，用短练习熟悉变量、判断、循环和函数。",
        weekly_plans=[
            WeeklyPlan(
                week_number=1,
                theme="让 Python 代码跑起来",
                milestone="能运行脚本，并用变量和条件完成一个小练习。",
                tasks=[
                    DailyTask(day=1, title="运行第一段代码", description="安装 Python，运行输出问候语的脚本。", duration_minutes=30, deliverable="hello.py"),
                    DailyTask(day=2, title="使用变量与输入", description="读取名字和学习时长，输出一段格式化说明。", duration_minutes=35, deliverable="profile.py"),
                    DailyTask(day=3, title="写一个条件判断", description="按输入的学习分钟数给出两种不同提示。", duration_minutes=40, deliverable="advice.py"),
                ],
            ),
            WeeklyPlan(
                week_number=2,
                theme="把重复操作交给程序",
                milestone="能用循环和函数组织一个简单的命令行小工具。",
                tasks=[
                    DailyTask(day=1, title="循环显示任务", description="用列表保存任务，再用循环逐条显示。", duration_minutes=35, deliverable="list_tasks.py"),
                    DailyTask(day=2, title="拆分添加任务函数", description="编写添加任务的函数，并在脚本中调用。", duration_minutes=40, deliverable="add_task.py"),
                    DailyTask(day=3, title="合并待办小工具", description="把添加与显示任务合并，运行三个手动测试。", duration_minutes=45, deliverable="todo.py 和三条测试记录"),
                ],
            ),
        ],
        learning_tips=[
            "运行代码后记下一个遇到的错误及处理办法。",
            "每周末检查自己能否独立解释脚本中的每一行。",
        ],
    )
    return profile, plan
