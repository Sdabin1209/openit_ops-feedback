"""시험 보조: 비식별 처리기 컴포넌트(DeidRequest -> DeidResult)를 옛 시험의 호출 모양(봉투를 바로 넘기고 결과를 속성으로 읽기)으로 감싼다.
컴포넌트 자체는 바뀌지 않았다. salt는 시험 안에서 pseudonymizer 컴포넌트에 주입한다."""
from ops_feedback.components import deidentifier as _d
from ops_feedback.components.deidentifier import DeidPolicy  # noqa: F401
from ops_feedback.wiring.build import make_pseudonymizer


class _Res(dict):
    trace = property(lambda s: s["trace"])
    stats = property(lambda s: s["stats"])
    policy_fingerprint = property(lambda s: s["policy_fingerprint"])


class Deidentifier(_d.Deidentifier):
    def __init__(self, policy, salt, view=None):
        if not salt:
            raise ValueError("의사 식별자용 salt가 비어 있다")
        super().__init__(policy, make_pseudonymizer(salt))
        self._view = view

    def deidentify(self, envelope):
        req = envelope if set(envelope) == {"envelope"} else {"envelope": envelope}      # run_once는 요청 모양으로 부른다
        return _Res(super().deidentify(req))

    def pseudonym(self, key, value):
        return self._pseudonymize(key, value)

    def pending(self, limit=None, after_seq=0):
        if self._view is None:
            raise RuntimeError("원본 읽기 권한이 없다")
        return self._view.read_unprocessed(limit, after_seq)
