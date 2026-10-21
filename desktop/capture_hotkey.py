"""Opt-in Windows global quick capture; no keyboard hook or silent takeover."""
import sys
from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtWidgets import QApplication


def parse_shortcut(value):
    parts=[p.strip().upper() for p in value.split('+')]
    modifiers={'ALT':1,'CTRL':2,'SHIFT':4,'WIN':8}
    if len(parts)<2 or any(p not in modifiers for p in parts[:-1]) or len(set(parts[:-1]))!=len(parts[:-1]):
        raise ValueError('快捷键示例：Ctrl+Alt+N；需要Ctrl、Alt或Win修饰键与一个字母、数字或F键')
    mask=sum(modifiers[p] for p in parts[:-1]);key=parts[-1]
    if not mask & 11: raise ValueError('全局快捷键至少需要Ctrl、Alt或Win，不能只用Shift')
    if len(key)==1 and key.isascii() and key.isalnum():vk=ord(key)
    elif key.startswith('F') and key[1:].isdigit() and 1<=int(key[1:])<=24:vk=111+int(key[1:])
    else:raise ValueError('末尾请选择一个字母、数字或F1—F24')
    return mask,vk


class CaptureHotkey(QAbstractNativeEventFilter):
    ID=0x4C44
    def __init__(self,callback,api=None):
        super().__init__();self.callback=callback;self.active=None;self.closed=False
        self.api=api
        if api is None and sys.platform=='win32':
            import ctypes
            self.api=ctypes.windll.user32
        QApplication.instance().installNativeEventFilter(self)

    def configure(self,enabled,sequence):
        if self.closed:raise ValueError('快捷录入已退出')
        new=parse_shortcut(sequence) if enabled else None
        if self.active==new:return
        old=self.active
        if old:self.api.UnregisterHotKey(None,self.ID)
        self.active=None
        if new:
            if self.api is None or not self.api.RegisterHotKey(None,self.ID,new[0]|0x4000,new[1]):
                if old and self.api.RegisterHotKey(None,self.ID,old[0]|0x4000,old[1]):self.active=old
                raise ValueError('快捷键无法注册：可能被其他程序占用。原有快捷键未被抢占，请换一个组合')
            self.active=new

    def nativeEventFilter(self,event_type,message):
        if self.active and sys.platform=='win32':
            import ctypes
            from ctypes.wintypes import MSG
            msg=MSG.from_address(int(message))
            if msg.message==0x0312 and msg.wParam==self.ID:
                QTimer.singleShot(0,self.callback)
                return True,0
        return False,0

    def close(self):
        if self.closed:return
        if self.active:self.api.UnregisterHotKey(None,self.ID)
        self.active=None;self.closed=True
        QApplication.instance().removeNativeEventFilter(self)
