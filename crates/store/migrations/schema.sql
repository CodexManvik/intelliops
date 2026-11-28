-- Generated from `alembic upgrade head --sql`. IF NOT EXISTS, so it is a no-op on
-- a database the Python stack already migrated.

CREATE TABLE IF NOT EXISTS audit_records (
    id BIGSERIAL NOT NULL,
    correlation_id VARCHAR NOT NULL,
    actor VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    resource VARCHAR NOT NULL,
    decision VARCHAR NOT NULL,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_audit_correlation_id ON audit_records (correlation_id);

CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_records (ts);

CREATE TABLE IF NOT EXISTS training_records (
    id BIGSERIAL NOT NULL,
    situation_id VARCHAR NOT NULL,
    signature VARCHAR NOT NULL,
    playbook_id VARCHAR NOT NULL,
    result VARCHAR NOT NULL,
    worked BOOLEAN NOT NULL,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_training_signature ON training_records (signature);

CREATE TABLE IF NOT EXISTS playbooks (
    id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    hitl_mode VARCHAR NOT NULL,
    reversible BOOLEAN NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id VARCHAR NOT NULL,
    situation_id VARCHAR NOT NULL,
    playbook_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_approvals_status ON approvals (status);

CREATE TABLE IF NOT EXISTS correlation_baseline (
    metric_name VARCHAR NOT NULL,
    n FLOAT NOT NULL,
    mean FLOAT NOT NULL,
    variance FLOAT NOT NULL,
    count BIGINT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (metric_name)
);

CREATE TABLE IF NOT EXISTS model_artifacts (
    name VARCHAR NOT NULL,
    artifact BYTEA NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (name)
);

CREATE TABLE IF NOT EXISTS meridian_submissions (
    id BIGSERIAL NOT NULL,
    client VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    amount FLOAT NOT NULL,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS meridian_reports (
    id BIGSERIAL NOT NULL,
    submission_id BIGINT NOT NULL,
    summary VARCHAR NOT NULL,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS author_decisions (
    id BIGSERIAL NOT NULL,
    signature VARCHAR NOT NULL,
    proposal_id VARCHAR NOT NULL,
    playbook_id VARCHAR NOT NULL,
    disposition VARCHAR NOT NULL,
    outcome VARCHAR NOT NULL,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_author_decisions_signature ON author_decisions (signature);

CREATE INDEX IF NOT EXISTS ix_author_decisions_proposal_id ON author_decisions (proposal_id);

CREATE INDEX IF NOT EXISTS ix_author_decisions_playbook_id ON author_decisions (playbook_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id BIGSERIAL NOT NULL,
    run_id VARCHAR NOT NULL,
    signature VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    proposal_id VARCHAR,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    finished_at TIMESTAMP WITH TIME ZONE,
    step_count INTEGER DEFAULT '0' NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_agent_runs_run_id ON agent_runs (run_id);

CREATE INDEX IF NOT EXISTS ix_agent_runs_started_at ON agent_runs (started_at);

CREATE TABLE IF NOT EXISTS agent_run_steps (
    id BIGSERIAL NOT NULL,
    run_id VARCHAR NOT NULL,
    seq INTEGER NOT NULL,
    kind VARCHAR NOT NULL,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_agent_run_steps_run_id ON agent_run_steps (run_id);

CREATE TABLE IF NOT EXISTS proposed_playbooks (
    id BIGSERIAL NOT NULL,
    proposal_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    source_situation_id VARCHAR,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_proposed_playbooks_proposal_id ON proposed_playbooks (proposal_id);

CREATE INDEX IF NOT EXISTS ix_proposed_playbooks_status ON proposed_playbooks (status);
