-- Coordination features: app clock, official alerts, offer windows,
-- dispatch/deliver, manual claims and the responder sync log.

-- One application clock. Live, it is wall time. During a scenario replay it is scenario time,
-- running at the replay speed, so time windows stay correct at 10x or 60x.
-- event_log keeps true wall time.
CREATE TABLE sim_clock (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    wall_start timestamptz NOT NULL,
    scenario_start timestamptz NOT NULL,
    speed real NOT NULL CHECK (speed > 0)
);

CREATE FUNCTION app_now() RETURNS timestamptz LANGUAGE sql VOLATILE AS $$
    SELECT coalesce(
        (SELECT scenario_start + (clock_timestamp() - wall_start) * speed FROM sim_clock),
        clock_timestamp())
$$;

ALTER TABLE inbox ALTER COLUMN received_at SET DEFAULT app_now();

-- Official alerts, switched on by a coordinator. A NULL ward means city-wide.
CREATE TABLE alerts (
    id bigserial PRIMARY KEY,
    source text NOT NULL CHECK (source IN ('manual', 'sachet')),
    title text NOT NULL,
    ward text REFERENCES wards (code),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    CHECK (ends_at > starts_at)
);

-- Offers are available only in a time window. An offer past its window expires.
ALTER TABLE offers
    ADD COLUMN available_from timestamptz NOT NULL DEFAULT app_now(),
    ADD COLUMN available_until timestamptz NOT NULL DEFAULT app_now() + interval '7 days',
    ADD COLUMN synthetic boolean NOT NULL DEFAULT false,
    ADD CHECK (available_until > available_from),
    DROP CONSTRAINT offers_status_check,
    ADD CONSTRAINT offers_status_check CHECK (status IN ('active', 'withdrawn', 'expired'));

-- Responders dispatch and deliver accepted allocations. A retried manual claim carries the
-- same client_action_id, so it is stored once.
ALTER TABLE allocations
    DROP CONSTRAINT allocations_status_check,
    ADD CONSTRAINT allocations_status_check
        CHECK (status IN ('proposed', 'accepted', 'dispatched', 'delivered', 'expired',
                          'cancelled')),
    ADD COLUMN client_action_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid();

-- Every action a responder device sends through POST /api/sync, recorded once by its
-- client-generated id, so a resend after a dropped connection is answered as a duplicate.
CREATE TABLE sync_actions (
    client_action_id uuid PRIMARY KEY,
    device_id text NOT NULL,
    user_id bigint REFERENCES users (id),
    type text NOT NULL,
    result text NOT NULL CHECK (result IN ('applied', 'rejected')),
    reason text,
    client_ts timestamptz,  -- kept for audit only; the server's clock decides ordering
    received_at timestamptz NOT NULL DEFAULT now()
);

-- 005 grants nothing by default, so grant the new tables explicitly.
GRANT SELECT, INSERT, UPDATE ON alerts, sync_actions TO relief_app;
GRANT USAGE, SELECT ON SEQUENCE alerts_id_seq TO relief_app;
-- Replays set and clear the clock.
GRANT SELECT, INSERT, UPDATE, DELETE ON sim_clock TO relief_app;
-- Merging incidents folds one need into another and removes the absorbed one.
GRANT DELETE ON needs TO relief_app;
