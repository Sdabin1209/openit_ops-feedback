from ops_feedback.components.failure_detector import make_detection


class _D:
    name = "custom"

    def detect(self, deid_trace):
        return [make_detection("CUSTOM", {}, "MODEL")]


def make():
    return _D()
