import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from models.apollo_result import ApolloEnrollResult

load_dotenv()

logger = logging.getLogger(__name__)

APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")
APOLLO_SEQUENCE_ID = os.environ.get("APOLLO_SEQUENCE_ID", "699f4e8028b17900190f67c7")
APOLLO_EMAIL_ACCOUNT_ID = os.environ.get("APOLLO_EMAIL_ACCOUNT_ID", "699caa2561cb02001181a4ab")
APOLLO_BASE_URL = "https://api.apollo.io/api/v1"


def _apollo_headers() -> dict:
    return {
        "X-Api-Key": APOLLO_API_KEY,
        "Content-Type": "application/json",
    }


def _split_name(full_name: Optional[str]) -> tuple[str, str]:
    parts = (full_name or "").strip().split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else "."
    return first_name, last_name


def find_email_with_apollo(
    name: Optional[str],
    company: Optional[str],
    linkedin_url: Optional[str] = None,
) -> Optional[str]:
    """
    Synchronous Apollo people/match call to find a prospect's email.
    Returns the email string if found, None otherwise.
    Handles 429 with one retry after 2 seconds.
    Never raises.
    """
    if not APOLLO_API_KEY:
        return None

    body: dict = {
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
    }
    if name:
        body["name"] = name
    if company:
        body["organization_name"] = company
    if linkedin_url:
        body["linkedin_url"] = linkedin_url

    headers = _apollo_headers()

    def _do_request() -> Optional[httpx.Response]:
        try:
            return httpx.post(
                f"{APOLLO_BASE_URL}/people/match",
                headers=headers,
                json=body,
                timeout=15,
            )
        except Exception as e:
            logger.warning("Apollo: people/match request failed: %s", e)
            return None

    resp = _do_request()
    if resp is None:
        return None

    if resp.status_code == 429:
        import time
        time.sleep(2)
        resp = _do_request()
        if resp is None:
            return None

    if not resp.is_success:
        logger.warning("Apollo: people/match error: %d %s", resp.status_code, resp.text[:200])
        return None

    person = resp.json().get("person") or {}
    email = person.get("email")
    return email if email else None


async def enroll_in_apollo_sequence(prospect: dict) -> ApolloEnrollResult:
    """
    Create or update a contact in Apollo and enroll them in the configured sequence.
    Never raises — always returns ApolloEnrollResult.
    """
    if not APOLLO_API_KEY:
        return ApolloEnrollResult(success=False, error="APOLLO_API_KEY not set")

    name = prospect.get("name", "")
    email = prospect.get("email")
    company = prospect.get("company")
    title = prospect.get("title")
    linkedin_url = prospect.get("linkedin_url")

    first_name, last_name = _split_name(name)

    try:
        async with httpx.AsyncClient(headers=_apollo_headers(), timeout=30) as client:
            # Step 1: Create or update contact
            contact_body: dict = {
                "first_name": first_name,
                "last_name": last_name,
            }
            if email:
                contact_body["email"] = email
            if company:
                contact_body["organization_name"] = company
            if title:
                contact_body["title"] = title
            if linkedin_url:
                contact_body["linkedin_url"] = linkedin_url

            resp = await client.post(
                f"{APOLLO_BASE_URL}/contacts",
                json=contact_body,
            )
            if not resp.is_success:
                logger.error("Apollo: error creating contact %r: %d %s", name, resp.status_code, resp.text[:200])
                return ApolloEnrollResult(
                    success=False,
                    error=f"Contact creation failed: {resp.status_code} {resp.text}",
                )

            contact_id = resp.json().get("contact", {}).get("id")
            if not contact_id:
                logger.error("Apollo: no contact_id in response for %r: %s", name, resp.text[:200])
                return ApolloEnrollResult(
                    success=False,
                    error="No contact_id returned from Apollo",
                )

            logger.info("Apollo: created contact %r (%s)", name, email)

            # Step 2: Enroll in sequence
            enroll_resp = await client.post(
                f"{APOLLO_BASE_URL}/emailer_campaigns/{APOLLO_SEQUENCE_ID}/add_contact_ids",
                json={
                    "contact_ids": [contact_id],
                    "emailer_campaign_id": APOLLO_SEQUENCE_ID,
                    "send_email_from_email_account_id": APOLLO_EMAIL_ACCOUNT_ID,
                },
            )
            if not enroll_resp.is_success:
                logger.error("Apollo: error enrolling %r in sequence: %d %s", name, enroll_resp.status_code, enroll_resp.text[:200])
                return ApolloEnrollResult(
                    success=False,
                    contact_id=contact_id,
                    error=f"Sequence enrollment failed: {enroll_resp.status_code} {enroll_resp.text}",
                )

            logger.info("Apollo: enrolled %r in sequence", name)
            return ApolloEnrollResult(
                success=True,
                contact_id=contact_id,
                sequence_id=APOLLO_SEQUENCE_ID,
            )

    except Exception as e:
        logger.error("Apollo: unexpected error enrolling %r: %s", name, e)
        return ApolloEnrollResult(success=False, error=str(e))
