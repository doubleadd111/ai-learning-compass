import threading

from PySide6.QtCore import QObject, QRunnable, Signal
from desktop.network import Cancelled
from desktop.gateway_loader import GatewayError


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function, cleanup=None):
        super().__init__()
        self.function = function
        self.signals = Signals()
        self.cancelled = threading.Event()
        self.cleanup = cleanup

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
            if self.cleanup:
                try:
                    self.cleanup()
                except Exception:
                    pass
            self.signals.finished.emit()
