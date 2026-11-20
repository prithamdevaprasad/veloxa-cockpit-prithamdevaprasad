-- 0026: named, recorded exceptions to the data room's two-person rule (owner decision 20 Nov 2026).
-- By default a submission needs three different people: the submitter, a reviewer and an admission authoriser.
-- An exception lets ONE NAMED PERSON review and authorise submissions of ONE SOURCE TYPE alone (for example the
-- owner's own connected mailbox). It is:
--   * per tenant, per source type, per person; never a wildcard;
--   * written only by an operator (no grant to the application role), with the decision that approved it;
--   * append-only: revoking writes a new row (active = false); the latest row for (tenant, type, person) applies;
--   * visible to the application role, so the UI can say why one person could approve.
-- The reviewer and authoriser relations are still required: the exception removes only the "different person" checks.

CREATE TABLE intake.single_approver_exception (
  tenant_id        uuid        NOT NULL,
  exception_id     uuid        NOT NULL DEFAULT uuidv7(),
  source_type      text        NOT NULL CHECK (length(source_type) BETWEEN 1 AND 64),
  principal_id     text        NOT NULL CHECK (length(principal_id) BETWEEN 1 AND 200),
  active           boolean     NOT NULL,
  decision_record  text        NOT NULL CHECK (length(btrim(decision_record)) >= 10),
  granted_by       text        NOT NULL,
  at               timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, exception_id)
);
ALTER TABLE intake.single_approver_exception ENABLE ROW LEVEL SECURITY;
ALTER TABLE intake.single_approver_exception FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON intake.single_approver_exception AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY within_tenant ON intake.single_approver_exception FOR ALL USING (true) WITH CHECK (true);
CREATE TRIGGER intake_single_approver_append_only BEFORE UPDATE OR DELETE ON intake.single_approver_exception
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER intake_single_approver_no_truncate BEFORE TRUNCATE ON intake.single_approver_exception
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
GRANT SELECT ON intake.single_approver_exception TO veloxa_app;

CREATE FUNCTION intake.single_approver(p_tenant uuid, p_source_type text, p_principal text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT coalesce((SELECT x.active FROM intake.single_approver_exception x WHERE x.tenant_id = p_tenant
                     AND x.source_type = p_source_type AND x.principal_id = p_principal
                   ORDER BY x.at DESC, x.exception_id DESC LIMIT 1), false)
$$;
REVOKE ALL ON FUNCTION intake.single_approver(uuid, text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION intake.decide(p_submission_id uuid, p_decision text, p_reason text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := current_setting('app.tenant_id')::uuid; caller text := current_setting('app.principal_id');
        s record; reviewer text; nxt text; solo boolean;
BEGIN
  SELECT * INTO s FROM intake.submission_state x WHERE x.tenant_id = t AND x.submission_id = p_submission_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no such submission' USING ERRCODE = 'no_data_found';
  END IF;
  IF length(btrim(coalesce(p_reason, ''))) = 0 THEN
    RAISE EXCEPTION 'a reason is required' USING ERRCODE = 'check_violation';
  END IF;
  solo := intake.single_approver(t, s.source_type, caller);
  IF p_decision IN ('accept', 'reject') THEN
    IF s.state <> 'awaiting_review' THEN
      RAISE EXCEPTION 'the submission is %, not awaiting review', s.state USING ERRCODE = 'check_violation';
    END IF;
    IF NOT intake.has_department_role(t, caller, s.department_id, ARRAY['reviewer']) THEN
      RAISE EXCEPTION 'reviewing needs the reviewer relation on the department' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF caller = s.submitted_by AND NOT solo THEN
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
    IF (caller = s.submitted_by OR caller = reviewer) AND NOT solo THEN
      RAISE EXCEPTION 'the admission authoriser must differ from the submitter and the reviewer'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    nxt := CASE p_decision WHEN 'authorise' THEN 'authorised' ELSE 'declined' END;
  ELSE
    RAISE EXCEPTION 'unknown decision %', p_decision USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO intake.submission_event (tenant_id, submission_id, state, actor, reason)
  VALUES (t, p_submission_id, nxt, caller,
          CASE WHEN solo THEN p_reason || ' [single-approver exception for ' || s.source_type || ']' ELSE p_reason END);
  RETURN nxt;
END
$$;
REVOKE ALL ON FUNCTION intake.decide(uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION intake.decide(uuid, text, text) TO veloxa_app;
