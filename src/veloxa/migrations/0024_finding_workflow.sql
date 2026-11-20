-- 0024: keep the finding, its notifications, the human investigation and follow-up actions apart (owner request
-- 20 Nov 2026).
-- * Acknowledging is about a NOTIFICATION (someone saw the alert): runtime.notification_ack, one row per
--   (notification, person). It no longer changes the finding's lifecycle state.
-- * Investigation, resolution, dismissal and reopening stay finding events. Resolution, dismissal and reopening
--   need a stated outcome or reason; an investigation may name who is responsible (never assumed to be the clicker).
-- * Notifications are in-app records: `channel` says so. No external delivery exists, so none is claimed.
-- * Follow-up actions (a recommendation turned into work, e.g. a Jira issue) are their own append-only events; a
--   created issue is not a completed action and a completed action does not resolve the finding.
-- Everything is append-only and tenant-isolated like the rest of runtime.*.

ALTER TABLE runtime.finding_event ADD COLUMN responsible text CHECK (responsible IS NULL OR
  length(btrim(responsible)) BETWEEN 1 AND 200);
ALTER TABLE runtime.finding_event DROP CONSTRAINT finding_event_state_check;
ALTER TABLE runtime.finding_event ADD CONSTRAINT finding_event_state_check CHECK (state IN ('detected', 'verified',
  'notified', 'acknowledged', 'investigating', 'resolved', 'dismissed', 'reassessed', 'reopened'));
-- Earlier rows predate the rule, so it applies to new rows only.
ALTER TABLE runtime.finding_event ADD CONSTRAINT finding_event_outcome_required CHECK (
  state NOT IN ('resolved', 'dismissed', 'reopened') OR length(btrim(coalesce(note, ''))) >= 5) NOT VALID;

ALTER TABLE runtime.notification ADD COLUMN channel text NOT NULL DEFAULT 'in_app' CHECK (channel IN ('in_app'));
COMMENT ON COLUMN runtime.notification.delivered_at IS
  'When the in-app notification record was created. No external channel exists, so nothing is claimed delivered.';

CREATE TABLE runtime.notification_ack (
  tenant_id        uuid        NOT NULL,
  notification_id  uuid        NOT NULL,
  finding_id       text        NOT NULL,
  actor            text        NOT NULL,
  at               timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, notification_id, actor),
  FOREIGN KEY (tenant_id, notification_id) REFERENCES runtime.notification (tenant_id, notification_id),
  FOREIGN KEY (tenant_id, finding_id) REFERENCES runtime.finding (tenant_id, finding_id)
);

CREATE TABLE runtime.follow_up_event (
  tenant_id       uuid        NOT NULL,
  event_id        uuid        NOT NULL DEFAULT uuidv7(),
  action_id       uuid        NOT NULL,
  item_id         text        NOT NULL CHECK (length(item_id) BETWEEN 1 AND 200),
  finding_id      text,
  recommendation  text        NOT NULL CHECK (length(btrim(recommendation)) BETWEEN 1 AND 2000),
  status          text        NOT NULL CHECK (status IN ('created', 'assigned', 'in_progress', 'completed',
                                                         'blocked')),
  jira_key        text        CHECK (jira_key IS NULL OR jira_key ~ '^[A-Z][A-Z0-9_]{0,19}-[0-9]{1,9}$'),
  jira_assignee   text,
  actor           text        NOT NULL,
  note            text,
  at              timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, finding_id) REFERENCES runtime.finding (tenant_id, finding_id),
  CHECK (status NOT IN ('completed', 'blocked') OR jira_key IS NOT NULL
         OR length(btrim(coalesce(note, ''))) >= 5)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['runtime.notification_ack', 'runtime.follow_up_event'] LOOP
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
  END LOOP;
END
$$;

-- Same visibility as the finding they belong to; an acknowledgement is always the actor's own.
CREATE POLICY notification_ack_scope ON runtime.notification_ack AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (EXISTS (SELECT 1 FROM runtime.finding f WHERE f.tenant_id = notification_ack.tenant_id
                   AND f.finding_id = notification_ack.finding_id));
CREATE POLICY notification_ack_own ON runtime.notification_ack AS RESTRICTIVE FOR INSERT TO veloxa_app
  WITH CHECK (actor = current_setting('app.principal_id')
              AND EXISTS (SELECT 1 FROM runtime.finding f WHERE f.tenant_id = notification_ack.tenant_id
                            AND f.finding_id = notification_ack.finding_id));
CREATE POLICY follow_up_scope ON runtime.follow_up_event AS RESTRICTIVE FOR SELECT TO veloxa_app
  USING (finding_id IS NULL OR EXISTS (SELECT 1 FROM runtime.finding f WHERE f.tenant_id = follow_up_event.tenant_id
                                         AND f.finding_id = follow_up_event.finding_id));
CREATE POLICY follow_up_own ON runtime.follow_up_event AS RESTRICTIVE FOR INSERT TO veloxa_app
  WITH CHECK (actor = current_setting('app.principal_id'));
GRANT SELECT, INSERT ON runtime.notification_ack, runtime.follow_up_event TO veloxa_app;

-- The finding's lifecycle state ignores acknowledgements (they belong to notifications); earlier acknowledged events
-- stay in the history.
CREATE OR REPLACE VIEW runtime.finding_current WITH (security_invoker = true) AS
SELECT f.*, (SELECT e.state FROM runtime.finding_event e WHERE e.tenant_id = f.tenant_id
               AND e.finding_id = f.finding_id AND e.state <> 'acknowledged'
             ORDER BY e.at DESC, e.event_id DESC LIMIT 1) AS state,
            (SELECT max(e.at) FROM runtime.finding_event e WHERE e.tenant_id = f.tenant_id
               AND e.finding_id = f.finding_id) AS updated_at
  FROM runtime.finding f;
