"""Rate limits (BR-4) as plain SQL counts over the report table."""

import uuid

from app.config import settings


def daily_limit_hit(conn, user_id: uuid.UUID) -> bool:
    count = conn.execute(
        "SELECT count(*) FROM report WHERE user_id = %s AND created_at > now() - interval '1 day'",
        (user_id,),
    ).fetchone()[0]
    return count >= settings.rate_reports_day


def same_spot_hit(conn, user_id: uuid.UUID, category: str, lat: float, lng: float) -> bool:
    """Same user + same category within 50 m in 24 h. ::geography makes the distance metres."""
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM report WHERE user_id = %s AND category = %s"
        " AND created_at > now() - make_interval(hours => %s)"
        " AND ST_DWithin(pin::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s))",
        (user_id, category, settings.rate_same_spot_h, lng, lat, settings.rate_same_spot_m),
    ).fetchone()[0]


def cooldown_hit(conn, user_id: uuid.UUID) -> bool:
    """3 junk photos within 10 min -> 15 min cooldown. Looks at the user's last 3 junk
    reports. Nothing sets junk_reason before Sprint 4, so this never fires yet."""
    return conn.execute(
        "SELECT count(*) = 3 AND max(created_at) - min(created_at) <= interval '10 minutes'"
        " AND max(created_at) > now() - interval '15 minutes'"
        " FROM (SELECT created_at FROM report WHERE user_id = %s AND junk_reason IS NOT NULL"
        "       ORDER BY created_at DESC LIMIT 3) last3",
        (user_id,),
    ).fetchone()[0]


def check(conn, user_id: uuid.UUID, category: str, lat: float, lng: float) -> str | None:
    """The 429 message_key, or None if the user may file this report."""
    if cooldown_hit(conn, user_id):
        return "rate_limited_cooldown"
    if daily_limit_hit(conn, user_id):
        return "rate_limited_daily"
    if same_spot_hit(conn, user_id, category, lat, lng):
        return "rate_limited_same_spot"
    return None
