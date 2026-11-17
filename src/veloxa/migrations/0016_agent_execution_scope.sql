-- 0016 Hardening: an interviewer sees only the executions (turns) of their OWN sessions (ARCHITECTURE.md §14.5
-- isolation; §9.3 RLS). runtime.agent_session already restricts the application role to its own sessions
-- (own_sessions); this makes runtime.agent_execution follow it in the database instead of relying on every query
-- joining the session. The subquery runs under the caller's RLS, so it only sees the caller's own sessions.

CREATE POLICY own_session_executions ON runtime.agent_execution AS RESTRICTIVE FOR ALL TO veloxa_app
  USING (EXISTS (SELECT 1 FROM runtime.agent_session s
                  WHERE s.tenant_id = agent_execution.tenant_id AND s.session_id = agent_execution.session_id))
  WITH CHECK (EXISTS (SELECT 1 FROM runtime.agent_session s
                       WHERE s.tenant_id = agent_execution.tenant_id AND s.session_id = agent_execution.session_id));
