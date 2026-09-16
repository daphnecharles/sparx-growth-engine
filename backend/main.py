import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
import redis as redis_lib
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

import db as _db
from agents.prospecting_agent import run_full_pipeline, run_prospecting_agent
from agents.research_agent import run_research_agent
from integrations.apollo import enroll_in_apollo_sequence
from integrations.attio import (
    get_list_entry_outreach_status,
    get_prospect_from_attio,
    push_prospect_to_attio,
    update_list_entry_outreach_status,
)
from models.pipeline_result import PipelineRunResult
from models.prospect import ProspectProfile, VerificationError
from models.prospect_batch import ProspectBatch

logger = logging.getLogger(__name__)

app = FastAPI(title="Sparx Growth Engine")

# ── Redis client ─────────────────────────────────────────────────────────────────

_redis: Optional[redis_lib.Redis] = None
try:
    _redis = redis_lib.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379"),
        decode_responses=True,
        socket_connect_timeout=2,
    )
    _redis.ping()
except Exception as _e:
    logger.warning("Redis unavailable — caching and prospect storage disabled. (%s)", _e)
    _redis = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


class ResearchProspectResponse(ProspectProfile):
    attio_synced: bool = False


class ResearchRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "prospect_name": "Jane Smith",
            "prospect_url": "https://janesmith.com",
            "company": "Acme Coaching",
            "force_refresh": False,
        }
    })

    prospect_name: Optional[str] = None
    prospect_url: Optional[str] = None
    company: Optional[str] = None
    force_refresh: bool = False


@app.on_event("startup")
def validate_env() -> None:
    """Fail fast if required environment variables are missing."""
    required = ["ANTHROPIC_API_KEY", "EXA_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    _db.init_db()


@app.get("/health")
def health() -> Dict[str, Any]:
    redis_ok = False
    if _redis is not None:
        try:
            _redis.ping()
            redis_ok = True
        except Exception:
            pass
    return {"status": "ok", "redis": "ok" if redis_ok else "unavailable"}


@app.post("/api/research-prospect", response_model=ResearchProspectResponse)
async def research_prospect(request: ResearchRequest) -> ResearchProspectResponse:
    if not any([request.prospect_name, request.prospect_url, request.company]):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of: prospect_name, prospect_url, company",
        )

    try:
        profile = await asyncio.to_thread(
            run_research_agent,
            name=request.prospect_name,
            company=request.company,
            linkedin_url=request.prospect_url,
            force_refresh=request.force_refresh,
        )
    except ValueError as e:
        detail = str(e)
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict):
                if "flagged_for_review" in parsed:
                    raise HTTPException(status_code=422, detail=parsed)
                if "platform_warning" in parsed:
                    raise HTTPException(status_code=400, detail=parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        raise HTTPException(status_code=500, detail=detail)

    attio_synced = False
    if profile.recommended_action in ("prioritize", "nurture"):
        attio_result = await push_prospect_to_attio(profile)
        attio_synced = attio_result.success
        if attio_synced and profile.prospect_key:
            _db.update_prospect(profile.prospect_key, {
                "attio_person_record_id": attio_result.person_record_id,
                "attio_list_entry_id": attio_result.list_entry_id,
            })

    return ResearchProspectResponse(**profile.dict(), attio_synced=attio_synced)


@app.post("/api/prospect/run", response_model=ProspectBatch)
def prospect_run() -> ProspectBatch:
    try:
        return run_prospecting_agent()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PipelineRunRequest(BaseModel):
    force_refresh: bool = False
    max_prospects: int = 10


@app.post("/api/pipeline/run", response_model=PipelineRunResult)
async def pipeline_run(request: PipelineRunRequest) -> PipelineRunResult:
    try:
        return await run_full_pipeline(
            force_refresh=request.force_refresh,
            max_prospects=max(1, min(request.max_prospects, 50)),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/attio/people-attributes")
async def attio_people_attributes() -> Dict[str, Any]:
    """Debug endpoint — returns all attribute slugs and types for the Attio people object."""
    api_key = os.environ.get("ATTIO_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ATTIO_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        resp = await client.get("https://api.attio.com/v2/objects/people/attributes")

    return resp.json()


@app.get("/api/attio/test")
async def attio_test() -> Dict[str, Any]:
    """Debug endpoint — returns raw Attio API responses for people, companies, and lists."""
    api_key = os.environ.get("ATTIO_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ATTIO_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base = "https://api.attio.com/v2"

    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        people_resp, companies_resp, lists_resp = await asyncio.gather(
            client.get(f"{base}/objects/people"),
            client.get(f"{base}/objects/companies"),
            client.get(f"{base}/lists"),
        )

    return {
        "people_object": people_resp.json(),
        "companies_object": companies_resp.json(),
        "lists": lists_resp.json(),
    }


_OUTREACH_STATUS_ATTR_ID = os.environ.get(
    "ATTIO_OUTREACH_STATUS_ATTR_ID", "480f90f7-7094-49bf-ae2a-d9ef39d8801c"
)


@app.post("/api/webhooks/attio")
async def attio_webhook(payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Receives Attio webhook events.
    When a prospect's Outreach Status changes to "Approved",
    fetches the record from Attio and enrolls them in the Apollo sequence.
    Always returns HTTP 200 to prevent Attio from retrying.
    """
    try:
        events = payload.get("events", [])
        if not events:
            logger.debug("Webhook: ignoring — no events in payload")
            return {"status": "ignored"}

        event = events[0]
        event_type = event.get("event_type")
        logger.debug("Webhook: event_type=%s", event_type)

        # Only handle list-entry.updated events
        if event_type != "list-entry.updated":
            logger.debug("Webhook: ignoring — event_type is not list-entry.updated")
            return {"status": "ignored"}

        # Only react to Outreach Status attribute changes
        event_id = event.get("id", {})
        attribute_id = event_id.get("attribute_id")
        entry_id = event_id.get("entry_id")
        logger.debug("Webhook: attribute_id=%s", attribute_id)

        if attribute_id != _OUTREACH_STATUS_ATTR_ID:
            logger.debug("Webhook: ignoring — attribute_id does not match Outreach Status")
            return {"status": "ignored"}

        person_record_id = event.get("parent_record_id")
        if not person_record_id:
            logger.warning("Webhook: ignoring — no parent_record_id in event")
            return {"status": "ignored"}

        if not entry_id:
            logger.warning("Webhook: ignoring — no entry_id in event")
            return {"status": "ignored"}

        logger.info("Webhook: Outreach Status updated — person_record_id=%s entry_id=%s", person_record_id, entry_id)

        # Fetch the current status value from the list entry by entry_id
        current_status = await get_list_entry_outreach_status(entry_id)
        logger.debug("Webhook: outreach_status=%r", current_status)

        if current_status != "Approved":
            logger.info("Webhook: ignoring — outreach_status is %r, not 'Approved'", current_status)
            return {"status": "ignored"}

        # Fetch full prospect data from Attio
        prospect_data = await get_prospect_from_attio(person_record_id)
        if not prospect_data:
            logger.error("Webhook: could not fetch prospect %s from Attio", person_record_id)
            return {"status": "error", "message": "could not fetch prospect from Attio"}

        name = prospect_data.get("name") or person_record_id
        email = prospect_data.get("email")
        company = prospect_data.get("company")

        local_key = _db.find_prospect_key(email=email, name=name, company=company)

        if not email:
            logger.info("Webhook: no email for %r — flagged for LinkedIn DM", name)
            if local_key:
                _db.update_prospect(local_key, {"outreach_status": "LinkedIn DM Needed"})
            return {"status": "linkedin_dm_needed", "name": name}

        logger.info("Webhook: approved %r (%s) — enrolling in Apollo sequence", name, email)

        result = await enroll_in_apollo_sequence(prospect_data)

        # Attio is the source of truth for the approval itself — record it locally
        # even if the downstream Apollo enrollment fails, so the two systems don't
        # silently diverge (mirrors /api/prospects/approve's behavior).
        if local_key:
            updates: Dict[str, Any] = {"outreach_status": "Approved"}
            if result.success and result.contact_id:
                updates["apollo_id"] = result.contact_id
            _db.update_prospect(local_key, updates)
        else:
            logger.warning("Webhook: no matching local record found for %r — outreach_status not synced locally", name)

        if result.success:
            return {"status": "enrolled", "name": name}
        else:
            logger.error("Webhook: Apollo enrollment failed for %r: %s", name, result.error)
            return {"status": "error", "message": result.error or "enrollment failed"}

    except Exception as e:
        logger.error("Webhook: unexpected error: %s", e)
        return {"status": "error", "message": str(e)}


@app.get("/api/apollo/email-accounts")
async def apollo_email_accounts() -> Dict[str, Any]:
    """Debug endpoint — returns all Apollo email accounts."""
    api_key = os.environ.get("APOLLO_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="APOLLO_API_KEY not set")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.apollo.io/api/v1/email_accounts",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        )

    return resp.json()


@app.get("/api/apollo/sequence-stats")
async def apollo_sequence_stats() -> Dict[str, Any]:
    """Returns Apollo sequence metrics for the configured sequence."""
    api_key = os.environ.get("APOLLO_API_KEY", "")
    sequence_id = os.environ.get("APOLLO_SEQUENCE_ID", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="APOLLO_API_KEY not set")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://api.apollo.io/api/v1/emailer_campaigns/{sequence_id}",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        )

    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.get("/api/prospects/{key:path}", response_model=ProspectProfile)
async def get_prospect(key: str) -> ProspectProfile:
    """Return a single prospect profile by its prospect_key."""
    data = _db.get_prospect(key)
    if data is None:
        # Redis fallback
        if _redis is not None:
            raw = _redis.get(f"sparx:profile:{key}")
            if raw:
                try:
                    return ProspectProfile(**json.loads(raw))
                except Exception as e:
                    raise HTTPException(status_code=500, detail=str(e))
        raise HTTPException(status_code=404, detail="Prospect not found")
    try:
        return ProspectProfile(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prospects", response_model=List[ProspectProfile])
async def get_prospects() -> List[ProspectProfile]:
    """Returns all researched prospect profiles from the database."""
    rows = _db.get_all_prospects()
    profiles: List[ProspectProfile] = []
    for row in rows:
        try:
            profiles.append(ProspectProfile(**row))
        except Exception:
            pass
    return sorted(profiles, key=lambda p: p.overall_fit_score or 0, reverse=True)


@app.patch("/api/prospects/approve")
async def approve_prospect(key: str) -> Dict[str, Any]:
    """Approve a prospect: enroll in Apollo (if email) or flag for LinkedIn DM."""
    profile_data = _db.get_prospect(key)
    if profile_data is None:
        raise HTTPException(status_code=404, detail="Prospect not found")

    updates: Dict[str, Any] = {}
    if profile_data.get("email"):
        try:
            result = await enroll_in_apollo_sequence({
                "name": profile_data.get("name"),
                "email": profile_data.get("email"),
                "company": profile_data.get("company"),
                "title": profile_data.get("role"),
                "linkedin_url": profile_data.get("linkedin_url"),
            })
            updates["outreach_status"] = "Approved"
            if result.success and result.contact_id:
                updates["apollo_id"] = result.contact_id
            if not result.success:
                logger.warning("Apollo enrollment failed for %s: %s", profile_data.get("name"), result.error)
        except Exception as e:
            logger.error("Unexpected error during Apollo enrollment for %s: %s", profile_data.get("name"), e)
            updates["outreach_status"] = "Approved"
    else:
        updates["outreach_status"] = "LinkedIn DM Needed"

    _db.update_prospect(key, updates)

    entry_id = profile_data.get("attio_list_entry_id")
    if entry_id and updates["outreach_status"] == "Approved":
        ok = await update_list_entry_outreach_status(entry_id, "Approved")
        if not ok:
            logger.warning("Failed to sync outreach_status=Approved to Attio for %s", profile_data.get("name"))

    return {"status": "ok", "outreach_status": updates["outreach_status"]}


@app.patch("/api/prospects/reject")
async def reject_prospect(key: str) -> Dict[str, Any]:
    """Reject a prospect."""
    profile_data = _db.get_prospect(key)
    if profile_data is None:
        raise HTTPException(status_code=404, detail="Prospect not found")
    _db.update_prospect(key, {"outreach_status": "Rejected"})

    entry_id = profile_data.get("attio_list_entry_id")
    if entry_id:
        ok = await update_list_entry_outreach_status(entry_id, "Rejected")
        if not ok:
            logger.warning("Failed to sync outreach_status=Rejected to Attio for %s", profile_data.get("name"))

    return {"status": "ok", "outreach_status": "Rejected"}


@app.get("/api/pipeline/logs")
async def pipeline_logs() -> List[str]:
    """Returns the last 100 pipeline log lines stored in Redis."""
    if _redis is None:
        return []
    try:
        return _redis.lrange("sparx:pipeline_logs", -100, -1)
    except Exception:
        return []
