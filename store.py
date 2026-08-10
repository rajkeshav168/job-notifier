"""
store.py — SQLite state store for tracking seen jobs.

WHY SQLITE:
- Zero setup, single file, no external service
- ACID-compliant — won't corrupt if the process crashes mid-write
- Portable — works on any machine, easy to back up (just copy the file)
- For a personal tool checking ~50 companies, this handles millions of rows easily

SCHEMA DESIGN:
- One table: `seen_jobs`
- Primary key: (company, job_id) — a job at Stripe with id "12345" is unique
- We store the full job data so we can reconstruct history later if needed
- `first_seen_at` is when we first discovered it (useful for analytics)
"""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class JobStore:
    def __init__(self, db_path: str = "jobs.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Create the table if it doesn't exist."""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_jobs (
                    company      TEXT NOT NULL,
                    job_id       TEXT NOT NULL,
                    title        TEXT,
                    location     TEXT,
                    url          TEXT,
                    first_seen_at TEXT NOT NULL,
                    raw_data     TEXT,  -- full JSON blob for future use
                    PRIMARY KEY (company, job_id)
                )
            """)
            # Index for quick lookups by company
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_seen_jobs_company
                ON seen_jobs(company)
            """)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def filter_new(self, jobs: list[dict]) -> list[dict]:
        """
        Given a list of fetched jobs, return only the ones we haven't seen before.

        This is the CORE DIFFING LOGIC:
        - Pull all known job_ids for this company from the DB
        - Compare against the incoming list
        - Return the difference
        """
        if not jobs:
            return []

        company = jobs[0]["company"]

        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT job_id FROM seen_jobs WHERE company = ?",
                (company,)
            )
            seen_ids = {row[0] for row in cursor.fetchall()}

        new_jobs = [j for j in jobs if j["id"] not in seen_ids]
        logger.info(f"[Store] {company}: {len(new_jobs)} new out of {len(jobs)} total")
        return new_jobs

    def mark_seen(self, jobs: list[dict]):
        """
        Mark jobs as seen. Uses INSERT OR IGNORE so re-runs are safe (idempotent).
        """
        if not jobs:
            return

        now = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO seen_jobs
                    (company, job_id, title, location, url, first_seen_at, raw_data)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        j["company"],
                        j["id"],
                        j["title"],
                        j["location"],
                        j["url"],
                        now,
                        json.dumps(j),
                    )
                    for j in jobs
                ],
            )

        logger.info(f"[Store] Marked {len(jobs)} jobs as seen")

    def get_stats(self) -> dict:
        """Get a summary of tracked jobs per company."""
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT company, COUNT(*) as cnt,
                       MIN(first_seen_at) as oldest,
                       MAX(first_seen_at) as newest
                FROM seen_jobs
                GROUP BY company
                ORDER BY company
            """)
            return {
                row[0]: {"count": row[1], "oldest": row[2], "newest": row[3]}
                for row in cursor.fetchall()
            }

    def clear(self, company: str = None):
        """Clear seen jobs. If company given, only clear that company."""
        with self._conn() as conn:
            if company:
                conn.execute("DELETE FROM seen_jobs WHERE company = ?", (company,))
                logger.info(f"[Store] Cleared seen jobs for {company}")
            else:
                conn.execute("DELETE FROM seen_jobs")
                logger.info("[Store] Cleared ALL seen jobs")
