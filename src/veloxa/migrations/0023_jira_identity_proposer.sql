-- 0023: the application role's candidate proposals (0022) keep the original least-privilege contract: anything the
-- application may not propose is refused as insufficient_privilege (as before 0022), and only the two Jira candidate
-- kinds in the Jira contexts are accepted from it. An administrator mapping in any other context is refused for
-- every role.
CREATE OR REPLACE FUNCTION judgement.check_candidate_proposer() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE ctx text;
BEGIN
  SELECT context INTO ctx FROM judgement.source_identity
   WHERE tenant_id = NEW.tenant_id AND identity_id = NEW.identity_id;
  IF current_user = 'veloxa_app' AND (ctx IS NULL OR ctx NOT IN ('jira.account_id', 'jira.assignee_code')
      OR NEW.classification NOT IN ('administrator_mapping', 'exact_identifier')) THEN
    RAISE EXCEPTION 'candidate: the application may only propose an administrator mapping or a verified match for a'
      ' Jira identity' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.classification = 'administrator_mapping' AND (ctx IS NULL OR ctx NOT IN ('jira.account_id',
                                                                                  'jira.assignee_code')) THEN
    RAISE EXCEPTION 'candidate: an administrator mapping is not available for context %', coalesce(ctx, '?')
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
