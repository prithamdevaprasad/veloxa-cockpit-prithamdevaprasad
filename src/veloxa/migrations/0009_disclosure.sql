-- 0009 C19 disclosure auditor: ledger, release gate and the only payroll value path (ARCHITECTURE.md §15; ADR-13).
--
-- The application role still cannot read payroll records (RLS, protected_pay_for_app). Two SECURITY DEFINER
-- functions are the ONLY database paths to payroll-derived information:
--   policy.payroll_structure(K)  -> who was paid in which month, with pro-rata day counts (the coefficient
--                                   structure M2 needs); NEVER an amount. Used only inside the auditor.
--   policy.release_payroll_sum(entry) -> a SUM of Gross, computed only for an `allowed` ledger entry of the calling
--                                   principal (committed, or written in this transaction), for a tenant and measure whose
--                                   release the owner has enabled. Otherwise it raises.
-- policy.disclosure_ledger: append-only, consecutive per (tenant, principal, measure); RLS isolates tenants AND
-- principals. Rows hold coefficient STRUCTURE (exact fractions as text) and decisions, never values.
-- policy.disclosure_release: owner-recorded enablement per (tenant, measure). It starts EMPTY: release is off.

CREATE TABLE policy.disclosure_ledger (
  tenant_id       uuid        NOT NULL,
  principal_id    text        NOT NULL CHECK (length(btrim(principal_id)) > 0),
  measure         text        NOT NULL CHECK (measure ~ '^[a-z_]+\.[a-z_]+$'),
  seq             bigint      NOT NULL CHECK (seq >= 1),
  entry_id        uuid        NOT NULL UNIQUE,
  created_at      timestamptz NOT NULL DEFAULT now(),
  xact_id         xid8        NOT NULL DEFAULT pg_current_xact_id(),
  request_key     text        NOT NULL CHECK (length(request_key) BETWEEN 8 AND 200),
  query_sha256    text        NOT NULL CHECK (query_sha256 ~ '^[0-9a-f]{64}$'),
  batch_seq       bigint      NOT NULL CHECK (batch_seq >= 1),
  statistic       text        NOT NULL,
  cohort_sizes    integer[]   NOT NULL,
  rows            jsonb       NOT NULL,   -- [{segment variable: "p/q"}]: structure only
  cells           text[]      NOT NULL,   -- the person|month cells the statistic covers (structure only)
  decision        text        NOT NULL CHECK (decision IN ('allowed', 'denied', 'refused')),
  reason          text        NOT NULL,
  k               integer     NOT NULL CHECK (k >= 2),
  engine_version  text        NOT NULL,
  PRIMARY KEY (tenant_id, principal_id, measure, seq),
  UNIQUE (tenant_id, principal_id, measure, request_key)
);

CREATE FUNCTION policy.check_disclosure_seq() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE latest bigint;
BEGIN
  SELECT max(seq) INTO latest FROM policy.disclosure_ledger
   WHERE tenant_id = NEW.tenant_id AND principal_id = NEW.principal_id AND measure = NEW.measure;
  IF NEW.seq <> coalesce(latest, 0) + 1 THEN
    RAISE EXCEPTION 'disclosure ledger sequence must be %, got %', coalesce(latest, 0) + 1, NEW.seq
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.principal_id <> current_setting('app.principal_id', true) THEN
    RAISE EXCEPTION 'a ledger entry belongs to the principal of the transaction' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER disclosure_ledger_seq BEFORE INSERT ON policy.disclosure_ledger
  FOR EACH ROW EXECUTE FUNCTION policy.check_disclosure_seq();

CREATE TABLE policy.disclosure_release (
  tenant_id        uuid        NOT NULL,
  measure          text        NOT NULL,
  enabled_by       text        NOT NULL CHECK (length(btrim(enabled_by)) > 0),
  enabled_on       date        NOT NULL,
  decision_record  text        NOT NULL CHECK (length(btrim(decision_record)) > 0),
  recorded_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, measure)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['policy.disclosure_ledger', 'policy.disclosure_release'] LOOP
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
-- One principal never sees (or consumes) another principal's disclosure history.
CREATE POLICY own_principal ON policy.disclosure_ledger AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (principal_id = current_setting('app.principal_id'))
  WITH CHECK (principal_id = current_setting('app.principal_id'));

GRANT SELECT, INSERT ON policy.disclosure_ledger TO veloxa_app;
GRANT SELECT ON policy.disclosure_release TO veloxa_app;    -- enablement is recorded by the owner, never the API

-- Payroll structure at K: latest version of each register row admitted in a sealed batch <= K, current tenant.
CREATE FUNCTION policy.payroll_structure(p_k bigint)
RETURNS TABLE (person text, month text, days_in_month integer, days_payable integer, record_id text, version integer)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT 'person:' || (r.payload->>'Emp ID'), r.payload->>'Month', (r.payload->>'Days in Month')::integer,
         (r.payload->>'Days Payable')::integer, r.record_id, r.version
    FROM ledger.source_record r
    JOIN ledger.admission_batch b ON b.tenant_id = r.tenant_id AND b.batch_seq = r.batch_seq
   WHERE r.tenant_id = current_setting('app.tenant_id')::uuid AND r.system = 'payroll_register'
     AND b.sealed AND r.batch_seq <= p_k
     AND NOT EXISTS (SELECT 1 FROM ledger.source_record l WHERE l.tenant_id = r.tenant_id
                       AND l.record_id = r.record_id AND l.version > r.version AND l.batch_seq <= p_k)
   ORDER BY 1, 2, r.record_id
$$;

-- The ONLY payroll value path.
CREATE FUNCTION policy.release_payroll_sum(p_entry uuid) RETURNS numeric
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE e record; total numeric;
BEGIN
  SELECT * INTO e FROM policy.disclosure_ledger
   WHERE entry_id = p_entry AND tenant_id = current_setting('app.tenant_id')::uuid
     AND principal_id = current_setting('app.principal_id');
  -- A visible entry is committed or belongs to this transaction; a rolled-back decision can never release.
  IF NOT FOUND OR e.decision <> 'allowed' THEN
    RAISE EXCEPTION 'no allowed disclosure decision in this transaction' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM policy.disclosure_release WHERE tenant_id = e.tenant_id AND measure = e.measure) THEN
    RAISE EXCEPTION 'release of % is not enabled for this tenant', e.measure USING ERRCODE = 'insufficient_privilege';
  END IF;
  -- The released cells (person|month) were fixed by the auditor and recorded with the decision.
  SELECT sum((r.payload->>'Gross (Rs.)')::numeric) INTO total
    FROM ledger.source_record r JOIN ledger.admission_batch b ON b.tenant_id = r.tenant_id AND b.batch_seq = r.batch_seq
   WHERE r.tenant_id = e.tenant_id AND r.system = 'payroll_register' AND b.sealed AND r.batch_seq <= e.batch_seq
     AND NOT EXISTS (SELECT 1 FROM ledger.source_record l WHERE l.tenant_id = r.tenant_id
                       AND l.record_id = r.record_id AND l.version > r.version AND l.batch_seq <= e.batch_seq)
     AND ('person:' || (r.payload->>'Emp ID')) || '|' || (r.payload->>'Month') = ANY(e.cells);
  RETURN total;
END
$$;

REVOKE ALL ON FUNCTION policy.payroll_structure(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION policy.release_payroll_sum(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.payroll_structure(bigint) TO veloxa_app;
GRANT EXECUTE ON FUNCTION policy.release_payroll_sum(uuid) TO veloxa_app;
