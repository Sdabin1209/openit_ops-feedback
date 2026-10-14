from ops_feedback.detect import Detection


class _D:
    name = "custom"

    def detect(self, deid_trace):
        return [Detection("CUSTOM", method="MODEL")]


def make():
    return _D()
