"""Rate limits (BR-4) as plain SQL counts over the report table."""

import uuid

from app.config import settings


def daily_limit_hit(conn, user_id: uuid.UUID) -> bool:
    """Reports in the last 24 h. Attempts the AI couldn't handle don't count (B-033): a user
    retrying during an outage must not lock themselves out."""
    count = conn.execute(
        "SELECT count(*) FROM report WHERE user_id = %s AND created_at > now() - interval '1 day'"
        " AND state <> 'ai_unavailable'",
        (user_id,),
    ).fetchone()[0]
    return count >= settings.rate_reports_day


def same_spot_hit(conn, user_id: uuid.UUID, category: str, lat: float, lng: float,
                  report_id=None) -> bool:
    """Same user + same category within 50 m in 24 h, not counting this report itself.
    ::geography makes the distance metres."""
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM report WHERE user_id = %s AND category = %s"
        " AND id IS DISTINCT FROM %s"
        " AND created_at > now() - make_interval(hours => %s)"
        " AND ST_DWithin(pin::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s))",
        (user_id, category, report_id, settings.rate_same_spot_h, lng, lat,
         settings.rate_same_spot_m),
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


def answer_limit_hit(conn, user_id: uuid.UUID, report_id) -> bool:
    """Free-text answers cost a model call (SEC-015): at most 5 per report and 30 per user in
    24 h. Each call leaves one 'model_call' event, written right after this check in the
    same transaction, under the per-user lock, so parallel requests can't both pass."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(user_id),))
    per_report, per_user = conn.execute(
        "SELECT count(*) FILTER (WHERE e.report_id = %s), count(*) FROM report_event e"
        " JOIN report r ON r.id = e.report_id"
        " WHERE r.user_id = %s AND e.kind = 'model_call' AND e.at > now() - interval '1 day'",
        (report_id, user_id),
    ).fetchone()
    return per_report >= settings.rate_answers_report or per_user >= settings.rate_answers_day


def check(conn, user_id: uuid.UUID) -> str | None:
    """Checked before any model call: the 429 message_key, or None if the user may go on.
    The same-spot rule needs the category, so the intake graph checks it at `route`."""
    if cooldown_hit(conn, user_id):
        return "rate_limited_cooldown"
    if daily_limit_hit(conn, user_id):
        return "rate_limited_daily"
    return None
