"""单一专注计时器，累计写入幂等，进程重启或休眠后暂停。"""
from __future__ import annotations

from datetime import datetime
from time import monotonic
from desktop.models import new_id


class FocusTimer:
    def __init__(self, store, clock=monotonic, wall_clock=None):
        self.store = store
        self.clock = clock
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        self.pending_dates = {}
        self.focus_minutes = int(store.setting("focus_minutes", 25))
        self.break_minutes = int(store.setting("break_minutes", 5))
        state = store.setting("focus_state", {})
        self.phase = state.get("phase", "focus")
        self.remaining = float(state.get("remaining", self.focus_minutes * 60))
        self.elapsed = float(state.get("elapsed", 0))
        self.session_id = state.get("session_id")
        self.task_id = state.get("task_id")
        self.started_at = state.get("started_at", "")
        self.running = False
        self.last = self.clock()
        self.last_commit = int(self.elapsed)

    def state(self):
        return dict(phase=self.phase, remaining=self.remaining, elapsed=self.elapsed,
                    session_id=self.session_id, task_id=self.task_id, started_at=self.started_at)

    def _commit(self):
        if self.phase == "focus" and self.session_id and int(self.elapsed) > 0:
            self.store.record_focus(self.session_id, self.task_id, int(self.elapsed), self.started_at,
                                    day_seconds=self.pending_dates if self.pending_dates else None)
            self.pending_dates.clear()
        self.store.set_setting("focus_state", self.state())
        self.last_commit = int(self.elapsed)

    def configure(self, focus_minutes, break_minutes):
        self.focus_minutes, self.break_minutes = int(focus_minutes), int(break_minutes)
        self.store.set_setting("focus_minutes", self.focus_minutes)
        self.store.set_setting("break_minutes", self.break_minutes)
        if not self.session_id and not self.running:
            self.remaining = (self.focus_minutes if self.phase == "focus" else self.break_minutes) * 60

    def start(self, task_id=None):
        target = self.task_id if self.session_id or self.phase == "break" else task_id
        if target:
            row = next((r for r in self.store.tasks() if r["id"] == target), None)
            if not row or row["archived"] or row.get("paused"):
                raise ValueError("关联计划已暂停、归档或任务不存在，请先恢复计划")
        if self.running:
            return
        if self.phase == "focus" and not self.session_id:
            self.task_id = task_id
            self.session_id = new_id()
            self.started_at = self.wall_clock().isoformat()
            self.elapsed = 0
        self.running = True
        self.last = self.clock()
        self._commit()

    def tick(self):
        if not self.running:
            return None
        current = self.clock()
        delta = max(0, current - self.last)
        self.last = current
        if delta > 10:
            # 没有把未观察到的休眠/长时间阻塞计入学习时间。
            self.running = False
            self._commit()
            return "sleep"
        used = min(delta, self.remaining)
        previous_whole = int(self.elapsed)
        self.elapsed += used
        if self.phase == 'focus' and int(self.elapsed) > previous_whole:
            # Split observed whole-second intervals at local midnight. Never
            # infer paused/offline time from the session's original start.
            from datetime import timedelta
            end = self.wall_clock()-timedelta(seconds=max(0,delta-used))
            amount = int(self.elapsed)-previous_whole
            for offset in range(amount):
                day = (end-timedelta(seconds=amount-offset)).date().isoformat()
                self.pending_dates[day] = self.pending_dates.get(day,0)+1
        self.remaining -= used
        if self.remaining <= 0:
            self._commit()
            completed = self.phase
            self.running = False
            self.phase = "break" if completed == "focus" else "focus"
            self.remaining = (self.break_minutes if self.phase == "break" else self.focus_minutes) * 60
            self.elapsed = 0
            self.session_id = None
            self._commit()
            return completed
        if int(self.elapsed) - self.last_commit >= 5:
            self._commit()
        return None

    def pause(self):
        if self.running:
            self.tick()
        self.running = False
        self._commit()

    def stop(self):
        self.pause()
        self.phase = "focus"
        self.session_id = None
        self.task_id = None
        self.elapsed = 0
        self.remaining = self.focus_minutes * 60
        self._commit()
