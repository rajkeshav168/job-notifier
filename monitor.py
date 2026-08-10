"""
monitor.py — Main orchestrator.

This is the entry point. It:
1. Loads config
2. For each company: fetch → filter by role → diff against DB → notify → save
3. Exits (for cron) or loops (for daemon mode)

ROLE FILTERING LOGIC:
- We do substring matching on job titles (case-insensitive)
- A job matches if ANY target role appears in its title
- Excluded roles are filtered out AFTER matching
- This keeps it simple. For fancier matching (e.g. semantic similarity),
  you'd plug in an LLM or embedding model here.
"""

import sys
import time
import logging
import argparse
from pathlib import Path

import yaml

from fetchers import fetch_jobs
from store import JobStore
from notifier import send_notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    """Load and validate the YAML config."""
    config_path = Path(path)
    if not config_path.exists():
        logger.error(f"Config file not found: {path}")
        logger.error("Copy config.example.yaml to config.yaml and edit it.")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Basic validation
    assert "companies" in config, "Config must have 'companies' list"
    assert "roles" in config, "Config must have 'roles' list"
    return config


def matches_role(job_title: str, target_roles: list[str], exclude_roles: list[str]) -> bool:
    """
    Check if a job title matches any of the target roles
    and doesn't match any excluded roles.

    >>> matches_role("Senior Data Engineer", ["data engineer"], ["intern"])
    True
    >>> matches_role("Data Engineering Intern", ["data engineer"], ["intern"])
    False
    """
    title_lower = job_title.lower()

    # Must match at least one target role
    if not any(role.lower() in title_lower for role in target_roles):
        return False

    # Must not match any excluded role
    if any(excl.lower() in title_lower for excl in exclude_roles):
        return False

    return True



def matches_location(job_location: str, target_locations: list[str]) -> bool:
    """
    Check if a job location matches any target location.
    If no target locations configured, allow all.
    If location is unknown/generic, let it through (filter at notification time).
    """
    if not target_locations:
        return True

    loc_lower = job_location.lower()

    # Unknown locations pass through — better to see an irrelevant job
    # than miss a real one. Playwright often can't extract location.
    unknown = ["see posting", "unknown", "multiple locations", "various"]
    if any(u in loc_lower for u in unknown):
        return True

    return any(loc.lower() in loc_lower for loc in target_locations)

def run_once(config: dict):
    """
    Single pass: check all companies, notify for new matching jobs.
    This is what cron calls.
    """
    store = JobStore(config.get("db_path", "jobs.db"))
    target_roles = config["roles"]
    exclude_roles = config.get("exclude_roles", [])
    target_locations = config.get("locations", [])

    all_new_matching = []

    for company in config["companies"]:
        logger.info(f"Checking {company['name']}...")

        # Step 1: FETCH — get all current listings
        all_jobs = fetch_jobs(company)

        # Step 2: FILTER — keep only jobs matching our target roles
        # matching = [j for j in all_jobs if matches_role(j["title"], target_roles, exclude_roles)]
        matching = [j for j in all_jobs if matches_role(j["title"], target_roles, exclude_roles) and matches_location(j["location"], target_locations)]
        logger.info(f"  {len(matching)}/{len(all_jobs)} jobs match role filters")

        # Step 3: DIFF — find jobs we haven't seen before
        new_jobs = store.filter_new(matching)

        if new_jobs:
            logger.info(f"  🆕 {len(new_jobs)} NEW matching job(s)!")
            all_new_matching.extend(new_jobs)

        # Step 4: SAVE — mark ALL fetched jobs as seen (not just matching ones).
        # Why all and not just matching? Because if you later broaden your role
        # filters, you don't want a flood of "new" notifications for jobs that
        # were already posted. We track everything, filter at notification time.
        store.mark_seen(all_jobs)

    # Step 5: NOTIFY — send one batch notification for all new matches
    if all_new_matching:
        send_notifications(all_new_matching, config.get("notifications", {}))
    else:
        logger.info("No new matching jobs found this run.")

    # Print stats
    stats = store.get_stats()
    logger.info(f"DB stats: {sum(s['count'] for s in stats.values())} total jobs tracked across {len(stats)} companies")


def run_daemon(config: dict):
    """
    Loop forever, running checks at the configured interval.
    Useful when running under PM2/systemd instead of cron.
    """
    interval = config.get("check_interval_minutes", 30) * 60
    logger.info(f"Running in daemon mode, checking every {interval//60} minutes")

    while True:
        try:
            run_once(config)
        except Exception as e:
            logger.error(f"Error in run cycle: {e}", exc_info=True)

        logger.info(f"Sleeping for {interval//60} minutes...")
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Job Notifier — monitor career pages for new openings")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--daemon", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--stats", action="store_true", help="Show DB stats and exit")
    parser.add_argument("--clear", metavar="COMPANY", nargs="?", const="__all__",
                        help="Clear seen jobs (optionally for a specific company)")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.stats:
        store = JobStore(config.get("db_path", "jobs.db"))
        stats = store.get_stats()
        if not stats:
            print("No jobs tracked yet.")
        for company, info in stats.items():
            print(f"  {company}: {info['count']} jobs (oldest: {info['oldest']}, newest: {info['newest']})")
        return

    if args.clear:
        store = JobStore(config.get("db_path", "jobs.db"))
        company = None if args.clear == "__all__" else args.clear
        store.clear(company)
        return

    if args.daemon:
        run_daemon(config)
    else:
        run_once(config)


if __name__ == "__main__":
    main()
