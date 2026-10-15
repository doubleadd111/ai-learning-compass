import os
import sys
from pathlib import Path


def set_autostart(enabled: bool):
    if sys.platform != "win32":
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}" --minimized'
            else:
                pythonw = Path(sys.executable).with_name("pythonw.exe")
                launcher = Path(__file__).parents[1] / "run_desktop.py"
                command = f'"{pythonw}" "{launcher}" --minimized'
            winreg.SetValueEx(key, "LearningCompass", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "LearningCompass")
            except FileNotFoundError:
                pass
