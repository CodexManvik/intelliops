from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import Situation, SituationStatus
from services.read.projection import ReadModel

TS = datetime(2026, 8, 15, tzinfo=UTC)


def _client(model):
    from services.read import app as appmod
    appmod.app.state.model = model
    return TestClient(appmod.app)


def test_situations_and_outcomes_endpoints():
    model = ReadModel()
    model.apply_detected(Situation(id="sit-1", status=SituationStatus.DETECTED,
                                   member_events=[], severity="high",
                                   first_seen=TS, last_seen=TS, signature="1"))
    c = _client(model)
    sits = c.get("/situations").json()
    assert sits[0]["id"] == "sit-1"
    assert c.get("/outcomes").json() == []
