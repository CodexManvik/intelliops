from services.action.adapters.kube_config import load_kube


class _FakeConfig:
    def __init__(self, incluster_raises=False):
        self.calls = []
        self._incluster_raises = incluster_raises

    def load_incluster_config(self):
        self.calls.append("incluster")
        if self._incluster_raises:
            raise RuntimeError("not in a pod")

    def load_kube_config(self):
        self.calls.append("kubeconfig")


def test_in_pod_uses_incluster():
    cfg = _FakeConfig(incluster_raises=False)
    load_kube(cfg)
    assert cfg.calls == ["incluster"]  # succeeds → no kubeconfig fallback


def test_out_of_pod_falls_back_to_kubeconfig():
    cfg = _FakeConfig(incluster_raises=True)
    load_kube(cfg)
    assert cfg.calls == ["incluster", "kubeconfig"]  # tried in-cluster, fell back
