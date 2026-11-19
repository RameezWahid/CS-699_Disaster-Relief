-- Verification queue and fast track for incidents.

-- Low-credibility incidents wait for a coordinator. Urgency-5 incidents skip that wait.
ALTER TABLE incidents
    ADD COLUMN needs_verification boolean NOT NULL DEFAULT false,
    ADD COLUMN fast_track boolean NOT NULL DEFAULT false;

-- The verification queue: open incidents a coordinator still has to check.
CREATE INDEX incidents_verify_idx ON incidents (first_report_at)
    WHERE status = 'open' AND needs_verification;
