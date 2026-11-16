-- 0001 foundation: schema boundaries, tenant directory, evidence ledger, raw manifest, audit chain.
-- Runs as veloxa_migrator with SET ROLE veloxa_owner, so veloxa_owner owns every object (§9.4).
-- References: ARCHITECTURE.md §6.2 (schemas), §6.4 (DDL), §8.2 (sealed admission), §9.3 (RLS), §21.3 (audit).

-- ---------------------------------------------------------------------------
-- Schema boundaries (§6.2). Day 1 creates tables only in directory, raw_index, ledger and audit.
-- The other schemas exist so that later components land inside the agreed boundaries.
-- ---------------------------------------------------------------------------
CREATE SCHEMA directory;   -- C03 tenant directory (hackathon profile: one cell, so it lives here)
CREATE SCHEMA raw_index;
CREATE SCHEMA ledger;
CREATE SCHEMA judgement;
CREATE SCHEMA knowledge;
CREATE SCHEMA projection;
CREATE SCHEMA policy;
CREATE SCHEMA runtime;
CREATE SCHEMA audit;

-- ---------------------------------------------------------------------------
-- Tenant directory (C03). No tenant business data; not RLS-protected.
-- ---------------------------------------------------------------------------
CREATE TABLE directory.tenant (
  tenant_id   uuid        PRIMARY KEY,
  slug        text        NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9-]{2,40}$'),
  synthetic   boolean     NOT NULL,
  time_zone   text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Admission batches (§8.2). K = batch_seq. A batch is visible only once sealed.
-- ---------------------------------------------------------------------------
CREATE TABLE ledger.admission_batch (
  tenant_id       uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  batch_seq       bigint      NOT NULL CHECK (batch_seq >= 1),
  pack_id         text        NOT NULL,
  release_at      timestamptz NOT NULL,          -- knowledge time of the batch (KNOWN_AT maps dates to this)
  admitted_at     timestamptz NOT NULL,          -- wall clock when admission started (informational)
  manifest_sha256 text        NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
  file_count      integer     NOT NULL CHECK (file_count >= 0),
  record_count    integer     NOT NULL CHECK (record_count >= 0),
  sealed          boolean     NOT NULL DEFAULT false,
  sealed_at       timestamptz,
  PRIMARY KEY (tenant_id, batch_seq),
  UNIQUE (tenant_id, pack_id),
  CHECK (sealed = (sealed_at IS NOT NULL))
);

-- Raw manifest (§7.2): one row per file in a pack, addressed by content hash.
CREATE TABLE raw_index.raw_object (
  tenant_id       uuid        NOT NULL,
  batch_seq       bigint      NOT NULL,
  source_path     text        NOT NULL CHECK (source_path !~ '(^/|\.\.|\\)'),
  content_sha256  text        NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes      bigint      NOT NULL CHECK (size_bytes >= 0),
  kind            text        NOT NULL,
  system          text        NOT NULL,
  data_class      text        NOT NULL,
  record_mode     text        NOT NULL CHECK (record_mode IN ('rows', 'document', 'none')),
  PRIMARY KEY (tenant_id, batch_seq, source_path),
  FOREIGN KEY (tenant_id, batch_seq) REFERENCES ledger.admission_batch (tenant_id, batch_seq)
);

-- Source-record versions (§6.4). Insert-only; corrections are new versions.
CREATE TABLE ledger.source_record (
  tenant_id       uuid        NOT NULL,
  record_id       text        NOT NULL,          -- natural key: system + source path + locator
  version         integer     NOT NULL CHECK (version >= 1),
  supersedes      integer,
  batch_seq       bigint      NOT NULL,
  source_path     text        NOT NULL,
  raw_object      text        NOT NULL CHECK (raw_object ~ '^[0-9a-f]{64}$'),  -- content hash of the raw file
  locator         jsonb       NOT NULL,          -- where in the file (sheet/row, paragraphs, pages, message)
  kind            text        NOT NULL,
  system          text        NOT NULL,
  parser_version  text        NOT NULL,
  data_class      text        NOT NULL CHECK (data_class IN ('internal', 'confidential', 'personal',
                     'sensitive_personal', 'payroll_aggregate', 'private_comms', 'suppressed', 'assumption')),
  extracted_at    timestamptz NOT NULL,
  payload         jsonb       NOT NULL,
  PRIMARY KEY (tenant_id, record_id, version),
  CHECK (supersedes IS NULL OR supersedes = version - 1),
  FOREIGN KEY (tenant_id, batch_seq) REFERENCES ledger.admission_batch (tenant_id, batch_seq),
  FOREIGN KEY (tenant_id, batch_seq, source_path)
    REFERENCES raw_index.raw_object (tenant_id, batch_seq, source_path)
);
CREATE INDEX source_record_batch_idx ON ledger.source_record (tenant_id, batch_seq);

-- Audit events (§21.3): append-only, hash-chained per tenant. Never contains record values.
CREATE TABLE audit.event (
  tenant_id    uuid        NOT NULL,
  seq          bigint      NOT NULL CHECK (seq >= 1),
  event_id     uuid        NOT NULL DEFAULT uuidv7(),
  occurred_at  timestamptz NOT NULL,
  kind         text        NOT NULL,
  detail       jsonb       NOT NULL,
  prev_hash    text        CHECK (prev_hash IS NULL OR prev_hash ~ '^[0-9a-f]{64}$'),
  hash         text        NOT NULL CHECK (hash ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (tenant_id, seq),
  CHECK ((seq = 1) = (prev_hash IS NULL))
);

-- ---------------------------------------------------------------------------
-- Integrity triggers: history is never destroyed (§6.2 rules)
-- ---------------------------------------------------------------------------
CREATE FUNCTION ledger.reject_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'append-only table %.%: % is not permitted', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END
$$;

CREATE TRIGGER source_record_append_only BEFORE UPDATE OR DELETE ON ledger.source_record
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER source_record_no_truncate BEFORE TRUNCATE ON ledger.source_record
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER raw_object_append_only BEFORE UPDATE OR DELETE ON raw_index.raw_object
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER raw_object_no_truncate BEFORE TRUNCATE ON raw_index.raw_object
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit.event
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit.event
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER admission_batch_no_truncate BEFORE TRUNCATE ON ledger.admission_batch
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

-- Admission order (§8.2): a new batch must be the next sequence number and every earlier batch sealed.
CREATE FUNCTION ledger.check_admission_order() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  last_seq bigint;
  open_batches bigint;
BEGIN
  IF NEW.sealed OR NEW.sealed_at IS NOT NULL THEN
    RAISE EXCEPTION 'a batch must be inserted unsealed' USING ERRCODE = 'check_violation';
  END IF;
  SELECT coalesce(max(batch_seq), 0), count(*) FILTER (WHERE NOT sealed)
    INTO last_seq, open_batches
    FROM ledger.admission_batch WHERE tenant_id = NEW.tenant_id;
  IF open_batches > 0 THEN
    RAISE EXCEPTION 'an earlier batch is still unsealed' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.batch_seq <> last_seq + 1 THEN
    RAISE EXCEPTION 'batch_seq % is out of order (expected %)', NEW.batch_seq, last_seq + 1
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER admission_batch_order BEFORE INSERT ON ledger.admission_batch
  FOR EACH ROW EXECUTE FUNCTION ledger.check_admission_order();

-- Sealing is the only permitted change to a batch: unsealed -> sealed, once, with matching counts.
CREATE FUNCTION ledger.check_seal() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  n_files bigint;
  n_records bigint;
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
  SELECT count(*) INTO n_records FROM ledger.source_record
    WHERE tenant_id = OLD.tenant_id AND batch_seq = OLD.batch_seq;
  IF n_files <> OLD.file_count OR n_records <> OLD.record_count THEN
    RAISE EXCEPTION 'batch % cannot be sealed: % files and % records present, % and % declared',
      OLD.batch_seq, n_files, n_records, OLD.file_count, OLD.record_count USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER admission_batch_seal BEFORE UPDATE OR DELETE ON ledger.admission_batch
  FOR EACH ROW EXECUTE FUNCTION ledger.check_seal();

-- Nothing is added to a batch once it is sealed.
CREATE FUNCTION ledger.check_batch_open() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM ledger.admission_batch
             WHERE tenant_id = NEW.tenant_id AND batch_seq = NEW.batch_seq AND sealed) THEN
    RAISE EXCEPTION 'batch % is sealed; nothing can be added', NEW.batch_seq USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER source_record_batch_open BEFORE INSERT ON ledger.source_record
  FOR EACH ROW EXECUTE FUNCTION ledger.check_batch_open();
CREATE TRIGGER raw_object_batch_open BEFORE INSERT ON raw_index.raw_object
  FOR EACH ROW EXECUTE FUNCTION ledger.check_batch_open();

-- ---------------------------------------------------------------------------
-- Visibility (§8.4 T1): a record is visible iff its batch is sealed and batch_seq <= K.
-- K comes from the transaction-local setting app.batch_seq; an unset or empty value raises (fail closed).
-- ---------------------------------------------------------------------------
CREATE VIEW ledger.visible_source_record WITH (security_invoker = true) AS
  SELECT r.*
    FROM ledger.source_record r
    JOIN ledger.admission_batch b USING (tenant_id, batch_seq)
   WHERE b.sealed
     AND r.batch_seq <= current_setting('app.batch_seq')::bigint;

-- ---------------------------------------------------------------------------
-- Row-level security (§9.3): FORCE RLS, restrictive tenant policy with USING and WITH CHECK,
-- transaction-local context. An unset/empty/malformed app.tenant_id raises instead of matching.
-- ---------------------------------------------------------------------------
ALTER TABLE ledger.admission_batch ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.admission_batch FORCE ROW LEVEL SECURITY;
ALTER TABLE raw_index.raw_object  ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_index.raw_object  FORCE ROW LEVEL SECURITY;
ALTER TABLE ledger.source_record  ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.source_record  FORCE ROW LEVEL SECURITY;
ALTER TABLE audit.event           ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.event           FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON ledger.admission_batch AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation ON raw_index.raw_object AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation ON ledger.source_record AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation ON audit.event AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

-- Permissive grants inside the tenant boundary (restrictive policies alone admit no rows).
CREATE POLICY within_tenant ON ledger.admission_batch FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY within_tenant ON raw_index.raw_object  FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY within_tenant ON ledger.source_record  FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY within_tenant ON audit.event           FOR ALL USING (true) WITH CHECK (true);

-- Individual payroll lines are unreadable by the application role (§9.4).
CREATE POLICY no_individual_pay_for_app ON ledger.source_record AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (data_class <> 'sensitive_personal');

-- ---------------------------------------------------------------------------
-- Grants (§9.4). Application roles own nothing and get no UPDATE/DELETE/TRUNCATE on history.
-- ---------------------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA directory, raw_index, ledger, audit FROM PUBLIC;
REVOKE ALL ON SCHEMA directory, raw_index, ledger, judgement, knowledge, projection, policy, runtime, audit
  FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA ledger FROM PUBLIC;

GRANT USAGE ON SCHEMA directory, raw_index, ledger, audit TO veloxa_ingest, veloxa_app;

GRANT SELECT ON directory.tenant TO veloxa_ingest, veloxa_app;

GRANT SELECT, INSERT ON ledger.admission_batch TO veloxa_ingest;
GRANT UPDATE (sealed, sealed_at) ON ledger.admission_batch TO veloxa_ingest;
GRANT SELECT, INSERT ON raw_index.raw_object TO veloxa_ingest;
GRANT SELECT, INSERT ON ledger.source_record TO veloxa_ingest;
GRANT SELECT, INSERT ON audit.event TO veloxa_ingest;
GRANT SELECT ON ledger.visible_source_record TO veloxa_ingest;

GRANT SELECT ON ledger.admission_batch TO veloxa_app;
GRANT SELECT ON raw_index.raw_object TO veloxa_app;
GRANT SELECT ON ledger.source_record TO veloxa_app;   -- needed by the security_invoker view; RLS applies
GRANT SELECT ON ledger.visible_source_record TO veloxa_app;
GRANT INSERT ON audit.event TO veloxa_app;
