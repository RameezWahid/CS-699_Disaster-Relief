CREATE TABLE needs (
    id bigserial PRIMARY KEY,
    incident_id bigint NOT NULL REFERENCES incidents (id),
    kind text NOT NULL,
    qty_total int NOT NULL CHECK (qty_total > 0),
    qty_open int NOT NULL CHECK (qty_open >= 0 AND qty_open <= qty_total),
    UNIQUE (incident_id, kind)
);

CREATE TABLE offers (
    id bigserial PRIMARY KEY,
    kind text NOT NULL,
    provider text NOT NULL,
    qty_total int NOT NULL CHECK (qty_total > 0),
    qty_available int NOT NULL CHECK (qty_available >= 0 AND qty_available <= qty_total),
    geom geometry(Point, 4326) NOT NULL,
    ward text REFERENCES wards (code),
    verified boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn'))
);

-- Spatial lookup for nearby offers.
CREATE INDEX offers_geom_idx ON offers USING gist (geom);

CREATE TABLE allocations (
    id bigserial PRIMARY KEY,
    need_id bigint NOT NULL REFERENCES needs (id),
    offer_id bigint NOT NULL REFERENCES offers (id),
    qty int NOT NULL CHECK (qty > 0),
    status text NOT NULL CHECK (status IN ('proposed', 'accepted', 'expired', 'cancelled')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Find proposed allocations that need expiry checks.
CREATE INDEX allocations_open_idx ON allocations (expires_at) WHERE status = 'proposed';

CREATE INDEX allocations_need_idx ON allocations (need_id);
CREATE INDEX allocations_offer_idx ON allocations (offer_id);