import threading

from PySide6.QtCore import QObject, QRunnable, Signal
from desktop.gateway import Cancelled, GatewayError


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()
        self.cancelled = threading.Event()

    def run(self):
        try:
            result = self.function(self.cancelled)
            if not self.cancelled.is_set():
                self.signals.done.emit(result)
        except Cancelled:
            pass
        except (GatewayError, ValueError) as error:
            if not self.cancelled.is_set():
                self.signals.failed.emit(str(error))
        except Exception:
            if not self.cancelled.is_set():
                self.signals.failed.emit("操作未完成，请检查数据与模型设置后重试。")
        finally:
            self.signals.finished.emit()
