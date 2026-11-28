--
-- PostgreSQL database dump
--

\restrict p38SNh1caDkIM24okt8hrR4UmdvoPSDccwm7bHbrAbspms8qknCmMEsCToiqafy

-- Dumped from database version 16.16
-- Dumped by pg_dump version 16.16

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_run_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_run_steps (
    id bigint NOT NULL,
    run_id character varying NOT NULL,
    seq integer NOT NULL,
    kind character varying NOT NULL,
    ts timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: agent_run_steps_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_run_steps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_run_steps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_run_steps_id_seq OWNED BY public.agent_run_steps.id;


--
-- Name: agent_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_runs (
    id bigint NOT NULL,
    run_id character varying NOT NULL,
    signature character varying NOT NULL,
    status character varying NOT NULL,
    proposal_id character varying,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    step_count integer DEFAULT 0 NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: agent_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_runs_id_seq OWNED BY public.agent_runs.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approvals (
    id character varying NOT NULL,
    situation_id character varying NOT NULL,
    playbook_id character varying NOT NULL,
    status character varying NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: audit_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_records (
    id bigint NOT NULL,
    correlation_id character varying NOT NULL,
    actor character varying NOT NULL,
    action character varying NOT NULL,
    resource character varying NOT NULL,
    decision character varying NOT NULL,
    ts timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: audit_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_records_id_seq OWNED BY public.audit_records.id;


--
-- Name: author_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.author_decisions (
    id bigint NOT NULL,
    signature character varying NOT NULL,
    proposal_id character varying NOT NULL,
    playbook_id character varying NOT NULL,
    disposition character varying NOT NULL,
    outcome character varying NOT NULL,
    ts timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: author_decisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.author_decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: author_decisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.author_decisions_id_seq OWNED BY public.author_decisions.id;


--
-- Name: correlation_baseline; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.correlation_baseline (
    metric_name character varying NOT NULL,
    n double precision NOT NULL,
    mean double precision NOT NULL,
    variance double precision NOT NULL,
    count bigint NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: meridian_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meridian_reports (
    id bigint NOT NULL,
    submission_id bigint NOT NULL,
    summary character varying NOT NULL,
    ts timestamp with time zone NOT NULL
);


--
-- Name: meridian_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.meridian_reports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: meridian_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.meridian_reports_id_seq OWNED BY public.meridian_reports.id;


--
-- Name: meridian_submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meridian_submissions (
    id bigint NOT NULL,
    client character varying NOT NULL,
    period character varying NOT NULL,
    status character varying NOT NULL,
    amount double precision NOT NULL,
    ts timestamp with time zone NOT NULL
);


--
-- Name: meridian_submissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.meridian_submissions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: meridian_submissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.meridian_submissions_id_seq OWNED BY public.meridian_submissions.id;


--
-- Name: model_artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_artifacts (
    name character varying NOT NULL,
    artifact bytea NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: playbooks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.playbooks (
    id character varying NOT NULL,
    name character varying NOT NULL,
    hitl_mode character varying NOT NULL,
    reversible boolean NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: proposed_playbooks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proposed_playbooks (
    id bigint NOT NULL,
    proposal_id character varying NOT NULL,
    status character varying NOT NULL,
    source_situation_id character varying,
    ts timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: proposed_playbooks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.proposed_playbooks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: proposed_playbooks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.proposed_playbooks_id_seq OWNED BY public.proposed_playbooks.id;


--
-- Name: training_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.training_records (
    id bigint NOT NULL,
    situation_id character varying NOT NULL,
    signature character varying NOT NULL,
    playbook_id character varying NOT NULL,
    result character varying NOT NULL,
    worked boolean NOT NULL,
    ts timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: training_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.training_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: training_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.training_records_id_seq OWNED BY public.training_records.id;


--
-- Name: agent_run_steps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_steps ALTER COLUMN id SET DEFAULT nextval('public.agent_run_steps_id_seq'::regclass);


--
-- Name: agent_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs ALTER COLUMN id SET DEFAULT nextval('public.agent_runs_id_seq'::regclass);


--
-- Name: audit_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_records ALTER COLUMN id SET DEFAULT nextval('public.audit_records_id_seq'::regclass);


--
-- Name: author_decisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.author_decisions ALTER COLUMN id SET DEFAULT nextval('public.author_decisions_id_seq'::regclass);


--
-- Name: meridian_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meridian_reports ALTER COLUMN id SET DEFAULT nextval('public.meridian_reports_id_seq'::regclass);


--
-- Name: meridian_submissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meridian_submissions ALTER COLUMN id SET DEFAULT nextval('public.meridian_submissions_id_seq'::regclass);


--
-- Name: proposed_playbooks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposed_playbooks ALTER COLUMN id SET DEFAULT nextval('public.proposed_playbooks_id_seq'::regclass);


--
-- Name: training_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_records ALTER COLUMN id SET DEFAULT nextval('public.training_records_id_seq'::regclass);


--
-- Name: agent_run_steps agent_run_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_steps
    ADD CONSTRAINT agent_run_steps_pkey PRIMARY KEY (id);


--
-- Name: agent_runs agent_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: approvals approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_pkey PRIMARY KEY (id);


--
-- Name: audit_records audit_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_records
    ADD CONSTRAINT audit_records_pkey PRIMARY KEY (id);


--
-- Name: author_decisions author_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.author_decisions
    ADD CONSTRAINT author_decisions_pkey PRIMARY KEY (id);


--
-- Name: correlation_baseline correlation_baseline_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.correlation_baseline
    ADD CONSTRAINT correlation_baseline_pkey PRIMARY KEY (metric_name);


--
-- Name: meridian_reports meridian_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meridian_reports
    ADD CONSTRAINT meridian_reports_pkey PRIMARY KEY (id);


--
-- Name: meridian_submissions meridian_submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meridian_submissions
    ADD CONSTRAINT meridian_submissions_pkey PRIMARY KEY (id);


--
-- Name: model_artifacts model_artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_artifacts
    ADD CONSTRAINT model_artifacts_pkey PRIMARY KEY (name);


--
-- Name: playbooks playbooks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.playbooks
    ADD CONSTRAINT playbooks_pkey PRIMARY KEY (id);


--
-- Name: proposed_playbooks proposed_playbooks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposed_playbooks
    ADD CONSTRAINT proposed_playbooks_pkey PRIMARY KEY (id);


--
-- Name: training_records training_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_records
    ADD CONSTRAINT training_records_pkey PRIMARY KEY (id);


--
-- Name: ix_agent_run_steps_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_run_steps_run_id ON public.agent_run_steps USING btree (run_id);


--
-- Name: ix_agent_runs_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_runs_run_id ON public.agent_runs USING btree (run_id);


--
-- Name: ix_agent_runs_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_runs_started_at ON public.agent_runs USING btree (started_at);


--
-- Name: ix_approvals_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_approvals_status ON public.approvals USING btree (status);


--
-- Name: ix_audit_correlation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_correlation_id ON public.audit_records USING btree (correlation_id);


--
-- Name: ix_audit_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_ts ON public.audit_records USING btree (ts);


--
-- Name: ix_author_decisions_playbook_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_author_decisions_playbook_id ON public.author_decisions USING btree (playbook_id);


--
-- Name: ix_author_decisions_proposal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_author_decisions_proposal_id ON public.author_decisions USING btree (proposal_id);


--
-- Name: ix_author_decisions_signature; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_author_decisions_signature ON public.author_decisions USING btree (signature);


--
-- Name: ix_proposed_playbooks_proposal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposed_playbooks_proposal_id ON public.proposed_playbooks USING btree (proposal_id);


--
-- Name: ix_proposed_playbooks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposed_playbooks_status ON public.proposed_playbooks USING btree (status);


--
-- Name: ix_training_signature; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_training_signature ON public.training_records USING btree (signature);


--
-- PostgreSQL database dump complete
--

\unrestrict p38SNh1caDkIM24okt8hrR4UmdvoPSDccwm7bHbrAbspms8qknCmMEsCToiqafy

