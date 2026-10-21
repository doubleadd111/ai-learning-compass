"""学习罗盘 Windows 桌面版。"""

VERSION = "2026.10.13"
DISPLAY_VERSION = VERSION


def version_parts(value):
    """YYYY.month.usable[.test]; zero is not a usable/test sequence."""
    import re
    match = re.fullmatch(r"([1-9][0-9]{3})\.([1-9][0-9]?)\.([1-9][0-9]*)(?:\.([1-9][0-9]*))?", value)
    if match is None:
        raise ValueError("版本号应为完整年份.月.可用序号，例如2026.10.12；测试版追加正整数，如2026.10.12.2")
    parts = tuple(int(part) for part in match.groups() if part is not None)
    if not 1 <= parts[1] <= 12 or any(part > 65535 for part in parts[2:]):
        raise ValueError("版本月份应为1—12，序号应为1—65535")
    return parts


def windows_version(value):
    """Windows requires exactly four numeric components, including for tests."""
    year, month, usable, *test = version_parts(value)
    return (year, month, usable, test[0] if test else 0)


def validate_build_version(value, *, local_test):
    parts = version_parts(value)
    if local_test != (len(parts) == 4):
        raise ValueError("本机测试必须使用四段版本号；可用版本打包必须使用三段版本号，不能将测试版误作可用版发布")
    return windows_version(value)


def guide_matches_version(markdown, value):
    import re
    return re.search(rf"^适用版本：[ \t]*`{re.escape(value)}`", markdown, re.MULTILINE) is not None


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
