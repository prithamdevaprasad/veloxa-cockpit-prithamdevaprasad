-- 0015 C27 LLMClient: hard token budgets and model-call records (ARCHITECTURE.md §18.1, §18.2).
--
-- runtime.llm_budget       per tenant and period: limit, used and reserved tokens. Limits are OWNER values: with no
--                          row there is no budget, and every model call is refused before it is made (fail closed).
--                          The application role may only reserve and reconcile through the two functions below.
-- runtime.llm_call         one append-only record per call or refusal: stage, provider, model and prompt version,
--                          request hash, data classes, reserved and used tokens, status, whether it was replayed.
--                          It NEVER stores prompt or response text.

CREATE TABLE runtime.llm_budget (
  tenant_id        uuid        NOT NULL,
  period           text        NOT NULL CHECK (period ~ '^\d{4}-\d{2}$'),
  limit_tokens     bigint      NOT NULL CHECK (limit_tokens >= 0),
  used_tokens      bigint      NOT NULL DEFAULT 0 CHECK (used_tokens >= 0),
  reserved_tokens  bigint      NOT NULL DEFAULT 0 CHECK (reserved_tokens >= 0),
  set_by           text        NOT NULL CHECK (length(btrim(set_by)) > 0),
  decision_record  text        NOT NULL CHECK (length(btrim(decision_record)) > 0),
  PRIMARY KEY (tenant_id, period),
  CHECK (used_tokens + reserved_tokens <= limit_tokens)
);

CREATE TABLE runtime.llm_call (
  tenant_id        uuid        NOT NULL,
  call_id          uuid        NOT NULL,
  stage            text        NOT NULL CHECK (stage IN ('plan', 'compose', 'verify', 'agent', 'extract',
                                                         'risk_narrative')),
  provider         text        NOT NULL,
  model            text        NOT NULL,
  prompt_version   text        NOT NULL,
  request_sha256   text        NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  idempotency_key  text        NOT NULL,
  data_classes     text[]      NOT NULL,
  redacted_items   integer     NOT NULL DEFAULT 0,
  reserved_tokens  bigint      NOT NULL DEFAULT 0,
  used_tokens      bigint      NOT NULL DEFAULT 0,
  status           text        NOT NULL CHECK (status IN ('ok', 'refused_policy', 'refused_budget', 'invalid_output',
                                                          'provider_error', 'replay_missing')),
  replayed         boolean     NOT NULL DEFAULT false,
  attempts         integer     NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, call_id),
  CHECK (NOT replayed OR used_tokens = 0)          -- replayed calls never count usage (§18.2 telemetry)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['runtime.llm_budget', 'runtime.llm_call'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s AS RESTRICTIVE FOR ALL'
                   ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
                   ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    EXECUTE format('CREATE POLICY within_tenant ON %s FOR ALL USING (true) WITH CHECK (true)', t);
  END LOOP;
END
$$;
CREATE TRIGGER llm_call_append_only BEFORE UPDATE OR DELETE ON runtime.llm_call
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER llm_call_no_truncate BEFORE TRUNCATE ON runtime.llm_call
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

-- Pre-call reservation: one atomic UPDATE; no row => refuse before calling the model (§18.2 illustrative SQL).
CREATE FUNCTION runtime.llm_reserve(p_tenant uuid, p_period text, p_tokens bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE ok boolean;
BEGIN
  IF p_tokens < 0 OR p_tenant IS DISTINCT FROM current_setting('app.tenant_id')::uuid THEN
    RETURN false;
  END IF;
  UPDATE runtime.llm_budget SET reserved_tokens = reserved_tokens + p_tokens
   WHERE tenant_id = p_tenant AND period = p_period AND used_tokens + reserved_tokens + p_tokens <= limit_tokens
  RETURNING true INTO ok;
  RETURN coalesce(ok, false);
END
$$;

-- Reconciliation after the call: release the reservation and count the actual usage (never above the limit).
CREATE FUNCTION runtime.llm_reconcile(p_tenant uuid, p_period text, p_reserved bigint, p_used bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
  IF p_tenant IS DISTINCT FROM current_setting('app.tenant_id')::uuid OR p_reserved < 0 OR p_used < 0 THEN
    RAISE EXCEPTION 'llm budget: invalid reconciliation' USING ERRCODE = 'check_violation';
  END IF;
  UPDATE runtime.llm_budget
     SET reserved_tokens = greatest(reserved_tokens - p_reserved, 0),
         used_tokens = least(used_tokens + p_used, limit_tokens - greatest(reserved_tokens - p_reserved, 0))
   WHERE tenant_id = p_tenant AND period = p_period;
END
$$;

REVOKE ALL ON FUNCTION runtime.llm_reserve(uuid, text, bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION runtime.llm_reconcile(uuid, text, bigint, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION runtime.llm_reserve(uuid, text, bigint) TO veloxa_app;
GRANT EXECUTE ON FUNCTION runtime.llm_reconcile(uuid, text, bigint, bigint) TO veloxa_app;
GRANT SELECT ON runtime.llm_budget TO veloxa_app;                 -- budgets are set by the owner, never the API
GRANT SELECT, INSERT ON runtime.llm_call TO veloxa_app;
