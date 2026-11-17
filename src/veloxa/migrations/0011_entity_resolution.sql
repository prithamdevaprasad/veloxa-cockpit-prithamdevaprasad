-- 0011 C12 entity and identity resolution (ARCHITECTURE.md §6.2 judgement, §7.5; P8).
--
-- Resolution decisions are human judgements: versioned, append-only, never inferred. The system PROPOSES
-- candidates with an explicit rule classification (no numeric score: the approved architecture defines none for
-- the hackathon profile, and no auto-accept threshold is approved, so EVERY candidate waits for a human).
--
--   judgement.source_identity    an identifier as a source states it, in a declared context (e.g. issue-tracker
--                                assignee). `origin` is 'synthetic_test' (test fixtures, reserved `syn-` namespace,
--                                no ledger record) or 'release_pack' (admitted evidence: must cite its ledger record)
--   judgement.entity             a canonical entity, anchored by exactly one source identity (e.g. an HR record)
--   judgement.alias              a surface form valid ONLY within its context (the same initials can differ by context)
--   judgement.match_candidate    a proposed identity -> entity link, with its rule classification and evidence
--   judgement.resolution_decision  consecutive versions per candidate: approved | rejected | deferred
--
-- Safety (development profile): an APPROVAL is accepted only when the candidate's identity AND the entity's anchor
-- are both synthetic test identities. Every real (release-pack) candidate stays unresolved. Enforced here by trigger,
-- and again by the application before it writes. Unresolved identifiers stay raw strings (§7.5).

CREATE SCHEMA IF NOT EXISTS judgement;

CREATE TABLE judgement.source_identity (
  tenant_id         uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  identity_id       uuid        NOT NULL,
  system            text        NOT NULL CHECK (system ~ '^[a-z][a-z0-9_]*$'),
  context           text        NOT NULL CHECK (context ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
  source_value      text        NOT NULL CHECK (length(btrim(source_value)) > 0 AND source_value = btrim(source_value)),
  origin            text        NOT NULL CHECK (origin IN ('synthetic_test', 'release_pack')),
  data_class        text        NOT NULL DEFAULT 'personal',
  source_record_id  text,
  source_version    integer,
  batch_seq         bigint,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        text        NOT NULL DEFAULT current_user,
  PRIMARY KEY (tenant_id, identity_id),
  UNIQUE (tenant_id, context, source_value),
  FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version),
  CHECK (origin <> 'synthetic_test'
         OR (source_value LIKE 'syn-%' AND source_record_id IS NULL AND batch_seq IS NULL)),
  CHECK (origin <> 'release_pack'
         OR (source_value NOT LIKE 'syn-%' AND source_record_id IS NOT NULL AND source_version IS NOT NULL
             AND batch_seq IS NOT NULL))
);

CREATE TABLE judgement.entity (
  tenant_id           uuid        NOT NULL,
  entity_id           uuid        NOT NULL,
  entity_type         text        NOT NULL CHECK (entity_type IN ('person', 'vendor', 'organisation_unit')),
  anchor_identity_id  uuid        NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          text        NOT NULL DEFAULT current_user,
  PRIMARY KEY (tenant_id, entity_id),
  UNIQUE (tenant_id, anchor_identity_id),
  FOREIGN KEY (tenant_id, anchor_identity_id) REFERENCES judgement.source_identity (tenant_id, identity_id)
);

CREATE TABLE judgement.alias (
  tenant_id     uuid        NOT NULL,
  alias_id      uuid        NOT NULL,
  identity_id   uuid        NOT NULL,
  context       text        NOT NULL CHECK (context ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
  alias_text    text        NOT NULL CHECK (length(btrim(alias_text)) > 0),
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, alias_id),
  UNIQUE (tenant_id, identity_id, context, alias_text),
  FOREIGN KEY (tenant_id, identity_id) REFERENCES judgement.source_identity (tenant_id, identity_id)
);

CREATE TABLE judgement.match_candidate (
  tenant_id       uuid        NOT NULL,
  candidate_id    uuid        NOT NULL,
  identity_id     uuid        NOT NULL,
  entity_id       uuid        NOT NULL,
  classification  text        NOT NULL CHECK (classification IN ('exact_identifier', 'unique_initials_in_context',
                                                                 'surname_initial', 'ambiguous', 'conflicting')),
  rule_version    text        NOT NULL CHECK (length(btrim(rule_version)) > 0),
  evidence        jsonb       NOT NULL CHECK (jsonb_typeof(evidence) = 'array'),
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, candidate_id),
  UNIQUE (tenant_id, identity_id, entity_id, rule_version),
  FOREIGN KEY (tenant_id, identity_id) REFERENCES judgement.source_identity (tenant_id, identity_id),
  FOREIGN KEY (tenant_id, entity_id) REFERENCES judgement.entity (tenant_id, entity_id)
);

CREATE TABLE judgement.resolution_decision (
  tenant_id        uuid        NOT NULL,
  candidate_id     uuid        NOT NULL,
  version          integer     NOT NULL CHECK (version >= 1),
  decision_id      uuid        NOT NULL UNIQUE,
  outcome          text        NOT NULL CHECK (outcome IN ('approved', 'rejected', 'deferred')),
  decided_by       text        NOT NULL CHECK (length(btrim(decided_by)) > 0),
  rationale        text        NOT NULL CHECK (length(btrim(rationale)) > 0),
  decision_record  text        NOT NULL CHECK (length(btrim(decision_record)) > 0),
  decided_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, candidate_id, version),
  FOREIGN KEY (tenant_id, candidate_id) REFERENCES judgement.match_candidate (tenant_id, candidate_id)
);

-- The decision guard: consecutive versions; approvals only for synthetic test identities; never an approval of an
-- ambiguous or conflicting candidate; never two approved entities for one identity.
CREATE FUNCTION judgement.check_resolution_decision() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  c        record;
  latest   integer;
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
  SELECT coalesce(max(version), 0) INTO latest FROM judgement.resolution_decision
   WHERE tenant_id = NEW.tenant_id AND candidate_id = NEW.candidate_id;
  IF NEW.version <> latest + 1 THEN
    RAISE EXCEPTION 'resolution decision: version % is not consecutive (latest %)', NEW.version, latest
      USING ERRCODE = 'unique_violation';
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
CREATE TRIGGER resolution_decision_guard BEFORE INSERT ON judgement.resolution_decision
  FOR EACH ROW EXECUTE FUNCTION judgement.check_resolution_decision();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['judgement.source_identity', 'judgement.entity', 'judgement.alias',
                           'judgement.match_candidate', 'judgement.resolution_decision'] LOOP
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

-- Identities observed in protected (pay) sources are invisible to the application role; a candidate built on one
-- is then an unknown candidate to the decision guard, which fails closed.
CREATE POLICY protected_pay_for_app ON judgement.source_identity AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (data_class NOT IN ('sensitive_personal', 'payroll_aggregate'));

GRANT USAGE ON SCHEMA judgement TO veloxa_projection, veloxa_app;
-- Proposals (identities, entities, aliases, candidates) are written by the projection role; humans decide through
-- the application role, which can only APPEND decisions (the guard above still applies).
GRANT SELECT, INSERT ON judgement.source_identity, judgement.entity, judgement.alias, judgement.match_candidate
  TO veloxa_projection;
GRANT SELECT ON judgement.resolution_decision TO veloxa_projection;
GRANT SELECT ON judgement.source_identity, judgement.entity, judgement.alias, judgement.match_candidate,
  judgement.resolution_decision TO veloxa_app;
GRANT INSERT ON judgement.resolution_decision TO veloxa_app;
