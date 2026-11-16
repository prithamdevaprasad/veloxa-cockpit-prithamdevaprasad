-- 0006 detector runs and risk signals (ARCHITECTURE.md §16.1 steps 1 and 5, §8.4 T1 and T5, §9.3).
--
-- runtime.detector_run: one row per distinct run of the detector registry at a read basis, with the detector
-- versions, a hash of the configuration they used (e.g. owner-approved figure mappings) and a hash of the output.
-- An identical run at the same basis and configuration is recorded once (reproducibility).
--
-- runtime.risk_signal: the signals that run emitted, exactly as emitted. These are DERIVED runtime objects, not a
-- system of record: every signal cites admitted ledger records and can be recomputed from them. Append-only.
-- The database itself enforces that cited records are admitted in a sealed batch at or below the run's K, that
-- lineage never exceeds K, and that a missing, unmapped, blocked or unquantified signal carries no number.

CREATE TABLE runtime.detector_run (
  tenant_id        uuid        NOT NULL,
  run_id           uuid        NOT NULL,
  detected_at      timestamptz NOT NULL,
  batch_seq        bigint      NOT NULL CHECK (batch_seq >= 1),
  mode             text        NOT NULL CHECK (mode IN ('CURRENT', 'KNOWN_AT', 'RECORDED_BY', 'VALID_AT')),
  as_of            text,
  assessment_date  date        NOT NULL,
  detectors        jsonb       NOT NULL,       -- [id@version, ...]
  config_sha256    text        NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  output_sha256    text        NOT NULL CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
  signal_count     integer     NOT NULL CHECK (signal_count >= 0),
  withheld         jsonb       NOT NULL,       -- why signals were NOT emitted (provenance of withheld signals)
  PRIMARY KEY (tenant_id, run_id),
  FOREIGN KEY (tenant_id, batch_seq) REFERENCES ledger.admission_batch (tenant_id, batch_seq)
);
CREATE UNIQUE INDEX detector_run_identity ON runtime.detector_run
  (tenant_id, batch_seq, mode, coalesce(as_of, ''), assessment_date, config_sha256, output_sha256);

CREATE TABLE runtime.risk_signal (
  tenant_id          uuid    NOT NULL,
  run_id             uuid    NOT NULL,
  signal_id          text    NOT NULL CHECK (signal_id ~ '^S-[0-9a-f]{16}$'),
  detector           text    NOT NULL CHECK (detector ~ '^[a-z0-9_]+@[0-9]+$'),
  criterion          text    NOT NULL CHECK (criterion IN ('C1', 'C2', 'C3', 'C4')),
  signal_type        text    NOT NULL,
  status             text    NOT NULL CHECK (status IN ('active', 'provisional', 'unquantified',
                       'unmapped_requires_owner_approval', 'blocked_owner_decision', 'missing_evidence')),
  lineage_batch_seq  bigint  NOT NULL CHECK (lineage_batch_seq >= 0),
  evidence           text[]  NOT NULL,
  payload            jsonb   NOT NULL,
  PRIMARY KEY (tenant_id, run_id, signal_id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES runtime.detector_run (tenant_id, run_id),
  CHECK (status NOT IN ('active', 'provisional') OR cardinality(evidence) > 0),
  -- Missing is never zero: these statuses carry no number.
  CHECK (status NOT IN ('unquantified', 'unmapped_requires_owner_approval', 'blocked_owner_decision',
                        'missing_evidence')
         OR (payload -> 'value' ->> 'low' IS NULL AND payload -> 'value' ->> 'high' IS NULL))
);
CREATE INDEX risk_signal_criterion ON runtime.risk_signal (tenant_id, run_id, criterion);

CREATE FUNCTION runtime.check_risk_signal() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE k bigint; missing text;
BEGIN
  SELECT batch_seq INTO k FROM runtime.detector_run WHERE tenant_id = NEW.tenant_id AND run_id = NEW.run_id;
  IF NEW.lineage_batch_seq > k THEN
    RAISE EXCEPTION 'signal lineage K=% exceeds the run K=%', NEW.lineage_batch_seq, k
      USING ERRCODE = 'check_violation';
  END IF;
  SELECT e INTO missing FROM unnest(NEW.evidence) AS e
   WHERE NOT EXISTS (SELECT 1 FROM ledger.source_record r
                       JOIN ledger.admission_batch b ON b.tenant_id = r.tenant_id AND b.batch_seq = r.batch_seq
                      WHERE r.tenant_id = NEW.tenant_id AND r.record_id = e AND b.sealed AND r.batch_seq <= k)
   LIMIT 1;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'signal cites % which is not admitted and sealed at K=%', missing, k
      USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER risk_signal_check BEFORE INSERT ON runtime.risk_signal
  FOR EACH ROW EXECUTE FUNCTION runtime.check_risk_signal();

CREATE TRIGGER detector_run_append_only BEFORE UPDATE OR DELETE ON runtime.detector_run
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER detector_run_no_truncate BEFORE TRUNCATE ON runtime.detector_run
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER risk_signal_append_only BEFORE UPDATE OR DELETE ON runtime.risk_signal
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER risk_signal_no_truncate BEFORE TRUNCATE ON runtime.risk_signal
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['runtime.detector_run', 'runtime.risk_signal'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s AS RESTRICTIVE FOR ALL'
                   ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
                   ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    EXECUTE format('CREATE POLICY within_tenant ON %s FOR ALL USING (true) WITH CHECK (true)', t);
  END LOOP;
END
$$;

GRANT SELECT, INSERT ON runtime.detector_run, runtime.risk_signal TO veloxa_app;
