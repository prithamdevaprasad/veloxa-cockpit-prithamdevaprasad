-- 0022: Jira assignee identity resolution (owner request, 19 Nov 2026).
--
-- Jira assignees become C12 source identities (contexts `jira.account_id` for Jira's stable account ID and
-- `jira.assignee_code` for the short codes in exported issue sheets). They link to HR person entities only through
-- the existing candidate + versioned human decision model. Two candidate kinds may be APPROVED for these contexts:
--   exact_identifier        a verified identifier equals exactly one HR record (e.g. the e-mail Jira reports for the
--                           account equals one HR work e-mail; compared server side, never shown in the UI)
--   administrator_mapping   a workspace administrator explicitly mapped the identity to one HR employee
-- Everything else stays as before: name-derived candidates (initials, surname) are suggestions only for real data,
-- ambiguous/conflicting candidates are never approvable, every other context (e-mail, ...) keeps the synthetic-only
-- guard, and an identity can be approved for at most one entity at a time. Original values are never altered.

ALTER TABLE judgement.match_candidate DROP CONSTRAINT IF EXISTS match_candidate_classification_check;
ALTER TABLE judgement.match_candidate ADD CONSTRAINT match_candidate_classification_check
  CHECK (classification IN ('exact_identifier', 'unique_initials_in_context', 'surname_initial', 'ambiguous',
                            'conflicting', 'administrator_mapping'));

-- The application role may PROPOSE only the two Jira candidate kinds (an administrator's mapping or a verified
-- exact match it checked); every other proposal still comes from the projection role.
CREATE FUNCTION judgement.check_candidate_proposer() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE ctx text;
BEGIN
  IF NEW.classification = 'administrator_mapping' OR current_user = 'veloxa_app' THEN
    SELECT context INTO ctx FROM judgement.source_identity
     WHERE tenant_id = NEW.tenant_id AND identity_id = NEW.identity_id;
    IF ctx IS NULL OR ctx NOT IN ('jira.account_id', 'jira.assignee_code') THEN
      RAISE EXCEPTION 'candidate: % is not available for context %', NEW.classification, coalesce(ctx, '?')
        USING ERRCODE = 'check_violation';
    END IF;
    IF current_user = 'veloxa_app' AND NEW.classification NOT IN ('administrator_mapping', 'exact_identifier') THEN
      RAISE EXCEPTION 'candidate: the application may only propose an administrator mapping or a verified match'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER candidate_proposer BEFORE INSERT ON judgement.match_candidate
  FOR EACH ROW EXECUTE FUNCTION judgement.check_candidate_proposer();
GRANT INSERT ON judgement.match_candidate TO veloxa_app;

CREATE OR REPLACE FUNCTION judgement.check_resolution_decision() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  c        record;
  latest   record;
  clash    integer;
BEGIN
  SELECT mc.identity_id, mc.classification, si.origin AS identity_origin, si.context AS identity_context,
         ai.origin AS anchor_origin, ai.system AS anchor_system
    INTO c
    FROM judgement.match_candidate mc
    JOIN judgement.source_identity si ON si.tenant_id = mc.tenant_id AND si.identity_id = mc.identity_id
    JOIN judgement.entity e ON e.tenant_id = mc.tenant_id AND e.entity_id = mc.entity_id
    JOIN judgement.source_identity ai ON ai.tenant_id = e.tenant_id AND ai.identity_id = e.anchor_identity_id
   WHERE mc.tenant_id = NEW.tenant_id AND mc.candidate_id = NEW.candidate_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'resolution decision: unknown candidate' USING ERRCODE = 'foreign_key_violation';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || '/' || c.identity_id::text, 12));
  SELECT version, decision_id INTO latest FROM judgement.resolution_decision
   WHERE tenant_id = NEW.tenant_id AND candidate_id = NEW.candidate_id ORDER BY version DESC LIMIT 1;
  IF NEW.version <> coalesce(latest.version, 0) + 1 THEN
    RAISE EXCEPTION 'resolution decision: version % is not consecutive (latest %)', NEW.version,
      coalesce(latest.version, 0) USING ERRCODE = 'unique_violation';
  END IF;
  IF NEW.previous_decision_id IS DISTINCT FROM latest.decision_id THEN
    RAISE EXCEPTION 'resolution decision: previous_decision_id must name the latest decision'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.outcome = 'approved' THEN
    IF c.classification IN ('ambiguous', 'conflicting') THEN
      RAISE EXCEPTION 'resolution decision: a % candidate cannot be approved', c.classification
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT (c.identity_origin = 'synthetic_test' AND c.anchor_origin = 'synthetic_test') THEN
      IF NOT (c.identity_context IN ('jira.account_id', 'jira.assignee_code') AND c.anchor_system = 'hr'
              AND c.classification IN ('exact_identifier', 'administrator_mapping')) THEN
        RAISE EXCEPTION 'resolution decision: only synthetic test identities may be approved'
          USING ERRCODE = 'insufficient_privilege';
      END IF;
    END IF;
    SELECT count(*) INTO clash FROM (
      SELECT DISTINCT ON (d.candidate_id) d.outcome
        FROM judgement.resolution_decision d
        JOIN judgement.match_candidate mc ON mc.tenant_id = d.tenant_id AND mc.candidate_id = d.candidate_id
       WHERE d.tenant_id = NEW.tenant_id AND mc.identity_id = c.identity_id AND d.candidate_id <> NEW.candidate_id
       ORDER BY d.candidate_id, d.version DESC) last
     WHERE last.outcome = 'approved';
    IF clash > 0 THEN
      RAISE EXCEPTION 'resolution decision: the identity is already approved for another entity'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
