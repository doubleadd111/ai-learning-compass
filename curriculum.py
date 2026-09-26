"""零基础 Python 学习计划的主题顺序与可检查产出。"""

PYTHON_FOUNDATIONS: tuple[tuple[str, str], ...] = (
    ("运行代码与 print", "能独立运行一个打印文字的 .py 文件"),
    ("变量、基本类型与输入", "能读取输入并输出计算结果"),
    ("条件判断", "能编写有两个以上分支的小程序"),
    ("循环", "能用循环处理一组数据"),
    ("函数", "能把重复代码拆成可复用函数"),
    ("列表与字典", "能保存并查询多条记录"),
    ("文件与异常处理", "能读写本地文本并处理常见错误"),
    ("综合小项目", "能完成、运行并说明一个命令行小工具"),
)


def curriculum_prompt() -> str:
    return "\n".join(
        f"{index}. {topic}：{deliverable}"
        for index, (topic, deliverable) in enumerate(PYTHON_FOUNDATIONS, 1)
    )
