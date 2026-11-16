-- 0005 briefing and the D-15 governance gate (ARCHITECTURE.md §16.1 step 5, §16.7, §19.3; DECISIONS.md D-15).
--
-- policy.ranking_decision: owner-approved D-15 ranking policies (weights, anchors, guardrail tiers, gate
-- thresholds). Rows are recorded only by the privileged command line (`veloxa policy record-d15`), never through
-- the API: the application role can only read them. Append-only and versioned: changing a policy means recording
-- a NEW version; an earlier version is never rewritten. While no row exists, D-15 is pending and weighted ranking
-- cannot run.
--
-- runtime.briefing_snapshot: every briefing as served, with its read basis, ranking mode, the D-15 policy state
-- and version in force, the engine version and an input hash. Append-only: a historical briefing keeps the
-- policy version it was produced under, even after a newer policy is recorded.

CREATE TABLE policy.ranking_decision (
  tenant_id        uuid        NOT NULL,
  version          integer     NOT NULL CHECK (version >= 1),
  decision_ref     text        NOT NULL CHECK (decision_ref = 'D-15'),
  status           text        NOT NULL CHECK (status = 'approved'),
  approved_by      text        NOT NULL CHECK (length(btrim(approved_by)) > 0),
  approved_on      date        NOT NULL,
  decision_record  text        NOT NULL CHECK (length(btrim(decision_record)) > 0),  -- where the owner recorded it
  content          jsonb       NOT NULL,       -- weights, anchors, guardrail tiers, gate thresholds, as approved
  content_sha256   text        NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at      timestamptz NOT NULL DEFAULT now(),
  recorded_by_role text        NOT NULL DEFAULT current_user,
  PRIMARY KEY (tenant_id, version)
);

-- Versions are consecutive per tenant (1, 2, 3 ...): a gap or a reuse is rejected.
CREATE FUNCTION policy.check_ranking_decision_version() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE latest integer;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('ranking_decision:' || NEW.tenant_id::text));
  SELECT max(version) INTO latest FROM policy.ranking_decision WHERE tenant_id = NEW.tenant_id;
  IF NEW.version <> coalesce(latest, 0) + 1 THEN
    RAISE EXCEPTION 'D-15 policy version must be %, got %', coalesce(latest, 0) + 1, NEW.version
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER ranking_decision_version BEFORE INSERT ON policy.ranking_decision
  FOR EACH ROW EXECUTE FUNCTION policy.check_ranking_decision_version();

CREATE TABLE runtime.briefing_snapshot (
  tenant_id        uuid        NOT NULL,
  snapshot_id      uuid        NOT NULL,
  created_at       timestamptz NOT NULL,
  principal_id     text        NOT NULL,
  batch_seq        bigint      NOT NULL CHECK (batch_seq >= 0),
  mode             text        NOT NULL CHECK (mode IN ('CURRENT', 'KNOWN_AT', 'RECORDED_BY', 'VALID_AT')),
  as_of            text,
  engine_version   text        NOT NULL,
  ranking_mode     text        NOT NULL CHECK (ranking_mode IN ('unweighted_partial_order', 'weighted')),
  policy_status    text        NOT NULL CHECK (policy_status IN ('pending', 'approved')),
  policy_version   integer,                    -- NULL while D-15 is pending
  policy_sha256    text,
  input_hash       text        NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
  payload          jsonb       NOT NULL,       -- the briefing exactly as served
  PRIMARY KEY (tenant_id, snapshot_id),
  CHECK ((policy_status = 'pending') = (policy_version IS NULL)),
  CHECK ((policy_version IS NULL) = (policy_sha256 IS NULL)),
  -- Weighted ranking needs an approved D-15 policy; it can never be recorded as served without one.
  CHECK (ranking_mode <> 'weighted' OR policy_status = 'approved')
);
CREATE UNIQUE INDEX briefing_snapshot_identity ON runtime.briefing_snapshot
  (tenant_id, principal_id, batch_seq, mode, coalesce(as_of, ''), engine_version, coalesce(policy_version, 0),
   input_hash);

CREATE TRIGGER ranking_decision_append_only BEFORE UPDATE OR DELETE ON policy.ranking_decision
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER ranking_decision_no_truncate BEFORE TRUNCATE ON policy.ranking_decision
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER briefing_snapshot_append_only BEFORE UPDATE OR DELETE ON runtime.briefing_snapshot
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER briefing_snapshot_no_truncate BEFORE TRUNCATE ON runtime.briefing_snapshot
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['policy.ranking_decision', 'runtime.briefing_snapshot'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s AS RESTRICTIVE FOR ALL'
                   ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
                   ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    EXECUTE format('CREATE POLICY within_tenant ON %s FOR ALL USING (true) WITH CHECK (true)', t);
  END LOOP;
END
$$;

GRANT USAGE ON SCHEMA policy TO veloxa_app;
GRANT SELECT ON policy.ranking_decision TO veloxa_app;          -- read-only: the API cannot record a policy
GRANT SELECT, INSERT ON runtime.briefing_snapshot TO veloxa_app;
