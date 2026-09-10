from services.governance.adapters.system_context import SystemContextProvider


def test_missing_file_is_unconfigured(tmp_path):
    p = SystemContextProvider(str(tmp_path / "nope.yaml"))
    assert p.load() is None
    assert "unconfigured" in p.summarize().lower()


def test_placeholder_file_is_unconfigured(tmp_path):
    f = tmp_path / "sc.yaml"
    f.write_text('system:\n  name: ""\n  summary: ""\nservices: []\n')
    p = SystemContextProvider(str(f))
    assert p.load() is None  # all-empty placeholder = unconfigured


def test_populated_file_summarizes(tmp_path):
    f = tmp_path / "sc.yaml"
    f.write_text(
        'system:\n  name: "Payments API"\n  summary: "Handles card auth."\n'
        'services:\n  - name: "auth-svc"\n    role: "authorizes"\n    depends_on: ["db"]\n'
        '    key_metrics: ["error_rate"]\n'
    )
    p = SystemContextProvider(str(f))
    got = p.load()
    assert got["system"]["name"] == "Payments API"
    s = p.summarize()
    assert "Payments API" in s and "auth-svc" in s


def test_malformed_file_is_unconfigured_not_raise(tmp_path):
    f = tmp_path / "sc.yaml"
    f.write_text("::: not: valid: yaml: [")
    p = SystemContextProvider(str(f))
    assert p.load() is None
    assert p.summarize()  # does not raise
