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
        " WHERE r.user_id = %s AND e.kind = 'model_call' AND e.at > now() - interval '1 day'"
        " AND NOT e.payload ? 'weight'",  # Q&A calls have their own budget (question_limit)
        (report_id, user_id),
    ).fetchone()
    return per_report >= settings.rate_answers_report or per_user >= settings.rate_answers_day


# A Q&A model_call row has payload {"weight": w, "tokens": n}: weight 1 for a question, 0.25
# for a fixed 0-token reply, 0 when nothing was answered (an outage, busy, or a closed stream
# must not lock anyone out); tokens = what the models used for it.
WEIGHT = "coalesce((e.payload->>'weight')::real, 1)"
TOKENS = "coalesce((e.payload->>'tokens')::int, 0)"
DAY = "e.kind = 'model_call' AND e.at > now() - interval '1 day'"


def question_limit(conn, user_id: uuid.UUID, report_id=None) -> str | None:
    """Q&A budget (contract §4, §4A), checked before any model or embedding call: 10 per
    report, 40 per user per 24 h shared by /ask and /chat (10 for an account younger than a
    day, REVIEW B-13), and the site-wide free-quota guard on requests AND tokens (B-056).
    The CALLER holds the per-user advisory lock and writes its model_call row in the same
    transaction, so parallel questions can't both slip under the limit (SEC-015 pattern).
    Returns the 429 message_key, or None."""
    per_report, ask_user = conn.execute(
        f"SELECT coalesce(sum({WEIGHT}) FILTER (WHERE e.report_id = %s), 0),"
        f" coalesce(sum({WEIGHT}), 0) FROM report_event e JOIN report r ON r.id = e.report_id"
        f" WHERE r.user_id = %s AND {DAY} AND e.payload ? 'weight'",
        (report_id, user_id)).fetchone()
    chat_user = conn.execute(f"SELECT coalesce(sum({WEIGHT}), 0) FROM chat_event e"
                             f" WHERE e.user_id = %s AND {DAY}", (user_id,)).fetchone()[0]
    # ponytail: the site-wide sums scan a day of rows; fine at free-tier volumes (~1k/day).
    requests, tokens = conn.execute(
        f"SELECT sum(w), sum(t) FROM ("
        f" SELECT {WEIGHT} AS w, {TOKENS} AS t FROM report_event e"
        f"  WHERE {DAY} AND e.payload ? 'weight'"
        f" UNION ALL SELECT {WEIGHT}, {TOKENS} FROM chat_event e WHERE {DAY}) calls"
    ).fetchone()
    guard = settings.qa_quota_guard
    if ((requests or 0) >= settings.qa_site_day * guard
            or (tokens or 0) >= settings.qa_site_tokens_day * guard):
        return "qa_busy"
    new_user = conn.execute("SELECT created_at > now() - interval '1 day' FROM app_user"
                            " WHERE id = %s", (user_id,)).fetchone()
    day_limit = settings.rate_questions_day
    if new_user and new_user[0]:
        day_limit = min(day_limit, settings.rate_questions_new_user)
    if ask_user + chat_user >= day_limit:
        return "rate_limited_questions"
    if report_id is not None and per_report >= settings.rate_questions_report:
        return "rate_limited_questions"
    return None


def check(conn, user_id: uuid.UUID) -> str | None:
    """Checked before any model call: the 429 message_key, or None if the user may go on.
    The same-spot rule needs the category, so the intake graph checks it at `route`."""
    if cooldown_hit(conn, user_id):
        return "rate_limited_cooldown"
    if daily_limit_hit(conn, user_id):
        return "rate_limited_daily"
    return None


LOGIN_WINDOW_S = 600  # all login limits count the last 10 minutes


def login_limited(conn, key: str, limit: int) -> bool:
    """SEC-004: login tries live in Postgres, so the count survives restarts and is shared
    by every worker. Locks the key so parallel tries can't both slip under the limit,
    drops rows older than the window (the table stays tiny), then counts and records."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
    conn.execute("DELETE FROM login_attempt WHERE at < now() - make_interval(secs => %s)",
                 (LOGIN_WINDOW_S,))
    count = conn.execute("SELECT count(*) FROM login_attempt WHERE key = %s",
                         (key,)).fetchone()[0]
    if count >= limit:
        return True
    conn.execute("INSERT INTO login_attempt (key) VALUES (%s)", (key,))
    return False
