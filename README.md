# Job Notifier — Build Your Own Web Monitor

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   SCHEDULER                      │
│        (cron / Cloud Scheduler / GH Actions)     │
└──────────────────────┬──────────────────────────┘
                       │ triggers every N minutes
                       ▼
┌─────────────────────────────────────────────────┐
│                  monitor.py                      │
│              (orchestration loop)                 │
│                                                  │
│  1. Load config.yaml                             │
│  2. For each company:                            │
│     a. FETCH  → fetchers.py gets current jobs    │
│     b. DIFF   → store.py compares vs seen jobs   │
│     c. NOTIFY → notifier.py alerts on new ones   │
│     d. SAVE   → store.py marks jobs as seen      │
└─────────────────────────────────────────────────┘
```

## How ATS APIs work

Most companies use hosted Applicant Tracking Systems. These expose
public JSON endpoints — no auth needed, no scraping required:

| ATS        | API Pattern                                          | Used By                    |
|------------|------------------------------------------------------|----------------------------|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{company}/jobs`  | Stripe, Airbnb, Coinbase   |
| Lever      | `api.lever.co/v0/postings/{company}`                 | Netflix, Notion, Figma     |
| Ashby      | `api.ashbyhq.com/posting-api/job-board/{company}`    | Vercel, Linear, Replit     |

For companies without a known ATS API, we fall back to HTML scraping.

## Setup

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit with your companies/roles
python monitor.py                     # run once
# or: set up as a cron job (see Deployment section below)
```

## Deployment Options

### Option 1: Cron (simplest — any Linux/Mac)
```bash
# Run every 30 minutes
*/30 * * * * cd /path/to/job-notifier && python monitor.py >> monitor.log 2>&1
```

### Option 2: GitHub Actions (free, no server needed)
See `.github/workflows/monitor.yml` — runs on a schedule, stores
state as an artifact between runs.

### Option 3: Google Cloud Function + Cloud Scheduler
Good fit if you're already in GCP (which you are with BigQuery).
Deploy monitor.py as a Cloud Function, trigger via Cloud Scheduler.

### Option 4: Always-on (PM2 / systemd)
Run `monitor.py --daemon` which loops internally with sleep intervals.
