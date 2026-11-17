-- 0007 time axes of detector runs and signals (ARCHITECTURE.md §8.1, §8.3, §8.4 T1-T5). Additive only.
--
-- A detector run records its full read basis: K (batch_seq), the mode, the recorded-time cutoff R and the valid
-- instant V it was evaluated at. A signal records upper(R) of its evidence (a derived fact is usable only once
-- every input is recorded, T2) and the valid interval of the observation when one is established. Per-evidence
-- version, extraction time E, R and V (with their bases) are kept in the signal payload.
--
-- The database enforces what the detectors promise:
-- - in a RECORDED_BY run, no signal's evidence was recorded after the cutoff;
-- - in a VALID_AT run, every cited evidence item has a KNOWN valid time that holds at the valid instant (a fact
--   of unknown valid time can never answer VALID_AT; T3).
-- Rows recorded before this migration keep NULL in the new columns (history is append-only and not rewritten).

ALTER TABLE runtime.detector_run
  ADD COLUMN recorded_cutoff timestamptz,
  ADD COLUMN valid_at        timestamptz,
  ADD COLUMN time_zone       text;

ALTER TABLE runtime.risk_signal
  ADD COLUMN recorded_upper timestamptz,
  ADD COLUMN valid_from     date,
  ADD COLUMN valid_to       date,
  ADD CONSTRAINT risk_signal_valid_order CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to);

CREATE FUNCTION runtime.check_risk_signal_time() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE run record; bad text;
BEGIN
  SELECT mode, recorded_cutoff, valid_at, time_zone INTO run FROM runtime.detector_run
   WHERE tenant_id = NEW.tenant_id AND run_id = NEW.run_id;
  IF run.mode = 'RECORDED_BY' AND NEW.recorded_upper IS NOT NULL AND NEW.recorded_upper > run.recorded_cutoff THEN
    RAISE EXCEPTION 'signal evidence recorded at % is after the RECORDED_BY cutoff %', NEW.recorded_upper,
      run.recorded_cutoff USING ERRCODE = 'check_violation';
  END IF;
  IF run.mode = 'VALID_AT' THEN
    SELECT e ->> 'record_id' INTO bad FROM jsonb_array_elements(NEW.payload -> 'evidence') AS e
     WHERE coalesce(e ->> 'valid_basis', 'unknown') = 'unknown'
        OR (e ->> 'valid_from')::date > (run.valid_at AT TIME ZONE run.time_zone)::date
        OR (e ->> 'valid_to') IS NOT NULL
           AND (e ->> 'valid_to')::date <= (run.valid_at AT TIME ZONE run.time_zone)::date
     LIMIT 1;
    IF bad IS NOT NULL THEN
      RAISE EXCEPTION 'signal cites % whose valid time is unknown or does not hold at %', bad, run.valid_at
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER risk_signal_time_check BEFORE INSERT ON runtime.risk_signal
  FOR EACH ROW EXECUTE FUNCTION runtime.check_risk_signal_time();
