-- 0025: one source-object registry for every ingestion route (upload, data room, Jira, GitHub, Gmail, future
-- connectors). Owner brief 20 Nov 2026 (final phase, section 2).
-- * Every object a route delivers is registered with its route, canonical identity (`object_key`, e.g.
--   email:<Message-ID>, github:<repo>:commit:<sha>, jira:<KEY>, file:<sha256>), external ID, version, URL, the
--   access scope it was read under, and a deterministic content fingerprint.
-- * Classification is decided in the application (`veloxa.identity`) and recorded here; NOTHING is merged:
--     new | new_version | duplicate (same key and version: not resubmitted)
--     | same_content_other_identity (same fingerprint, different key: both kept, linked)
--     | review_candidate (near-duplicate text: a person decides; never merged automatically).
-- * Access is never widened: each row keeps its own access scope; a link to an identical copy grants nothing.
-- Append-only and tenant-isolated like the rest of runtime.*.

CREATE TABLE runtime.source_object (
  tenant_id        uuid        NOT NULL,
  registration_id  uuid        NOT NULL DEFAULT uuidv7(),
  route            text        NOT NULL CHECK (route ~ '^[a-z][a-z0-9_]{1,30}$'),
  object_key       text        NOT NULL CHECK (length(object_key) BETWEEN 3 AND 600),
  external_id      text        CHECK (external_id IS NULL OR length(external_id) <= 600),
  version          text        NOT NULL DEFAULT '' CHECK (length(version) <= 200),
  fingerprint      text        NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
  shingles         bigint[]    NOT NULL DEFAULT '{}',
  url              text        CHECK (url IS NULL OR length(url) <= 2000),
  access_scope     jsonb       NOT NULL DEFAULT '{}'::jsonb,
  observed_at      timestamptz,
  classification   text        NOT NULL CHECK (classification IN ('new', 'new_version', 'duplicate',
                                                'same_content_other_identity', 'review_candidate')),
  related_keys     text[]      NOT NULL DEFAULT '{}',
  similarity       real        CHECK (similarity IS NULL OR similarity BETWEEN 0 AND 1),
  submission_id    uuid,
  actor            text        NOT NULL,
  at               timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, registration_id)
);
CREATE INDEX source_object_key ON runtime.source_object (tenant_id, object_key, version);
CREATE INDEX source_object_fp ON runtime.source_object (tenant_id, fingerprint);

ALTER TABLE runtime.source_object ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime.source_object FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime.source_object AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY within_tenant ON runtime.source_object FOR ALL USING (true) WITH CHECK (true);
CREATE TRIGGER runtime_source_object_append_only BEFORE UPDATE OR DELETE ON runtime.source_object
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER runtime_source_object_no_truncate BEFORE TRUNCATE ON runtime.source_object
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
GRANT SELECT, INSERT ON runtime.source_object TO veloxa_app;
