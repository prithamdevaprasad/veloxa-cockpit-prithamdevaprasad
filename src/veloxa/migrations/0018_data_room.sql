-- 0018 Company data room: submissions, immutable originals, review and admission authorisation (§7.2, §8.2;
-- C05, C06; owner recommendations K-05 and K-13 — separation of duties — NOT approved real-data policy).
--
-- Nothing here is evidence. A submission becomes evidence ONLY through the existing admission sequencer
-- (ingest/pipeline.admit_pack): one sealed batch per authorised submission, in the existing ledger.
--
--   policy.catalogue_rule        tenant source types as versioned DATA (kind, system, data class, record mode,
--                                contract, business key). Only an admission authoriser may add one, with a decision
--                                record. The hackathon tenant keeps config/source_catalogue.toml.
--   intake.stored_object         the registry of immutable originals (content-addressed by SHA-256).
--   intake.submission            a candidate pack: department, source type, submitter.
--   intake.submission_file       its files (name, SHA-256, size, media type).
--   intake.submission_event      the state history (append-only); the latest event is the current state.
--   intake.validation_report     the dry-run report (same functions, bytes and hashes as admission).
--
-- Permissions are checked IN THE DATABASE by the transition function, not only by the API:
--   submit                       department contributor or head of the submission's department;
--   review (accept / reject)     department reviewer, and never the submitter;
--   authorise / decline          tenant admission_authoriser, never the submitter and never the reviewer;
--   validation and admission     the system (veloxa_ingest), never the API role.

CREATE SCHEMA intake;
GRANT USAGE ON SCHEMA intake TO veloxa_app, veloxa_ingest, veloxa_projection;
-- The system worker (ingest) reads the approved source type and the tenant's slug and time zone.
GRANT USAGE ON SCHEMA policy, directory TO veloxa_ingest, veloxa_projection;
GRANT SELECT ON directory.tenant TO veloxa_ingest, veloxa_projection;

CREATE TABLE policy.catalogue_rule (
  tenant_id        uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  source_type      text        NOT NULL CHECK (source_type ~ '^[a-z][a-z0-9_]{1,39}$'),
  version          integer     NOT NULL CHECK (version >= 1),
  title            text        NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 120),
  department_id    text        NOT NULL,
  kind             text        NOT NULL CHECK (kind IN ('export', 'document', 'email', 'transcript', 'brief',
                                                       'readme')),
  system           text        NOT NULL CHECK (system ~ '^[a-z][a-z0-9_]{1,39}$'),
  data_class       text        NOT NULL CHECK (data_class IN ('internal', 'confidential', 'personal',
                                                             'private_comms')),       -- protected classes: never
  records          text        NOT NULL CHECK (records IN ('rows', 'document')),
  file_glob        text        NOT NULL CHECK (file_glob ~ '^\*\.[a-z0-9]{2,5}$'),
  contract         jsonb       CHECK (contract IS NULL OR jsonb_typeof(contract) = 'object'),
  key_columns      text[]      NOT NULL DEFAULT '{}',
  active           boolean     NOT NULL,
  decision_record  text        NOT NULL CHECK (length(btrim(decision_record)) > 0),
  approved_by      text        NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, source_type, version),
  FOREIGN KEY (tenant_id, department_id) REFERENCES directory.department (tenant_id, department_id),
  CHECK (records = 'rows' OR (contract IS NULL AND key_columns = '{}'))
);

CREATE TABLE intake.stored_object (
  tenant_id    uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  sha256       text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes   bigint      NOT NULL CHECK (size_bytes >= 0),
  media_type   text        NOT NULL,
  stored_by    text        NOT NULL,
  stored_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, sha256)
);

CREATE TABLE intake.submission (
  tenant_id       uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  submission_id   uuid        NOT NULL,
  department_id   text        NOT NULL,
  source_type     text        NOT NULL,
  rule_version    integer     NOT NULL,
  title           text        NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  data_period     text        CHECK (data_period IS NULL OR length(data_period) <= 60),
  submitted_by    text        NOT NULL,
  submitted_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, submission_id),
  FOREIGN KEY (tenant_id, department_id) REFERENCES directory.department (tenant_id, department_id),
  FOREIGN KEY (tenant_id, source_type, rule_version)
    REFERENCES policy.catalogue_rule (tenant_id, source_type, version)
);

CREATE TABLE intake.submission_file (
  tenant_id      uuid    NOT NULL,
  submission_id  uuid    NOT NULL,
  file_name      text    NOT NULL CHECK (file_name ~ '^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,150}$'
                                         AND file_name !~ '\.\.'),
  sha256         text    NOT NULL,
  size_bytes     bigint  NOT NULL,
  media_type     text    NOT NULL,
  PRIMARY KEY (tenant_id, submission_id, file_name),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES intake.submission (tenant_id, submission_id),
  FOREIGN KEY (tenant_id, sha256) REFERENCES intake.stored_object (tenant_id, sha256)
);

CREATE TABLE intake.submission_event (
  tenant_id      uuid        NOT NULL,
  event_id       uuid        NOT NULL DEFAULT uuidv7(),
  submission_id  uuid        NOT NULL,
  state          text        NOT NULL CHECK (state IN ('uploaded', 'validating', 'validation_failed',
                    'awaiting_review', 'rejected', 'awaiting_authorisation', 'declined', 'authorised', 'admitting',
                    'admission_failed', 'admitted', 'sealed', 'projected')),
  actor          text        NOT NULL,
  reason         text,
  detail         jsonb       NOT NULL DEFAULT '{}',
  at             timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES intake.submission (tenant_id, submission_id)
);

CREATE TABLE intake.validation_report (
  tenant_id        uuid        NOT NULL,
  submission_id    uuid        NOT NULL,
  manifest_sha256  text,
  ok               boolean     NOT NULL,
  report           jsonb       NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, submission_id),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES intake.submission (tenant_id, submission_id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['policy.catalogue_rule', 'intake.stored_object', 'intake.submission',
                           'intake.submission_file', 'intake.submission_event', 'intake.validation_report'] LOOP
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
    EXECUTE format('GRANT SELECT ON %s TO veloxa_app, veloxa_ingest, veloxa_projection', t);
  END LOOP;
END
$$;

-- Every table with a data_class column carries the protected-pay policy for the API role (startup self-check).
-- Catalogue rules can never name a protected class (CHECK above); the policy is the uniform second guard.
CREATE POLICY protected_pay_for_app ON policy.catalogue_rule AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (data_class NOT IN ('payroll_aggregate', 'sensitive_personal'));

-- The system (ingest role) writes reports and system events; the API role writes nothing directly.
GRANT INSERT ON intake.validation_report, intake.submission_event TO veloxa_ingest;
-- The system may write ONLY system states: human decisions (accept, reject, authorise, decline) come from
-- intake.decide() with its permission checks.
CREATE POLICY ingest_system_states ON intake.submission_event AS RESTRICTIVE FOR INSERT TO veloxa_ingest
  WITH CHECK (state IN ('validating', 'validation_failed', 'awaiting_review', 'admitting', 'admission_failed',
                        'admitted', 'sealed', 'projected'));

CREATE VIEW policy.catalogue_rule_current WITH (security_invoker = true) AS
SELECT DISTINCT ON (tenant_id, source_type) * FROM policy.catalogue_rule ORDER BY tenant_id, source_type, version DESC;

CREATE VIEW intake.submission_state WITH (security_invoker = true) AS
SELECT s.*, (SELECT e.state FROM intake.submission_event e WHERE e.tenant_id = s.tenant_id
               AND e.submission_id = s.submission_id ORDER BY e.at DESC, e.event_id DESC LIMIT 1) AS state,
       (SELECT e.at FROM intake.submission_event e WHERE e.tenant_id = s.tenant_id
               AND e.submission_id = s.submission_id ORDER BY e.at DESC, e.event_id DESC LIMIT 1) AS state_at
  FROM intake.submission s;
GRANT SELECT ON policy.catalogue_rule_current, intake.submission_state TO veloxa_app, veloxa_ingest,
  veloxa_projection;

-- Department roles that count for a relation (a head also contributes).
CREATE FUNCTION intake.has_department_role(p_tenant uuid, p_principal text, p_department text, p_roles text[])
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT EXISTS (SELECT 1 FROM policy.relationship_tuple t
                  WHERE t.tenant_id = p_tenant AND t.resource_type = 'department' AND t.resource_id = p_department
                    AND t.relation = ANY(p_roles) AND t.subject_type = 'person' AND t.subject_relation IS NULL
                    AND 'person:' || t.subject_id = p_principal
                    AND NOT EXISTS (SELECT 1 FROM policy.relationship_revocation r
                                     WHERE r.tenant_id = t.tenant_id AND r.tuple_id = t.tuple_id))
    AND (SELECT status FROM directory.principal_status s WHERE s.tenant_id = p_tenant AND s.principal_id = p_principal)
        = 'active'
$$;

-- An admission authoriser approves a NEW source type (catalogue rule + data class) once, with a decision record.
CREATE FUNCTION policy.authorise_source_type(p_source_type text, p_title text, p_department text, p_kind text,
                                             p_system text, p_data_class text, p_records text, p_file_glob text,
                                             p_contract jsonb, p_key_columns text[], p_decision_record text)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := current_setting('app.tenant_id')::uuid; caller text := current_setting('app.principal_id');
        v integer;
BEGIN
  IF NOT policy.holds(t, caller, 'tenant', t::text, 'admission_authoriser')
     OR (SELECT status FROM directory.principal_status s WHERE s.tenant_id = t AND s.principal_id = caller)
        IS DISTINCT FROM 'active' THEN
    RAISE EXCEPTION 'approving a source type needs the admission_authoriser relation' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT coalesce(max(version), 0) + 1 INTO v FROM policy.catalogue_rule
   WHERE tenant_id = t AND source_type = p_source_type;
  INSERT INTO policy.catalogue_rule (tenant_id, source_type, version, title, department_id, kind, system, data_class,
                                     records, file_glob, contract, key_columns, active, decision_record, approved_by)
  VALUES (t, p_source_type, v, p_title, p_department, p_kind, p_system, p_data_class, p_records, p_file_glob,
          p_contract, coalesce(p_key_columns, '{}'), true, p_decision_record, caller);
  RETURN v;
END
$$;

-- Submit: the caller must contribute to (or head) the department; the source type must be active for it.
CREATE FUNCTION intake.submit(p_submission_id uuid, p_department text, p_source_type text, p_title text,
                              p_data_period text, p_files jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := current_setting('app.tenant_id')::uuid; caller text := current_setting('app.principal_id');
        rule record; f jsonb;
BEGIN
  IF NOT intake.has_department_role(t, caller, p_department, ARRAY['contributor', 'head']) THEN
    RAISE EXCEPTION 'submitting needs the contributor (or head) relation on the department'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO rule FROM policy.catalogue_rule_current c WHERE c.tenant_id = t AND c.source_type = p_source_type;
  IF NOT FOUND OR NOT rule.active OR rule.department_id <> p_department THEN
    RAISE EXCEPTION 'source type % is not approved for department %', p_source_type, p_department
      USING ERRCODE = 'check_violation';
  END IF;
  IF jsonb_array_length(coalesce(p_files, '[]')) = 0 OR jsonb_array_length(p_files) > 50 THEN
    RAISE EXCEPTION 'a submission has 1 to 50 files' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO intake.submission (tenant_id, submission_id, department_id, source_type, rule_version, title,
                                 data_period, submitted_by)
  VALUES (t, p_submission_id, p_department, p_source_type, rule.version, p_title, p_data_period, caller);
  FOR f IN SELECT * FROM jsonb_array_elements(p_files) LOOP
    INSERT INTO intake.stored_object (tenant_id, sha256, size_bytes, media_type, stored_by)
    VALUES (t, f->>'sha256', (f->>'size_bytes')::bigint, f->>'media_type', caller) ON CONFLICT DO NOTHING;
    INSERT INTO intake.submission_file (tenant_id, submission_id, file_name, sha256, size_bytes, media_type)
    VALUES (t, p_submission_id, f->>'file_name', f->>'sha256', (f->>'size_bytes')::bigint, f->>'media_type');
  END LOOP;
  INSERT INTO intake.submission_event (tenant_id, submission_id, state, actor) VALUES (t, p_submission_id, 'uploaded',
                                                                                     caller);
END
$$;

-- Human decisions on a submission (the API role). System states are written by veloxa_ingest only.
CREATE FUNCTION intake.decide(p_submission_id uuid, p_decision text, p_reason text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := current_setting('app.tenant_id')::uuid; caller text := current_setting('app.principal_id');
        s record; reviewer text; nxt text;
BEGIN
  SELECT * INTO s FROM intake.submission_state x WHERE x.tenant_id = t AND x.submission_id = p_submission_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no such submission' USING ERRCODE = 'no_data_found';
  END IF;
  IF length(btrim(coalesce(p_reason, ''))) = 0 THEN
    RAISE EXCEPTION 'a reason is required' USING ERRCODE = 'check_violation';
  END IF;
  IF p_decision IN ('accept', 'reject') THEN
    IF s.state <> 'awaiting_review' THEN
      RAISE EXCEPTION 'the submission is %, not awaiting review', s.state USING ERRCODE = 'check_violation';
    END IF;
    IF NOT intake.has_department_role(t, caller, s.department_id, ARRAY['reviewer']) THEN
      RAISE EXCEPTION 'reviewing needs the reviewer relation on the department' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF caller = s.submitted_by THEN
      RAISE EXCEPTION 'a submitter cannot review their own submission' USING ERRCODE = 'insufficient_privilege';
    END IF;
    nxt := CASE p_decision WHEN 'accept' THEN 'awaiting_authorisation' ELSE 'rejected' END;
  ELSIF p_decision IN ('authorise', 'decline') THEN
    IF s.state <> 'awaiting_authorisation' THEN
      RAISE EXCEPTION 'the submission is %, not awaiting authorisation', s.state USING ERRCODE = 'check_violation';
    END IF;
    IF NOT policy.holds(t, caller, 'tenant', t::text, 'admission_authoriser')
       OR (SELECT status FROM directory.principal_status x WHERE x.tenant_id = t AND x.principal_id = caller)
          IS DISTINCT FROM 'active' THEN
      RAISE EXCEPTION 'authorising admission needs the admission_authoriser relation'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT e.actor INTO reviewer FROM intake.submission_event e WHERE e.tenant_id = t
       AND e.submission_id = p_submission_id AND e.state = 'awaiting_authorisation' ORDER BY e.at DESC LIMIT 1;
    IF caller = s.submitted_by OR caller = reviewer THEN
      RAISE EXCEPTION 'the admission authoriser must differ from the submitter and the reviewer'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    nxt := CASE p_decision WHEN 'authorise' THEN 'authorised' ELSE 'declined' END;
  ELSE
    RAISE EXCEPTION 'unknown decision %', p_decision USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO intake.submission_event (tenant_id, submission_id, state, actor, reason)
  VALUES (t, p_submission_id, nxt, caller, p_reason);
  RETURN nxt;
END
$$;

DO $$
DECLARE f text;
BEGIN
  FOREACH f IN ARRAY ARRAY['intake.has_department_role(uuid, text, text, text[])',
                           'policy.authorise_source_type(text, text, text, text, text, text, text, text, jsonb, text[], text)',
                           'intake.submit(uuid, text, text, text, text, jsonb)',
                           'intake.decide(uuid, text, text)'] LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO veloxa_app', f);
  END LOOP;
END
$$;
