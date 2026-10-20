"""Keep Qt's built-in controls Chinese even on an English Windows install."""
from PySide6.QtCore import QTranslator


class ChineseControls(QTranslator):
    LABELS = {
        "OK": "确定", "Cancel": "取消", "Close": "关闭", "Yes": "是", "No": "否",
        "Save": "保存", "Save All": "全部保存", "Open": "打开", "Apply": "应用",
        "Reset": "重置", "Help": "帮助", "Discard": "不保存", "Abort": "中止",
        "Retry": "重试", "Ignore": "忽略", "Restore Defaults": "恢复默认设置",
        "Yes to All": "全部选择是", "No to All": "全部选择否",
        "Show Details...": "展开详情", "Hide Details...": "收起详情",
        "Show Details…": "展开详情", "Hide Details…": "收起详情",
        "Copy": "复制", "Paste": "粘贴", "Cut": "剪切", "Undo": "撤销",
        "Redo": "重做", "Delete": "删除", "Select All": "全选",
    }
    CONTEXTS = {"QPlatformTheme", "QDialogButtonBox", "QMessageBox", "QLineEdit", "QWidgetTextControl"}

    def isEmpty(self):
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context in self.CONTEXTS:
            return self.LABELS.get(sourceText.replace("&", ""), "")
        return ""


def install_chinese_controls(app):
    if not getattr(app, "_chinese_controls", None):
        app._chinese_controls = ChineseControls(app)
        app.installTranslator(app._chinese_controls)
