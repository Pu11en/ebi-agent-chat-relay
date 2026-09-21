"""SQLite database schema and initialization."""

from __future__ import annotations

import contextlib
import logging

import aiosqlite

logger = logging.getLogger(__name__)

_CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    working_dir TEXT,
    model TEXT,
    origin TEXT NOT NULL DEFAULT 'discord',
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_used_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- Close lifecycle: 'open' | 'closing' | 'closed'. A session leaves 'open'
    -- only through an authorized close, and 'closing' survives a restart so a
    -- close requested during an active turn still finishes afterwards.
    -- Closing never deletes the row: the record, its folder and its wrap-up
    -- stay queryable so Sessions can list and reopen it.
    lifecycle_state TEXT NOT NULL DEFAULT 'open',
    close_requested_at TEXT,
    close_authority TEXT,      -- which authority source asked (never model intent)
    wrap_up TEXT,              -- summary written once, at the moment of closing
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_last_used ON sessions(last_used_at);
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);
-- idx_sessions_lifecycle lives in _MIGRATIONS instead: this script also runs
-- against databases whose sessions table predates lifecycle_state, and an index
-- on a missing column is a hard error here rather than a suppressed one there.

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_asks (
    thread_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    question_idx INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS lounge_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL DEFAULT 'AI',
    message TEXT NOT NULL,
    thread_id INTEGER,
    posted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_lounge_posted_at ON lounge_messages(posted_at);

-- Sessions that should be resumed after a bot restart.
-- Rows expire automatically via TTL checks in PendingResumeRepository.
-- A Claude session that is about to restart the bot writes a row here first;
-- on_ready reads and deletes it to resume the session.
CREATE TABLE IF NOT EXISTS pending_resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL UNIQUE,
    session_id TEXT,           -- optional: used for "claude --resume" continuity
    reason TEXT NOT NULL DEFAULT 'self_restart',
    resume_prompt TEXT,        -- message to post + send to Claude on resume
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Thread inbox: persistent status tracking across bot restarts.
-- Populated when a Claude session ends; cleared when the user replies.
-- status: 'waiting' (user's reply needed) | 'ambiguous' (unclear)
-- confidence: 'high' | 'low' (from claude -p classification)
CREATE TABLE IF NOT EXISTS thread_inbox (
    thread_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'waiting',
    confidence TEXT NOT NULL DEFAULT 'high',
    last_message_url TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Rate limit events emitted by the Claude Code CLI (rate_limit_event stream-json type).
-- One row per rate_limit_type; upserted on every event so this holds the latest state.
CREATE TABLE IF NOT EXISTS usage_stats (
    rate_limit_type TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    utilization REAL NOT NULL,
    resets_at INTEGER NOT NULL,
    is_using_overage INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Advisory resource claims: a session announces "I am working on X" so a
-- second session asking for the same X is told to step aside *before* it
-- starts. Advisory only — nothing enforces them. Every claim has a TTL so a
-- session that dies cannot pin a resource forever.
CREATE TABLE IF NOT EXISTS resource_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource TEXT NOT NULL UNIQUE,
    thread_id INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resource_claims_thread ON resource_claims(thread_id);

-- Where a ThreadKey actually lives. Every other table stores a conversation as
-- a bare integer; for Discord that integer is the thread's snowflake and needs
-- no translation, but a frontend whose ids are strings mints a surrogate here.
-- A surrogate is a hash, and a hash does not run backwards — without this row
-- a session could be looked up and still be unreplyable.
CREATE TABLE IF NOT EXISTS frontend_threads (
    thread_key INTEGER PRIMARY KEY,
    frontend TEXT NOT NULL,
    external_id TEXT NOT NULL,
    parent_external_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(frontend, external_id)
);

CREATE INDEX IF NOT EXISTS idx_frontend_threads_frontend ON frontend_threads(frontend);
"""

# Handoff tables are defined once and used twice: appended to SCHEMA for a fresh
# database, and replayed statement by statement in _MIGRATIONS for an existing
# one. Keeping a single source avoids the two drifting apart.
_HANDOFF_SCHEMA = """
-- ---------------------------------------------------------------------------
-- Trusted cross-computer handoffs (see openspec `trusted-agent-handoffs`).
--
-- Discord is an at-least-once transport: the same task packet can arrive twice,
-- out of order, or right as the bot restarts. These five tables are what makes
-- that survivable. The uniqueness constraints are the load-bearing part — they
-- are what turns "two copies arrived" into one logical job without a lock, and
-- what stops a redelivered terminal result from queueing a second delivery.
--
-- Every table is new and unreferenced by existing code, so a database written
-- before handoffs existed simply gains them and keeps working.
-- ---------------------------------------------------------------------------

-- One row per logical job: the same task id addressed to two recipients is two
-- jobs, the same task id redelivered to one recipient is one.
CREATE TABLE IF NOT EXISTS handoff_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    sender_agent_id TEXT NOT NULL,
    -- The validated packet, stored verbatim so a job can be replayed exactly as
    -- it was authorized rather than as today's code would re-derive it.
    packet_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'accepted',
    attempt INTEGER NOT NULL DEFAULT 1,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    retryable INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    job_thread_id INTEGER,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, recipient_agent_id)
);

CREATE INDEX IF NOT EXISTS idx_handoff_tasks_recipient_state
    ON handoff_tasks(recipient_agent_id, state);
CREATE INDEX IF NOT EXISTS idx_handoff_tasks_job_thread ON handoff_tasks(job_thread_id);

-- Every protocol message about a task, in its own declared order. The unique
-- event id is the deduplication rule from the spec: a redelivered event is
-- recognised rather than applied twice.
CREATE TABLE IF NOT EXISTS handoff_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    sender_agent_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    packet_json TEXT,
    created_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_handoff_events_task ON handoff_events(task_id, sequence, id);

-- One row per execution attempt. Claiming an attempt is how a restarted bot
-- tells work that is still running from work that only says it is.
CREATE TABLE IF NOT EXISTS handoff_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    execution_ref TEXT,
    outcome TEXT,
    detail TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(task_id, recipient_agent_id, attempt)
);

-- The terminal outcome of a logical job, written once.
CREATE TABLE IF NOT EXISTS handoff_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    outcome TEXT NOT NULL,
    summary TEXT,
    retryable INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, recipient_agent_id)
);

-- Delivery of a terminal result back to the origin conversation. It is a
-- separate row, written in the same transaction as the result, so an origin
-- that was unreachable is retried later instead of rerunning the task.
CREATE TABLE IF NOT EXISTS handoff_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    destination_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_handoff_outbox_due ON handoff_outbox(status, next_attempt_at);
"""

_CAPACITY_RECOVERY_SCHEMA = """
-- ---------------------------------------------------------------------------
-- Durable model-capacity recovery pending turns.
--
-- This table stores one row per logical turn that is waiting for provider
-- capacity, rate-limit recovery, or an explicitly authorized fallback. It is
-- deliberately separate from `sessions` and handoff tables: local relay
-- admission, cross-computer jobs, and provider recovery are three different
-- states with different owners.
--
-- The uniqueness and conditional updates are the idempotency boundary. A turn
-- is created once, a retry attempt is claimed by one worker, and the first
-- completion wins even if a late provider result races a retry.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS capacity_pending_turns (
    turn_key TEXT PRIMARY KEY,
    frontend TEXT NOT NULL,
    thread_id INTEGER NOT NULL,
    session_id TEXT,
    prompt_ref TEXT NOT NULL,
    backend TEXT NOT NULL,
    model TEXT,
    fallback_chain_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    claim_token TEXT,
    claimed_at TEXT,
    accepted_at TEXT,
    accepted_result_ref TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capacity_pending_due
    ON capacity_pending_turns(state, next_attempt_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_capacity_pending_thread
    ON capacity_pending_turns(frontend, thread_id);
"""

# ---------------------------------------------------------------------------
# Shared project catalog: personal Favorite / Hide / recency metadata (v4.1).
#
# Keyed by the stable catalog identity (owner:computer:root-key:folder), never
# by path, so a moved root or a renamed drive letter does not orphan a user's
# favorites.  Discovery is filesystem truth; nothing here says a project
# exists, so metadata for a folder that is temporarily absent stays put.
# ---------------------------------------------------------------------------
_PROJECT_CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_catalog_metadata (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    project_key TEXT NOT NULL,
    favorite INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    last_opened_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id, project_key)
);

CREATE INDEX IF NOT EXISTS idx_project_catalog_recent
    ON project_catalog_metadata(guild_id, user_id, last_opened_at);
"""

# Fresh databases get everything in one script.
SCHEMA = _CORE_SCHEMA + _HANDOFF_SCHEMA + _CAPACITY_RECOVERY_SCHEMA + _PROJECT_CATALOG_SCHEMA


def _statements(script: str) -> list[str]:
    """Split a DDL script into individually replayable statements."""
    return [part.strip() for part in script.split(";") if part.strip()]


# Migrations for existing databases that lack new columns.
_MIGRATIONS = [
    "ALTER TABLE sessions ADD COLUMN origin TEXT NOT NULL DEFAULT 'discord'",
    "ALTER TABLE sessions ADD COLUMN summary TEXT",
    # Which CLI produced this session ID (claude / codex) — see claude_code_core.models.
    "ALTER TABLE sessions ADD COLUMN backend TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id)",
    # Lounge table added in v1.x — safe to run on existing DBs
    (
        "CREATE TABLE IF NOT EXISTS lounge_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "label TEXT NOT NULL DEFAULT 'AI', "
        "message TEXT NOT NULL, "
        "posted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    "CREATE INDEX IF NOT EXISTS idx_lounge_posted_at ON lounge_messages(posted_at)",
    # pending_resumes added in v1.3 — safe to run on existing DBs
    (
        "CREATE TABLE IF NOT EXISTS pending_resumes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "thread_id INTEGER NOT NULL UNIQUE, "
        "session_id TEXT, "
        "reason TEXT NOT NULL DEFAULT 'self_restart', "
        "resume_prompt TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    # thread_inbox added in v1.9 — safe to run on existing DBs
    (
        "CREATE TABLE IF NOT EXISTS thread_inbox ("
        "thread_id INTEGER PRIMARY KEY, "
        "status TEXT NOT NULL DEFAULT 'waiting', "
        "confidence TEXT NOT NULL DEFAULT 'high', "
        "last_message_url TEXT, "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    # Drop UNIQUE constraint on session_id to allow /fork (multiple threads, same source session)
    # SQLite cannot ALTER INDEX, so we drop and recreate as a non-unique index.
    "DROP INDEX IF EXISTS idx_sessions_session_id",
    "CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id)",
    # context stats columns added in v2.0
    "ALTER TABLE sessions ADD COLUMN context_window INTEGER",
    "ALTER TABLE sessions ADD COLUMN context_used INTEGER",
    # usage_stats table added in v2.0
    (
        "CREATE TABLE IF NOT EXISTS usage_stats ("
        "rate_limit_type TEXT PRIMARY KEY, "
        "status TEXT NOT NULL, "
        "utilization REAL NOT NULL, "
        "resets_at INTEGER NOT NULL, "
        "is_using_overage INTEGER NOT NULL DEFAULT 0, "
        "recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    # thread_id column on lounge_messages — tracks which Discord thread posted the message
    "ALTER TABLE lounge_messages ADD COLUMN thread_id INTEGER",
    # resource_claims added in v3.2 — advisory cross-session locks
    (
        "CREATE TABLE IF NOT EXISTS resource_claims ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "resource TEXT NOT NULL UNIQUE, "
        "thread_id INTEGER NOT NULL, "
        "note TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')), "
        "expires_at TEXT NOT NULL)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_resource_claims_thread ON resource_claims(thread_id)",
    # Session close lifecycle added in v3.3. Existing rows adopt 'open' via the
    # column default, so a database written before close existed keeps every
    # session usable and nothing has to be backfilled.
    "ALTER TABLE sessions ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'open'",
    "ALTER TABLE sessions ADD COLUMN close_requested_at TEXT",
    "ALTER TABLE sessions ADD COLUMN close_authority TEXT",
    "ALTER TABLE sessions ADD COLUMN wrap_up TEXT",
    "ALTER TABLE sessions ADD COLUMN closed_at TEXT",
    "CREATE INDEX IF NOT EXISTS idx_sessions_lifecycle ON sessions(lifecycle_state)",
    # Trusted agent handoffs added in v3.4. Every statement is CREATE IF NOT
    # EXISTS, so replaying the whole handoff schema is how an older database
    # gains the ledger without a separate hand-written migration per table.
    *_statements(_HANDOFF_SCHEMA),
    # Model-capacity recovery pending turns added in v4.1. The table is
    # additive and owns no foreign keys, so older session and handoff rows are
    # preserved exactly.
    *_statements(_CAPACITY_RECOVERY_SCHEMA),
    # Shared project catalog metadata added in v4.1: additive, no foreign keys.
    *_statements(_PROJECT_CATALOG_SCHEMA),
]


async def init_db(db_path: str) -> None:
    """Initialize the database with the schema.

    For fresh databases the full SCHEMA is applied. For existing databases
    the migration statements add any missing columns idempotently.
    """
    async with aiosqlite.connect(db_path) as db:
        # WAL lets readers continue using the last committed snapshot while a
        # different connection holds a write transaction.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            with contextlib.suppress(Exception):
                await db.execute(stmt)
        await db.commit()
    logger.info("Database initialized at %s", db_path)
