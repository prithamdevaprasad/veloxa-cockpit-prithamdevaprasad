-- 0004 answers (ARCHITECTURE.md C16, §13.1 stage 11, §19.4): every /v1/ask answer is recorded with its read basis,
-- grade, status, claims and citations, in ONE transaction with its audit event. Answers are append-only.
-- No data_class column: an answer never contains protected classes (the pipeline's policy scan drops them).

CREATE TABLE runtime.answer (
  tenant_id         uuid        NOT NULL,
  answer_id         uuid        NOT NULL,
  asked_at          timestamptz NOT NULL,
  principal_id      text        NOT NULL,
  question          text        NOT NULL,
  pipeline_version  text        NOT NULL,
  batch_seq         bigint      NOT NULL CHECK (batch_seq >= 0),
  mode              text        NOT NULL CHECK (mode IN ('CURRENT', 'KNOWN_AT', 'RECORDED_BY', 'VALID_AT')),
  as_of             text,
  status            text        NOT NULL CHECK (status IN ('answered', 'qualified', 'conflict', 'abstained',
                                                           'refused')),
  grade             text        NOT NULL CHECK (grade IN ('A', 'B', 'C', 'D')),
  policy_id         text,                       -- set when refused
  answer            jsonb       NOT NULL,       -- the Answer object exactly as returned (§19.4)
  PRIMARY KEY (tenant_id, answer_id),
  CHECK ((status = 'refused') = (policy_id IS NOT NULL))
);

-- Which evidence records each answer cited (evidence usage counts, §19.3 Evidence).
CREATE TABLE runtime.answer_citation (
  tenant_id   uuid   NOT NULL,
  answer_id   uuid   NOT NULL,
  record_id   text   NOT NULL,
  PRIMARY KEY (tenant_id, answer_id, record_id),
  FOREIGN KEY (tenant_id, answer_id) REFERENCES runtime.answer (tenant_id, answer_id)
);

CREATE TRIGGER answer_append_only BEFORE UPDATE OR DELETE ON runtime.answer
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER answer_no_truncate BEFORE TRUNCATE ON runtime.answer
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER answer_citation_append_only BEFORE UPDATE OR DELETE ON runtime.answer_citation
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER answer_citation_no_truncate BEFORE TRUNCATE ON runtime.answer_citation
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['runtime.answer', 'runtime.answer_citation'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s AS RESTRICTIVE FOR ALL'
                   ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
                   ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    EXECUTE format('CREATE POLICY within_tenant ON %s FOR ALL USING (true) WITH CHECK (true)', t);
  END LOOP;
END
$$;

GRANT USAGE ON SCHEMA runtime TO veloxa_app;
GRANT SELECT, INSERT ON runtime.answer, runtime.answer_citation TO veloxa_app;
-- The application role appends audit events for answers. To extend the hash chain it reads ONLY the chain
-- columns of earlier events, never their details.
GRANT SELECT (tenant_id, seq, hash) ON audit.event TO veloxa_app;
