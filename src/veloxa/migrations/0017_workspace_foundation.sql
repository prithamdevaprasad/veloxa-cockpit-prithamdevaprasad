-- 0017 Multi-company workspace foundation (ARCHITECTURE.md §9, §10.3, §10.4; C02, C03, C20).
--
-- Tenants become data:
--   directory.tenant_setting     versioned settings per tenant (latest version in force): display name, admission
--                                mode ('release_schedule' = the hackathon schedule, 'submission' = authorised
--                                submissions), evidence access mode ('tenant_wide' = the pre-0017 behaviour, kept
--                                for existing tenants; 'rebac' = per-source ReBAC + ABAC, the default for every new
--                                tenant) and lifecycle status.
--   directory.tenant_issuer      the tenant's OWN trusted OIDC issuers (issuer, audience, JWKS URL, algorithms).
--                                Never secrets. Versioned; the latest version per issuer is in force.
-- People and organisation:
--   directory.principal          a workspace user: typed id `person:<id>`, bound to (issuer, subject) on first
--                                sign-in. Membership is NEVER derived from an e-mail domain.
--   directory.principal_event    lifecycle (invited, activated, suspended, reactivated, removed); append-only.
--   directory.invitation         one-time invitations: only the SHA-256 of the token is stored.
--   directory.invitation_role    the relations an accepted invitation grants.
--   directory.invitation_event   accepted | revoked (an invitation is used at most once).
--   directory.department         department records. HR data may PROPOSE departments; nothing here grants access.
-- Roles are C20 relationship tuples (policy.relationship_tuple), never a second permission model:
--   tenant:<tenant_id>#admin | #admission_authoriser | #company_reader | #member @ person:<id>
--   department:<id>#head | #contributor | #reviewer | #member @ person:<id>
--   evidence_source:<system>#read @ department:<id>#member   (or @ person:<id>)
-- The API never writes tuples directly: the SECURITY DEFINER functions below check, in the database, that the
-- caller (app.principal_id) is a workspace administrator of the tenant in app.tenant_id, and refuse self-escalation.

CREATE TABLE directory.tenant_setting (
  tenant_id        uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  version          integer     NOT NULL CHECK (version >= 1),
  display_name     text        NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
  admission_mode   text        NOT NULL CHECK (admission_mode IN ('release_schedule', 'submission')),
  evidence_access  text        NOT NULL CHECK (evidence_access IN ('tenant_wide', 'rebac')),
  status           text        NOT NULL CHECK (status IN ('active', 'suspended')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  created_by       text        NOT NULL DEFAULT current_user,
  PRIMARY KEY (tenant_id, version)
);

CREATE TABLE directory.tenant_issuer (
  tenant_id   uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  issuer      text        NOT NULL CHECK (issuer ~ '^https://[^\s]+$'),
  version     integer     NOT NULL CHECK (version >= 1),
  audience    text        NOT NULL CHECK (length(btrim(audience)) > 0),
  jwks_url    text        NOT NULL CHECK (jwks_url ~ '^https://[^\s]+$'),
  algorithms  text[]      NOT NULL CHECK (algorithms <@ ARRAY['RS256', 'ES256'] AND cardinality(algorithms) > 0),
  active      boolean     NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  text        NOT NULL DEFAULT current_user,
  PRIMARY KEY (tenant_id, issuer, version)
);
CREATE UNIQUE INDEX tenant_issuer_global ON directory.tenant_issuer (issuer, version);
-- Like directory.tenant, issuer configuration holds no tenant business data and must be readable BEFORE a tenant
-- is known (a token names its issuer). It is NOT RLS-protected; the application role cannot read it directly,
-- only through directory.issuer_config(). Append-only.
CREATE TRIGGER tenant_issuer_append_only BEFORE UPDATE OR DELETE ON directory.tenant_issuer
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

-- Pre-tenant lookups (same reasoning): invitation token hash -> tenant, and verified sign-in -> principal.
CREATE TABLE directory.invitation_lookup (
  token_sha256   text PRIMARY KEY CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
  tenant_id      uuid NOT NULL REFERENCES directory.tenant (tenant_id),
  invitation_id  uuid NOT NULL
);
CREATE TABLE directory.subject_binding (
  issuer        text NOT NULL,
  subject       text NOT NULL,
  tenant_id     uuid NOT NULL REFERENCES directory.tenant (tenant_id),
  principal_id  text NOT NULL,
  bound_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (issuer, subject),
  UNIQUE (tenant_id, principal_id)
);
CREATE TRIGGER invitation_lookup_append_only BEFORE UPDATE OR DELETE ON directory.invitation_lookup
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER subject_binding_append_only BEFORE UPDATE OR DELETE ON directory.subject_binding
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
REVOKE ALL ON directory.tenant_issuer, directory.invitation_lookup, directory.subject_binding FROM PUBLIC;

CREATE TABLE directory.principal (
  tenant_id     uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  principal_id  text        NOT NULL CHECK (principal_id ~ '^person:[A-Za-z0-9_.-]{1,80}$'),
  display_name  text        NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
  email         text        CHECK (email IS NULL OR email = lower(btrim(email))),
  person_anchor text,                         -- HR employee ID, only through a reviewed C12 decision (K-04)
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, principal_id)
);

CREATE TABLE directory.principal_event (
  tenant_id     uuid        NOT NULL,
  event_id      uuid        NOT NULL DEFAULT uuidv7(),
  principal_id  text        NOT NULL,
  event         text        NOT NULL CHECK (event IN ('invited', 'activated', 'suspended', 'reactivated', 'removed')),
  actor         text        NOT NULL,
  reason        text,
  at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, principal_id) REFERENCES directory.principal (tenant_id, principal_id)
);

CREATE TABLE directory.invitation (
  tenant_id      uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  invitation_id  uuid        NOT NULL,
  principal_id   text        NOT NULL,
  email          text        NOT NULL CHECK (email = lower(btrim(email)) AND email ~ '^[^@\s]+@[^@\s]+$'),
  token_sha256   text        NOT NULL UNIQUE CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
  invited_by     text        NOT NULL,
  expires_at     timestamptz NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, invitation_id),
  FOREIGN KEY (tenant_id, principal_id) REFERENCES directory.principal (tenant_id, principal_id),
  CHECK (expires_at > created_at)
);

CREATE TABLE directory.invitation_role (
  tenant_id       uuid NOT NULL,
  invitation_id   uuid NOT NULL,
  resource_type   text NOT NULL CHECK (resource_type IN ('tenant', 'department')),
  resource_id     text NOT NULL,
  relation        text NOT NULL,
  PRIMARY KEY (tenant_id, invitation_id, resource_type, resource_id, relation),
  FOREIGN KEY (tenant_id, invitation_id) REFERENCES directory.invitation (tenant_id, invitation_id)
);

CREATE TABLE directory.invitation_event (
  tenant_id      uuid        NOT NULL,
  invitation_id  uuid        NOT NULL,
  event          text        NOT NULL CHECK (event IN ('accepted', 'revoked')),
  actor          text        NOT NULL,
  at             timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, invitation_id),                 -- at most ONE event: an invitation is used once
  FOREIGN KEY (tenant_id, invitation_id) REFERENCES directory.invitation (tenant_id, invitation_id)
);

CREATE TABLE directory.department (
  tenant_id      uuid        NOT NULL REFERENCES directory.tenant (tenant_id),
  department_id  text        NOT NULL CHECK (department_id ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
  name           text        NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
  proposed_from  text,                                    -- e.g. 'hr_export' (a proposal, never a grant)
  created_by     text        NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, department_id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['directory.tenant_setting', 'directory.principal',
                           'directory.principal_event', 'directory.invitation', 'directory.invitation_role',
                           'directory.invitation_event', 'directory.department'] LOOP
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
    EXECUTE format('GRANT SELECT ON %s TO veloxa_app', t);
  END LOOP;
END
$$;

-- Issuer lookup happens BEFORE a tenant is known (the token names its issuer). This definer function returns only
-- the active issuer configuration (no tenant business data) for one exact issuer URL.
CREATE FUNCTION directory.issuer_config(p_issuer text)
RETURNS TABLE (tenant_id uuid, audience text, jwks_url text, algorithms text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT i.tenant_id, i.audience, i.jwks_url, i.algorithms FROM directory.tenant_issuer i
   WHERE i.issuer = p_issuer
     AND i.version = (SELECT max(version) FROM directory.tenant_issuer j WHERE j.issuer = p_issuer)
     AND i.active
$$;

-- Current views (security_invoker: RLS applies to the caller).
CREATE VIEW directory.tenant_setting_current WITH (security_invoker = true) AS
SELECT DISTINCT ON (tenant_id) * FROM directory.tenant_setting ORDER BY tenant_id, version DESC;

CREATE VIEW directory.principal_status WITH (security_invoker = true) AS
SELECT p.tenant_id, p.principal_id, p.display_name, p.email, p.person_anchor,
       coalesce((SELECT CASE e.event WHEN 'reactivated' THEN 'active' WHEN 'activated' THEN 'active'
                                     ELSE e.event END
                   FROM directory.principal_event e
                  WHERE e.tenant_id = p.tenant_id AND e.principal_id = p.principal_id
                  ORDER BY e.at DESC, e.event_id DESC LIMIT 1), 'invited') AS status
  FROM directory.principal p;
GRANT SELECT ON directory.tenant_setting_current, directory.principal_status TO veloxa_app;

-- Unrevoked relationship tuples (the current C20 projection minus appended revocations).
CREATE VIEW policy.live_tuple WITH (security_invoker = true) AS
SELECT t.* FROM policy.relationship_tuple t
 WHERE NOT EXISTS (SELECT 1 FROM policy.relationship_revocation r
                    WHERE r.tenant_id = t.tenant_id AND r.tuple_id = t.tuple_id);
GRANT SELECT ON policy.live_tuple TO veloxa_app;

-- ---------------------------------------------------------------------------------------------------------------
-- Guarded administration (SECURITY DEFINER; owner veloxa_owner). Every function:
--   * acts only in the tenant of app.tenant_id, as the caller in app.principal_id;
--   * requires the caller to hold tenant:<tenant>#admin (directly, unrevoked) and to be an ACTIVE principal;
--   * refuses self-escalation (granting oneself admin, admission_authoriser or company_reader).
-- ---------------------------------------------------------------------------------------------------------------
CREATE FUNCTION policy.holds(p_tenant uuid, p_principal text, p_rtype text, p_rid text, p_relation text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT EXISTS (SELECT 1 FROM policy.relationship_tuple t
                  WHERE t.tenant_id = p_tenant AND t.resource_type = p_rtype AND t.resource_id = p_rid
                    AND t.relation = p_relation AND t.subject_type = 'person'
                    AND 'person:' || t.subject_id = p_principal AND t.subject_relation IS NULL
                    AND NOT EXISTS (SELECT 1 FROM policy.relationship_revocation r
                                     WHERE r.tenant_id = t.tenant_id AND r.tuple_id = t.tuple_id))
$$;

CREATE FUNCTION policy.require_admin() RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := current_setting('app.tenant_id')::uuid; p text := current_setting('app.principal_id');
BEGIN
  IF NOT policy.holds(t, p, 'tenant', t::text, 'admin') THEN
    RAISE EXCEPTION 'not a workspace administrator' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF (SELECT status FROM directory.principal_status s WHERE s.tenant_id = t AND s.principal_id = p) IS DISTINCT
     FROM 'active' THEN
    RAISE EXCEPTION 'the administrator is not an active principal' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN t;
END
$$;

CREATE FUNCTION policy.admin_grant(p_resource_type text, p_resource_id text, p_relation text, p_subject_type text,
                                   p_subject_id text, p_subject_relation text, p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := policy.require_admin(); caller text := current_setting('app.principal_id'); id uuid := uuidv7();
BEGIN
  IF length(btrim(coalesce(p_reason, ''))) = 0 THEN
    RAISE EXCEPTION 'a reason is required' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT ((p_resource_type = 'tenant' AND p_resource_id = t::text
             AND p_relation IN ('admin', 'admission_authoriser', 'company_reader', 'member'))
       OR (p_resource_type = 'department' AND p_relation IN ('head', 'contributor', 'reviewer', 'member')
             AND EXISTS (SELECT 1 FROM directory.department d WHERE d.tenant_id = t
                           AND d.department_id = p_resource_id))
       OR (p_resource_type = 'evidence_source' AND p_relation = 'read')) THEN
    RAISE EXCEPTION 'relation % on % is not grantable', p_relation, p_resource_type USING ERRCODE = 'check_violation';
  END IF;
  IF NOT ((p_subject_type = 'person' AND p_subject_relation IS NULL
             AND EXISTS (SELECT 1 FROM directory.principal x WHERE x.tenant_id = t
                           AND x.principal_id = 'person:' || p_subject_id))
       OR (p_subject_type = 'department' AND p_subject_relation = 'member'
             AND EXISTS (SELECT 1 FROM directory.department d WHERE d.tenant_id = t
                           AND d.department_id = p_subject_id))) THEN
    RAISE EXCEPTION 'unknown subject' USING ERRCODE = 'check_violation';
  END IF;
  IF p_subject_type = 'person' AND 'person:' || p_subject_id = caller
     AND p_relation IN ('admin', 'admission_authoriser', 'company_reader') THEN
    RAISE EXCEPTION 'self-escalation is refused' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id, relation, subject_type,
                                         subject_id, subject_relation, source, created_by)
  VALUES (t, id, p_resource_type, p_resource_id, p_relation, p_subject_type, p_subject_id, p_subject_relation,
          'workspace admin: ' || p_reason, caller);
  RETURN id;
END
$$;

CREATE FUNCTION policy.admin_revoke(p_tuple_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := policy.require_admin(); caller text := current_setting('app.principal_id'); r record;
BEGIN
  SELECT * INTO r FROM policy.relationship_tuple WHERE tenant_id = t AND tuple_id = p_tuple_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no such relationship' USING ERRCODE = 'no_data_found';
  END IF;
  IF r.resource_type = 'tenant' AND r.relation = 'admin' AND 'person:' || r.subject_id = caller THEN
    RAISE EXCEPTION 'an administrator cannot revoke their own administration' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO policy.relationship_revocation (tenant_id, tuple_id) VALUES (t, p_tuple_id)
  ON CONFLICT DO NOTHING;
END
$$;

CREATE FUNCTION directory.admin_create_department(p_department_id text, p_name text, p_proposed_from text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := policy.require_admin();
BEGIN
  INSERT INTO directory.department (tenant_id, department_id, name, proposed_from, created_by)
  VALUES (t, p_department_id, p_name, p_proposed_from, current_setting('app.principal_id'));
END
$$;

-- Invite: creates the (not yet bound) principal, the invitation (token hash only) and the roles it will grant.
CREATE FUNCTION directory.admin_invite(p_principal_id text, p_display_name text, p_email text, p_token_sha256 text,
                                       p_valid_for interval, p_roles jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := policy.require_admin(); caller text := current_setting('app.principal_id'); id uuid := uuidv7();
        r jsonb;
BEGIN
  IF p_valid_for > interval '14 days' OR p_valid_for < interval '10 minutes' THEN
    RAISE EXCEPTION 'invitation validity must be between 10 minutes and 14 days' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM directory.principal WHERE tenant_id = t AND principal_id = p_principal_id) THEN
    INSERT INTO directory.principal (tenant_id, principal_id, display_name, email)
    VALUES (t, p_principal_id, p_display_name, lower(btrim(p_email)));
  END IF;
  INSERT INTO directory.principal_event (tenant_id, principal_id, event, actor) VALUES (t, p_principal_id, 'invited', caller);
  INSERT INTO directory.invitation (tenant_id, invitation_id, principal_id, email, token_sha256, invited_by, expires_at)
  VALUES (t, id, p_principal_id, lower(btrim(p_email)), p_token_sha256, caller, now() + p_valid_for);
  INSERT INTO directory.invitation_lookup (token_sha256, tenant_id, invitation_id) VALUES (p_token_sha256, t, id);
  FOR r IN SELECT * FROM jsonb_array_elements(coalesce(p_roles, '[]'::jsonb)) LOOP
    IF p_principal_id = caller AND r->>'relation' IN ('admin', 'admission_authoriser', 'company_reader') THEN
      RAISE EXCEPTION 'self-escalation is refused' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT ((r->>'resource_type' = 'tenant' AND r->>'resource_id' = t::text
               AND r->>'relation' IN ('admin', 'admission_authoriser', 'company_reader', 'member'))
         OR (r->>'resource_type' = 'department' AND r->>'relation' IN ('head', 'contributor', 'reviewer', 'member')
               AND EXISTS (SELECT 1 FROM directory.department d WHERE d.tenant_id = t
                             AND d.department_id = r->>'resource_id'))) THEN
      RAISE EXCEPTION 'role % is not grantable', r USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO directory.invitation_role (tenant_id, invitation_id, resource_type, resource_id, relation)
    VALUES (t, id, r->>'resource_type', r->>'resource_id', r->>'relation');
  END LOOP;
  RETURN id;
END
$$;

CREATE FUNCTION directory.admin_set_status(p_principal_id text, p_event text, p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE t uuid := policy.require_admin(); caller text := current_setting('app.principal_id');
BEGIN
  IF p_event NOT IN ('suspended', 'reactivated', 'removed') THEN
    RAISE EXCEPTION 'unknown status change' USING ERRCODE = 'check_violation';
  END IF;
  IF p_principal_id = caller THEN
    RAISE EXCEPTION 'an administrator cannot change their own status' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO directory.principal_event (tenant_id, principal_id, event, actor, reason)
  VALUES (t, p_principal_id, p_event, caller, p_reason);
END
$$;

-- Accept: called with the VERIFIED (issuer, subject) of the signing-in user and the presented token's hash. The
-- invitation must be unexpired and unused, and its issuer must be one of the tenant's active issuers. Binds the
-- principal, activates it and grants the invited roles. Runs before the user has any principal, so it takes the
-- tenant from the invitation (looked up by token hash only) and never from the request.
CREATE FUNCTION directory.accept_invitation(p_token_sha256 text, p_issuer text, p_subject text)
RETURNS TABLE (tenant_id uuid, principal_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE i record; role record; l record;
BEGIN
  SELECT * INTO l FROM directory.invitation_lookup x WHERE x.token_sha256 = p_token_sha256;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'invitation is invalid, expired or already used' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM set_config('app.tenant_id', l.tenant_id::text, true);     -- the tenant comes from the invitation
  SELECT * INTO i FROM directory.invitation v WHERE v.tenant_id = l.tenant_id AND v.invitation_id = l.invitation_id;
  IF NOT FOUND OR i.expires_at <= now()
     OR EXISTS (SELECT 1 FROM directory.invitation_event e WHERE e.tenant_id = i.tenant_id
                  AND e.invitation_id = i.invitation_id) THEN
    RAISE EXCEPTION 'invitation is invalid, expired or already used' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM directory.issuer_config(p_issuer) c WHERE c.tenant_id = i.tenant_id) THEN
    RAISE EXCEPTION 'the issuer is not trusted by this workspace' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF EXISTS (SELECT 1 FROM directory.subject_binding b WHERE b.issuer = p_issuer AND b.subject = p_subject) THEN
    RAISE EXCEPTION 'this sign-in is already bound to a member' USING ERRCODE = 'unique_violation';
  END IF;
  INSERT INTO directory.invitation_event (tenant_id, invitation_id, event, actor)
  VALUES (i.tenant_id, i.invitation_id, 'accepted', i.principal_id);
  INSERT INTO directory.subject_binding (issuer, subject, tenant_id, principal_id)
  VALUES (p_issuer, p_subject, i.tenant_id, i.principal_id);
  INSERT INTO directory.principal_event (tenant_id, principal_id, event, actor)
  VALUES (i.tenant_id, i.principal_id, 'activated', i.principal_id);
  FOR role IN SELECT * FROM directory.invitation_role r WHERE r.tenant_id = i.tenant_id
                AND r.invitation_id = i.invitation_id LOOP
    INSERT INTO policy.relationship_tuple (tenant_id, tuple_id, resource_type, resource_id, relation, subject_type,
                                           subject_id, source, created_by)
    VALUES (i.tenant_id, uuidv7(), role.resource_type, role.resource_id, role.relation, 'person',
            substr(i.principal_id, 8), 'invitation ' || i.invitation_id, i.invited_by);
  END LOOP;
  RETURN QUERY SELECT i.tenant_id, i.principal_id;
END
$$;

-- Who a verified sign-in is (before the tenant is known): exact (issuer, subject) only.
CREATE FUNCTION directory.principal_for_subject(p_issuer text, p_subject text)
RETURNS TABLE (tenant_id uuid, principal_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT b.tenant_id, b.principal_id FROM directory.subject_binding b
   WHERE b.issuer = p_issuer AND b.subject = p_subject
$$;

DO $$
DECLARE f text;
BEGIN
  FOREACH f IN ARRAY ARRAY['directory.issuer_config(text)', 'policy.holds(uuid, text, text, text, text)',
                           'policy.require_admin()',
                           'policy.admin_grant(text, text, text, text, text, text, text)',
                           'policy.admin_revoke(uuid)', 'directory.admin_create_department(text, text, text)',
                           'directory.admin_invite(text, text, text, text, interval, jsonb)',
                           'directory.admin_set_status(text, text, text)',
                           'directory.accept_invitation(text, text, text)',
                           'directory.principal_for_subject(text, text)'] LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO veloxa_app', f);
  END LOOP;
END
$$;
