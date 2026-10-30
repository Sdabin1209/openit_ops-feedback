"""시험 보조(U422): 실제 시간을 기다리거나 재서 판정하지 않는다. 신호는 사건(Event, 프로세스가 찍는 로그 줄)으로 받는다.
HANG_GUARD_SECONDS는 판정이 아니라 '코드가 진짜로 멈췄을 때 시험이 영원히 멈추지 않게' 하는 매달림 감시 하나다. 정상이면 대기는 즉시 끝나므로 이 값에 닿지 않는다."""
import signal
import subprocess
import sys
import threading

HANG_GUARD_SECONDS = 300      # 매달림 감시


class Proc:
    """서버·잡 하위 프로세스. 출력을 스레드가 모으고, 기동은 시계가 아니라 로그 줄(`listening on`)이라는 사건으로 안다."""

    def __init__(self, argv, env):
        self.p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.lines, self._cond, self.eof = [], threading.Condition(), threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            with self._cond:
                self.lines.append(line)
                self._cond.notify_all()
        self.eof.set()
        with self._cond:
            self._cond.notify_all()

    def wait_for(self, needle="listening on"):
        with self._cond:
            ok = self._cond.wait_for(lambda: any(needle in ln for ln in self.lines) or self.eof.is_set(), HANG_GUARD_SECONDS)      # 매달림 감시
        assert ok and any(needle in ln for ln in self.lines), "프로세스가 기동 로그를 내지 않고 끝났거나 멈췄다:\n" + "".join(self.lines)[-500:]

    def stop(self, sig=signal.SIGTERM):
        if self.p.poll() is None:
            self.p.send_signal(sig)
        self.p.wait(HANG_GUARD_SECONDS)      # 매달림 감시
        assert self.eof.wait(HANG_GUARD_SECONDS)      # 매달림 감시
        return "".join(self.lines)
