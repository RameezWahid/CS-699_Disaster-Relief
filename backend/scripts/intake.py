# Shared intake for web form, Telegram bot, and replay.
# Hide sender IDs and put each new message in the database inbox.

import hashlib
import hmac
import json

from psycopg import AsyncConnection

from scripts.config import SENDER_HASH_SECRET


def sender_hash(source: str, raw_sender_id: str) -> str:
    # Include the source so identical IDs from different platforms stay separate.
    message = f"{source}:{raw_sender_id}".encode()

    # Use a secret key to make the hash harder to guess.
    # Store the hash, not the original phone number or Telegram ID.
    return hmac.new(
        SENDER_HASH_SECRET,
        message,
        hashlib.sha256,
    ).hexdigest()


async def enqueue(
    conn: AsyncConnection,
    source: str,
    source_msg_id: str,
    sender: str,
    payload: dict,
) -> int | None:
    # Insert the message and let the database reject duplicates.
    # A separate duplicate check would create a race between concurrent requests.
    cursor = await conn.execute(
        """
        INSERT INTO inbox (source, source_msg_id, sender_hash, payload)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source, source_msg_id) DO NOTHING
        RETURNING id
        """,
        (source, source_msg_id, sender, json.dumps(payload, ensure_ascii=False)),
    )

    # A new message returns its inbox ID.
    # A duplicate returns no row, so return None.
    row = await cursor.fetchone()
    return row[0] if row else None