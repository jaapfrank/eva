--
-- PostgreSQL database dump
--

\restrict ilsYopiEaQvKfwH6GRpicD2Pfn0dH3UZsc2qCsqNScFm31y72PTsZuMSWwHtMkL

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.alembic_version (version_num) FROM stdin;
c4f7a91b2d38
\.


--
-- Data for Name: async_operations; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.async_operations (operation_id, bank_id, operation_type, status, created_at, updated_at, completed_at, error_message, result_metadata, worker_id, claimed_at, retry_count, task_payload, next_retry_at, serialization_key) FROM stdin;
\.


--
-- Data for Name: audit_log; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.audit_log (id, action, transport, bank_id, started_at, ended_at, request, response, metadata) FROM stdin;
\.


--
-- Data for Name: bank_stats_cache; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.bank_stats_cache (bank_id, payload, computed_at) FROM stdin;
\.


--
-- Data for Name: banks; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.banks (bank_id, name, disposition, created_at, updated_at, mission, last_consolidated_at, mission_changed_at, config, internal_id) FROM stdin;
restore-fixture	Synthetic restore bank	{"empathy": 3, "literalism": 3, "skepticism": 3}	2026-09-24 21:30:36.945639+00	2026-09-24 21:30:36.952578+00		\N	\N	{}	d27c26bb-d185-4805-bb4d-81faa81a99c2
\.


--
-- Data for Name: chunks; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.chunks (chunk_id, document_id, bank_id, chunk_index, chunk_text, created_at, content_hash) FROM stdin;
\.


--
-- Data for Name: directives; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.directives (id, bank_id, name, content, priority, is_active, tags, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: documents; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.documents (id, bank_id, original_text, content_hash, created_at, updated_at, retain_params, tags, file_storage_key, file_original_name, file_content_type) FROM stdin;
\.


--
-- Data for Name: entities; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.entities (id, canonical_name, bank_id, metadata, first_seen, last_seen, mention_count, entity_kind) FROM stdin;
\.


--
-- Data for Name: entity_cooccurrences; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.entity_cooccurrences (entity_id_1, entity_id_2, cooccurrence_count, last_cooccurred) FROM stdin;
\.


--
-- Data for Name: entity_maintenance_queue; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.entity_maintenance_queue (bank_id, entity_id, enqueued_at) FROM stdin;
\.


--
-- Data for Name: file_storage; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.file_storage (storage_key, data) FROM stdin;
\.


--
-- Data for Name: graph_maintenance_queue; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.graph_maintenance_queue (bank_id, unit_id, enqueued_at) FROM stdin;
\.


--
-- Data for Name: invalidated_memory_units; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.invalidated_memory_units (id, bank_id, document_id, text, context, event_date, occurred_start, occurred_end, mentioned_at, fact_type, metadata, created_at, updated_at, chunk_id, tags, proof_count, source_memory_ids, consolidated_at, observation_scopes, text_signals, consolidation_failed_at, edited_at, invalidation_reason, invalidated_at, entity_ids, causal_links) FROM stdin;
\.


--
-- Data for Name: knowledge_pages; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.knowledge_pages (id, bank_id, parent_id, kind, name, mental_model_id, sort_order, managed, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: llm_requests; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.llm_requests (id, bank_id, operation, scope, trace_id, span_id, parent_span_id, provider, model, status, started_at, ended_at, duration_ms, input_tokens, output_tokens, cached_tokens, total_tokens, input, output, error, llm_info, metadata) FROM stdin;
\.


--
-- Data for Name: memory_links; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.memory_links (from_unit_id, to_unit_id, link_type, entity_id, weight, created_at, bank_id) FROM stdin;
\.


--
-- Data for Name: memory_units; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.memory_units (id, bank_id, document_id, text, embedding, context, event_date, occurred_start, occurred_end, mentioned_at, fact_type, metadata, created_at, updated_at, chunk_id, tags, proof_count, source_memory_ids, consolidated_at, observation_scopes, text_signals, search_vector, consolidation_failed_at, edited_at) FROM stdin;
\.


--
-- Data for Name: mental_model_history; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.mental_model_history (id, mental_model_id, bank_id, content, changed_at) FROM stdin;
\.


--
-- Data for Name: mental_models; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.mental_models (id, bank_id, name, source_query, content, embedding, tags, last_refreshed_at, created_at, reflect_response, max_tokens, trigger, last_refreshed_source_query, structured_content, subtype, description, entity_id, observations, links, last_updated) FROM stdin;
\.


--
-- Data for Name: observation_history; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.observation_history (id, observation_id, bank_id, content, changed_at) FROM stdin;
\.


--
-- Data for Name: unit_entities; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.unit_entities (unit_id, entity_id) FROM stdin;
\.


--
-- Data for Name: webhooks; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.webhooks (id, bank_id, url, secret, event_types, enabled, created_at, updated_at, http_config) FROM stdin;
\.


--
-- Name: mental_model_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.mental_model_history_id_seq', 1, false);


--
-- Name: observation_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.observation_history_id_seq', 1, false);


--
-- PostgreSQL database dump complete
--

\unrestrict ilsYopiEaQvKfwH6GRpicD2Pfn0dH3UZsc2qCsqNScFm31y72PTsZuMSWwHtMkL

