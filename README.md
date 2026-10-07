# Sparx Growth Engine

An AI-powered prospect research and outreach pipeline for Sparx Labs' "AI for Entrepreneurs" course. The system automatically discovers potential customers, researches them across the web, scores their fit, drafts personalized outreach, and syncs qualified leads to Attio CRM and Apollo email sequences — with a human-in-the-loop approval step before any outreach is sent.

> **Also in this repo:** [`contact-sync/`](contact-sync/README.md) — the self-hosted n8n setup that syncs contacts between Attio, Kit (ConvertKit) and Substack. It is independent of the app below.

---

## What It Does

1. **Discovers** leads by searching LinkedIn via Exa and enriching them with Apollo
2. **Researches** each prospect using a multi-step LangGraph pipeline powered by Claude and Exa
3. **Scores** each prospect on course fit, AI opportunity, and outreach priority
4. **Drafts** personalized LinkedIn DMs for prospects without email addresses
5. **Syncs** qualified leads to Attio CRM with AI-generated research notes
6. **Presents** all prospects in a review dashboard where you approve or reject outreach
7. **Enrolls** approved prospects in an Apollo email sequence (or flags them for LinkedIn DM)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Frontend (Next.js 16)               │
│  Dashboard · Prospects Table · Profile Detail · Analytics│
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP (axios)
┌───────────────────────▼─────────────────────────────────┐
│                    Backend (FastAPI)                      │
│                                                          │
│  ┌─────────────────────┐   ┌──────────────────────────┐ │
│  │  Prospecting Agent  │   │    Research Agent         │ │
│  │  (Exa + Apollo)     │──▶│    (LangGraph pipeline)  │ │
│  └─────────────────────┘   └──────────────────────────┘ │
│                                        │                 │
│  ┌─────────────────────────────────────▼──────────────┐ │
│  │             SQLite (primary store)                   │ │
│  │  Durable prospect profiles · read/written by all     │ │
│  │  endpoints and agents (backend/data/sparx.db)         │ │
│  └─────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────┐ │
│  │             Redis (optional, best-effort)             │ │
│  │  Research cache · pipeline logs · URL dedup cache ·   │ │
│  │  single-profile fallback if SQLite lookup misses      │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌──────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │  Attio CRM   │  │  Apollo.io  │  │  Exa Search    │  │
│  │ (bidirectional│  │  (sequence) │  │  (web search)  │  │
│  │  sync + hook) │  │             │  │                │  │
│  └──────────────┘  └─────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 App Router, TanStack Query, Tailwind CSS v4 (Satoshi + Rubik, Sparx brand palette) |
| Backend | FastAPI, LangGraph, SQLite (primary store), Redis (optional cache) |
| AI | Anthropic Claude (Sonnet 4.6 + Haiku 4.5) |
| Discovery | Exa neural search |
| Enrichment | Apollo.io (email + sequence enrollment) |
| CRM | Attio |

---

## How AI Is Used

Claude powers three distinct steps inside the research pipeline:

| Step | Model | Purpose |
|---|---|---|
| **Analysis** | claude-sonnet-4-6 | Synthesizes raw Exa search results into a structured prospect profile: name, role, pain points, recommended course modules, and personalized outreach angle |
| **Verification** | claude-sonnet-4-6 | Acts as a QA layer — scores the generated profile for identity match, internal consistency, and business orientation before it proceeds to scoring |
| **Fit Scoring** | claude-sonnet-4-6 | Evaluates the verified profile against the course ICP, assigns 1–5 scores for course fit / AI opportunity / outreach priority, and recommends an action |
| **DM Drafting** | claude-haiku-4-5 | Writes a short, personalized LinkedIn DM for prospects who have no discoverable email address |

Claude is given the full course context (target audience, curriculum, pricing, testimonials, disqualifiers) in every prompt so its output is grounded in real sales criteria rather than generic analysis.

---

## Agents

### Prospecting Agent (`backend/agents/prospecting_agent.py`)

Discovers new leads using a two-step pipeline:

1. **Exa discovery** — runs a set of predefined keyword searches targeting LinkedIn profiles (e.g. `"online coach founder site:linkedin.com"`) across nine ICP audience segments
2. **Apollo enrichment** — calls `people/match` for each discovered profile to get structured data (full name, verified LinkedIn URL, company, employee count)
3. **Quality filtering** — skips companies with >50 employees (too large for the course ICP), deduplicates URLs against Redis (7-day TTL) to avoid re-researching the same person, and filters out malformed names

Returns a `ProspectBatch` of up to 25 leads, which the pipeline orchestrator then feeds into the Research Agent.

---

### Research Agent (`backend/agents/research_agent.py`)

A LangGraph state machine that transforms a prospect URL or name into a fully researched, scored profile. Runs as a directed graph with conditional routing between seven nodes:

```
platform_detection
        │
        ▼
contact_enrichment
        │
        ▼
    research
        │
        ▼
    analysis  ──(Claude)──▶  structured profile
        │
        ▼
 verification  ──(Claude)──▶  confidence: high/medium/low
        │
        ▼
  fit_scoring  ──(Claude)──▶  scores + recommended action
        │
        ▼
 course_angle  ──(Claude)──▶  LinkedIn DM draft (no-email only)
```

#### Node Details

**`platform_detection`**
Checks whether the input URL is a "walled garden" (LinkedIn, Instagram, Facebook, TikTok). If so, uses Exa to find the prospect's public website or personal domain instead, since walled gardens block direct scraping.

**`contact_enrichment`**
Discovers the prospect's contact details without any manual input:
- Finds their personal/business website via Exa
- Fetches raw HTML and parses `href` attributes to find social links (catches icon-only links that text-regex misses)
- Falls back to text-regex parsing for emails and social handles
- Runs Instagram, Twitter, and Facebook fallback Exa searches in parallel
- Calls Apollo `people/match` as a last resort for email lookup

**`research`**
Runs up to three Exa searches per prospect: by LinkedIn URL, by name + company (people category), and by company name (company category). Aggregates all results into a raw text corpus.

**`analysis`** *(Claude Sonnet)*
Sends the raw corpus to Claude with a detailed system prompt containing the full course context. Claude returns a structured JSON profile with pain points tied to specific course weeks, recommended modules chosen from a fixed list, a personalized pitch angle, and outreach personalization hooks.

**`verification`** *(Claude Sonnet)*
Sends the generated profile back to Claude for QA. Claude checks identity match (does the profile name match the input?), internal consistency (do role/company/industry cohere?), and profession fit (is this a business-oriented person, not an artist or athlete?). Profiles with `low` confidence are flagged for human review and do not proceed to scoring.

**`fit_scoring`** *(Claude Sonnet)*
Scores the verified profile on three dimensions (1–5 each):
- **Course fit** — how well the prospect's background matches the non-technical entrepreneur ICP
- **AI opportunity** — how much they stand to gain from AI upskilling right now
- **Outreach priority** — how likely they are to respond positively

Disqualified prospects (high AI maturity, pre-revenue, no real operations) are capped at a max score of 2 on every dimension. Profiles scoring ≥3.5 overall are marked `prioritize`, 2.5–3.4 get `nurture`, and below 2.5 get `deprioritize`.

**`course_angle`** *(Claude Haiku)*
For prospects without a discoverable email, generates a short (3–4 sentence) LinkedIn DM draft that opens with a genuine observation about the prospect's work, ties to their top pain point, and ends with a soft question. Only runs for prospects who scored high enough to warrant outreach.

---

## Project Structure

```
sparx-growth-engine/
├── backend/
│   ├── agents/
│   │   ├── research_agent.py      # LangGraph pipeline (7 nodes)
│   │   └── prospecting_agent.py   # Exa + Apollo discovery + pipeline orchestrator
│   ├── integrations/
│   │   ├── attio.py               # Attio CRM sync (people, companies, notes, lists, outreach status R/W)
│   │   └── apollo.py              # Apollo contact creation + sequence enrollment
│   ├── models/
│   │   ├── prospect.py            # ProspectProfile Pydantic model
│   │   ├── prospect_batch.py      # ProspectBatch + ProspectLead
│   │   ├── pipeline_result.py     # PipelineRunResult
│   │   ├── attio_result.py        # AttioSyncResult
│   │   └── apollo_result.py       # ApolloEnrollResult
│   ├── data/                      # SQLite DB file lives here (gitignored)
│   ├── db.py                      # SQLite persistence — primary prospect store
│   ├── main.py                    # FastAPI app + all endpoints
│   └── requirements.txt
└── frontend/
    └── src/
        ├── app/
        │   ├── page.tsx            # Dashboard (stats, pipeline trigger, logs)
        │   ├── prospects/
        │   │   ├── page.tsx        # Prospects table (approve/reject)
        │   │   └── [id]/page.tsx   # Prospect detail view
        │   ├── analytics/page.tsx  # Charts (fit score distribution, etc.)
        │   └── sequences/page.tsx  # Apollo sequence stats + LinkedIn DM queue
        ├── components/
        │   ├── Sidebar.tsx
        │   ├── Logo.tsx            # Sparx wordmark + gradient star mark
        │   └── Providers.tsx       # TanStack Query provider
        └── lib/
            └── api.ts              # Typed API client (axios)
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check (includes Redis status) |
| `POST` | `/api/research-prospect` | Research a single prospect by name/URL/company |
| `POST` | `/api/prospect/run` | Run prospecting agent (Exa + Apollo discovery) |
| `POST` | `/api/pipeline/run` | Run full end-to-end pipeline |
| `GET` | `/api/prospects` | All researched profiles from the local SQLite store |
| `GET` | `/api/prospects/{key}` | Single prospect profile by key (SQLite, falls back to Redis) |
| `PATCH` | `/api/prospects/approve?key={key}` | Approve → enroll in Apollo or flag for LinkedIn DM |
| `PATCH` | `/api/prospects/reject?key={key}` | Reject a prospect |
| `GET` | `/api/pipeline/logs` | Last 100 pipeline log lines |
| `GET` | `/api/apollo/sequence-stats` | Apollo sequence metrics |
| `GET` | `/api/apollo/email-accounts` | Apollo email accounts (debug) |
| `GET` | `/api/attio/people-attributes` | Attio people object attributes (debug) |
| `GET` | `/api/attio/test` | Attio API connectivity test (debug) |
| `POST` | `/api/webhooks/attio` | Attio webhook — triggers Apollo enrollment on status change |

---

## Storage

**SQLite** (`backend/data/sparx.db`, via `backend/db.py`) is the primary, durable store for prospect profiles. Every profile produced by the research agent is upserted here, keyed by `prospect_key` (the source URL, or `{name}:{company}` if no URL). `GET /api/prospects`, `GET /api/prospects/{key}`, and the approve/reject endpoints all read/write through it. No setup required — it's the Python stdlib `sqlite3` module, WAL mode, no external dependency.

**Redis** is optional and used only for caching and ephemeral state — the app runs fine without it (falls back gracefully with a warning at startup):

| Key | TTL | Contents |
|---|---|---|
| `sparx:profile:{key}` | 7 days | Individual `ProspectProfile` JSON — fallback for `GET /api/prospects/{key}` if not yet in SQLite |
| `sparx:pipeline_logs` | — | List of pipeline log lines (capped at 500) |
| `prospect:{name}:{company}` | 24 hours | Research cache (avoids re-running the graph) |
| `prospected_url:{url}` | 3 days | URL dedup cache (avoids re-discovering the same person) |

---

## Setup

### Prerequisites

- Python 3.9+
- Node.js 20+ (Next.js 16 requirement)
- Redis (optional — caching/dedup only; prospect data persists to SQLite regardless)
- API keys for: Anthropic, Exa, Apollo, Attio

### Environment Variables

Copy `.env.example` to `.env` in the project root and fill in your values:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...
EXA_API_KEY=...

# Optional but needed for full functionality
APOLLO_API_KEY=...
ATTIO_API_KEY=...

# Apollo sequence config (defaults to hardcoded IDs if omitted)
APOLLO_SEQUENCE_ID=...
APOLLO_EMAIL_ACCOUNT_ID=...

# Attio list/attribute config (defaults to hardcoded IDs if omitted)
ATTIO_PROSPECT_LIST_ID=...
ATTIO_OUTREACH_STATUS_ATTR_ID=...

# Attio Outreach Status select-attribute option IDs — required to write the
# status back to Attio on approve/reject (defaults to hardcoded IDs if omitted;
# re-fetch via GET /v2/lists/{list_id}/attributes/{attr_id}/options if your
# Attio workspace's option IDs differ)
ATTIO_OUTREACH_OPTION_PENDING=...
ATTIO_OUTREACH_OPTION_APPROVED=...
ATTIO_OUTREACH_OPTION_REJECTED=...

# Redis (defaults to localhost)
REDIS_URL=redis://localhost:6379

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev   # runs on http://localhost:3000
```

---

## Human-in-the-Loop Flow

The pipeline never sends outreach automatically. Every prospect goes through a manual review step, and can be approved/rejected from either the dashboard or directly in Attio — the two stay in sync in both directions:

1. Pipeline runs and persists all researched profiles to SQLite (`backend/data/sparx.db`)
2. Reviewer opens `/prospects` and sees all prospects sorted by fit score
3. Prospects without email are highlighted — their LinkedIn DM draft is pre-written and ready to copy
4. Reviewer clicks **Approve** or **Reject** on each prospect (`PATCH /api/prospects/approve|reject`)
5. On approval:
   - If the prospect has an email → creates a contact in Apollo and enrolls them in the configured sequence
   - If no email → marks status as "LinkedIn DM Needed" for manual follow-up
6. Both approve and reject write the Outreach Status back to the prospect's Attio list entry (when it's been synced there), so the CRM always reflects the local decision
7. Conversely, updating a prospect's Outreach Status to "Approved" directly in Attio fires a webhook (`POST /api/webhooks/attio`) that enrolls them in Apollo and updates the local SQLite record — the local status reflects the Attio approval even if the downstream Apollo enrollment fails, so the two systems never silently diverge
8. Apollo enrollment refuses to run (and returns a clear error) if the configured sequence is archived or inactive, rather than silently creating contacts that will never receive outreach
