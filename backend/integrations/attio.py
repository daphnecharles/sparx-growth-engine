import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from models.attio_result import AttioSyncResult
from models.prospect import ProspectProfile

load_dotenv()

logger = logging.getLogger(__name__)

ATTIO_API_KEY = os.environ.get("ATTIO_API_KEY", "")
ATTIO_BASE_URL = "https://api.attio.com/v2"
_PROSPECT_LIST_ID = os.environ.get("ATTIO_PROSPECT_LIST_ID", "021c4027-b4de-40c9-8a07-ac1a2d7462b8")


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _attio_headers() -> dict:
    return {
        "Authorization": f"Bearer {ATTIO_API_KEY}",
        "Content-Type": "application/json",
    }


def _extract_domain(url: Optional[str]) -> Optional[str]:
    """Return bare domain from a URL, stripping scheme and www."""
    if not url:
        return None
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.netloc or parsed.path
        if domain.startswith("www."):
            domain = domain[4:]
        return domain or None
    except Exception:
        return None


def _parse_name(full_name: Optional[str]) -> tuple[str, str, str]:
    """
    Returns (first_name, last_name, full_name) for Attio's name attribute.
    Strips parentheticals first. Attio requires all three fields.
    Falls back to the original string if stripping yields empty.
    """
    original = (full_name or "").strip()
    clean = original.split("(")[0].strip() or original
    parts = clean.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else "."
    return first_name, last_name, clean


def _format_note(profile: ProspectProfile) -> str:
    pain_points = "\n".join(profile.top_pain_points or [])
    modules = ", ".join(profile.course_modules or []) if profile.course_modules else "N/A"
    return (
        f"FIT SCORE: {profile.overall_fit_score}/5\n"
        f"RECOMMENDED ACTION: {profile.recommended_action}\n"
        f"AI MATURITY: {profile.ai_maturity}\n"
        f"BUDGET SIGNAL: {profile.budget_signal_found}\n"
        f"\n"
        f"TOP PAIN POINTS:\n"
        f"{pain_points}\n"
        f"\n"
        f"RECOMMENDED COURSE ANGLE:\n"
        f"{profile.recommended_course_angle or 'N/A'}\n"
        f"\n"
        f"OUTREACH PERSONALIZATION:\n"
        f"{profile.outreach_personalization_notes}\n"
        f"\n"
        f"COURSE MODULES:\n"
        f"{modules}\n"
        f"\n"
        f"Researched by Sparx Labs AI Growth Engine"
    )


# ── API calls ────────────────────────────────────────────────────────────────────

async def _create_company(
    client: httpx.AsyncClient, profile: ProspectProfile
) -> Optional[str]:
    """Create a company record. Returns record_id or None on failure."""
    values: dict = {
        "name": [{"value": profile.company}],
    }
    domain = _extract_domain(profile.website_url)
    if domain:
        values["domains"] = [{"domain": domain}]

    try:
        resp = await client.post(
            f"{ATTIO_BASE_URL}/objects/companies/records",
            json={"data": {"values": values}},
        )
        if resp.is_success:
            record_id = resp.json()["data"]["id"]["record_id"]
            logger.info("Attio: created company %r", profile.company)
            return record_id
        body = resp.json()
        # Domain uniqueness conflict — company already exists; reuse the existing record
        if body.get("code") == "uniqueness_conflict":
            match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", body.get("message", ""))
            if match:
                record_id = match.group(0)
                logger.info("Attio: company already exists, reusing %r", profile.company)
                return record_id
        logger.error("Attio: error creating company %r: %d %s", profile.company, resp.status_code, resp.text)
        return None
    except Exception as e:
        logger.warning("Attio: company create failed for %r: %s", profile.company, e)
        return None


async def _create_person(
    client: httpx.AsyncClient,
    profile: ProspectProfile,
) -> Optional[str]:
    """Create a person record. Returns record_id or None on failure."""
    first_name, last_name, full_name = _parse_name(profile.name)

    values: dict = {
        "name": [{"first_name": first_name, "last_name": last_name, "full_name": full_name}],
    }

    if profile.email:
        values["email_addresses"] = [{"email_address": profile.email}]
    if profile.role:
        values["job_title"] = [{"value": profile.role}]
    if profile.linkedin_url:
        values["linkedin"] = [{"value": profile.linkedin_url}]

    try:
        resp = await client.post(
            f"{ATTIO_BASE_URL}/objects/people/records",
            json={"data": {"values": values}},
        )
        if resp.is_success:
            record_id = resp.json()["data"]["id"]["record_id"]
            logger.info("Attio: created person %r", profile.name)
            return record_id
        body = resp.json()
        # Email uniqueness conflict — person already exists; reuse the existing record
        # (mirrors the company dedup handling above)
        if body.get("code") == "uniqueness_conflict":
            match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", body.get("message", ""))
            if match:
                record_id = match.group(0)
                logger.info("Attio: person already exists, reusing %r", profile.name)
                return record_id
        logger.error("Attio: error creating person %r: %d %s", profile.name, resp.status_code, resp.text)
        return None
    except Exception as e:
        logger.warning("Attio: person create failed for %r: %s", profile.name, e)
        return None


async def _link_person_to_company(
    client: httpx.AsyncClient,
    person_record_id: str,
    company_record_id: str,
) -> None:
    """Link a person to a company via PATCH. Non-fatal."""
    try:
        resp = await client.patch(
            f"{ATTIO_BASE_URL}/objects/people/records/{person_record_id}",
            json={
                "data": {
                    "values": {
                        "company": [
                            {
                                "target_object": "companies",
                                "target_record_id": company_record_id,
                            }
                        ]
                    }
                }
            },
        )
        if not resp.is_success:
            logger.error("Attio: error linking person to company: %d %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("Attio: link person→company failed: %s", e)


async def _add_note(
    client: httpx.AsyncClient,
    person_record_id: str,
    profile: ProspectProfile,
) -> Optional[str]:
    """Add a Sparx note to the person record. Returns note_id or None on failure."""
    try:
        resp = await client.post(
            f"{ATTIO_BASE_URL}/notes",
            json={
                "data": {
                    "parent_object": "people",
                    "parent_record_id": person_record_id,
                    "title": f"Sparx Labs AI Research — {profile.name}",
                    "format": "plaintext",
                    "content": _format_note(profile),
                }
            },
        )
        if not resp.is_success:
            logger.error("Attio: error creating note for person %r: %d %s", person_record_id, resp.status_code, resp.text)
            return None
        return resp.json()["data"]["id"]["note_id"]
    except Exception as e:
        logger.warning("Attio: note creation failed for person %r: %s", person_record_id, e)
        return None


async def _add_to_list(
    client: httpx.AsyncClient,
    person_record_id: str,
    profile: ProspectProfile,
) -> Optional[str]:
    """Add the person to the prospect list. Returns entry_id or None. Non-fatal."""
    try:
        resp = await client.post(
            f"{ATTIO_BASE_URL}/lists/{_PROSPECT_LIST_ID}/entries",
            json={
                "data": {
                    "parent_object": "people",
                    "parent_record_id": person_record_id,
                    "entry_values": {},
                }
            },
        )
        if not resp.is_success:
            logger.error("Attio: error adding %r to list: %d %s", profile.name, resp.status_code, resp.text)
            return None
        logger.info("Attio: added %r to prospect list", profile.name)
        return resp.json()["data"]["id"]["entry_id"]
    except Exception as e:
        logger.warning("Attio: list entry failed for person %r: %s", person_record_id, e)
        return None


# ── Public interface ─────────────────────────────────────────────────────────────

_OUTREACH_STATUS_ATTR_ID = os.environ.get(
    "ATTIO_OUTREACH_STATUS_ATTR_ID", "480f90f7-7094-49bf-ae2a-d9ef39d8801c"
)


# Outreach Status select-attribute option IDs (list-scoped, fetched from
# GET /v2/lists/{list_id}/attributes/{attr_id}/options). Attio requires the
# option_id — not the display text — when writing a select attribute.
_OUTREACH_STATUS_OPTION_IDS = {
    "Pending Review": os.environ.get("ATTIO_OUTREACH_OPTION_PENDING", "f38daaaa-307d-4d76-a7f1-9a364bd4ed99"),
    "Approved": os.environ.get("ATTIO_OUTREACH_OPTION_APPROVED", "e9a8168f-88ff-4ddc-bd62-db46ce5a2855"),
    "Rejected": os.environ.get("ATTIO_OUTREACH_OPTION_REJECTED", "6865d82c-a4c8-452f-a051-631daef81f3e"),
}


async def update_list_entry_outreach_status(entry_id: str, status: str) -> bool:
    """
    Write the Outreach Status select attribute on a list entry.
    `status` must be one of the keys in _OUTREACH_STATUS_OPTION_IDS.
    Returns True on success, False otherwise. Never raises.
    """
    if not ATTIO_API_KEY:
        return False

    option_id = _OUTREACH_STATUS_OPTION_IDS.get(status)
    if not option_id:
        logger.error("Attio: unknown outreach status %r — cannot update list entry %s", status, entry_id)
        return False

    try:
        async with httpx.AsyncClient(headers=_attio_headers(), timeout=15) as client:
            resp = await client.patch(
                f"{ATTIO_BASE_URL}/lists/{_PROSPECT_LIST_ID}/entries/{entry_id}",
                json={
                    "data": {
                        "entry_values": {
                            "outreach_status": [{"option": option_id}],
                        }
                    }
                },
            )
        if not resp.is_success:
            logger.error("Attio: error updating outreach status for entry %s: %d %s", entry_id, resp.status_code, resp.text)
            return False
        logger.info("Attio: set outreach_status=%r for entry %s", status, entry_id)
        return True
    except Exception as e:
        logger.error("Attio: outreach status update failed for entry %s: %s", entry_id, e)
        return False


async def get_list_entry_outreach_status(entry_id: str) -> Optional[str]:
    """
    Fetch the Outreach Status for a specific list entry by entry_id.
    Returns the status string (e.g. "Approved") or None if not found.
    """
    if not ATTIO_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(headers=_attio_headers(), timeout=15) as client:
            resp = await client.get(
                f"{ATTIO_BASE_URL}/lists/{_PROSPECT_LIST_ID}/entries/{entry_id}",
            )
        response_body = resp.text
        logger.debug("Attio: list entry response for %s: %s", entry_id, response_body)

        if not resp.is_success:
            logger.error("Attio: error fetching list entry %s: %d %s", entry_id, resp.status_code, response_body)
            return None

        entry_data = resp.json()

        outreach_status_entries = entry_data.get("data", {}).get("entry_values", {}).get("outreach_status") or []
        status_value = (
            outreach_status_entries[0].get("option", {}).get("title")
            if outreach_status_entries else None
        )

        logger.debug("Attio: outreach_status for entry %s: %r", entry_id, status_value)
        return status_value
    except Exception as e:
        logger.error("Attio: outreach status fetch failed for entry %s: %s", entry_id, e)
        return None


async def get_prospect_from_attio(record_id: str) -> Optional[dict]:
    """
    Fetch a person record from Attio and return a flat dict with:
      name, email, linkedin_url, company, title
    Returns None on any failure.
    """
    if not ATTIO_API_KEY:
        logger.warning("Attio: ATTIO_API_KEY not set")
        return None

    try:
        async with httpx.AsyncClient(headers=_attio_headers(), timeout=15) as client:
            resp = await client.get(
                f"{ATTIO_BASE_URL}/objects/people/records/{record_id}"
            )
        if not resp.is_success:
            logger.error("Attio: error fetching person %s: %d %s", record_id, resp.status_code, resp.text)
            return None

        values = resp.json().get("data", {}).get("values", {})

        name_entries = values.get("name", [])
        name = name_entries[0].get("full_name") if name_entries else None

        email_entries = values.get("email_addresses", [])
        email = email_entries[0].get("email_address") if email_entries else None

        linkedin_entries = values.get("linkedin", [])
        linkedin_url = linkedin_entries[0].get("value") if linkedin_entries else None

        company_entries = values.get("company", [])
        company = company_entries[0].get("name") if company_entries else None

        title_entries = values.get("job_title", [])
        title = title_entries[0].get("value") if title_entries else None

        return {
            "name": name,
            "email": email,
            "linkedin_url": linkedin_url,
            "company": company,
            "title": title,
        }
    except Exception as e:
        logger.error("Attio: fetch person failed for %r: %s", record_id, e)
        return None


async def push_prospect_to_attio(profile: ProspectProfile) -> AttioSyncResult:
    """
    Push a ProspectProfile to Attio CRM:
      1. Create company (non-fatal if it fails)
      2. Create person (required — failure aborts)
      3. Link person to company (non-fatal)
      4. Add Sparx note to person (non-fatal)
      5. Add person to prospect list (non-fatal)

    Never raises — always returns AttioSyncResult.
    """
    if not ATTIO_API_KEY:
        return AttioSyncResult(success=False, error="ATTIO_API_KEY not set")

    try:
        async with httpx.AsyncClient(headers=_attio_headers(), timeout=30) as client:
            # Step 1: Company (failure is non-fatal)
            company_record_id = await _create_company(client, profile)

            # Step 2: Person — required
            person_record_id = await _create_person(client, profile)
            if not person_record_id:
                return AttioSyncResult(
                    success=False,
                    company_record_id=company_record_id,
                    error="Person create failed — cannot add note or list entry",
                )

            # Step 3: Link person to company (non-fatal)
            if company_record_id:
                await _link_person_to_company(client, person_record_id, company_record_id)

            # Step 4: Note (non-fatal)
            note_id = await _add_note(client, person_record_id, profile)

            # Step 5: List entry (non-fatal)
            list_entry_id = await _add_to_list(client, person_record_id, profile)

        return AttioSyncResult(
            success=True,
            person_record_id=person_record_id,
            company_record_id=company_record_id,
            note_id=note_id,
            list_entry_id=list_entry_id,
        )

    except Exception as e:
        logger.error("Attio: unexpected error pushing %r: %s", profile.name, e)
        return AttioSyncResult(success=False, error=str(e))
