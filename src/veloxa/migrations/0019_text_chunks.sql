-- 0019 Retrieval units (§12, §13.2): paragraph/page/section chunks of admitted prose, and descriptive text of
-- admitted row exports, each with its EXACT locator inside the source record. A disposable projection (rebuilt
-- from the ledger, `veloxa project --rebuild`); never evidence itself: citations point at the source record plus the
-- chunk locator.
--
-- Row text is built only from string fields of rows whose data class is internal or confidential (never personal,
-- private communications or protected classes); figures stay with governed metrics.

CREATE TABLE projection.text_chunk (
  tenant_id         uuid        NOT NULL,
  batch_seq         bigint      NOT NULL,
  source_record_id  text        NOT NULL,
  source_version    integer     NOT NULL,
  chunk_no          integer     NOT NULL CHECK (chunk_no >= 0),
  chunk_locator     jsonb       NOT NULL CHECK (jsonb_typeof(chunk_locator) = 'object'),
  unit              text        NOT NULL CHECK (unit IN ('paragraph', 'page', 'section', 'message', 'row')),
  kind              text        NOT NULL,
  system            text        NOT NULL,
  data_class        text        NOT NULL,
  title             text        NOT NULL,
  body              text        NOT NULL,
  event_at          timestamptz,
  parties           text[]      NOT NULL DEFAULT '{}',
  tsv               tsvector    GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED,
  PRIMARY KEY (tenant_id, batch_seq, source_record_id, chunk_no),
  FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version)
);
CREATE INDEX text_chunk_tsv_idx ON projection.text_chunk USING gin (tsv);

ALTER TABLE projection.text_chunk ENABLE ROW LEVEL SECURITY;
ALTER TABLE projection.text_chunk FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON projection.text_chunk AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY within_tenant ON projection.text_chunk FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY protected_pay_for_app ON projection.text_chunk AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (data_class NOT IN ('sensitive_personal', 'payroll_aggregate'));
GRANT SELECT, INSERT, DELETE ON projection.text_chunk TO veloxa_projection;
GRANT SELECT ON projection.text_chunk TO veloxa_app;
