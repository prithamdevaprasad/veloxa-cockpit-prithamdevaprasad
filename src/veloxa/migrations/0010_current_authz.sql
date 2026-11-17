-- 0010 current authorization (ARCHITECTURE.md §10.1 layers 2-3, §10.3, §25.1 hackathon profile; C02, C20, C21).
--
-- policy.relationship_tuple: the CURRENT ReBAC projection as relationship tuples in a SpiceDB-compatible shape
-- (resource_type:resource_id#relation@subject_type:subject_id[#subject_relation]), behind RelationshipStore. It is
-- never a source of history (that is C32 over ledger ACL history). Revocation is an appended row in
-- policy.relationship_revocation; nothing is updated in place.
-- policy.revoked_token: revoked session/token identifiers (jti) for C02 identity validation.
-- All three are written only by privileged administration, never by the API; the application role reads them.

CREATE TABLE policy.relationship_tuple (
  tenant_id         uuid        NOT NULL,
  tuple_id          uuid        NOT NULL,
  resource_type     text        NOT NULL CHECK (resource_type ~ '^[a-z_]+$'),
  resource_id       text        NOT NULL CHECK (length(resource_id) > 0),
  relation          text        NOT NULL CHECK (relation ~ '^[a-z_]+$'),
  subject_type      text        NOT NULL CHECK (subject_type ~ '^[a-z_]+$'),
  subject_id        text        NOT NULL CHECK (length(subject_id) > 0),
  subject_relation  text        CHECK (subject_relation IS NULL OR subject_relation ~ '^[a-z_]+$'),
  source            text        NOT NULL CHECK (length(btrim(source)) > 0),   -- where the relationship comes from
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        text        NOT NULL DEFAULT current_user,
  PRIMARY KEY (tenant_id, tuple_id)
);
CREATE INDEX relationship_tuple_lookup ON policy.relationship_tuple
  (tenant_id, resource_type, resource_id, relation);

CREATE TABLE policy.relationship_revocation (
  tenant_id   uuid        NOT NULL,
  tuple_id    uuid        NOT NULL,
  revoked_at  timestamptz NOT NULL DEFAULT now(),
  revoked_by  text        NOT NULL DEFAULT current_user,
  PRIMARY KEY (tenant_id, tuple_id),
  FOREIGN KEY (tenant_id, tuple_id) REFERENCES policy.relationship_tuple (tenant_id, tuple_id)
);

CREATE TABLE policy.revoked_token (
  tenant_id   uuid        NOT NULL,
  jti         text        NOT NULL CHECK (length(jti) BETWEEN 8 AND 200),
  revoked_at  timestamptz NOT NULL DEFAULT now(),
  reason      text        NOT NULL CHECK (length(btrim(reason)) > 0),
  PRIMARY KEY (tenant_id, jti)
);

-- C19 readable-document background: which payroll-aggregate postings exist at K (structure only, never an
-- amount). Whether a principal can READ one is decided by current authorization, not here.
CREATE FUNCTION policy.payroll_aggregate_documents(p_k bigint) RETURNS TABLE (record_id text, txn_date date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT r.record_id, (r.payload->>'Txn Date')::date FROM ledger.source_record r
    JOIN ledger.admission_batch b ON b.tenant_id = r.tenant_id AND b.batch_seq = r.batch_seq
   WHERE r.tenant_id = current_setting('app.tenant_id')::uuid AND r.data_class = 'payroll_aggregate'
     AND r.system = 'zoho_books' AND b.sealed AND r.batch_seq <= p_k
     AND NOT EXISTS (SELECT 1 FROM ledger.source_record l WHERE l.tenant_id = r.tenant_id
                       AND l.record_id = r.record_id AND l.version > r.version AND l.batch_seq <= p_k)
   ORDER BY 1
$$;
REVOKE ALL ON FUNCTION policy.payroll_aggregate_documents(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.payroll_aggregate_documents(bigint) TO veloxa_app;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['policy.relationship_tuple', 'policy.relationship_revocation', 'policy.revoked_token']
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s AS RESTRICTIVE FOR ALL'
                   ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
                   ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    EXECUTE format('CREATE POLICY within_tenant ON %s FOR ALL USING (true) WITH CHECK (true)', t);
    EXECUTE format('CREATE TRIGGER %s_append_only BEFORE UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION'
                   ' ledger.reject_mutation()', replace(t, '.', '_'), t);
    EXECUTE format('GRANT SELECT ON %s TO veloxa_app', t);
  END LOOP;
END
$$;
