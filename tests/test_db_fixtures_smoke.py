import pytest
from sqlalchemy import text


@pytest.mark.postgres
def test_container_up_and_schema_present(clean_db):
    with clean_db.connect() as conn:
        tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )).scalars().all()
    assert {"audit_records", "training_records", "playbooks"} <= set(tables)
