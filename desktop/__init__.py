"""学习罗盘 Windows 桌面版。"""

VERSION = "2026.10.12"
DISPLAY_VERSION = VERSION


def build_label():
    import json
    import sys
    from pathlib import Path
    if not getattr(sys, "frozen", False):
        return "源码运行"
    try:
        data = json.loads((Path(sys.executable).parent / "build-info.json").read_text(encoding="utf-8"))
        if data.get("version") == VERSION:
            return ("本机测试构建 " if data.get("kind") == "local-test" else "构建 ") + data["built_at"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return "构建时间未记录"
