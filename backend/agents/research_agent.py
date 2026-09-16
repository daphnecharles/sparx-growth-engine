import concurrent.futures
import ipaddress
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Literal, Optional, TypedDict
from urllib.parse import urlparse

import anthropic
import httpx
import redis as redis_lib
from dotenv import load_dotenv
from exa_py import Exa
from langgraph.graph import END, StateGraph

from integrations.apollo import find_email_with_apollo
from models.prospect import ProspectInput, ProspectProfile, VerificationError

load_dotenv()

logger = logging.getLogger(__name__)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
exa = Exa(api_key=os.environ["EXA_API_KEY"])

# ── Redis client (optional — pipeline continues without caching if unavailable) ─

_redis: Optional[redis_lib.Redis] = None
try:
    _redis = redis_lib.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379"),
        decode_responses=True,
        socket_connect_timeout=2,
    )
    _redis.ping()
except Exception as _e:
    logger.warning("Redis unavailable — caching disabled. (%s)", _e)
    _redis = None

_CACHE_TTL = 86400  # 24 hours


# ── Claude call with retry ──────────────────────────────────────────────────────

def _claude_create(**kwargs) -> anthropic.types.Message:
    """
    Wrapper around claude.messages.create that retries on 529 overload errors
    with exponential backoff (2 s, 4 s, 8 s) before re-raising.
    """
    delays = [2, 4, 8]
    for attempt, delay in enumerate(delays, 1):
        try:
            return claude.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt <= len(delays):
                logger.warning(
                    "Claude overloaded (529) — retrying in %ds (attempt %d/%d)",
                    delay, attempt, len(delays),
                )
                time.sleep(delay)
            else:
                raise
    return claude.messages.create(**kwargs)


# ── Graph state ────────────────────────────────────────────────────────────────

COURSE_CONTEXT = """\
COURSE: AI for Entrepreneurs — 30-Day Intensive by Sparx Labs
PROMISE: "15+ hours back every week. Build systems that run while you sleep."
PRICE: $397 early bird / $547 regular
TARGET: Non-technical entrepreneurs with existing businesses — service providers, consultants, coaches,
  e-commerce owners, creative entrepreneurs, and small business owners drowning in manual work.

CURRICULUM:
  Week 1 "Clone Yourself with AI" — Custom AI assistant, prompt library, ChatGPT, Custom GPT Builder.
    Saves 5-10 hrs/week. Best for: overwhelming admin, repetitive communication, scattered workflows.
  Week 2 "Content That Sells Itself" — Automated content engine, 7-day content calendar, AI video.
    Tools: Canva, HeyGen, Buffer, Metricool. Saves 6-8 hrs/week.
    Best for: coaches, personal brands, anyone spending hours on social/content creation.
  Week 3 "Fill Your Pipeline on Autopilot" — Lead capture, CRM integration, automated email nurture,
    DM automation. Tools: Typeform, Attio, Zapier, ManyChat, Kit. Saves 5-7 hrs/week.
    Best for: service providers, consultants, anyone with inconsistent or manual lead flow.
  Week 4 "Build Your AI Chief of Staff" — Operations dashboard, expense tracking, productivity workflows.
    Tools: Notion, Granola, Motion. Saves 10+ hrs/week.
    Best for: scaling operators, growing teams, anyone juggling too many systems.

SOCIAL PROOF (match to prospect's industry/role when referencing):
  Sarah M. (Wellness Coach) — "Automated client onboarding, saved 12 hours/week. Now takes Fridays off."
    Best match: coaches, wellness, personal development, client-service businesses.
  Marcus T. (Real Estate Agent) — "Lead response rate up 300% after Week 3 funnel."
    Best match: sales-heavy businesses, high-volume outreach, real estate, finance, insurance.
  Aisha K. (E-commerce Founder) — "Not tech-savvy at all but Randy and Daphne made everything easy."
    Best match: e-commerce, product businesses, non-technical founders.
  James L. (Marketing Consultant) — "Custom GPT handles 80% of customer questions automatically."
    Best match: consultants, agencies, B2B service providers, marketers.

DISQUALIFIERS (auto-deprioritize, score capped at 2.0):
  - Technical founders already building with AI (high ai_maturity)
  - Pre-revenue or pre-business prospects
  - Anyone without real operations to automate
"""

WALLED_GARDENS = {"linkedin", "instagram", "facebook", "tiktok"}

PLATFORM_MAP = {
    "linkedin.com": "linkedin",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "tiktok.com": "tiktok",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "substack.com": "substack",
    "youtube.com": "youtube",
}


def get_platform(url: str) -> str:
    """Return a normalized platform name for a URL."""
    try:
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        for known_domain, platform in PLATFORM_MAP.items():
            if domain == known_domain or domain.endswith(f".{known_domain}"):
                return platform
        return "personal_website"
    except Exception:
        return "unknown"


def extract_url_handle(url: str) -> Optional[str]:
    """Extract the username/handle from the first path segment of a social profile URL."""
    _GENERIC_SEGMENTS = {"home", "profile", "about", "in", "u", "user", "channel"}
    try:
        path = urlparse(url).path.strip("/")
        handle = path.split("/")[0] if path else None
        if handle and handle.lower() not in _GENERIC_SEGMENTS:
            return handle
        return None
    except Exception:
        return None


def build_discovery_query(name: str, url: str, company: Optional[str] = None) -> str:
    """Build a richer Exa search query for discovering a prospect's public web presence."""
    parts = [name]

    # Company name sharpens disambiguation for common prospect names
    if company:
        parts.append(company)

    handle = extract_url_handle(url)
    # Include handle only when it adds signal beyond the name itself
    if handle and handle.lower().replace(".", "") != name.lower().replace(" ", ""):
        parts.append(handle)

    parts.extend(["entrepreneur", "founder", "business", "coach"])
    return " ".join(parts) + " website"


# ── Contact enrichment helpers ──────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")
_IG_URL_RE = re.compile(r"instagram\.com/([^/\s\"'?#]+)")
_TW_URL_RE = re.compile(r"(?:twitter|x)\.com/([^/\s\"'?#]+)")
_FB_URL_RE = re.compile(r"facebook\.com/([^/\s\"'?#]+)")

# Path segments that are platform features, not usernames
_IG_SKIP = {"p", "reel", "reels", "explore", "hashtag", "stories", "tv", "live", "accounts"}
_TW_SKIP = {"hashtag", "search", "share", "intent", "i", "status", "home",
            "notifications", "messages", "explore", "compose"}
_FB_SKIP = {"sharer", "share", "dialog", "plugins", "pages", "groups",
            "events", "hashtag", "login", "photo", "video", "watch"}

_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_email(text: str) -> Optional[str]:
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else None


def _extract_instagram(text: str) -> Optional[str]:
    for m in _IG_URL_RE.finditer(text):
        handle = m.group(1).strip("/")
        if handle.lower() not in _IG_SKIP and handle:
            return f"https://instagram.com/{handle}"
    return None


def _extract_twitter(text: str) -> Optional[str]:
    for m in _TW_URL_RE.finditer(text):
        handle = m.group(1).strip("/")
        if handle.lower() not in _TW_SKIP and handle:
            return f"https://x.com/{handle}"
    return None


_TW_VALID_PREFIXES = ("https://twitter.com/", "http://twitter.com/",
                       "https://x.com/", "http://x.com/")


def _extract_href_twitter(html: str) -> Optional[str]:
    """
    Find a Twitter/X profile URL in raw HTML href attributes.
    Only matches hrefs that start with a valid twitter.com or x.com prefix
    (prevents false positives from domains like axios.com or ux.com).
    Normalizes the result to x.com format.
    """
    for m in _HREF_RE.finditer(html):
        href = m.group(1)
        if not href.startswith(_TW_VALID_PREFIXES):
            continue
        social_m = _TW_URL_RE.search(href)
        if social_m:
            handle = social_m.group(1).strip("/")
            if handle.lower() not in _TW_SKIP and handle:
                return f"https://x.com/{handle}"
    return None


def _is_safe_url(url: str) -> bool:
    """Return False for private/loopback IPs, non-http(s) schemes, or malformed URLs."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        # Block known internal metadata hostnames
        if host in ("localhost", "metadata.google.internal", "169.254.169.254"):
            return False
        # If the host parses as an IP, block private/loopback/reserved ranges
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
        except ValueError:
            pass  # Hostname — not an IP literal, allow it
        return True
    except Exception:
        return False


def _fetch_website_html(url: str) -> Optional[str]:
    """Fetch raw HTML from a URL. Returns None on any failure or unsafe URL."""
    if not _is_safe_url(url):
        logger.warning("Blocked unsafe URL: %s", url)
        return None
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Sparx/1.0)"},
        )
        if response.is_success:
            return response.text
    except Exception as e:
        logger.warning("HTML fetch failed for %s: %s", url, e)
    return None


def _extract_href_social(html: str, url_re: re.Pattern,
                          skip_set: set, base_url: str) -> Optional[str]:
    """Find the first social profile URL in raw HTML href attributes."""
    for m in _HREF_RE.finditer(html):
        href = m.group(1)
        social_m = url_re.search(href)
        if social_m:
            handle = social_m.group(1).strip("/")
            if handle.lower() not in skip_set and handle:
                return f"{base_url}/{handle}"
    return None


def _search_social(name: str, query_suffix: str, url_re: re.Pattern,
                   skip_set: set, base_url: str) -> Optional[str]:
    """Fallback Exa search to find a social profile URL for a prospect."""
    try:
        r = exa.search(query=f'"{name}" {query_suffix}', type="auto", num_results=5)
        for item in r.results:
            url_text = item.url or ""
            m = url_re.search(url_text)
            if m:
                handle = m.group(1).strip("/")
                if handle.lower() not in skip_set and handle:
                    return f"{base_url}/{handle}"
    except Exception as e:
        logger.warning("Social fallback search failed (%s): %s", query_suffix, e)
    return None


def _search_social_twitter(name: str, company: str) -> Optional[str]:
    """Improved Twitter fallback: searches '{name} {company} twitter'."""
    parts = [f'"{name}"']
    if company:
        parts.append(company)
    parts.append("twitter")
    try:
        r = exa.search(query=" ".join(parts), type="auto", num_results=5)
        for item in r.results:
            url_text = item.url or ""
            m = _TW_URL_RE.search(url_text)
            if m:
                handle = m.group(1).strip("/")
                if handle.lower() not in _TW_SKIP and handle:
                    return f"https://x.com/{handle}"
    except Exception as e:
        logger.warning("Twitter fallback search failed: %s", e)
    return None


class ProspectState(TypedDict, total=False):
    # Input
    prospect_name: Optional[str]
    prospect_url: Optional[str]
    # Platform detection
    detected_platform: Optional[str]
    source_url_used: Optional[str]
    # Research
    raw_research: Optional[str]
    # Metadata
    current_node: Optional[str]
    errors: List[str]
    node_log: List[str]
    flagged_for_review: bool
    cached: bool
    # ProspectProfile fields — flat, optional until populated by nodes
    name: Optional[str]
    company: Optional[str]
    role: Optional[str]
    company_size: Optional[str]
    industry: Optional[str]
    ai_maturity: Optional[Literal["low", "medium", "high"]]
    top_pain_points: Optional[List[str]]
    course_modules: Optional[List[str]]
    recommended_course_angle: Optional[str]
    outreach_personalization_notes: Optional[str]
    budget_signal_found: Optional[bool]
    verified: Optional[bool]
    confidence: Optional[Literal["high", "medium", "low"]]
    disqualified: Optional[bool]
    course_fit: Optional[int]
    ai_opportunity: Optional[int]
    outreach_priority: Optional[int]
    overall_fit_score: Optional[float]
    fit_summary: Optional[str]
    recommended_action: Optional[Literal["prioritize", "nurture", "deprioritize"]]
    # Contact enrichment — populated by contact_enrichment_node
    website_url: Optional[str]
    email: Optional[str]
    instagram_url: Optional[str]
    twitter_url: Optional[str]
    facebook_url: Optional[str]
    contact_enrichment_confidence: Optional[str]
    linkedin_dm_draft: Optional[str]
    # Transition fields — kept so existing node logic continues to work until nodes are updated
    prospect_input: Optional[ProspectInput]
    profile: Optional[ProspectProfile]
    error: Optional[str]
    verification_error: Optional[VerificationError]
    platform_warning: Optional[Dict[str, Any]]


# ── Nodes ──────────────────────────────────────────────────────────────────────

def platform_detection_node(state: ProspectState) -> Dict[str, Any]:
    """Detect the input URL platform; replace walled-garden URLs with a discoverable public URL."""
    _NODE = "platform_detection"
    _log = list(state.get("node_log") or [])
    _errs = list(state.get("errors") or [])

    try:
        inp = state.get("prospect_input")

        if not inp.linkedin_url:
            return {"current_node": _NODE, "node_log": _log + [f"{_NODE}: complete"]}

        platform = get_platform(inp.linkedin_url)

        if platform not in WALLED_GARDENS:
            # URL is already researchable — record it and pass through
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: complete"],
                "source_url_used": inp.linkedin_url,
            }

        # Walled garden: search for the prospect's public web presence
        if not inp.name:
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: complete"],
                "platform_warning": {
                    "platform_warning": True,
                    "message": (
                        f"The provided URL is from {platform}, which cannot be directly researched. "
                        "Please also provide a prospect name so we can find their public web presence."
                    ),
                },
            }

        try:
            query = build_discovery_query(inp.name, inp.linkedin_url, company=inp.company)
            r = exa.search(query=query, type="auto", num_results=5)
            good_results = [
                item for item in r.results
                if get_platform(item.url) not in WALLED_GARDENS
            ]

            if good_results:
                new_url = good_results[0].url
                return {
                    "current_node": _NODE,
                    "node_log": _log + [f"{_NODE}: complete"],
                    "prospect_input": ProspectInput(
                        name=inp.name,
                        company=inp.company,
                        linkedin_url=new_url,
                    ),
                    "source_url_used": new_url,
                }
            else:
                return {
                    "current_node": _NODE,
                    "node_log": _log + [f"{_NODE}: complete"],
                    "platform_warning": {
                        "platform_warning": True,
                        "message": (
                            "Could not find a public web presence for this prospect. "
                            "Please provide a personal website URL instead."
                        ),
                    },
                }

        except Exception as e:
            msg = f"Platform detection search failed: {e}"
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }

    except Exception as e:
        msg = f"platform_detection: {e}"
        return {
            "current_node": _NODE,
            "node_log": _log + [f"{_NODE}: error"],
            "errors": _errs + [msg],
            "error": msg,
        }


def contact_enrichment_node(state: ProspectState) -> Dict[str, Any]:
    """
    Discover the prospect's personal/business website and extract contact details.
    Extracts email, Instagram, Twitter, and Facebook via href-based HTML parsing
    (catches icon links) plus text-regex fallback and Exa social searches.
    Never blocks the pipeline — all failures are logged and produce None values.
    """
    _NODE = "contact_enrichment"
    _log = list(state.get("node_log") or [])
    _errs = list(state.get("errors") or [])

    website_url: Optional[str] = None
    email: Optional[str] = None
    instagram_url: Optional[str] = None
    twitter_url: Optional[str] = None
    facebook_url: Optional[str] = None
    confidence = "low"

    try:
        inp = state.get("prospect_input")
        source_url = state.get("source_url_used")
        name = (inp.name if inp else None) or ""
        company = (inp.company if inp else None) or ""

        # ── 1. Website discovery ─────────────────────────────────────────────
        if source_url and get_platform(source_url) == "personal_website":
            website_url = source_url
        elif name:
            q_parts = [f'"{name}"']
            if company:
                q_parts.append(f'"{company}"')
            q_parts.append("official website")
            try:
                r = exa.search(query=" ".join(q_parts), type="auto", num_results=5)
                for item in r.results:
                    if get_platform(item.url) == "personal_website":
                        website_url = item.url
                        break
            except Exception as e:
                logger.warning("contact_enrichment: website search failed: %s", e)

        # ── 2. Extract contacts from website content ─────────────────────────
        page_text = ""
        if website_url:
            # 2a. Exa text — used for email extraction
            try:
                r = exa.search(
                    query=website_url,
                    type="auto",
                    num_results=2,
                    contents={"text": {"max_characters": 5000}},
                )
                page_text = r.results[0].text if r.results else ""
                if page_text:
                    email = _extract_email(page_text)
            except Exception as e:
                logger.warning("contact_enrichment: Exa content fetch failed for %s: %s", website_url, e)

            # 2b. Raw HTML — href-based social link detection (catches icon links)
            html = _fetch_website_html(website_url)
            if html:
                instagram_url = _extract_href_social(html, _IG_URL_RE, _IG_SKIP, "https://instagram.com")
                twitter_url = _extract_href_twitter(html)
                facebook_url = _extract_href_social(html, _FB_URL_RE, _FB_SKIP, "https://facebook.com")

            # 2c. Text-regex fallback for any social not found via hrefs
            if page_text:
                if not instagram_url:
                    instagram_url = _extract_instagram(page_text)
                if not twitter_url:
                    twitter_url = _extract_twitter(page_text)

        # ── 3. Fallback Exa social searches (run in parallel) ────────────────
        if name:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                ig_future = (
                    pool.submit(_search_social, name, "instagram", _IG_URL_RE, _IG_SKIP, "https://instagram.com")
                    if not instagram_url else None
                )
                tw_future = (
                    pool.submit(_search_social_twitter, name, company)
                    if not twitter_url else None
                )
                fb_future = (
                    pool.submit(_search_social, name, "facebook", _FB_URL_RE, _FB_SKIP, "https://facebook.com")
                    if not facebook_url else None
                )
                if ig_future:
                    instagram_url = ig_future.result()
                if tw_future:
                    twitter_url = tw_future.result()
                if fb_future:
                    facebook_url = fb_future.result()

        # ── 4. Apollo email lookup (only if Exa enrichment found no email) ─────────
        if not email and name:
            linkedin_for_apollo = None
            prospect_url = state.get("prospect_url") or (inp.linkedin_url if inp else None)
            if prospect_url and "linkedin" in prospect_url:
                linkedin_for_apollo = prospect_url
            apollo_email = find_email_with_apollo(
                name=name,
                company=company or None,
                linkedin_url=linkedin_for_apollo,
            )
            if apollo_email:
                email = apollo_email
                logger.info("Enrichment: Apollo found email for %r", name)
            else:
                logger.info("Enrichment: no email found for %r", name)

        # ── 5. Confidence ────────────────────────────────────────────────────
        social_count = sum(1 for x in [instagram_url, twitter_url, facebook_url] if x)
        if email and social_count >= 2:
            confidence = "high"
        elif email or social_count >= 1:
            confidence = "medium"

    except Exception as e:
        msg = f"contact_enrichment: {e}"
        logger.warning(msg)
        _errs = _errs + [msg]

    return {
        "current_node": _NODE,
        "node_log": _log + ["contact_enrichment: complete"],
        "errors": _errs,
        "website_url": website_url,
        "email": email,
        "instagram_url": instagram_url,
        "twitter_url": twitter_url,
        "facebook_url": facebook_url,
        "contact_enrichment_confidence": confidence,
    }


def research_node(state: ProspectState) -> Dict[str, Any]:
    """Use Exa to gather real information about the prospect."""
    _NODE = "research"
    _log = list(state.get("node_log") or [])
    _errs = list(state.get("errors") or [])

    try:
        inp = state.get("prospect_input")
        results: list[str] = []

        try:
            # Search by LinkedIn URL if provided
            if inp.linkedin_url:
                r = exa.search(
                    query=inp.linkedin_url,
                    type="auto",
                    num_results=3,
                    contents={"text": {"max_characters": 10000}},
                )
                for item in r.results:
                    results.append(f"Source: {item.url}\n{item.text or ''}")

            # Search by name + company
            if inp.name or inp.company:
                query_parts = []
                if inp.name:
                    query_parts.append(inp.name)
                if inp.company:
                    query_parts.append(f"at {inp.company}")
                query = " ".join(query_parts)

                r = exa.search(
                    query=query,
                    type="auto",
                    num_results=5,
                    category="people",
                    contents={"text": {"max_characters": 10000}},
                )
                for item in r.results:
                    results.append(f"Source: {item.url}\n{item.text or ''}")

                # Also search for the company itself
                if inp.company:
                    r = exa.search(
                        query=inp.company,
                        type="auto",
                        num_results=3,
                        category="company",
                        contents={"text": {"max_characters": 5000}},
                    )
                    for item in r.results:
                        results.append(f"Source: {item.url}\n{item.text or ''}")

        except Exception as e:
            msg = f"Exa search failed: {e}"
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }

        raw = "\n\n---\n\n".join(results) if results else "No results found."
        return {
            "current_node": _NODE,
            "node_log": _log + [f"{_NODE}: complete"],
            "raw_research": raw,
        }

    except Exception as e:
        msg = f"research: {e}"
        return {
            "current_node": _NODE,
            "node_log": _log + [f"{_NODE}: error"],
            "errors": _errs + [msg],
            "error": msg,
        }


def analysis_node(state: ProspectState) -> Dict[str, Any]:
    """Use Claude to synthesize research into a structured ProspectProfile."""
    _NODE = "analysis"
    _log = list(state.get("node_log") or [])
    _errs = list(state.get("errors") or [])

    try:
        if state.get("error"):
            return {"current_node": _NODE, "node_log": _log + [f"{_NODE}: skipped (upstream error)"]}

        inp = state.get("prospect_input")
        prospect_hint = " | ".join(
            filter(None, [inp.name, inp.company, inp.linkedin_url])
        )

        system_prompt = f"""You are an expert B2B sales researcher and copywriter for Sparx Labs.
Given raw research about a prospect, produce a precise, actionable prospect profile grounded in the \
following course context. Use this to frame all pain point analysis and course recommendations.

{COURSE_CONTEXT}
Be specific and honest — if information is unavailable, make a reasonable inference and note it."""

        user_prompt = f"""Prospect: {prospect_hint}

Research gathered:
{state.get("raw_research", "")}

Return a JSON object with exactly these fields:

- name (string): Full name
- company (string): Company name
- role (string): Current job title
- company_size (string): e.g. "50-200 employees", "Series B startup", "Fortune 500"
- industry (string): e.g. "FinTech", "Healthcare SaaS", "E-commerce"
- ai_maturity (string): one of "low", "medium", "high" — how mature is their AI adoption?
- budget_signal_found (boolean): true if research shows signs the prospect generates real revenue and \
could afford $397-$547 (e.g. paid programs, active client base, product sales, pricing pages, \
testimonials from paying clients). false if unclear or absent.
- top_pain_points (array of 3 strings): specific pain points. For each, append which course week \
directly solves it in parentheses, e.g. "Spends 10+ hours/week manually writing social content (→ Week 2)".
- course_modules (array of 3-5 strings): most relevant modules, chosen ONLY from this list (verbatim):
    "AI tools for content creation"
    "Prompt engineering for business"
    "AI workflow automation"
    "Building AI-powered lead generation"
    "AI for client communication"
    "No-code AI tools"
    "AI for scaling operations"
    "AI product strategy"
- recommended_course_angle (string): a 1-2 sentence personalized outreach PITCH — not a description. \
Rules:
  • Reference the specific Week(s) most relevant to them by name
  • Lead with a concrete result (time saved or revenue gained), not features
  • If their industry matches a social proof testimonial, reference it naturally \
(Sarah M. for coaches/wellness, Marcus T. for sales-heavy/real estate, Aisha K. for e-commerce, \
James L. for consultants/agencies)
  • Write as if speaking directly to this person — no templates, no fluff
- outreach_personalization_notes (string): 2-3 sentences of specific hooks using their actual content, \
brand language, recent work, or public statements. Make it feel researched, not generic.

Respond with only valid JSON, no markdown fences."""

        try:
            response = _claude_create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_json = response.content[0].text.strip()
            # Strip markdown code fences Claude occasionally wraps the JSON in
            if raw_json.startswith("```"):
                raw_json = "\n".join(
                    line for line in raw_json.split("\n")
                    if not line.strip().startswith("```")
                ).strip()
            if not raw_json:
                msg = f"Claude returned empty response (stop_reason: {response.stop_reason})"
                return {
                    "current_node": _NODE,
                    "node_log": _log + [f"{_NODE}: error"],
                    "errors": _errs + [msg],
                    "error": msg,
                }
            data = json.loads(raw_json)
            data["source_url_used"] = state.get("source_url_used")
            profile = ProspectProfile(**data)
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: complete"],
                "profile": profile,
            }

        except json.JSONDecodeError as e:
            msg = f"Claude returned invalid JSON: {e}"
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }
        except Exception as e:
            msg = f"Analysis failed: {e}"
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }

    except Exception as e:
        msg = f"analysis: {e}"
        return {
            "current_node": _NODE,
            "node_log": _log + [f"{_NODE}: error"],
            "errors": _errs + [msg],
            "error": msg,
        }


def verification_node(state: ProspectState) -> Dict[str, Any]:
    """Use Claude to verify the profile matches the intended prospect."""
    _NODE = "verification"
    _log = list(state.get("node_log") or [])
    _errs = list(state.get("errors") or [])

    try:
        profile = state.get("profile")
        if profile is None:
            msg = "No profile available to verify."
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }

        inp = state.get("prospect_input")
        prospect_hint = " | ".join(filter(None, [inp.name, inp.company, inp.linkedin_url]))

        system_prompt = """You are a data quality analyst verifying AI-generated prospect profiles for a B2B sales team \
that sells AI/ML training courses to business owners, entrepreneurs, coaches, and founders. \
Your job is to assess accuracy AND fit — a technically accurate profile that describes the wrong type of person is still a failure."""

        user_prompt = f"""Original prospect input: {prospect_hint}

Generated profile:
- Name: {profile.name}
- Company: {profile.company}
- Role: {profile.role}
- Industry: {profile.industry}
- Company Size: {profile.company_size}

Assess ALL of the following:

1. Identity match — Does the name match the input name (if provided)? Does the company match (if provided)?
2. Internal consistency — Are name, company, role, and industry coherent with each other?
3. Source alignment — If a URL was provided, does the profile match what you would expect from that source?
4. Business orientation — Does this profile represent someone in a business-oriented role (entrepreneur, \
founder, business owner, executive, coach, consultant, marketer, etc.)?
   - Assign "low" confidence if the profile appears to be primarily an artist, musician, actor, athlete, \
or other non-business profession, UNLESS the original input clearly indicated that profession was the intent.
   - Solopreneurs, personal brand builders, and content creators who run a business are still valid — focus \
on whether they have a business context, not whether they work for a company.

Assign a confidence score:
- "high": profile clearly matches the intended prospect, all fields consistent, and person is business-oriented
- "medium": profile likely matches but some uncertainty exists on identity, consistency, or profession fit
- "low": profile does not match the input, has significant inconsistencies, OR the person is clearly \
in a non-business profession inconsistent with the original search intent

Return a JSON object with exactly these fields:
- confidence (string): "high", "medium", or "low"
- reason (string): brief explanation covering identity match AND profession fit (2-3 sentences)

Respond with only valid JSON, no markdown fences."""

        try:
            response = _claude_create(
                model="claude-sonnet-4-6",
                max_tokens=256,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            data = json.loads(response.content[0].text.strip())
            confidence = data["confidence"]
            reason = data["reason"]

            if confidence in ("high", "medium"):
                profile_data = profile.dict()
                profile_data["verified"] = True
                profile_data["confidence"] = confidence
                return {
                    "current_node": _NODE,
                    "node_log": _log + [f"{_NODE}: complete"],
                    "profile": ProspectProfile(**profile_data),
                }
            else:
                return {
                    "current_node": _NODE,
                    "node_log": _log + [f"{_NODE}: complete"],
                    "flagged_for_review": True,
                    "verification_error": VerificationError(
                        verified=False,
                        reason=reason,
                        flagged_for_review=True,
                    ),
                }

        except json.JSONDecodeError as e:
            msg = f"Verification returned invalid JSON: {e}"
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }
        except Exception as e:
            msg = f"Verification failed: {e}"
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }

    except Exception as e:
        msg = f"verification: {e}"
        return {
            "current_node": _NODE,
            "node_log": _log + [f"{_NODE}: error"],
            "errors": _errs + [msg],
            "error": msg,
        }


def fit_scoring_node(state: ProspectState) -> Dict[str, Any]:
    """Score the verified prospect against the AI for Entrepreneurs ICP."""
    _NODE = "fit_scoring"
    _log = list(state.get("node_log") or [])
    _errs = list(state.get("errors") or [])

    try:
        profile = state.get("profile")
        if profile is None:
            msg = "No profile available to score."
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }

        system_prompt = f"""You are a sales strategist and enrollment advisor for Sparx Labs. \
You evaluate whether prospects are a genuine fit for the "AI for Entrepreneurs" course and prioritize outreach accordingly.

{COURSE_CONTEXT}"""

        user_prompt = f"""Evaluate this prospect:

Name: {profile.name}
Role: {profile.role}
Company: {profile.company}
Industry: {profile.industry}
Company Size: {profile.company_size}
AI Maturity: {profile.ai_maturity}
Budget Signal Found: {profile.budget_signal_found}
Top Pain Points: {chr(10).join(f"  - {p}" for p in profile.top_pain_points)}

STEP 1 — DISQUALIFICATION CHECK (evaluate first, before scoring):
A prospect is DISQUALIFIED if ANY of the following apply:
  • ai_maturity is "high" (already technical, building with AI)
  • Appears to be pre-revenue or pre-business (no real clients, no operations to automate)
  • No clear existing business or team to apply course learnings to

Set disqualified = true if disqualified. Disqualified prospects are CAPPED at a max score of 2 on every dimension.

STEP 2 — SCORING (integers 1-5, or 1-2 if disqualified):

course_fit — Background match to "AI for Entrepreneurs"
  5 = Non-technical entrepreneur, clear business ops to automate
  3 = Business context but somewhat technical or large-company scale
  1 = Highly technical, enterprise, or fundamentally misaligned

ai_opportunity — Potential gain from AI upskilling right now
  5 = Low AI maturity, obvious manual work to automate, actively growing
  3 = Moderate AI awareness, real but not urgent opportunity
  1 = Already AI-advanced, or AI irrelevant to their work

outreach_priority — Likelihood of positive response to outreach
  5 = Active presence, sharp pain points, clearly receptive to tools
  3 = Moderate visibility, some pain points
  1 = Weak signal, unclear pain points, hard to reach

STEP 3 — SUMMARY:
fit_summary: One sentence. If NOT disqualified, name the single most relevant course week \
(e.g. "Week 2 would save her 6-8 hrs/week on content she's currently doing manually"). \
If disqualified, state why and which disqualifier applies.

recommended_action: "prioritize" / "nurture" / "deprioritize"
  - disqualified = true → always "deprioritize"
  - overall_fit_score >= 3.5 → "prioritize"
  - 2.5–3.4 → "nurture"
  - < 2.5 → "deprioritize"

Return only a JSON object with fields: disqualified, course_fit, ai_opportunity, outreach_priority, fit_summary, recommended_action
No markdown fences."""

        try:
            response = _claude_create(
                model="claude-sonnet-4-6",
                max_tokens=400,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            data = json.loads(response.content[0].text.strip())

            disqualified = bool(data.get("disqualified", False))
            course_fit = int(data["course_fit"])
            ai_opportunity = int(data["ai_opportunity"])
            outreach_priority = int(data["outreach_priority"])

            # Enforce score cap for disqualified prospects
            if disqualified:
                course_fit = min(course_fit, 2)
                ai_opportunity = min(ai_opportunity, 2)
                outreach_priority = min(outreach_priority, 2)

            overall_fit_score = round((course_fit + ai_opportunity + outreach_priority) / 3, 1)

            # Suppress course recommendation when prospect is not a good fit
            suppress_pitch = disqualified or overall_fit_score < 3.0

            # Enforce the action bucket in code rather than trusting the model's
            # recommended_action — disqualified/low-score prospects must never
            # end up "prioritize"/"nurture" and get pushed to Attio/Apollo.
            if disqualified:
                recommended_action = "deprioritize"
            elif overall_fit_score >= 3.5:
                recommended_action = "prioritize"
            elif overall_fit_score >= 2.5:
                recommended_action = "nurture"
            else:
                recommended_action = "deprioritize"

            profile_data = profile.dict()
            profile_data.update({
                "disqualified": disqualified,
                "course_fit": course_fit,
                "ai_opportunity": ai_opportunity,
                "outreach_priority": outreach_priority,
                "overall_fit_score": overall_fit_score,
                "fit_summary": data["fit_summary"],
                "recommended_action": recommended_action,
                "recommended_course_angle": None if suppress_pitch else profile_data.get("recommended_course_angle"),
            })
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: complete"],
                "profile": ProspectProfile(**profile_data),
            }

        except json.JSONDecodeError as e:
            msg = f"Fit scoring returned invalid JSON: {e}"
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }
        except Exception as e:
            msg = f"Fit scoring failed: {e}"
            return {
                "current_node": _NODE,
                "node_log": _log + [f"{_NODE}: error"],
                "errors": _errs + [msg],
                "error": msg,
            }

    except Exception as e:
        msg = f"fit_scoring: {e}"
        return {
            "current_node": _NODE,
            "node_log": _log + [f"{_NODE}: error"],
            "errors": _errs + [msg],
            "error": msg,
        }


def course_angle_node(state: ProspectState) -> Dict[str, Any]:
    """
    Generate a LinkedIn DM draft for prospects without an email address.
    For prospects with email, this is a no-op.
    """
    _NODE = "course_angle"
    _log = list(state.get("node_log") or [])

    email = state.get("email")
    if email:
        return {"current_node": _NODE, "node_log": _log + [f"{_NODE}: skipped (has email)"]}

    profile = state.get("profile")
    if not profile:
        return {"current_node": _NODE, "node_log": _log + [f"{_NODE}: skipped (no profile)"]}

    try:
        pain_point = profile.top_pain_points[0] if profile.top_pain_points else "manual work overload"
        prompt = f"""Write a short, friendly LinkedIn DM from a course creator reaching out to a prospect.

Prospect: {profile.name}, {profile.role} at {profile.company}
Key pain point: {pain_point}
Course: AI for Entrepreneurs — 30-Day Intensive. Helps non-technical business owners automate 15+ hours/week of manual work.

Requirements:
- 3–4 sentences max
- Open with a genuine observation about their work (not generic)
- Mention one specific benefit tied to their pain point
- End with a soft question (not a hard CTA)
- Conversational, NOT salesy. No exclamation marks.
- Do not mention pricing.
- Do not use em dashes (—). Use commas or short sentences instead.

Respond with only the DM text, no quotes or labels."""

        response = _claude_create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        dm_text = response.content[0].text.strip().replace("—", "-").replace("–", "-")
        logger.info("DM: generated LinkedIn DM for %r", profile.name)
        return {
            "current_node": _NODE,
            "node_log": _log + [f"{_NODE}: complete"],
            "linkedin_dm_draft": dm_text,
        }
    except Exception as e:
        logger.warning("DM: failed to generate DM for %r: %s", profile.name, e)
        return {"current_node": _NODE, "node_log": _log + [f"{_NODE}: error"]}


# ── Routing ────────────────────────────────────────────────────────────────────

def after_platform(state: ProspectState) -> str:
    if state.get("error") or state.get("platform_warning"):
        return END
    return "contact_enrichment"


def after_contact_enrichment(state: ProspectState) -> str:
    # Enrichment failures are non-blocking — always proceed to research
    return "research"


def after_research(state: ProspectState) -> str:
    if state.get("error"):
        return END
    return "analysis"


def after_analysis(state: ProspectState) -> str:
    if state.get("error") or state.get("profile") is None:
        return END
    return "verification"


def after_verification(state: ProspectState) -> str:
    if state.get("error"):
        return END
    if state.get("verification_error"):
        return END
    profile = state.get("profile")
    if profile is None or not profile.verified or profile.confidence == "low":
        return END
    return "fit_scoring"


def after_fit_scoring(state: ProspectState) -> str:
    if state.get("error"):
        return END
    profile = state.get("profile")
    if profile is None:
        return END
    if profile.disqualified or (profile.overall_fit_score is not None and profile.overall_fit_score < 3.0):
        return END
    return "course_angle"


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_key(name: str, company: Optional[str] = None) -> str:
    key = name.lower().strip()
    if company:
        key = f"{key}:{company.lower().strip()}"
    return f"prospect:{key}"


def _cache_get(name: str, company: Optional[str] = None) -> Optional[ProspectProfile]:
    if _redis is None or not name:
        return None
    try:
        raw = _redis.get(_cache_key(name, company))
        if raw:
            return ProspectProfile(**json.loads(raw))
    except Exception as e:
        logger.warning("Cache read failed: %s", e)
    return None


def _cache_set(name: str, profile: ProspectProfile, company: Optional[str] = None) -> None:
    if _redis is None or not name:
        return
    if not profile.verified or profile.overall_fit_score is None:
        return
    try:
        _redis.set(_cache_key(name, company), json.dumps(profile.dict()), ex=_CACHE_TTL)
    except Exception as e:
        logger.warning("Cache write failed: %s", e)


# ── Live progress logging ────────────────────────────────────────────────────

_NODE_PROGRESS_LABELS = {
    "platform_detection": "detecting platform & source",
    "contact_enrichment": "finding contact details",
    "research": "gathering research",
    "analysis": "analyzing profile with Claude",
    "verification": "verifying profile accuracy",
    "fit_scoring": "scoring course fit",
    "course_angle": "drafting LinkedIn DM",
}


def _log_progress(label: str, node: str) -> None:
    """Append a per-step progress line to the shared pipeline log, visible live
    in the dashboard. Mirrors prospecting_agent._pipeline_log — best-effort,
    never raises, no-op if Redis is unavailable."""
    if _redis is None:
        return
    friendly = _NODE_PROGRESS_LABELS.get(node)
    if not friendly:
        return
    try:
        _redis.rpush("sparx:pipeline_logs", f"  {label}: {friendly}...")
        _redis.ltrim("sparx:pipeline_logs", -500, -1)
    except Exception as e:
        logger.warning("Failed to write progress log: %s", e)


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_graph() -> Any:
    graph = StateGraph(ProspectState)

    graph.add_node("platform_detection", platform_detection_node)
    graph.add_node("contact_enrichment", contact_enrichment_node)
    graph.add_node("research", research_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("verification", verification_node)
    graph.add_node("fit_scoring", fit_scoring_node)
    graph.add_node("course_angle", course_angle_node)

    graph.set_entry_point("platform_detection")
    graph.add_conditional_edges("platform_detection", after_platform)
    graph.add_conditional_edges("contact_enrichment", after_contact_enrichment)
    graph.add_conditional_edges("research", after_research)
    graph.add_conditional_edges("analysis", after_analysis)
    graph.add_conditional_edges("verification", after_verification)
    graph.add_conditional_edges("fit_scoring", after_fit_scoring)
    graph.add_edge("course_angle", END)

    return graph.compile()


_graph = build_graph()


# ── Flagged-profile persistence ─────────────────────────────────────────────────

def _persist_flagged_profile(
    result: Dict[str, Any],
    name: Optional[str],
    company: Optional[str],
    linkedin_url: Optional[str],
    verification_error: VerificationError,
) -> None:
    """
    Persist a low-confidence profile for manual review instead of silently
    dropping it. Called right before run_research_agent raises for a
    verification failure — the analyzed (pre-fit-scoring) profile is still
    present in the graph state at this point. Never raises.
    """
    profile = result.get("profile")
    if profile is None:
        return

    try:
        profile_data = profile.dict()
        profile_data.update({
            "linkedin_url": linkedin_url,
            "website_url": result.get("website_url"),
            "email": result.get("email"),
            "instagram_url": result.get("instagram_url"),
            "twitter_url": result.get("twitter_url"),
            "facebook_url": result.get("facebook_url"),
            "contact_enrichment_confidence": result.get("contact_enrichment_confidence"),
            "verified": False,
            "confidence": "low",
            "outreach_status": "Flagged for Review",
            "fit_summary": verification_error.reason,
        })
        flagged_profile = ProspectProfile(**profile_data)

        if not flagged_profile.prospect_key:
            flagged_profile = ProspectProfile(**{
                **flagged_profile.dict(),
                "prospect_key": flagged_profile.source_url_used or f"{(name or '').lower()}:{(company or '').lower()}",
            })

        import db as _db
        existing_key = _db.find_prospect_key(email=flagged_profile.email, name=flagged_profile.name)
        if existing_key and existing_key != flagged_profile.prospect_key:
            flagged_profile = ProspectProfile(**{**flagged_profile.dict(), "prospect_key": existing_key})

        _db.upsert_prospect(flagged_profile.dict())
        logger.info("Persisted flagged-for-review profile: %r", flagged_profile.name)
    except Exception as e:
        logger.warning("Failed to persist flagged profile for %r: %s", name, e)


# ── Public interface ───────────────────────────────────────────────────────────

def run_research_agent(
    name: Optional[str] = None,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    force_refresh: bool = False,
) -> ProspectProfile:
    """
    Run the research agent for a prospect.

    Returns a cached ProspectProfile (TTL 24h) when available, unless
    force_refresh=True is passed to bypass the cache and re-run the full graph.

    Raises:
        ValueError: if the agent encounters an error during research or analysis.
    """
    if not any([name, company, linkedin_url]):
        raise ValueError("Provide at least one of: name, company, linkedin_url")

    # ── Cache check ────────────────────────────────────────────────────────────
    if not force_refresh and name:
        cached = _cache_get(name, company)
        if cached is not None:
            cached_data = cached.dict()
            cached_data["cached"] = True
            return ProspectProfile(**cached_data)

    # ── Graph invocation ───────────────────────────────────────────────────────
    initial_state: ProspectState = {
        "prospect_name": name,
        "prospect_url": linkedin_url,
        "errors": [],
        "node_log": [],
        "flagged_for_review": False,
        "cached": False,
        # Transition field — populated here so existing node logic keeps working
        "prospect_input": ProspectInput(
            name=name,
            company=company,
            linkedin_url=linkedin_url,
        ),
    }

    progress_label = name or linkedin_url or "prospect"
    result: Dict[str, Any] = initial_state  # fallback if the graph yields nothing
    last_logged_node: Optional[str] = None
    for state_snapshot in _graph.stream(initial_state, stream_mode="values"):
        result = state_snapshot
        node = result.get("current_node")
        if node and node != last_logged_node:
            last_logged_node = node
            _log_progress(progress_label, node)

    error = result.get("error")
    if error:
        raise ValueError(error)

    platform_warning = result.get("platform_warning")
    if platform_warning:
        raise ValueError(json.dumps(platform_warning))

    verification_error = result.get("verification_error")
    if verification_error:
        ve = verification_error if isinstance(verification_error, VerificationError) else VerificationError(**verification_error)
        _persist_flagged_profile(result, name, company, linkedin_url, ve)
        raise ValueError(json.dumps(ve.dict()))

    profile = result.get("profile")
    if profile is None:
        raise ValueError("Agent completed without producing a profile.")

    # ── Merge contact enrichment + DM fields from state into the profile ──────
    extra_data: Dict[str, Any] = {
        "linkedin_url": linkedin_url,
        "website_url": result.get("website_url"),
        "email": result.get("email"),
        "instagram_url": result.get("instagram_url"),
        "twitter_url": result.get("twitter_url"),
        "facebook_url": result.get("facebook_url"),
        "contact_enrichment_confidence": result.get("contact_enrichment_confidence"),
        "linkedin_dm_draft": result.get("linkedin_dm_draft"),
    }
    if any(v is not None for v in extra_data.values()):
        merged = profile.dict()
        merged.update({k: v for k, v in extra_data.items() if v is not None})
        profile = ProspectProfile(**merged)

    # ── Set prospect_key (used for Redis and frontend URL routing) ──────────────
    if not profile.prospect_key:
        profile_data = profile.dict()
        profile_data["prospect_key"] = profile.source_url_used or f"{(name or '').lower()}:{(company or '').lower()}"
        profile = ProspectProfile(**profile_data)

    # ── Reconcile against an existing record for the same person ────────────────
    # Dedup safety net for callers that bypass the prospecting agent (e.g. direct
    # /api/research-prospect calls): if we already have this person stored under a
    # different prospect_key (a different source URL), reuse that key so this
    # research updates the existing row instead of creating a duplicate.
    try:
        import db as _db
        existing_key = _db.find_prospect_key(email=profile.email, name=profile.name)
        if existing_key and existing_key != profile.prospect_key:
            profile_data = profile.dict()
            profile_data["prospect_key"] = existing_key
            profile = ProspectProfile(**profile_data)
    except Exception as e:
        logger.warning("Dedup lookup failed for %r: %s", profile.name, e)

    # ── Cache write ────────────────────────────────────────────────────────────
    if name:
        _cache_set(name, profile, company)

    # ── Store in prospect index for GET /api/prospects ──────────────────────────
    if _redis and profile.prospect_key:
        try:
            pipe = _redis.pipeline()
            pipe.set(f"sparx:profile:{profile.prospect_key}", json.dumps(profile.dict()), ex=7 * _CACHE_TTL)
            pipe.sadd("sparx:profile_keys", profile.prospect_key)
            pipe.execute()
        except Exception as e:
            logger.warning("Failed to index profile in Redis: %s", e)

    # ── Persist to SQLite (primary durable store) ──────────────────────────────
    try:
        import db as _db
        _db.upsert_prospect(profile.dict())
    except Exception as e:
        logger.warning("Failed to persist profile to SQLite: %s", e)

    return profile
