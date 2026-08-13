import json

from common.interfaces import ContextProvider
from services.rca.adapters.context_provider import (
    FileContextProvider,
    NullContextProvider,
)


def test_null_provider_satisfies_protocol_and_is_empty():
    p = NullContextProvider()
    assert isinstance(p, ContextProvider)
    assert p.recent_deploys() == []
    assert p.topology_for({"service": "web"}) == {}
    assert p.config_changes() == []


def test_file_provider_reads_json(tmp_path):
    (tmp_path / "deploys.json").write_text(json.dumps(
        [{"service": "web", "version": "v2", "ts": "2026-08-13T00:00:00+00:00"}]))
    (tmp_path / "topology.json").write_text(json.dumps({"web": ["db", "cache"]}))
    (tmp_path / "config_changes.json").write_text(json.dumps(
        [{"key": "web.replicas", "ts": "2026-08-13T00:00:00+00:00"}]))
    p = FileContextProvider(str(tmp_path))
    assert p.recent_deploys()[0]["service"] == "web"
    assert p.topology_for({"service": "web"}) == {"web": ["db", "cache"]}
    assert p.config_changes()[0]["key"] == "web.replicas"


def test_file_provider_missing_files_are_empty(tmp_path):
    p = FileContextProvider(str(tmp_path))  # empty dir
    assert p.recent_deploys() == []
    assert p.topology_for({}) == {}
    assert p.config_changes() == []


def test_file_provider_satisfies_protocol(tmp_path):
    assert isinstance(FileContextProvider(str(tmp_path)), ContextProvider)
