-- 0012 C12 completion: provenance, history, requests, replay and state (ARCHITECTURE.md §7.5; extends 0011).
--
-- * Source identities record WHEN the source observed them (`observed_at`, NULL when the source does not say; never
--   defaulted to "now").
-- * Aliases carry provenance and are never deleted: a change is a retirement event (judgement.alias_retirement) plus,
--   where needed, a new alias. The same alias text active on two identities in one context is CONFLICTING and is
--   surfaced, never silently resolved. An alias never becomes an approved identity link by itself.
-- * Decision requests (judgement.decision_request) record who asked for a decision and why; pending = no decision
--   yet, or deferred.
-- * Decisions reference the decision they supersede (`previous_decision_id`, enforced), cite evidence references,
--   and may carry a `request_key` so that a replayed request is idempotent (one row, never two).
-- * A person entity may only be anchored by an HR identity (or a synthetic test identity): a Git e-mail, Jira assignee
--   or rota name never becomes a canonical person on its own.
-- * State is DERIVED (views, security_invoker): identity resolution_state is one of
--   unresolved | candidate | conflicting | approved | rejected.

ALTER TABLE judgement.source_identity ADD COLUMN observed_at timestamptz;

ALTER TABLE judgement.alias
  ADD COLUMN source_record_id text,
  ADD COLUMN source_version   integer,
  ADD COLUMN created_by       text NOT NULL DEFAULT current_user,
  ADD FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version) NOT VALID;
-- NOT VALID skips only the scan of EXISTING rows, whose new columns are all NULL (nothing to validate; the scan
-- would also run under FORCE RLS without a tenant). Every row inserted from now on is checked.

CREATE TABLE judgement.alias_retirement (
  tenant_id    uuid        NOT NULL,
  alias_id     uuid        NOT NULL,
  retired_at   timestamptz NOT NULL DEFAULT now(),
  retired_by   text        NOT NULL CHECK (length(btrim(retired_by)) > 0),
  reason       text        NOT NULL CHECK (length(btrim(reason)) > 0),
  PRIMARY KEY (tenant_id, alias_id),
  FOREIGN KEY (tenant_id, alias_id) REFERENCES judgement.alias (tenant_id, alias_id)
);

CREATE TABLE judgement.decision_request (
  tenant_id     uuid        NOT NULL,
  request_id    uuid        NOT NULL,
  candidate_id  uuid        NOT NULL,
  requested_by  text        NOT NULL CHECK (length(btrim(requested_by)) > 0),
  reason        text        NOT NULL CHECK (length(btrim(reason)) > 0),
  requested_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, request_id),
  FOREIGN KEY (tenant_id, candidate_id) REFERENCES judgement.match_candidate (tenant_id, candidate_id)
);

ALTER TABLE judgement.resolution_decision
  ADD COLUMN previous_decision_id uuid,
  ADD COLUMN evidence_refs        jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(evidence_refs) = 'array'),
  ADD COLUMN request_key          text  CHECK (request_key IS NULL OR length(request_key) BETWEEN 8 AND 200),
  ADD UNIQUE (tenant_id, candidate_id, request_key);

-- The guard of 0011, extended: the previous-decision reference must name the latest decision (NULL for version 1).
CREATE OR REPLACE FUNCTION judgement.check_resolution_decision() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  c        record;
  latest   record;
  clash    integer;
BEGIN
  SELECT mc.identity_id, mc.classification, si.origin AS identity_origin, ai.origin AS anchor_origin
    INTO c
    FROM judgement.match_candidate mc
    JOIN judgement.source_identity si ON si.tenant_id = mc.tenant_id AND si.identity_id = mc.identity_id
    JOIN judgement.entity e ON e.tenant_id = mc.tenant_id AND e.entity_id = mc.entity_id
    JOIN judgement.source_identity ai ON ai.tenant_id = e.tenant_id AND ai.identity_id = e.anchor_identity_id
   WHERE mc.tenant_id = NEW.tenant_id AND mc.candidate_id = NEW.candidate_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'resolution decision: unknown candidate' USING ERRCODE = 'foreign_key_violation';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || '/' || c.identity_id::text, 12));
  SELECT version, decision_id INTO latest FROM judgement.resolution_decision
   WHERE tenant_id = NEW.tenant_id AND candidate_id = NEW.candidate_id ORDER BY version DESC LIMIT 1;
  IF NEW.version <> coalesce(latest.version, 0) + 1 THEN
    RAISE EXCEPTION 'resolution decision: version % is not consecutive (latest %)', NEW.version,
      coalesce(latest.version, 0) USING ERRCODE = 'unique_violation';
  END IF;
  IF NEW.previous_decision_id IS DISTINCT FROM latest.decision_id THEN
    RAISE EXCEPTION 'resolution decision: previous_decision_id must name the latest decision'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.outcome = 'approved' THEN
    IF c.identity_origin <> 'synthetic_test' OR c.anchor_origin <> 'synthetic_test' THEN
      RAISE EXCEPTION 'resolution decision: only synthetic test identities may be approved'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF c.classification IN ('ambiguous', 'conflicting') THEN
      RAISE EXCEPTION 'resolution decision: a % candidate cannot be approved', c.classification
        USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO clash FROM (
      SELECT DISTINCT ON (d.candidate_id) d.outcome
        FROM judgement.resolution_decision d
        JOIN judgement.match_candidate mc ON mc.tenant_id = d.tenant_id AND mc.candidate_id = d.candidate_id
       WHERE d.tenant_id = NEW.tenant_id AND mc.identity_id = c.identity_id AND d.candidate_id <> NEW.candidate_id
       ORDER BY d.candidate_id, d.version DESC) last
     WHERE last.outcome = 'approved';
    IF clash > 0 THEN
      RAISE EXCEPTION 'resolution decision: the identity is already approved for another entity'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

-- A person entity is anchored only by a trusted HR record (or a synthetic test identity).
CREATE FUNCTION judgement.check_entity_anchor() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE a record;
BEGIN
  SELECT system, origin INTO a FROM judgement.source_identity
   WHERE tenant_id = NEW.tenant_id AND identity_id = NEW.anchor_identity_id;
  IF NEW.entity_type = 'person' AND a.system <> 'hr' THEN
    RAISE EXCEPTION 'entity: a person is anchored only by an HR identity, not %', a.system
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER entity_anchor_guard BEFORE INSERT ON judgement.entity
  FOR EACH ROW EXECUTE FUNCTION judgement.check_entity_anchor();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['judgement.alias_retirement', 'judgement.decision_request'] LOOP
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

-- Derived state. security_invoker: the caller's RLS (tenant, protected classes) applies through the views.
CREATE VIEW judgement.alias_state WITH (security_invoker = true) AS
SELECT a.tenant_id, a.alias_id, a.identity_id, a.context, a.alias_text, a.source_record_id, a.created_at,
       r.retired_at, (r.alias_id IS NULL) AS active,
       (r.alias_id IS NULL AND EXISTS (
          SELECT 1 FROM judgement.alias o
           WHERE o.tenant_id = a.tenant_id AND o.context = a.context AND o.alias_text = a.alias_text
             AND o.identity_id <> a.identity_id
             AND NOT EXISTS (SELECT 1 FROM judgement.alias_retirement ro
                              WHERE ro.tenant_id = o.tenant_id AND ro.alias_id = o.alias_id))) AS conflicting
  FROM judgement.alias a
  LEFT JOIN judgement.alias_retirement r ON r.tenant_id = a.tenant_id AND r.alias_id = a.alias_id;

CREATE VIEW judgement.candidate_state WITH (security_invoker = true) AS
SELECT mc.tenant_id, mc.candidate_id, mc.identity_id, mc.entity_id, mc.classification,
       last.outcome AS latest_outcome, last.version AS latest_version, last.decision_id AS latest_decision_id
  FROM judgement.match_candidate mc
  LEFT JOIN LATERAL (SELECT d.outcome, d.version, d.decision_id FROM judgement.resolution_decision d
                      WHERE d.tenant_id = mc.tenant_id AND d.candidate_id = mc.candidate_id
                      ORDER BY d.version DESC LIMIT 1) last ON true;

CREATE VIEW judgement.identity_state WITH (security_invoker = true) AS
SELECT si.tenant_id, si.identity_id, si.system, si.context, si.origin, si.observed_at,
       CASE
         WHEN EXISTS (SELECT 1 FROM judgement.candidate_state c WHERE c.tenant_id = si.tenant_id
                       AND c.identity_id = si.identity_id AND c.latest_outcome = 'approved') THEN 'approved'
         WHEN EXISTS (SELECT 1 FROM judgement.candidate_state c WHERE c.tenant_id = si.tenant_id
                       AND c.identity_id = si.identity_id AND c.classification IN ('conflicting', 'ambiguous')
                       AND c.latest_outcome IS DISTINCT FROM 'rejected')
           OR EXISTS (SELECT 1 FROM judgement.alias_state a WHERE a.tenant_id = si.tenant_id
                       AND a.identity_id = si.identity_id AND a.conflicting) THEN 'conflicting'
         WHEN EXISTS (SELECT 1 FROM judgement.candidate_state c WHERE c.tenant_id = si.tenant_id
                       AND c.identity_id = si.identity_id
                       AND (c.latest_outcome IS NULL OR c.latest_outcome = 'deferred')) THEN 'candidate'
         WHEN EXISTS (SELECT 1 FROM judgement.candidate_state c WHERE c.tenant_id = si.tenant_id
                       AND c.identity_id = si.identity_id AND c.latest_outcome = 'rejected') THEN 'rejected'
         ELSE 'unresolved'
       END AS resolution_state
  FROM judgement.source_identity si;

GRANT SELECT, INSERT ON judgement.alias_retirement TO veloxa_projection;
GRANT SELECT ON judgement.alias_retirement TO veloxa_app;
GRANT SELECT, INSERT ON judgement.decision_request TO veloxa_app;
GRANT SELECT ON judgement.decision_request TO veloxa_projection;
GRANT SELECT ON judgement.alias_state, judgement.candidate_state, judgement.identity_state
  TO veloxa_projection, veloxa_app;
