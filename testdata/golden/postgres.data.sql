--
-- PostgreSQL database dump
--

\restrict d9jltdVBHOZNO5FUxPawuHezfzp3cmj7wVYEvgEx2xBjBCLNNT0dVheR8q32cGU

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

--
-- Data for Name: agent_run_steps; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.agent_run_steps (id, run_id, seq, kind, ts, payload) FROM stdin;
\.


--
-- Data for Name: agent_runs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.agent_runs (id, run_id, signature, status, proposal_id, started_at, finished_at, step_count, payload) FROM stdin;
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.alembic_version (version_num) FROM stdin;
0007_proposed_playbooks
\.


--
-- Data for Name: approvals; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.approvals (id, situation_id, playbook_id, status, payload, updated_at) FROM stdin;
appr-sit-5c025fa0200c747c	sit-5c025fa0200c747c	restart-pod	pending	{"id": "appr-sit-5c025fa0200c747c", "status": "pending", "preflight": {"mode": "off", "detail": "not rehearsed (sandbox off)", "passed": true, "sandbox_namespace": null}, "decided_by": null, "playbook_id": "restart-pod", "requested_by": "action-service", "situation_id": "sit-5c025fa0200c747c"}	2026-11-28 13:55:47.045369+00
\.


--
-- Data for Name: audit_records; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.audit_records (id, correlation_id, actor, action, resource, decision, ts, payload) FROM stdin;
1	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:46:01.357933+00	{"ts": "2026-11-28T13:46:01.357933Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
2	sit-5c025fa0200c747c	oncall-alice	approve	playbook:restart-pod	allow	2026-11-28 13:46:01.571095+00	{"ts": "2026-11-28T13:46:01.571095Z", "actor": "oncall-alice", "action": "approve", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
3	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	allow	2026-11-28 13:46:01.924479+00	{"ts": "2026-11-28T13:46:01.924479Z", "actor": "action-service", "action": "execute", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
4	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:46:16.061049+00	{"ts": "2026-11-28T13:46:16.061049Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
5	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:46:31.527361+00	{"ts": "2026-11-28T13:46:31.527361Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
6	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:46:46.523374+00	{"ts": "2026-11-28T13:46:46.523374Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
7	sit-5c025fa0200c747c	action-service	expire	playbook:restart-pod	allow	2026-11-28 13:47:16.925028+00	{"ts": "2026-11-28T13:47:16.925028Z", "actor": "action-service", "action": "expire", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
8	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:47:16.929322+00	{"ts": "2026-11-28T13:47:16.929322Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
9	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:47:45.655977+00	{"ts": "2026-11-28T13:47:45.655977Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
10	sit-5c025fa0200c747c	oncall-alice	reject	playbook:restart-pod	allow	2026-11-28 13:47:45.854818+00	{"ts": "2026-11-28T13:47:45.854818Z", "actor": "oncall-alice", "action": "reject", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
11	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:47:46.180555+00	{"ts": "2026-11-28T13:47:46.180555Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
12	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:48:01.097231+00	{"ts": "2026-11-28T13:48:01.097231Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
13	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:48:16.128544+00	{"ts": "2026-11-28T13:48:16.128544Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
14	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:48:31.435392+00	{"ts": "2026-11-28T13:48:31.435392Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
15	sit-5c025fa0200c747c	action-service	expire	playbook:restart-pod	allow	2026-11-28 13:49:01.934302+00	{"ts": "2026-11-28T13:49:01.934302Z", "actor": "action-service", "action": "expire", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
16	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:49:01.944771+00	{"ts": "2026-11-28T13:49:01.944771Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
17	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:49:30.55298+00	{"ts": "2026-11-28T13:49:30.552980Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
18	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:49:46.227861+00	{"ts": "2026-11-28T13:49:46.227861Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
19	sit-5c025fa0200c747c	action-service	expire	playbook:restart-pod	allow	2026-11-28 13:50:00.67353+00	{"ts": "2026-11-28T13:50:00.673530Z", "actor": "action-service", "action": "expire", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
20	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:50:00.687251+00	{"ts": "2026-11-28T13:50:00.687251Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
21	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:50:01.335397+00	{"ts": "2026-11-28T13:50:01.335397Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
22	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:50:15.71438+00	{"ts": "2026-11-28T13:50:15.714380Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
23	sit-5c025fa0200c747c	action-service	expire	playbook:restart-pod	allow	2026-11-28 13:50:30.823968+00	{"ts": "2026-11-28T13:50:30.823968Z", "actor": "action-service", "action": "expire", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
24	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:50:30.830105+00	{"ts": "2026-11-28T13:50:30.830105Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
25	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:50:31.490726+00	{"ts": "2026-11-28T13:50:31.490726Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
26	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:51:01.235379+00	{"ts": "2026-11-28T13:51:01.235379Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
27	sit-5c025fa0200c747c	action-service	expire	playbook:restart-pod	allow	2026-11-28 13:51:31.761477+00	{"ts": "2026-11-28T13:51:31.761477Z", "actor": "action-service", "action": "expire", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
28	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:51:31.770175+00	{"ts": "2026-11-28T13:51:31.770175Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
29	sit-5c025fa0200c747c	oncall-alice	approve	playbook:restart-pod	allow	2026-11-28 13:51:39.827383+00	{"ts": "2026-11-28T13:51:39.827383Z", "actor": "oncall-alice", "action": "approve", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
30	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	allow	2026-11-28 13:51:39.950605+00	{"ts": "2026-11-28T13:51:39.950605Z", "actor": "action-service", "action": "execute", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
32	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:52:31.815391+00	{"ts": "2026-11-28T13:52:31.815391Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
34	sit-5c025fa0200c747c	oncall-alice	reject	playbook:restart-pod	allow	2026-11-28 13:53:32.419316+00	{"ts": "2026-11-28T13:53:32.419316Z", "actor": "oncall-alice", "action": "reject", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
35	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:53:32.627923+00	{"ts": "2026-11-28T13:53:32.627923Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
38	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:54:16.914045+00	{"ts": "2026-11-28T13:54:16.914045Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
39	sit-5c025fa0200c747c	action-service	expire	playbook:restart-pod	allow	2026-11-28 13:54:47.420652+00	{"ts": "2026-11-28T13:54:47.420652Z", "actor": "action-service", "action": "expire", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
40	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:54:47.423905+00	{"ts": "2026-11-28T13:54:47.423905Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
44	sit-5c025fa0200c747c	action-service	expire	playbook:restart-pod	allow	2026-11-28 13:55:46.96457+00	{"ts": "2026-11-28T13:55:46.964570Z", "actor": "action-service", "action": "expire", "decision": "allow", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
45	sit-5c025fa0200c747c	action-service	execute	playbook:restart-pod	abort	2026-11-28 13:55:46.991176+00	{"ts": "2026-11-28T13:55:46.991176Z", "actor": "action-service", "action": "execute", "decision": "abort", "resource": "playbook:restart-pod", "correlation_id": "sit-5c025fa0200c747c"}
31	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:52:01.449164+00	{"ts": "2026-11-28T13:52:01.449164Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
33	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:53:32.108255+00	{"ts": "2026-11-28T13:53:32.108255Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
36	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:53:46.411905+00	{"ts": "2026-11-28T13:53:46.411905Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
37	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:54:01.842573+00	{"ts": "2026-11-28T13:54:01.842573Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
41	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:55:16.718319+00	{"ts": "2026-11-28T13:55:16.718319Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
42	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:55:31.087565+00	{"ts": "2026-11-28T13:55:31.087565Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
43	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:55:46.170075+00	{"ts": "2026-11-28T13:55:46.170075Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
46	sit-5c025fa0200c747c	rca-service	diagnose	situation:sit-5c025fa0200c747c	allow	2026-11-28 13:56:01.077271+00	{"ts": "2026-11-28T13:56:01.077271Z", "actor": "rca-service", "action": "diagnose", "decision": "allow", "resource": "situation:sit-5c025fa0200c747c", "correlation_id": "sit-5c025fa0200c747c"}
\.


--
-- Data for Name: author_decisions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.author_decisions (id, signature, proposal_id, playbook_id, disposition, outcome, ts, payload) FROM stdin;
\.


--
-- Data for Name: correlation_baseline; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.correlation_baseline (metric_name, n, mean, variance, count, updated_at) FROM stdin;
cpu_usage	120	25.4	496.9815126050421	120	2026-11-28 13:55:59.834572+00
meridian_error_rate	96	0	0	96	2026-11-28 13:55:59.834572+00
request_rate	120	50	0	120	2026-11-28 13:55:59.834572+00
latency_p50_ms	120	39.99999999999999	3630.2521008403355	120	2026-11-28 13:55:59.834572+00
latency_p99_ms	120	167.00000000000006	68693.44537815126	120	2026-11-28 13:55:59.834572+00
memory_usage_mb	120	300.40000000000003	17891.334453781506	120	2026-11-28 13:55:59.834572+00
saturation	120	0.18	0.05808403361344537	120	2026-11-28 13:55:59.834572+00
queue_depth	120	19.8	2875.5226890756303	120	2026-11-28 13:55:59.834572+00
db_pool_in_use	120	3	0	120	2026-11-28 13:55:59.834572+00
db_pool_max	120	20	0	120	2026-11-28 13:55:59.834572+00
service_up	96	1	0	96	2026-11-28 13:55:59.834572+00
tls_handshake_failures	96	0.4	0	96	2026-11-28 13:55:59.834572+00
disk_usage_percent	120	35	0	120	2026-11-28 13:55:59.834572+00
\.


--
-- Data for Name: meridian_reports; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.meridian_reports (id, submission_id, summary, ts) FROM stdin;
\.


--
-- Data for Name: meridian_submissions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.meridian_submissions (id, client, period, status, amount, ts) FROM stdin;
\.


--
-- Data for Name: model_artifacts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.model_artifacts (name, artifact, updated_at) FROM stdin;
\.


--
-- Data for Name: playbooks; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.playbooks (id, name, hitl_mode, reversible, payload, updated_at) FROM stdin;
restart-pod	Restart affected pod	hitl	t	{"id": "restart-pod", "name": "Restart affected pod", "steps": [{"note": "kubectl rollout restart deployment/${service}", "probe": null, "action": "restart", "replicas": null, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}, {"note": "wait for readiness probe to pass", "probe": null, "action": "wait", "replicas": null, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}], "symptoms": "crash loops, wedged or hung process, memory leak trending to OOM, database connection-pool exhaustion, elevated error rate, stuck workers — a process that must be recycled", "hitl_mode": "hitl", "match_rule": "error spike in service logs or metrics", "reversible": true, "rollback_steps": [{"note": "kubectl rollout undo deployment/${service}", "probe": null, "action": "rollback_deploy", "replicas": null, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}]}	2026-11-28 13:39:49.944984+00
rollback-deploy	Roll back recent deployment	hitl	t	{"id": "rollback-deploy", "name": "Roll back recent deployment", "steps": [{"note": "kubectl rollout undo deployment/${service}", "probe": null, "action": "rollback_deploy", "replicas": null, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}, {"note": "verify health after rollback", "probe": null, "action": "wait", "replicas": null, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}], "symptoms": "a regression immediately after a recent deployment or release, a bad new version misbehaving — revert to the prior revision", "hitl_mode": "hitl", "match_rule": "recent deploy preceded the incident", "reversible": true, "rollback_steps": [{"note": "kubectl rollout undo deployment/${service} --to-revision=previous", "probe": null, "action": "rollback_deploy", "replicas": null, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}]}	2026-11-28 13:39:50.226819+00
scale-service	Scale out affected service	hitl	t	{"id": "scale-service", "name": "Scale out affected service", "steps": [{"note": "kubectl scale deployment/${service} --replicas=+2", "probe": null, "action": "scale", "replicas": 2, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}, {"note": "wait for new replicas to become ready", "probe": null, "action": "wait", "replicas": null, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}], "symptoms": "CPU or resource saturation, high latency under load, growing queue depth, a traffic surge or capacity shortfall — needs more replicas", "hitl_mode": "hitl", "match_rule": "resource saturation (cpu/mem/disk)", "reversible": true, "rollback_steps": [{"note": "kubectl scale deployment/${service} --replicas=-2", "probe": null, "action": "scale", "replicas": -2, "revision": null, "container": null, "cpu_limit": null, "mem_limit": null, "period_seconds": null, "timeout_seconds": null, "failure_threshold": null, "initial_delay_seconds": null}]}	2026-11-28 13:39:50.25027+00
\.


--
-- Data for Name: proposed_playbooks; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.proposed_playbooks (id, proposal_id, status, source_situation_id, ts, payload) FROM stdin;
\.


--
-- Data for Name: training_records; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.training_records (id, situation_id, signature, playbook_id, result, worked, ts, payload) FROM stdin;
1	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	success	t	2026-11-28 13:46:01.939552+00	{"ts": "2026-11-28T13:46:01.939552Z", "mode": "dry_run", "result": "success", "worked": true, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
2	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:46:46.540073+00	{"ts": "2026-11-28T13:46:46.540073Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
3	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:47:16.934337+00	{"ts": "2026-11-28T13:47:16.934337Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
4	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:47:46.188421+00	{"ts": "2026-11-28T13:47:46.188421Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
5	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:48:31.442418+00	{"ts": "2026-11-28T13:48:31.442418Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
6	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:49:01.953992+00	{"ts": "2026-11-28T13:49:01.953992Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
7	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:50:00.704435+00	{"ts": "2026-11-28T13:50:00.704435Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
8	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:50:30.839174+00	{"ts": "2026-11-28T13:50:30.839174Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
9	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:51:01.245725+00	{"ts": "2026-11-28T13:51:01.245725Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
10	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:51:31.778102+00	{"ts": "2026-11-28T13:51:31.778102Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
11	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	success	t	2026-11-28 13:51:39.973703+00	{"ts": "2026-11-28T13:51:39.973703Z", "mode": "dry_run", "result": "success", "worked": true, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
12	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:52:31.836862+00	{"ts": "2026-11-28T13:52:31.836862Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
13	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:53:32.632644+00	{"ts": "2026-11-28T13:53:32.632644Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
14	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:54:16.932376+00	{"ts": "2026-11-28T13:54:16.932376Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
15	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:54:47.429635+00	{"ts": "2026-11-28T13:54:47.429635Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
16	sit-5c025fa0200c747c	5c025fa0200c747c	restart-pod	failure	f	2026-11-28 13:55:47.011476+00	{"ts": "2026-11-28T13:55:47.011476Z", "mode": "dry_run", "result": "failure", "worked": false, "signature": "5c025fa0200c747c", "playbook_id": "restart-pod", "situation_id": "sit-5c025fa0200c747c"}
\.


--
-- Name: agent_run_steps_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.agent_run_steps_id_seq', 1, false);


--
-- Name: agent_runs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.agent_runs_id_seq', 1, false);


--
-- Name: audit_records_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.audit_records_id_seq', 46, true);


--
-- Name: author_decisions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.author_decisions_id_seq', 1, false);


--
-- Name: meridian_reports_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.meridian_reports_id_seq', 1, false);


--
-- Name: meridian_submissions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.meridian_submissions_id_seq', 1, false);


--
-- Name: proposed_playbooks_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.proposed_playbooks_id_seq', 1, false);


--
-- Name: training_records_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.training_records_id_seq', 16, true);


--
-- PostgreSQL database dump complete
--

\unrestrict d9jltdVBHOZNO5FUxPawuHezfzp3cmj7wVYEvgEx2xBjBCLNNT0dVheR8q32cGU

