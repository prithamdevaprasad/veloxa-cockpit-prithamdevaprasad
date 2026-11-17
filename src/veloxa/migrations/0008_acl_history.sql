-- 0008 historical ACL, identity and group-membership history (ARCHITECTURE.md §6.2, §10.2, §10.3, §10.7; C32).
--
-- ACL history is ADMITTED evidence: connector ACL, identity-link, membership, resource-existence and ACL-state
-- records arrive in a release pack, are admitted into ledger.source_record (versioned, sealed, T8 corrections)
-- and are projected here as typed history items. This table is a rebuildable PROJECTION of that ledger history;
-- the ledger stays canonical. Bitemporal: `valid` is when the item held in the world; `recorded_hi` is the upper
-- bound of when the source recorded it (its stated recording time, else the extraction time, T4).
-- Nothing is ever updated in place: a change is a new record version admitted in a later batch.
--
-- No real release pack carries ACL history today; until one does, every historical ACL evaluation is `unknown`.
-- Email parties in projection.document_text are NOT ACL evidence and are never projected here.

CREATE TABLE projection.acl_history (
  tenant_id         uuid        NOT NULL,
  batch_seq         bigint      NOT NULL,
  source_record_id  text        NOT NULL,
  source_version    integer     NOT NULL,
  data_class        text        NOT NULL,
  item_type         text        NOT NULL CHECK (item_type IN ('identity_link', 'membership', 'acl', 'resource',
                                                             'acl_state', 'invalid')),
  valid             tstzrange,                 -- identity_link, membership, acl; NULL for the others
  recorded_hi       timestamptz NOT NULL,      -- upper(R), exclusive
  recorded_basis    text        NOT NULL CHECK (recorded_basis IN ('source_recorded', 'extraction_upper_bound')),
  extracted_at      timestamptz NOT NULL,      -- E
  payload           jsonb       NOT NULL,      -- the typed fields, as projected (or the invalid row and its reason)
  PRIMARY KEY (tenant_id, batch_seq, source_record_id),
  FOREIGN KEY (tenant_id, source_record_id, source_version)
    REFERENCES ledger.source_record (tenant_id, record_id, version),
  CHECK ((item_type IN ('identity_link', 'membership', 'acl')) = (valid IS NOT NULL)),
  CHECK (valid IS NULL OR NOT isempty(valid))
);
CREATE INDEX acl_history_type ON projection.acl_history (tenant_id, item_type);

ALTER TABLE projection.acl_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE projection.acl_history FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON projection.acl_history AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY within_tenant ON projection.acl_history FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY protected_pay_for_app ON projection.acl_history AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (data_class NOT IN ('sensitive_personal', 'payroll_aggregate'));

GRANT SELECT, INSERT, DELETE ON projection.acl_history TO veloxa_projection;
GRANT SELECT ON projection.acl_history TO veloxa_app;   -- read-only: the evaluator never writes history
