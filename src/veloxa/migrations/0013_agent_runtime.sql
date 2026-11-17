-- 0013 Shared employee-agent runtime (ARCHITECTURE.md §5.2 C23/C24, §14, §10.6; Sprint 6).
--
-- One runtime for all agents: agents are DATA (versioned configuration), never separate programs.
--
--   runtime.agent_config        versioned agent configuration (append-only; the latest version is in force).
--                               `activation`: blocked (default: no owner mapping yet) | synthetic_test (reserved
--                               `syn-` namespace, synthetic person only) | active (owner decision record required).
--                               A configuration NEVER grants authorization: tools still pass the agent-tool PDP and
--                               every evidence item still passes KB ∩ AZ ∩ POL at execution time.
--   runtime.agent_session       an interview session: (agent, config version, interviewer, read basis K).
--   runtime.agent_execution     one turn; idempotent per (session, idempotency key) with a request hash.
--   runtime.agent_memory        the five approved tiers (§14.5): working | episodic | semantic | interview |
--                               reflective. tenant_id, agent_id and interviewer_id are SEPARATE RLS-enforced
--                               columns; the application role reads only rows of the agent and interviewer set in
--                               its transaction (an unset scope raises: reads fail without a full scope).
--   runtime.agent_memory_event  expiry, supersession, invalidation, retention and session-close events. Memory is
--                               expired, never deleted or updated.

CREATE TABLE runtime.agent_config (
  tenant_id          uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  agent_id           text        NOT NULL CHECK (agent_id ~ '^(agent|syn)-[a-z0-9-]{2,40}$'),
  config_version     integer     NOT NULL CHECK (config_version >= 1),
  category           text        NOT NULL CHECK (category IN ('engineering', 'finance', 'people_ops', 'marketing')),
  represented_person text        CHECK (represented_person IS NULL OR represented_person ~ '^person:'),
  capabilities       text[]      NOT NULL,
  tools              text[]      NOT NULL,
  knowledge_scopes   text[]      NOT NULL,
  policy_refs        text[]      NOT NULL,
  memory_config      jsonb       NOT NULL CHECK (jsonb_typeof(memory_config) = 'object'),
  runtime_version    text        NOT NULL,
  activation         text        NOT NULL CHECK (activation IN ('blocked', 'synthetic_test', 'active')),
  approved_by        text,
  decision_record    text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  created_by         text        NOT NULL DEFAULT current_user,
  PRIMARY KEY (tenant_id, agent_id, config_version),
  CHECK (activation <> 'synthetic_test' OR (agent_id LIKE 'syn-%'
                                           AND (represented_person IS NULL OR represented_person LIKE 'person:syn-%'))),
  CHECK (activation <> 'active' OR (represented_person IS NOT NULL AND agent_id NOT LIKE 'syn-%'
                                    AND length(btrim(coalesce(approved_by, ''))) > 0
                                    AND length(btrim(coalesce(decision_record, ''))) > 0)),
  CHECK (agent_id NOT LIKE 'syn-%' OR activation <> 'active')
);

CREATE FUNCTION runtime.check_agent_config_version() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE latest integer;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || '/agent/' || NEW.agent_id, 13));
  SELECT coalesce(max(config_version), 0) INTO latest FROM runtime.agent_config
   WHERE tenant_id = NEW.tenant_id AND agent_id = NEW.agent_id;
  IF NEW.config_version <> latest + 1 THEN
    RAISE EXCEPTION 'agent config: version % is not consecutive (latest %)', NEW.config_version, latest
      USING ERRCODE = 'unique_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER agent_config_version BEFORE INSERT ON runtime.agent_config
  FOR EACH ROW EXECUTE FUNCTION runtime.check_agent_config_version();

CREATE TABLE runtime.agent_session (
  tenant_id       uuid        NOT NULL,
  session_id      uuid        NOT NULL,
  agent_id        text        NOT NULL,
  config_version  integer     NOT NULL,
  interviewer_id  text        NOT NULL CHECK (interviewer_id ~ '^person:'),
  batch_seq       bigint      NOT NULL CHECK (batch_seq >= 0),
  mode            text        NOT NULL CHECK (mode IN ('CURRENT', 'KNOWN_AT', 'RECORDED_BY', 'VALID_AT')),
  as_of           text,
  opened_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, session_id),
  FOREIGN KEY (tenant_id, agent_id, config_version) REFERENCES runtime.agent_config (tenant_id, agent_id, config_version)
);

CREATE TABLE runtime.agent_execution (
  tenant_id        uuid        NOT NULL,
  execution_id     uuid        NOT NULL,
  session_id       uuid        NOT NULL,
  idempotency_key  text        NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
  request_sha256   text        NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  status           text        NOT NULL CHECK (status IN ('answered', 'qualified', 'abstained', 'refused',
                                                          'unavailable', 'failed')),
  runtime_version  text        NOT NULL,
  response         jsonb       NOT NULL,
  executed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, execution_id),
  UNIQUE (tenant_id, session_id, idempotency_key),
  FOREIGN KEY (tenant_id, session_id) REFERENCES runtime.agent_session (tenant_id, session_id)
);

CREATE TABLE runtime.agent_memory (
  tenant_id            uuid        NOT NULL,
  memory_id            uuid        NOT NULL,
  agent_id             text        NOT NULL,
  interviewer_id       text        CHECK (interviewer_id IS NULL OR interviewer_id ~ '^person:'),
  session_id           uuid,
  tier                 text        NOT NULL CHECK (tier IN ('working', 'episodic', 'semantic', 'interview',
                                                            'reflective')),
  label                text        NOT NULL CHECK (label IN ('source_evidence', 'derived_observation',
                                                             'approved_decision', 'execution_context', 'inference')),
  content              jsonb       NOT NULL,
  knowledge_basis      text        CHECK (knowledge_basis IS NULL OR knowledge_basis IN ('participant', 'possible',
                                                                                         'role_plausible')),
  source_records       text[]      NOT NULL DEFAULT '{}',
  provenance_episodes  uuid[]      NOT NULL DEFAULT '{}',
  data_classes         text[]      NOT NULL DEFAULT '{}',
  batch_seq            bigint      NOT NULL CHECK (batch_seq >= 0),       -- T5: max(session K, inputs)
  valid_from           timestamptz,
  valid_to             timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  created_by_execution uuid,
  PRIMARY KEY (tenant_id, memory_id),
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
  -- Tier rules (§14.5)
  CHECK (tier <> 'working' OR (session_id IS NOT NULL AND interviewer_id IS NOT NULL
                               AND label = 'execution_context')),
  CHECK (tier <> 'episodic' OR (cardinality(source_records) > 0 AND label = 'source_evidence'
                                AND knowledge_basis IS NOT NULL)),
  CHECK (tier <> 'semantic' OR (cardinality(provenance_episodes) > 0
                                AND label IN ('derived_observation', 'approved_decision'))),
  CHECK (tier <> 'interview' OR (session_id IS NOT NULL AND interviewer_id IS NOT NULL
                                 AND label = 'derived_observation')),
  CHECK (tier <> 'reflective' OR (interviewer_id IS NOT NULL AND label = 'inference')),
  CHECK (label <> 'inference' OR tier = 'reflective'),
  -- Protected classes never enter agent memory (C19 and policy; §14.2 rule 4)
  CHECK (NOT (data_classes && ARRAY['sensitive_personal', 'payroll_aggregate']::text[]))
);
CREATE INDEX agent_memory_scope ON runtime.agent_memory (tenant_id, agent_id, interviewer_id, tier);

CREATE TABLE runtime.agent_memory_event (
  tenant_id      uuid        NOT NULL,
  event_id       uuid        NOT NULL,
  memory_id      uuid        NOT NULL,
  agent_id       text        NOT NULL,
  interviewer_id text,
  event          text        NOT NULL CHECK (event IN ('expired', 'superseded', 'invalidated',
                                                       'retention_expired', 'session_closed')),
  superseded_by  uuid,
  reason         text        NOT NULL CHECK (length(btrim(reason)) > 0),
  decision       jsonb,              -- model-assisted invalidation: model, prompt version, candidates, chosen
  occurred_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, memory_id) REFERENCES runtime.agent_memory (tenant_id, memory_id),
  CHECK ((event = 'superseded') = (superseded_by IS NOT NULL))
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['runtime.agent_config', 'runtime.agent_session', 'runtime.agent_execution',
                           'runtime.agent_memory', 'runtime.agent_memory_event'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s AS RESTRICTIVE FOR ALL'
                   ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
                   ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    EXECUTE format('CREATE POLICY within_tenant ON %s FOR ALL USING (true) WITH CHECK (true)', t);
    EXECUTE format('CREATE TRIGGER %s_append_only BEFORE UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION'
                   ' ledger.reject_mutation()', replace(t, '.', '_'), t);
    EXECUTE format('CREATE TRIGGER %s_no_truncate BEFORE TRUNCATE ON %s FOR EACH STATEMENT EXECUTE FUNCTION'
                   ' ledger.reject_mutation()', replace(t, '.', '_'), t);
  END LOOP;
END
$$;

-- Memory scope: the application role sees (and writes) only the agent and interviewer set for its transaction.
-- current_setting without missing_ok raises when the scope is unset: reads fail without a full scope (§14.5).
-- Agent-level tiers (episodic, semantic: interviewer_id NULL) are readable by any interviewer of that agent; they are
-- re-checked against the interviewer's CURRENT authorization at recall (revoked knowledge is not returned).
CREATE POLICY memory_scope ON runtime.agent_memory AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (agent_id = current_setting('app.agent_id')
         AND (interviewer_id IS NULL OR interviewer_id = current_setting('app.interviewer_id')))
  WITH CHECK (agent_id = current_setting('app.agent_id')
              AND (interviewer_id IS NULL OR interviewer_id = current_setting('app.interviewer_id')));
CREATE POLICY memory_event_scope ON runtime.agent_memory_event AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (agent_id = current_setting('app.agent_id')
         AND (interviewer_id IS NULL OR interviewer_id = current_setting('app.interviewer_id')))
  WITH CHECK (agent_id = current_setting('app.agent_id')
              AND (interviewer_id IS NULL OR interviewer_id = current_setting('app.interviewer_id')));
-- An interviewer sees only their own sessions and executions.
CREATE POLICY own_sessions ON runtime.agent_session AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (interviewer_id = current_setting('app.principal_id'))
  WITH CHECK (interviewer_id = current_setting('app.principal_id'));

-- Memory current state: an item is current until any expiry-type event (derived; history kept).
CREATE VIEW runtime.agent_memory_current WITH (security_invoker = true) AS
SELECT m.* FROM runtime.agent_memory m
 WHERE NOT EXISTS (SELECT 1 FROM runtime.agent_memory_event e
                    WHERE e.tenant_id = m.tenant_id AND e.memory_id = m.memory_id);

-- Configuration is published by the internal configuration role (projection); the API can only read it.
GRANT SELECT, INSERT ON runtime.agent_config TO veloxa_projection;
GRANT USAGE ON SCHEMA runtime TO veloxa_projection;
GRANT SELECT ON runtime.agent_config TO veloxa_app;
GRANT SELECT, INSERT ON runtime.agent_session, runtime.agent_execution, runtime.agent_memory,
  runtime.agent_memory_event TO veloxa_app;
GRANT SELECT ON runtime.agent_memory_current TO veloxa_app;
