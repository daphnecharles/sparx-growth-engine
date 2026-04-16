import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
import redis as redis_lib
from dotenv import load_dotenv
from exa_py import Exa

from integrations.attio import push_prospect_to_attio
from models.pipeline_result import PipelineRunResult
from models.prospect import ProspectProfile
from models.prospect_batch import ProspectBatch, ProspectLead

load_dotenv()

logger = logging.getLogger(__name__)

exa = Exa(api_key=os.environ["EXA_API_KEY"])

APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")
APOLLO_MATCH_ENDPOINT = "https://api.apollo.io/api/v1/people/match"

# ── Redis client (optional — prospecting continues without dedup if unavailable) ─

_redis: Optional[redis_lib.Redis] = None
try:
    _redis = redis_lib.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379"),
        decode_responses=True,
        socket_connect_timeout=2,
    )
    _redis.ping()
except Exception as _e:
    logger.warning("Redis unavailable — URL dedup disabled. (%s)", _e)
    _redis = None

_PROSPECTED_URL_TTL = 3 * 86400  # 3 days

# Limit concurrent research graph executions to avoid overwhelming Claude / Exa APIs
_research_semaphore = asyncio.Semaphore(2)


# ── ICP definition ─────────────────────────────────────────────────────────────

TARGET_AUDIENCES: List[str] = [
    "Service providers and consultants drowning in admin, onboarding, and client management",
    "E-commerce owners losing leads due to slow follow-ups and manual customer support",
    "Creative entrepreneurs struggling with consistent content creation",
    "Small business owners ready to scale without hiring a full team",
    "Freelancers (designers, copywriters, marketers) building their own client base for the first time",
    "Real estate agents drowning in lead follow-up and client communication",
    "Health and wellness practitioners (personal trainers, nutritionists, therapists, yoga instructors) who have gone independent",
    "Event planners and wedding professionals with highly manual client operations",
    "Local service businesses (salons, med spas, photography studios, cleaning services) with real revenue and zero tech sophistication",
]

# ── Exa discovery queries ───────────────────────────────────────────────────────

EXA_QUERIES: List[str] = [
    "online coach founder site:linkedin.com",
    "real estate agent founder site:linkedin.com",
    "personal trainer founder site:linkedin.com",
    "wedding photographer founder site:linkedin.com",
    "salon owner founder site:linkedin.com",
    "ecommerce founder owner site:linkedin.com",
    "freelance designer founder site:linkedin.com",
    "consultant solopreneur founder site:linkedin.com",
    "course creator founder site:linkedin.com",
]

MAX_RESULTS_PER_QUERY = 5
MAX_BATCH_SIZE = 25


# ── Step 1: Exa result parsing ──────────────────────────────────────────────────

def _parse_exa_title(title: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract (raw_name, raw_company) from a LinkedIn page title.
    Title format is typically "Name - Title | LinkedIn" or "Name | Title | LinkedIn".
    Returns (raw_name, raw_company) where raw_company may be None.
    """
    if not title:
        return None, None

    # Strip trailing "| LinkedIn" suffix before splitting
    for suffix in (" | LinkedIn", "| LinkedIn", " - LinkedIn", "- LinkedIn"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
            break

    # "Jane Smith - Business Coach" → raw_name="Jane Smith", raw_company="Business Coach"
    if " - " in title:
        parts = title.split(" - ", 1)
        return parts[0].strip() or None, parts[1].strip() or None

    # "Jane Smith | Business Coach" → raw_name="Jane Smith", raw_company=None
    if " | " in title:
        parts = title.split(" | ", 1)
        return parts[0].strip() or None, None

    return title.strip() or None, None


# ── Step 2: Apollo enrichment ───────────────────────────────────────────────────

def _call_apollo_match(
    name: str,
    org_name: Optional[str],
    linkedin_url: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Call Apollo people/match for a single prospect.
    On 429 waits 1 second and retries once.
    Returns the full JSON response dict, or None on any failure.
    """
    payload: Dict[str, Any] = {"name": name}
    if org_name:
        payload["organization_name"] = org_name
    if linkedin_url:
        payload["linkedin_url"] = linkedin_url

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": APOLLO_API_KEY,
    }

    try:
        response = httpx.post(
            APOLLO_MATCH_ENDPOINT, json=payload, headers=headers, timeout=30
        )
        if response.status_code == 429:
            logger.warning("Apollo rate limit — retrying in 1s. name=%r", name)
            time.sleep(1)
            response = httpx.post(
                APOLLO_MATCH_ENDPOINT, json=payload, headers=headers, timeout=30
            )
    except Exception as e:
        logger.error("Apollo match request failed: %s", e)
        return None

    if response.status_code == 401:
        logger.error("Invalid Apollo API key")
        return None
    if response.status_code == 429:
        logger.error("Apollo rate limit hit on retry — using Exa fallback. name=%r", name)
        return None
    if not response.is_success:
        logger.error(
            "Apollo match returned %d — using Exa fallback. name=%r body=%s",
            response.status_code, name, response.text[:200],
        )
        return None

    return response.json()


def _extract_apollo_fields(
    person: Dict[str, Any],
) -> Tuple[str, str, str, str, Optional[int]]:
    """
    Pull (name, linkedin_url, snippet, company, num_employees) from an Apollo person record.
    """
    name = (person.get("name") or "").strip()
    url = (person.get("linkedin_url") or "").strip()
    title = (person.get("title") or "").strip()
    city = (person.get("city") or "").strip()
    state = (person.get("state") or "").strip()

    org = person.get("organization") or {}
    company = (org.get("name") or person.get("organization_name") or "").strip()
    num_employees = org.get("num_employees") or org.get("estimated_num_employees")

    snippet_parts: List[str] = []
    if title:
        snippet_parts.append(title)
    if company:
        snippet_parts.append(f"at {company}")
    location = ", ".join(filter(None, [city, state]))
    if location:
        snippet_parts.append(f"| {location}")
    snippet = " ".join(snippet_parts)

    return name, url, snippet, company, num_employees


# ── Redis URL dedup ─────────────────────────────────────────────────────────────

def _redis_has_url(normalized_url: str) -> bool:
    if _redis is None:
        return False
    try:
        return bool(_redis.exists(f"prospected_url:{normalized_url}"))
    except Exception:
        return False


def _redis_mark_url(normalized_url: str) -> None:
    if _redis is None:
        return
    try:
        _redis.setex(f"prospected_url:{normalized_url}", _PROSPECTED_URL_TTL, "1")
    except Exception:
        pass


# ── Main entry point ────────────────────────────────────────────────────────────

def run_prospecting_agent() -> ProspectBatch:
    """
    Two-step prospecting pipeline:
      1. Exa keyword search discovers LinkedIn profiles.
      2. Apollo people/match enriches each profile with structured data.
    Falls back to raw Exa data when Apollo match fails.
    """
    seen_urls: Set[str] = set()
    leads: List[ProspectLead] = []
    total_skipped = 0

    for query in EXA_QUERIES:
        if len(leads) >= MAX_BATCH_SIZE:
            break

        # ── Step 1: Exa discovery ───────────────────────────────────────────
        try:
            exa_response = exa.search_and_contents(
                query=query,
                type="auto",
                num_results=MAX_RESULTS_PER_QUERY,
                highlights={"num_sentences": 2, "highlights_per_url": 1},
            )
        except Exception as e:
            logger.warning("Exa query failed — skipping. query=%r error=%s", query, e)
            continue

        for result in exa_response.results:
            if len(leads) >= MAX_BATCH_SIZE:
                break

            try:
                # Parse LinkedIn URL
                linkedin_url = getattr(result, "url", None) or ""
                if "linkedin.com/in" not in linkedin_url:
                    continue

                # Parse name + company from title
                title = getattr(result, "title", "") or ""
                raw_name, raw_company = _parse_exa_title(title)

                # Name quality filters
                if not raw_name or len(raw_name) <= 3:
                    continue
                if "linkedin" in raw_name.lower() or "page" in raw_name.lower():
                    continue

                # Exa snippet (used as fallback)
                raw_highlights = getattr(result, "highlights", None) or []
                exa_snippet = (
                    " ".join(raw_highlights).strip() if raw_highlights else title
                )

                # ── Step 2: Apollo enrichment ───────────────────────────────
                prospect_name = raw_name
                prospect_url = linkedin_url
                company = raw_company
                snippet = exa_snippet

                apollo_data = _call_apollo_match(raw_name, raw_company, linkedin_url)
                if apollo_data:
                    person = apollo_data.get("person")
                    if person:
                        ap_name, ap_url, ap_snippet, ap_company, num_employees = (
                            _extract_apollo_fields(person)
                        )
                        if ap_name:
                            prospect_name = ap_name
                        if ap_url:
                            prospect_url = ap_url
                        if ap_snippet:
                            snippet = ap_snippet
                        if ap_company:
                            company = ap_company

                        # Quality filter: skip large organisations
                        if num_employees is not None and num_employees > 50:
                            continue

                # ── Step 3: Quality filters ─────────────────────────────────
                if not prospect_name or not prospect_url:
                    continue

                # ── Step 4: Redis URL dedup ─────────────────────────────────
                norm_url = prospect_url.rstrip("/").lower()
                if norm_url in seen_urls:
                    total_skipped += 1
                    continue
                if _redis_has_url(norm_url):
                    total_skipped += 1
                    continue

                seen_urls.add(norm_url)
                leads.append(ProspectLead(
                    prospect_name=prospect_name,
                    prospect_url=prospect_url,
                    company=company or None,
                    context_snippet=snippet or None,
                ))
                _redis_mark_url(norm_url)

            except Exception as e:
                logger.warning(
                    "Failed to process result — skipping. url=%s error=%s",
                    getattr(result, "url", "unknown"), e,
                )
                continue

    return ProspectBatch(
        prospects=leads,
        total_found=len(leads) + total_skipped,
        total_skipped_duplicates=total_skipped,
        queries_run=EXA_QUERIES,
        run_timestamp=datetime.utcnow(),
    )


# ── Pipeline log helper ─────────────────────────────────────────────────────────

def _pipeline_log(msg: str) -> None:
    """Log a pipeline message and append it to Redis for the frontend logs panel."""
    logger.info(msg)
    if _redis:
        try:
            _redis.rpush("sparx:pipeline_logs", msg)
            _redis.ltrim("sparx:pipeline_logs", -500, -1)
        except Exception as e:
            logger.warning("Failed to write pipeline log to Redis: %s", e)


# ── Pipeline orchestrator ───────────────────────────────────────────────────────

async def run_full_pipeline(force_refresh: bool = False) -> PipelineRunResult:
    """
    End-to-end pipeline:
      1. ProspectingAgent discovers leads via Exa + Apollo enrichment.
      2. Each lead is fed into the research graph (run_research_agent).
      3. Results are aggregated into a PipelineRunResult.

    A semaphore caps concurrent research graph executions at 3 to avoid
    overwhelming Claude and Exa APIs. A 2-second delay is inserted between
    each prospect to further reduce API pressure.
    """
    _pipeline_log("PIPELINE STARTED")

    # Lazy import avoids a circular dependency at module load time
    from agents.research_agent import run_research_agent

    start_time = time.time()

    batch = run_prospecting_agent()
    prospects = batch.prospects
    total = len(prospects)

    profiles: List[ProspectProfile] = []
    total_prioritized = 0
    total_deprioritized = 0
    total_flagged = 0
    total_attio_synced = 0

    for idx, prospect in enumerate(prospects, 1):
        name = prospect.prospect_name or "Unknown"
        url = prospect.prospect_url
        company = prospect.company

        _pipeline_log(f"[{idx}/{total}] Processing: {prospect.prospect_name}")
        try:
            async with _research_semaphore:
                profile = await asyncio.to_thread(
                    run_research_agent,
                    name=prospect.prospect_name,
                    linkedin_url=url,
                    company=company,
                    force_refresh=force_refresh,
                )

        except ValueError as e:
            detail = str(e)
            is_flagged = False
            is_platform_warning = False
            try:
                parsed = json.loads(detail)
                if isinstance(parsed, dict):
                    if parsed.get("flagged_for_review"):
                        is_flagged = True
                    elif parsed.get("platform_warning"):
                        is_platform_warning = True
            except (json.JSONDecodeError, TypeError):
                pass

            if is_flagged:
                total_flagged += 1
                _pipeline_log(f"[{idx}/{total}] {name} — flagged for review")
            elif is_platform_warning:
                _pipeline_log(f"[{idx}/{total}] {name} — skipped (no public web presence)")
            else:
                _pipeline_log(f"[{idx}/{total}] {name} — error: {detail[:80]}")

            if idx < total:
                await asyncio.sleep(2)
            continue

        except Exception as e:
            _pipeline_log(f"[{idx}/{total}] {name} — unexpected error: {e}")
            if idx < total:
                await asyncio.sleep(2)
            continue

        profiles.append(profile)

        score = profile.overall_fit_score
        if score is not None and score >= 4.0:
            total_prioritized += 1
            _pipeline_log(f"[{idx}/{total}] {name} — prioritize ({score:.1f}) ✓")
        else:
            total_deprioritized += 1
            score_label = f"{score:.1f}" if score is not None else "N/A"
            _pipeline_log(f"[{idx}/{total}] {name} — deprioritize ({score_label}) ✗")

        # ── Attio sync for prioritize / nurture prospects ───────────────────
        if profile.recommended_action in ("prioritize", "nurture"):
            attio_result = await push_prospect_to_attio(profile)
            if attio_result.success:
                total_attio_synced += 1
                _pipeline_log(f"[Attio] Synced: {name} → {profile.recommended_action}")
            else:
                _pipeline_log(f"[Attio] Sync failed for {name}: {attio_result.error}")

        if idx < total:
            await asyncio.sleep(2)

    return PipelineRunResult(
        total_prospects_found=batch.total_found,
        total_researched=len(profiles),
        total_prioritized=total_prioritized,
        total_deprioritized=total_deprioritized,
        total_flagged_for_review=total_flagged,
        total_skipped_duplicates=batch.total_skipped_duplicates,
        profiles=profiles,
        attio_synced=total_attio_synced,
        run_timestamp=datetime.utcnow(),
        run_duration_seconds=round(time.time() - start_time, 2),
    )
