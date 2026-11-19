-- Dashboard users. An empty wards list means all wards.
CREATE TABLE users (
    id bigserial PRIMARY KEY,
    username text NOT NULL UNIQUE,
    password_hash text NOT NULL,  -- Argon2id
    role text NOT NULL CHECK (role IN ('responder', 'coordinator', 'admin')),
    wards text[] NOT NULL DEFAULT '{}'
);

GRANT SELECT, INSERT, UPDATE ON users TO relief_app;
GRANT USAGE, SELECT ON SEQUENCE users_id_seq TO relief_app;
