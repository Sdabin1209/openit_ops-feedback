"""시험 보조: 가짜 시계와 대기. 시간은 시험 코드가 움직일 때만 흐른다(실제 시간 0)."""


class FakeClock:
    def __init__(self, start=1000.0):
        self.now, self.slept = start, []

    def __call__(self):
        return self.now

    def sleep(self, secs):
        self.slept.append(secs)
        self.now += secs

    def advance(self, secs):
        self.now += secs
