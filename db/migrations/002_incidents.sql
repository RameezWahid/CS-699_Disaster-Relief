-- Group related reports. Keep category groups separate.
CREATE TABLE incidents (
    id bigserial PRIMARY KEY,
    category_group text NOT NULL
        CHECK (category_group IN ('rescue', 'medical', 'supply', 'road', 'info')),
    category text NOT NULL,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'verified', 'resolved', 'dismissed', 'merged')),
    merged_into bigint REFERENCES incidents (id),
    geom geometry(Point, 4326),
    uncertainty_m real,
    ward text REFERENCES wards (code),
    headcount int,
    headcount_conflict boolean NOT NULL DEFAULT false,
    urgency_level int NOT NULL CHECK (urgency_level BETWEEN 1 AND 5),
    urgency jsonb NOT NULL,
    credibility real NOT NULL,
    credibility_signals jsonb NOT NULL,
    report_count int NOT NULL,
    sender_count int NOT NULL,
    first_report_at timestamptz NOT NULL,
    last_report_at timestamptz NOT NULL
);

-- Find incidents by location. Keep the spatial lookup fast.
CREATE INDEX incidents_geom_idx ON incidents USING gist (geom);

-- Index only active incidents. Resolved and dismissed incidents do not belong here.
CREATE INDEX incidents_active_idx ON incidents (last_report_at)
    WHERE status IN ('open', 'verified');

-- Link reports to their incidents.
ALTER TABLE reports ADD COLUMN incident_id bigint REFERENCES incidents (id);

-- Find all reports belonging to an incident.
CREATE INDEX reports_incident_idx ON reports (incident_id);