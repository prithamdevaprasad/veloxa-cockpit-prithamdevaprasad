-- 0021 Company intelligence: claim-level knowledge, the Conflict Registry, the 14 agent people and their
-- Assumption Ledger, bounded agent interactions, the Findings Registry with notifications, and connector runs.
-- Everything is append-only (ledger.reject_mutation) and tenant-isolated (FORCE RLS). Nothing here is a second
-- evidence store: every claim and finding references ledger.source_record ids; admission stays in the ingest
-- pipeline and the data room.
--
--   knowledge.claim               one extracted statement (subject, predicate, object) from ONE admitted record
--                                 version, with its locator, valid time AS WRITTEN (precision kept), the batch K at
--                                 which it became known and the pack release time. Built deterministically by
--                                 veloxa.knowledge (projection role); claims are visible at K only if batch_seq <= K.
--   knowledge.conflict            a registry entry: claims about the same subject+predicate whose values differ.
--                                 `resolution` records an explicit precedence rule, or 'needs_review' (no rule).
--   knowledge.conflict_decision   a human resolution (reviewer, decision reference, rationale). Append-only.
--   runtime.agent_person          an agent's grounding: the HR employee record it represents (K-01/K-02, owner
--                                 decision 18 Nov 2026, hackathon tenant only) and the decision reference.
--   runtime.assumption            the Assumption Ledger: invented persona details, never evidence (categories are
--                                 constrained; `scope` = 'persona' can never be used for work facts).
--   runtime.agent_interaction     one bounded multi-agent investigation: trigger, participants, reasons,
--                                 evidence, findings, assumptions used, outcome.
--   runtime.finding / finding_event / notification   the Findings Registry and its lifecycle.
--   runtime.connector_run         one connector synchronisation attempt (scope, counts, cursor, error).

CREATE TABLE knowledge.claim (
  tenant_id          uuid        NOT NULL,
  claim_id           text        NOT NULL CHECK (claim_id ~ '^CL-[0-9a-f]{16}$'),
  batch_seq          integer     NOT NULL CHECK (batch_seq >= 1),
  subject_type       text        NOT NULL,
  subject            text        NOT NULL,
  predicate          text        NOT NULL,
  object             jsonb       NOT NULL,
  valid_at           text,                 -- as written in the source (date, month, quarter...); never widened
  valid_precision    text        NOT NULL CHECK (valid_precision IN ('instant', 'day', 'week', 'month', 'quarter',
                                                                     'year', 'range', 'unstated')),
  available_at       timestamptz NOT NULL, -- the pack's release time (the source could not be known before it)
  record_id          text        NOT NULL,
  record_version     integer     NOT NULL CHECK (record_version >= 1),
  locator            jsonb       NOT NULL,
  extraction_method  text        NOT NULL,
  data_class         text        NOT NULL,
  speaker            text,                 -- author or speaker when the source states one (source identifier)
  created_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, claim_id)
);
CREATE INDEX claim_subject_idx ON knowledge.claim (tenant_id, subject, predicate);

CREATE TABLE knowledge.conflict (
  tenant_id          uuid        NOT NULL,
  conflict_id        text        NOT NULL CHECK (conflict_id ~ '^CF-[0-9a-f]{12}$'),
  batch_seq          integer     NOT NULL,      -- the knowledge point at which the disagreement became visible
  subject            text        NOT NULL,
  predicate          text        NOT NULL,
  claim_ids          text[]      NOT NULL CHECK (cardinality(claim_ids) >= 2),
  resolution         text        NOT NULL CHECK (resolution IN ('rule', 'needs_review')),
  rule               text,
  preferred_claim_id text,
  rationale          text        NOT NULL,
  uncertainty        text        NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, conflict_id),
  CHECK ((resolution = 'rule') = (rule IS NOT NULL AND preferred_claim_id IS NOT NULL))
);

CREATE TABLE knowledge.conflict_decision (
  tenant_id          uuid        NOT NULL,
  decision_id        uuid        NOT NULL DEFAULT uuidv7(),
  conflict_id        text        NOT NULL,
  preferred_claim_id text,                      -- NULL = reviewed and left unresolved
  reviewer           text        NOT NULL,
  decision_ref       text        NOT NULL,
  rationale          text        NOT NULL,
  at                 timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, decision_id),
  FOREIGN KEY (tenant_id, conflict_id) REFERENCES knowledge.conflict (tenant_id, conflict_id)
);

CREATE TABLE runtime.agent_person (
  tenant_id       uuid        NOT NULL,
  agent_id        text        NOT NULL,
  version         integer     NOT NULL CHECK (version >= 1),
  person_record   text        NOT NULL,      -- ledger record id of the HR employee row
  display_name    text        NOT NULL,
  title           text,
  department      text,
  link_rule       text        NOT NULL CHECK (link_rule IN ('hr_employee_id', 'exact_corporate_email')),
  decision_ref    text        NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, agent_id, version)
);

CREATE TABLE runtime.assumption (
  tenant_id        uuid        NOT NULL,
  assumption_id    text        NOT NULL CHECK (assumption_id ~ '^AS-[0-9a-f]{12}$'),
  agent_id         text        NOT NULL,
  category         text        NOT NULL CHECK (category IN ('interests', 'hobbies', 'background', 'goals',
                     'communication_style', 'routine', 'living_context', 'preferences', 'personality')),
  description      text        NOT NULL,
  value            text        NOT NULL,
  origin           text        NOT NULL CHECK (origin = 'synthetic_persona'),
  reason           text        NOT NULL,
  generator        text        NOT NULL,
  generator_version text       NOT NULL,
  status           text        NOT NULL CHECK (status IN ('active', 'superseded', 'withdrawn')),
  scope            text        NOT NULL CHECK (scope = 'persona'),   -- never evidence for work, pay or risk
  source_ref       text,
  supersedes       text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, assumption_id)
);

CREATE TABLE runtime.agent_interaction (
  tenant_id        uuid        NOT NULL,
  interaction_id   uuid        NOT NULL DEFAULT uuidv7(),
  trigger          text        NOT NULL CHECK (trigger IN ('ask', 'event', 'daily', 'manual')),
  question         text        NOT NULL,
  participants     jsonb       NOT NULL CHECK (jsonb_typeof(participants) = 'array'),  -- [{agent_id, reason}]
  evidence         jsonb       NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(evidence) = 'array'),
  findings         jsonb       NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(findings) = 'array'),
  assumptions_used jsonb       NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(assumptions_used) = 'array'),
  outcome          text        NOT NULL,
  principal_id     text        NOT NULL,
  batch_seq        integer     NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, interaction_id)
);

CREATE TABLE runtime.finding (
  tenant_id        uuid        NOT NULL,
  finding_id       text        NOT NULL CHECK (finding_id ~ '^FD-[0-9a-f]{12}$'),
  dedup_key        text        NOT NULL,
  title            text        NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  description      text        NOT NULL,
  department       text,
  project          text,
  related_agents   jsonb       NOT NULL DEFAULT '[]',
  trigger          text        NOT NULL CHECK (trigger IN ('event', 'daily', 'manual')),
  evidence         jsonb       NOT NULL CHECK (jsonb_typeof(evidence) = 'array'),
  claims           jsonb       NOT NULL DEFAULT '[]',
  ranking          jsonb,                      -- the D-15 breakdown at creation (null = not rankable)
  confidence       text        NOT NULL CHECK (confidence IN ('high', 'moderate', 'low', 'unknown', 'conflicting')),
  unknowns         jsonb       NOT NULL DEFAULT '[]',
  assumptions      jsonb       NOT NULL DEFAULT '[]',
  visibility       jsonb       NOT NULL,       -- {"departments": [...], "systems": [...]} rechecked on every read
  batch_seq        integer     NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, finding_id),
  UNIQUE (tenant_id, dedup_key)
);

CREATE TABLE runtime.finding_event (
  tenant_id   uuid        NOT NULL,
  event_id    uuid        NOT NULL DEFAULT uuidv7(),
  finding_id  text        NOT NULL,
  state       text        NOT NULL CHECK (state IN ('detected', 'verified', 'notified', 'acknowledged',
                                                    'investigating', 'resolved', 'dismissed', 'reassessed')),
  actor       text        NOT NULL,
  note        text,
  ref         text,                            -- clarification request, submission or decision reference
  at          timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, finding_id) REFERENCES runtime.finding (tenant_id, finding_id)
);

CREATE TABLE runtime.notification (
  tenant_id     uuid        NOT NULL,
  notification_id uuid      NOT NULL DEFAULT uuidv7(),
  finding_id    text        NOT NULL,
  recipient     text        NOT NULL,          -- a principal (verified role) or 'queue:<department>'
  basis         text        NOT NULL,          -- why this recipient (role tuple or department queue)
  delivered_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, notification_id),
  FOREIGN KEY (tenant_id, finding_id) REFERENCES runtime.finding (tenant_id, finding_id),
  CHECK (recipient ~ '^(person:|queue:)')
);

CREATE TABLE runtime.connector_run (
  tenant_id     uuid        NOT NULL,
  run_id        uuid        NOT NULL DEFAULT uuidv7(),
  connector     text        NOT NULL,
  mode          text        NOT NULL CHECK (mode IN ('initial', 'incremental', 'webhook', 'reconcile')),
  scope         jsonb       NOT NULL,
  cursor_before text,
  cursor_after  text,
  fetched       integer     NOT NULL DEFAULT 0,
  changed       integer     NOT NULL DEFAULT 0,
  submitted     integer     NOT NULL DEFAULT 0,   -- handed to the data room (pending admission)
  status        text        NOT NULL CHECK (status IN ('ok', 'failed', 'disabled')),
  error         text,
  started_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at   timestamptz,
  PRIMARY KEY (tenant_id, run_id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['knowledge.claim', 'knowledge.conflict', 'knowledge.conflict_decision',
                           'runtime.agent_person', 'runtime.assumption', 'runtime.agent_interaction',
                           'runtime.finding', 'runtime.finding_event', 'runtime.notification',
                           'runtime.connector_run'] LOOP
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

GRANT USAGE ON SCHEMA knowledge TO veloxa_projection, veloxa_app;
-- Claims and conflicts are built by the projection role; the app reads them (protected classes never).
GRANT SELECT, INSERT ON knowledge.claim, knowledge.conflict TO veloxa_projection;
GRANT SELECT ON knowledge.claim, knowledge.conflict, knowledge.conflict_decision TO veloxa_app;
GRANT INSERT ON knowledge.conflict_decision TO veloxa_app;
CREATE POLICY protected_pay_for_app ON knowledge.claim AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (data_class NOT IN ('sensitive_personal', 'payroll_aggregate'));
-- A conflict decision is recorded only by its named reviewer, who must be a company reader or admin.
CREATE POLICY conflict_reviewer ON knowledge.conflict_decision AS RESTRICTIVE FOR INSERT TO veloxa_app
  WITH CHECK (reviewer = current_setting('app.principal_id')
              AND (policy.holds(tenant_id, reviewer, 'tenant', tenant_id::text, 'company_reader')
                   OR policy.holds(tenant_id, reviewer, 'tenant', tenant_id::text, 'admin')));

GRANT SELECT, INSERT ON runtime.agent_person, runtime.assumption, runtime.agent_interaction, runtime.finding,
  runtime.finding_event, runtime.notification, runtime.connector_run TO veloxa_app;

-- Findings: readable by company readers, admins and the heads/reviewers of the finding's departments. The API
-- additionally rechecks evidence access on every read (never a cached decision).
CREATE FUNCTION runtime.may_see_finding(p_tenant uuid, p_principal text, p_visibility jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT policy.holds(p_tenant, p_principal, 'tenant', p_tenant::text, 'company_reader')
      OR policy.holds(p_tenant, p_principal, 'tenant', p_tenant::text, 'admin')
      OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(coalesce(p_visibility -> 'departments', '[]'::jsonb)) d
                  WHERE intake.has_department_role(p_tenant, p_principal, d, ARRAY['head', 'reviewer']))
$$;
REVOKE ALL ON FUNCTION runtime.may_see_finding(uuid, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION runtime.may_see_finding(uuid, text, jsonb) TO veloxa_app;
CREATE POLICY finding_scope ON runtime.finding AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (runtime.may_see_finding(tenant_id, current_setting('app.principal_id'), visibility));
CREATE POLICY finding_event_scope ON runtime.finding_event AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (EXISTS (SELECT 1 FROM runtime.finding f WHERE f.tenant_id = finding_event.tenant_id
                   AND f.finding_id = finding_event.finding_id));
CREATE POLICY notification_scope ON runtime.notification AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (recipient = current_setting('app.principal_id')
         OR EXISTS (SELECT 1 FROM runtime.finding f WHERE f.tenant_id = notification.tenant_id
                      AND f.finding_id = notification.finding_id));
-- Agent interactions: the asking principal and company readers.
CREATE POLICY interaction_scope ON runtime.agent_interaction AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (principal_id = current_setting('app.principal_id')
         OR policy.holds(tenant_id, current_setting('app.principal_id'), 'tenant', tenant_id::text, 'company_reader'));

CREATE VIEW runtime.finding_current WITH (security_invoker = true) AS
SELECT f.*, (SELECT e.state FROM runtime.finding_event e WHERE e.tenant_id = f.tenant_id
               AND e.finding_id = f.finding_id ORDER BY e.at DESC, e.event_id DESC LIMIT 1) AS state,
            (SELECT max(e.at) FROM runtime.finding_event e WHERE e.tenant_id = f.tenant_id
               AND e.finding_id = f.finding_id) AS updated_at
  FROM runtime.finding f;
CREATE VIEW runtime.assumption_current WITH (security_invoker = true) AS
SELECT a.* FROM runtime.assumption a
 WHERE a.status = 'active'
   AND NOT EXISTS (SELECT 1 FROM runtime.assumption s WHERE s.tenant_id = a.tenant_id
                     AND s.supersedes = a.assumption_id);
GRANT SELECT ON runtime.finding_current, runtime.assumption_current TO veloxa_app;
