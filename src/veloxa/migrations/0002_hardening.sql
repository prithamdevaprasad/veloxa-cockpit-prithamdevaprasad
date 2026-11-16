-- 0002 hardening (forward migration; 0001 is never rewritten once applied).
-- 1. Provenance: a record's raw_object hash must equal the raw manifest hash of the same file in the same batch.
-- 2. Time ordering: release_at <= extracted_at <= admitted_at <= sealed_at, enforced in the database.
-- 3. Protected pay: the application role reads neither individual payroll lines nor payroll aggregates.
--    Aggregates are released only through the disclosure auditor (ARCHITECTURE.md §15), which does not exist yet,
--    so today the application role cannot read them at all (fail closed).

-- 1. Provenance -----------------------------------------------------------------------------------------------
ALTER TABLE raw_index.raw_object
  ADD CONSTRAINT raw_object_path_hash_key UNIQUE (tenant_id, batch_seq, source_path, content_sha256);

-- Validating a new foreign key scans existing rows as the table owner. Under FORCE RLS that scan would be
-- filtered by the tenant policy (and fail closed with no tenant context), so FORCE is suspended for these two
-- tables only while the constraint is added and validated across ALL tenants, then restored. This runs inside
-- the migration's single transaction; the ACCESS EXCLUSIVE locks taken by ALTER TABLE block every other
-- session until commit, so no query can ever observe the tables without FORCE RLS.
ALTER TABLE ledger.source_record NO FORCE ROW LEVEL SECURITY;
ALTER TABLE raw_index.raw_object NO FORCE ROW LEVEL SECURITY;

ALTER TABLE ledger.source_record
  ADD CONSTRAINT source_record_raw_hash_fk
  FOREIGN KEY (tenant_id, batch_seq, source_path, raw_object)
  REFERENCES raw_index.raw_object (tenant_id, batch_seq, source_path, content_sha256);

ALTER TABLE ledger.source_record FORCE ROW LEVEL SECURITY;
ALTER TABLE raw_index.raw_object FORCE ROW LEVEL SECURITY;

-- 2. Time ordering --------------------------------------------------------------------------------------------
ALTER TABLE ledger.admission_batch
  ADD CONSTRAINT admitted_not_before_release CHECK (admitted_at >= release_at),
  ADD CONSTRAINT sealed_not_before_admitted CHECK (sealed_at IS NULL OR sealed_at >= admitted_at);

CREATE OR REPLACE FUNCTION ledger.check_seal() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  n_files bigint;
  n_records bigint;
  n_bad_extraction bigint;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'admission batches are never deleted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.sealed THEN
    RAISE EXCEPTION 'batch % is sealed and immutable', OLD.batch_seq USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT NEW.sealed OR NEW.sealed_at IS NULL
     OR (NEW.tenant_id, NEW.batch_seq, NEW.pack_id, NEW.release_at, NEW.admitted_at, NEW.manifest_sha256,
         NEW.file_count, NEW.record_count)
        IS DISTINCT FROM
        (OLD.tenant_id, OLD.batch_seq, OLD.pack_id, OLD.release_at, OLD.admitted_at, OLD.manifest_sha256,
         OLD.file_count, OLD.record_count) THEN
    RAISE EXCEPTION 'the only permitted change to a batch is sealing it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT count(*) INTO n_files FROM raw_index.raw_object
    WHERE tenant_id = OLD.tenant_id AND batch_seq = OLD.batch_seq;
  SELECT count(*),
         count(*) FILTER (WHERE extracted_at < OLD.release_at OR extracted_at > OLD.admitted_at)
    INTO n_records, n_bad_extraction
    FROM ledger.source_record
    WHERE tenant_id = OLD.tenant_id AND batch_seq = OLD.batch_seq;
  IF n_files <> OLD.file_count OR n_records <> OLD.record_count THEN
    RAISE EXCEPTION 'batch % cannot be sealed: % files and % records present, % and % declared',
      OLD.batch_seq, n_files, n_records, OLD.file_count, OLD.record_count USING ERRCODE = 'check_violation';
  END IF;
  IF n_bad_extraction > 0 THEN
    RAISE EXCEPTION 'batch % cannot be sealed: % records extracted outside [release_at, admitted_at]',
      OLD.batch_seq, n_bad_extraction USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;

-- 3. Protected pay --------------------------------------------------------------------------------------------
DROP POLICY no_individual_pay_for_app ON ledger.source_record;
CREATE POLICY protected_pay_for_app ON ledger.source_record AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (data_class NOT IN ('sensitive_personal', 'payroll_aggregate'));
