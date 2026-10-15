from desktop.focus import FocusTimer
from desktop.store import Store
from tests.test_desktop_store import create


def test_pause_restart_resume_sleep_and_stop_do_not_double_count(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    task_id = store.tasks(pid)[0]["id"]
    clock = [0.0]
    timer = FocusTimer(store, lambda: clock[0])
    timer.start(task_id)
    for _ in range(2):
        clock[0] += 5
        timer.tick()
    timer.pause()
    timer.pause()
    assert store.tasks(pid)[0]["focus_seconds"] == 10
    timer = FocusTimer(store, lambda: clock[0])
    assert not timer.running
    timer.start()
    clock[0] += 5
    timer.tick()
    clock[0] += 120
    assert timer.tick() == "sleep"
    assert not timer.running
    timer.stop()
    assert store.tasks(pid)[0]["focus_seconds"] == 15


def test_focus_completion_and_break_do_not_mark_task_completed(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    clock = [0.0]
    timer = FocusTimer(store, lambda: clock[0])
    timer.configure(1, 1)
    timer.start(store.tasks(pid)[0]["id"])
    event = None
    for _ in range(12):
        clock[0] += 5
        event = timer.tick()
    assert event == "focus"
    assert timer.phase == "break"
    timer.start()
    for _ in range(12):
        clock[0] += 5
        timer.tick()
    assert store.tasks(pid)[0]["focus_seconds"] == 60
    assert store.tasks(pid)[0]["status"] == "未开始"
