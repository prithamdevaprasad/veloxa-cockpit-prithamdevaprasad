-- 0020 Product records (§19): saved Ask threads, department clarification requests and agent updates.
-- All append-only; RLS confines each to the people it belongs to.
--
--   runtime.thread               a saved conversation, owned by one principal; optional context (an agent update,
--                                a record or a department). Renames and archiving are events.
--   runtime.thread_event         renamed | archived (no deletion: retention is an owner decision, OD-G).
--   runtime.thread_answer        the ordered answers of a thread, referencing runtime.answer (the existing
--                                answer record), so a thread is the real sequence of recorded answers.
--   runtime.clarification_request  a question addressed to a DEPARTMENT, with the suggested recipient (a department
--                                head from the authorised directory). Veloxa contacts nobody: status is tracked here.
--   runtime.clarification_event  submitted | answered | withdrawn; an answer intended as evidence must reference a
--                                data-room submission (it becomes evidence only through review and admission).
--   runtime.agent_update         a summary of ONE persisted agent session (its executions and their citations).
--                                `synthetic` mirrors the agent's synthetic_test activation (shown as DEMO).

CREATE TABLE runtime.thread (
  tenant_id     uuid        NOT NULL,
  thread_id     uuid        NOT NULL,
  owner_id      text        NOT NULL CHECK (owner_id ~ '^person:'),
  title         text        NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  context       jsonb       NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(context) = 'object'),
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, thread_id)
);

CREATE TABLE runtime.thread_event (
  tenant_id   uuid        NOT NULL,
  event_id    uuid        NOT NULL DEFAULT uuidv7(),
  thread_id   uuid        NOT NULL,
  event       text        NOT NULL CHECK (event IN ('renamed', 'archived', 'restored')),
  title       text,
  at          timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, thread_id) REFERENCES runtime.thread (tenant_id, thread_id),
  CHECK (event <> 'renamed' OR length(btrim(coalesce(title, ''))) BETWEEN 1 AND 200)
);

CREATE TABLE runtime.thread_answer (
  tenant_id   uuid        NOT NULL,
  thread_id   uuid        NOT NULL,
  seq         integer     NOT NULL CHECK (seq >= 1),
  answer_id   uuid        NOT NULL,
  added_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, thread_id, seq),
  UNIQUE (tenant_id, answer_id),
  FOREIGN KEY (tenant_id, thread_id) REFERENCES runtime.thread (tenant_id, thread_id),
  FOREIGN KEY (tenant_id, answer_id) REFERENCES runtime.answer (tenant_id, answer_id)
);

CREATE TABLE runtime.clarification_request (
  tenant_id            uuid        NOT NULL,
  request_id           uuid        NOT NULL,
  department_id        text        NOT NULL,
  question             text        NOT NULL CHECK (length(btrim(question)) BETWEEN 1 AND 2000),
  context              jsonb       NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(context) = 'object'),
  suggested_recipient  text,
  created_by           text        NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, request_id),
  FOREIGN KEY (tenant_id, department_id) REFERENCES directory.department (tenant_id, department_id)
);

CREATE TABLE runtime.clarification_event (
  tenant_id      uuid        NOT NULL,
  event_id       uuid        NOT NULL DEFAULT uuidv7(),
  request_id     uuid        NOT NULL,
  state          text        NOT NULL CHECK (state IN ('draft', 'submitted', 'answered', 'withdrawn')),
  actor          text        NOT NULL,
  note           text        CHECK (note IS NULL OR length(note) <= 4000),
  submission_id  uuid,                  -- an answer meant as evidence: its data-room submission
  at             timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, request_id) REFERENCES runtime.clarification_request (tenant_id, request_id)
);

CREATE TABLE runtime.agent_update (
  tenant_id            uuid        NOT NULL,
  update_id            uuid        NOT NULL,
  session_id           uuid        NOT NULL,
  agent_id             text        NOT NULL,
  department           text        NOT NULL,
  kind                 text        NOT NULL CHECK (kind IN ('new_finding', 'commitment_changed',
                         'dependency_identified', 'source_disagreement', 'incident_update', 'department_update',
                         'unresolved_question', 'new_evidence')),
  title                text        NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  summary              text        NOT NULL CHECK (length(summary) <= 2000),
  status               text        NOT NULL CHECK (status IN ('answered', 'qualified', 'abstained', 'refused')),
  project              text,
  evidence             jsonb       NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(evidence) = 'array'),
  suggested_follow_up  text,
  synthetic            boolean     NOT NULL,
  interviewer_id       text        NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, update_id),
  UNIQUE (tenant_id, session_id),
  FOREIGN KEY (tenant_id, session_id) REFERENCES runtime.agent_session (tenant_id, session_id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['runtime.thread', 'runtime.thread_event', 'runtime.thread_answer',
                           'runtime.clarification_request', 'runtime.clarification_event', 'runtime.agent_update'] LOOP
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
    EXECUTE format('GRANT SELECT, INSERT ON %s TO veloxa_app', t);
  END LOOP;
END
$$;

-- Threads: the owner only.
CREATE POLICY own_threads ON runtime.thread AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (owner_id = current_setting('app.principal_id'))
  WITH CHECK (owner_id = current_setting('app.principal_id'));
CREATE POLICY own_thread_events ON runtime.thread_event AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (EXISTS (SELECT 1 FROM runtime.thread t WHERE t.tenant_id = thread_event.tenant_id
                   AND t.thread_id = thread_event.thread_id))
  WITH CHECK (EXISTS (SELECT 1 FROM runtime.thread t WHERE t.tenant_id = thread_event.tenant_id
                        AND t.thread_id = thread_event.thread_id));
-- A thread's answers: the owner's thread AND the owner's own recorded answer.
CREATE POLICY own_thread_answers ON runtime.thread_answer AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (EXISTS (SELECT 1 FROM runtime.thread t WHERE t.tenant_id = thread_answer.tenant_id
                   AND t.thread_id = thread_answer.thread_id))
  WITH CHECK (EXISTS (SELECT 1 FROM runtime.thread t WHERE t.tenant_id = thread_answer.tenant_id
                        AND t.thread_id = thread_answer.thread_id)
              AND EXISTS (SELECT 1 FROM runtime.answer a WHERE a.tenant_id = thread_answer.tenant_id
                            AND a.answer_id = thread_answer.answer_id
                            AND a.principal_id = current_setting('app.principal_id')));

-- Clarification requests: the creator, the department's head/reviewer/members, and company readers.
CREATE FUNCTION runtime.may_see_department(p_tenant uuid, p_principal text, p_department text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT policy.holds(p_tenant, p_principal, 'tenant', p_tenant::text, 'company_reader')
      OR policy.holds(p_tenant, p_principal, 'tenant', p_tenant::text, 'admin')
      OR intake.has_department_role(p_tenant, p_principal, p_department,
                                    ARRAY['head', 'member', 'reviewer', 'contributor'])
$$;
REVOKE ALL ON FUNCTION runtime.may_see_department(uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION runtime.may_see_department(uuid, text, text) TO veloxa_app;

CREATE POLICY clarification_scope ON runtime.clarification_request AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (created_by = current_setting('app.principal_id')
         OR runtime.may_see_department(tenant_id, current_setting('app.principal_id'), department_id))
  WITH CHECK (created_by = current_setting('app.principal_id'));
CREATE POLICY clarification_event_scope ON runtime.clarification_event AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (EXISTS (SELECT 1 FROM runtime.clarification_request r WHERE r.tenant_id = clarification_event.tenant_id
                   AND r.request_id = clarification_event.request_id))
  WITH CHECK (actor = current_setting('app.principal_id')
              AND EXISTS (SELECT 1 FROM runtime.clarification_request r WHERE r.tenant_id = clarification_event.tenant_id
                            AND r.request_id = clarification_event.request_id));

-- Agent updates: written only by the session's own interviewer; read by that interviewer and company readers.
CREATE POLICY agent_update_scope ON runtime.agent_update AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (interviewer_id = current_setting('app.principal_id')
         OR policy.holds(tenant_id, current_setting('app.principal_id'), 'tenant', tenant_id::text, 'company_reader'))
  WITH CHECK (interviewer_id = current_setting('app.principal_id')
              AND EXISTS (SELECT 1 FROM runtime.agent_session s WHERE s.tenant_id = agent_update.tenant_id
                            AND s.session_id = agent_update.session_id
                            AND s.interviewer_id = current_setting('app.principal_id')));

CREATE VIEW runtime.thread_current WITH (security_invoker = true) AS
SELECT t.tenant_id, t.thread_id, t.owner_id, t.context, t.created_at,
       coalesce((SELECT e.title FROM runtime.thread_event e WHERE e.tenant_id = t.tenant_id
                   AND e.thread_id = t.thread_id AND e.event = 'renamed' ORDER BY e.at DESC LIMIT 1), t.title) AS title,
       coalesce((SELECT e.event = 'archived' FROM runtime.thread_event e WHERE e.tenant_id = t.tenant_id
                   AND e.thread_id = t.thread_id AND e.event IN ('archived', 'restored')
                   ORDER BY e.at DESC LIMIT 1), false) AS archived,
       (SELECT count(*) FROM runtime.thread_answer a WHERE a.tenant_id = t.tenant_id
          AND a.thread_id = t.thread_id) AS answers,
       (SELECT max(a.added_at) FROM runtime.thread_answer a WHERE a.tenant_id = t.tenant_id
          AND a.thread_id = t.thread_id) AS last_at
  FROM runtime.thread t;
CREATE VIEW runtime.clarification_current WITH (security_invoker = true) AS
SELECT r.*, (SELECT e.state FROM runtime.clarification_event e WHERE e.tenant_id = r.tenant_id
               AND e.request_id = r.request_id ORDER BY e.at DESC, e.event_id DESC LIMIT 1) AS state
  FROM runtime.clarification_request r;
GRANT SELECT ON runtime.thread_current, runtime.clarification_current TO veloxa_app;
