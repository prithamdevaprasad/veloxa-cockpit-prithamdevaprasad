-- 0014 Durable agent turns (ARCHITECTURE.md §17.1, §17.4; C26 with DBOS).
--
-- A durable agent turn is a DBOS workflow whose persisted input is OPAQUE: (tenant, request id, interviewer id).
-- The message itself never enters the DBOS system database (which is outside Veloxa's RLS); it is stored here, in
-- the application database, append-only and visible only to its own interviewer (RLS), and read by the workflow's
-- step under that interviewer's principal. The turn's result stays in runtime.agent_execution as before.

CREATE TABLE runtime.agent_request (
  tenant_id        uuid        NOT NULL,
  request_id       uuid        NOT NULL,
  session_id       uuid        NOT NULL,
  interviewer_id   text        NOT NULL CHECK (interviewer_id ~ '^person:'),
  message          text        NOT NULL CHECK (length(btrim(message)) BETWEEN 1 AND 2000),
  idempotency_key  text        NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
  requested_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, request_id),
  UNIQUE (tenant_id, session_id, idempotency_key),
  FOREIGN KEY (tenant_id, session_id) REFERENCES runtime.agent_session (tenant_id, session_id)
);

ALTER TABLE runtime.agent_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime.agent_request FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime.agent_request AS RESTRICTIVE FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY within_tenant ON runtime.agent_request FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY own_requests ON runtime.agent_request AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (interviewer_id = current_setting('app.principal_id'))
  WITH CHECK (interviewer_id = current_setting('app.principal_id'));
CREATE TRIGGER agent_request_append_only BEFORE UPDATE OR DELETE ON runtime.agent_request
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER agent_request_no_truncate BEFORE TRUNCATE ON runtime.agent_request
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

GRANT SELECT, INSERT ON runtime.agent_request TO veloxa_app;
