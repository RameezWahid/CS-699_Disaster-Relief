-- Count a sender's recent messages for intake rate limiting without scanning the inbox.
CREATE INDEX inbox_sender_recent_idx ON inbox (sender_hash, received_at);
