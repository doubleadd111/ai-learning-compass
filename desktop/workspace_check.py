"""Exercise real workspace entry points in the isolated frozen smoke store."""
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication
from desktop.guide import open_guide, close_guide


def check_workspace_windows(window):
    app = QApplication.instance()
    native = app.platformName() == 'windows'
    checks, switches, ordering, denied = 0, 0, 0, 0
    if native:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        import os
        from PySide6.QtTest import QTest
        # Optional human-assisted QA only; never used in normal application runs.
        if os.environ.get('COMPASS_QA_WAIT_FOREGROUND') == '1':
            window.setWindowTitle('隔离窗口测试 · 请点击此窗口一次')
            window.show()
            for _ in range(600):
                active_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(active_pid))
                if active_pid.value == os.getpid():
                    break
                QTest.qWait(50)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        def pair_order():
            handles = []
            @callback_type
            def visit(handle, _):
                handles.append(int(handle))
                return True
            user32.EnumWindows(visit, 0)
            return handles
    window.navigate(1)
    try:
        for operation in (window.open_statistics, window.open_recurring, window.open_structure,
                          window.show_history, window.undo_selected, window.add_manual_task,
                          window.open_schedule, window.resume_draft, window.rename_selected,
                          window.modify_plan, window.create_plan, lambda: window.edit_connection(new=True),
                          lambda: window.edit_task_id(window.store.tasks()[0]['id'])):
            before = set(window.tools.windows)
            operation()
            key = (set(window.tools.windows) - before).pop()
            dialog = window.tools.windows[key]
            app.processEvents()
            assert dialog.isVisible() and dialog.parentWidget() is None and not dialog.isModal()
            assert dialog.windowHandle().transientParent() is None
            assert not dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            if native:
                hwnd = int(dialog.winId())
                assert not user32.GetWindow(hwnd, 4)
                assert not user32.GetWindowLongW(hwnd, -20) & (0x8 | 0x80)
                # Ownerless HWNDs can alternate in both directions; no topmost hack.
                for target in (window, dialog, window, dialog):
                    # Windows activation is asynchronous and may overlap user input.
                    # Keep the same strict assertion after a bounded native settle.
                    for attempt in range(10):
                        target.raise_()
                        target.activateWindow()
                        app.processEvents()
                        QTest.qWait(50)
                        if user32.GetForegroundWindow() == int(target.winId()):
                            break
                    actual = user32.GetForegroundWindow()
                    handles = pair_order()
                    other = dialog if target is window else window
                    assert handles.index(int(target.winId())) < handles.index(int(other.winId())), (key, 'peer z-order')
                    ordering += 1
                    if actual != int(target.winId()):
                        fg_pid = wintypes.DWORD()
                        user32.GetWindowThreadProcessId(actual, ctypes.byref(fg_pid))
                        assert fg_pid.value != os.getpid(), f'Wrong application peer activated: {key}'
                        denied += 1
                    else:
                        switches += 1
            guide = open_guide(dialog, 'tools')
            app.processEvents()
            assert guide.windowHandle().transientParent() is None
            close_guide()
            dialog.showMinimized()
            assert window.tools.existing(key) is dialog and not dialog.isMinimized()
            dialog.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert key not in window.tools.windows
            checks += 1
        window.plan_more_button.click()
        assert window.plan_more_menu.isVisible()
        window.plan_more_menu.hide()
        return dict(ownerless_tools=checks, native=native, foreground_switches=switches,
                    relative_z_order_switches=ordering, background_activation_denied=denied,
                    minimize_reuse=True, guide_peer=True, grouped_actions=True, success=True)
    finally:
        close_guide()
        window.tools.close_all()
