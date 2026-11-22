-- Offline responder app (M7, demo scope).

-- After a coordinator accepts an allocation, the responder marks it dispatched, then delivered.
ALTER TABLE allocations DROP CONSTRAINT allocations_status_check;
ALTER TABLE allocations ADD CONSTRAINT allocations_status_check
    CHECK (status IN ('proposed', 'accepted', 'dispatched', 'delivered', 'expired', 'cancelled'));

-- Every action a responder device sends through POST /api/sync is recorded once by its
-- client-generated id, so a resend after a dropped connection is answered as a duplicate.
CREATE TABLE sync_actions (
    client_action_id uuid PRIMARY KEY,
    device_id text NOT NULL,
    username text NOT NULL,
    type text NOT NULL,
    result text NOT NULL CHECK (result IN ('applied', 'rejected')),
    reason text,
    client_ts timestamptz,  -- kept for audit only; the server's clock decides ordering
    received_at timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE ON sync_actions TO relief_app;
