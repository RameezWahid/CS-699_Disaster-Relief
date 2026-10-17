-- Phase 1: store geography, place names, incoming reports, and the audit log.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Store the 24 BMC wards. Each ward has a unique code and a geographic boundary.
CREATE TABLE wards (
    code text PRIMARY KEY,
    geom geometry(MultiPolygon, 4326) NOT NULL
);
-- Use spatial lookup instead of scanning every ward.
CREATE INDEX wards_geom_idx ON wards USING gist (geom);

-- Store H3 resolution-8 cells. Later phases add risk and population data.
CREATE TABLE cells (
    h3 text PRIMARY KEY,
    ward text REFERENCES wards (code),
    geom geometry(Polygon, 4326) NOT NULL
);
CREATE INDEX cells_geom_idx ON cells USING gist (geom);

-- Store one record per real place. Keep alternate spellings in place_names.
CREATE TABLE places (
    id bigserial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('chronic_spot', 'landmark', 'road', 'locality', 'ward')),
    name text NOT NULL,
    geom geometry(Point, 4326) NOT NULL,
    uncertainty_m real NOT NULL,
    ward text REFERENCES wards (code),
    osm_id text UNIQUE,
    lgd_code text,
    source text NOT NULL
);
CREATE INDEX places_geom_idx ON places USING gist (geom);

-- Store alternate names separately. Use the same normalizer as the worker.
CREATE TABLE place_names (
    place_id bigint NOT NULL REFERENCES places (id) ON DELETE CASCADE,
    name text NOT NULL,
    name_norm text NOT NULL,
    PRIMARY KEY (place_id, name_norm)
);
-- Find similar normalized names without scanning the whole table.
CREATE INDEX place_names_trgm_idx ON place_names USING gin (name_norm gin_trgm_ops);

-- Keep incoming messages until a worker processes them.
-- SKIP LOCKED lets workers claim different rows without waiting on each other.
CREATE TABLE inbox (
    id bigserial PRIMARY KEY,
    source text NOT NULL CHECK (source IN ('web', 'telegram', 'replay')),
    source_msg_id text NOT NULL,
    sender_hash text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'dead')),
    attempts int NOT NULL DEFAULT 0,
    last_error text,
    UNIQUE (source, source_msg_id)
);
-- Index only pending rows. Completed rows do not belong in this queue.
CREATE INDEX inbox_pending_idx ON inbox (id) WHERE status = 'pending';

-- Wake up listeners when a new message arrives.
CREATE FUNCTION notify_inbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('inbox', NEW.id::text);
    RETURN NULL;
END $$;

CREATE TRIGGER inbox_notify
AFTER INSERT ON inbox
FOR EACH ROW EXECUTE FUNCTION notify_inbox();

-- Store the extracted report and link it to its original message.
CREATE TABLE reports (
    id bigserial PRIMARY KEY,
    inbox_id bigint NOT NULL UNIQUE REFERENCES inbox (id),
    received_at timestamptz NOT NULL,
    source text NOT NULL,
    sender_hash text NOT NULL,
    text text NOT NULL,
    lang text NOT NULL,
    simhash bigint NOT NULL,
    repeat_of bigint REFERENCES reports (id),
    has_gps boolean NOT NULL,
    has_photo boolean NOT NULL,
    hazard_type text NOT NULL DEFAULT 'flood',
    category text NOT NULL,
    category_p real NOT NULL,
    headcount int,
    needs text[] NOT NULL,
    vulnerability text[] NOT NULL,
    geom geometry(Point, 4326),
    ward text REFERENCES wards (code),
    loc_uncertainty_m real,
    loc_confidence real,
    loc_source text,
    urgency_level int NOT NULL CHECK (urgency_level BETWEEN 1 AND 5),
    needs_review boolean NOT NULL,
    extraction jsonb NOT NULL
);
CREATE INDEX reports_geom_idx ON reports USING gist (geom);
CREATE INDEX reports_received_idx ON reports (received_at);

-- Keep an append-only, hash-chained audit log.
-- Each hash covers the previous hash and the current event.
-- Use the same serialization in scripts/verify_chain.py.
CREATE TABLE event_log (
    id bigint PRIMARY KEY,
    ts timestamptz NOT NULL DEFAULT clock_timestamp(),
    kind text NOT NULL,
    ward text,
    payload jsonb NOT NULL,
    prev_hash text NOT NULL,
    hash text NOT NULL
);

CREATE SEQUENCE event_log_id_seq;

-- Build the hash chain before each insert.
CREATE FUNCTION event_log_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    last_hash text;
BEGIN
    -- Serialize inserts. Assign the ID after taking the lock so IDs follow chain order.
    PERFORM pg_advisory_xact_lock(hashtext('event_log_chain'));
    NEW.id := nextval('event_log_id_seq');
    NEW.ts := clock_timestamp();

    SELECT hash INTO last_hash
    FROM event_log
    ORDER BY id DESC
    LIMIT 1;

    -- Start the first chain entry with 64 zeroes.
    NEW.prev_hash := coalesce(last_hash, repeat('0', 64));

    -- Hash epoch microseconds, not timestamp text. Timestamp text depends on session settings.
    NEW.hash := encode(sha256(convert_to(
        NEW.prev_hash || '|' || NEW.id || '|' || (extract(epoch FROM NEW.ts) * 1000000)::bigint
        || '|' || NEW.kind || '|' || coalesce(NEW.ward, '') || '|' || NEW.payload::text,
        'UTF8')), 'hex');

    RETURN NEW;
END $$;

CREATE TRIGGER event_log_chain
BEFORE INSERT ON event_log
FOR EACH ROW EXECUTE FUNCTION event_log_chain();

-- Wake up event listeners when a new audit entry is committed.
CREATE FUNCTION notify_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('events', NEW.id::text);
    RETURN NULL;
END $$;

CREATE TRIGGER event_log_notify
AFTER INSERT ON event_log
FOR EACH ROW EXECUTE FUNCTION notify_event();
