-- 0003 projections (ARCHITECTURE.md §6.2, §10, §11, §12): rebuildable read models over SEALED ledger records.
-- Every row carries the batch_seq that produced it, so the K predicate (§8.4 T1) applies to projections too.
-- Projections are written only by veloxa_projection and can be dropped and rebuilt from the ledger at any time.

-- People (from the HR export). Status-like fields are "as at extraction" of their batch (§8.4 T3).
CREATE TABLE projection.person (
  tenant_id         uuid    NOT NULL,
  batch_seq         bigint  NOT NULL,
  entity_id         text    NOT NULL,           -- 'person:' || source employee id
  emp_id            text    NOT NULL,
  full_name         text    NOT NULL,
  work_email        text,
  department        text    NOT NULL,
  title             text    NOT NULL,
  manager_emp_id    text,
  join_date         date    NOT NULL,
  location          text,
  status            text    NOT NULL,
  exit_date         date,
  data_class        text    NOT NULL,
  source_record_id  text    NOT NULL,
  source_version    integer NOT NULL,
  PRIMARY KEY (tenant_id, batch_seq, entity_id),
  FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version)
);

-- Ledger lines (from the GL export). Payroll lines keep their payroll_aggregate class (§15.3).
CREATE TABLE projection.ledger_line (
  tenant_id         uuid    NOT NULL,
  batch_seq         bigint  NOT NULL,
  voucher           text    NOT NULL,
  txn_date          date    NOT NULL,
  vendor            text    NOT NULL,
  cost_centre       text    NOT NULL,
  amount_inr        bigint  NOT NULL,
  narration         text    NOT NULL,
  data_class        text    NOT NULL,
  source_record_id  text    NOT NULL,
  source_version    integer NOT NULL,
  PRIMARY KEY (tenant_id, batch_seq, voucher),
  FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version)
);

-- Figures as REPORTED in formal documents (board updates' key-numbers tables). Values are kept exactly as
-- written (with their unit text); no arithmetic is performed on them.
CREATE TABLE projection.reported_figure (
  tenant_id         uuid    NOT NULL,
  batch_seq         bigint  NOT NULL,
  source_record_id  text    NOT NULL,
  source_version    integer NOT NULL,
  period            text,                      -- the period the document covers, as written
  issued_on         date,                      -- the date the document says it was sent
  metric            text    NOT NULL,
  value_text        text    NOT NULL,
  data_class        text    NOT NULL,
  PRIMARY KEY (tenant_id, batch_seq, source_record_id, metric),
  FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version)
);

-- Prose for lexical retrieval (§12.1): emails, transcripts, documents, brief, readme.
CREATE TABLE projection.document_text (
  tenant_id         uuid    NOT NULL,
  batch_seq         bigint  NOT NULL,
  source_record_id  text    NOT NULL,
  source_version    integer NOT NULL,
  kind              text    NOT NULL,
  system            text    NOT NULL,
  data_class        text    NOT NULL,
  title             text    NOT NULL,
  body              text    NOT NULL,
  event_at          timestamptz,               -- e.g. an email's Date header; NULL when the source states none
  parties           text[]  NOT NULL DEFAULT '{}',  -- lower-cased addresses of an email's sender and recipients
  tsv               tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED,
  PRIMARY KEY (tenant_id, batch_seq, source_record_id),
  FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version)
);
CREATE INDEX document_text_tsv_idx ON projection.document_text USING gin (tsv);

-- Which sealed batches each tenant's projections were built from (rebuild bookkeeping).
CREATE TABLE projection.build (
  tenant_id   uuid        NOT NULL,
  batch_seq   bigint      NOT NULL,
  built_at    timestamptz NOT NULL,
  rows_built  integer     NOT NULL,
  PRIMARY KEY (tenant_id, batch_seq)
);

-- Row-level security: identical model to the ledger (§9.3).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['projection.person', 'projection.ledger_line', 'projection.reported_figure',
                           'projection.document_text', 'projection.build'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s AS RESTRICTIVE FOR ALL'
                   ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
                   ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    EXECUTE format('CREATE POLICY within_tenant ON %s FOR ALL USING (true) WITH CHECK (true)', t);
  END LOOP;
END
$$;

-- Protected pay (§9.4, §15): the application role never reads payroll lines or payroll aggregates directly,
-- on ANY table that carries a data class (the self-check verifies this for every such table).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['projection.person', 'projection.ledger_line', 'projection.reported_figure',
                           'projection.document_text'] LOOP
    EXECUTE format('CREATE POLICY protected_pay_for_app ON %s AS RESTRICTIVE FOR SELECT TO veloxa_app'
                   ' USING (data_class NOT IN (''sensitive_personal'', ''payroll_aggregate''))', t);
  END LOOP;
END
$$;

-- The raw manifest also carries a data class: the application role does not see manifest entries (paths, hashes)
-- of protected-pay files either. Found by the generalised self-check.
CREATE POLICY protected_pay_for_app ON raw_index.raw_object AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (data_class NOT IN ('sensitive_personal', 'payroll_aggregate'));

-- Grants: projection role writes (and may rebuild) projections; application role reads them.
GRANT USAGE ON SCHEMA projection TO veloxa_projection, veloxa_app;
GRANT USAGE ON SCHEMA directory, ledger, audit TO veloxa_projection;
GRANT SELECT ON directory.tenant TO veloxa_projection;
GRANT SELECT ON ledger.admission_batch, ledger.source_record TO veloxa_projection;
GRANT SELECT, INSERT, DELETE ON projection.person, projection.ledger_line, projection.reported_figure,
  projection.document_text, projection.build TO veloxa_projection;
GRANT INSERT ON audit.event TO veloxa_projection;
GRANT SELECT ON audit.event TO veloxa_projection;
GRANT SELECT ON projection.person, projection.ledger_line, projection.reported_figure, projection.document_text,
  projection.build TO veloxa_app;
