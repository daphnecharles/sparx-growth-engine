from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel


class ProspectInput(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    linkedin_url: Optional[str] = None


class ProspectProfile(BaseModel):
    name: str
    company: str
    role: str
    company_size: str
    industry: str
    ai_maturity: Literal["low", "medium", "high"]
    top_pain_points: List[str]
    course_modules: Optional[List[str]] = None
    recommended_course_angle: Optional[str] = None  # Nulled out if disqualified or score < 3.0
    outreach_personalization_notes: str
    budget_signal_found: bool = False  # Set by analysis node
    # Set by platform detection node
    source_url_used: Optional[str] = None
    # Original LinkedIn profile URL (preserved even when source_url_used is replaced by personal site)
    linkedin_url: Optional[str] = None
    # Set by contact enrichment node
    website_url: Optional[str] = None
    email: Optional[str] = None
    instagram_url: Optional[str] = None
    twitter_url: Optional[str] = None
    facebook_url: Optional[str] = None
    contact_enrichment_confidence: Optional[str] = None
    apollo_id: Optional[str] = None
    linkedin_dm_draft: Optional[str] = None
    outreach_status: Optional[str] = None
    prospect_key: Optional[str] = None
    # Set by verification node
    verified: bool = False
    confidence: Optional[Literal["high", "medium", "low"]] = None
    # Set by fit scoring node
    disqualified: bool = False
    course_fit: Optional[int] = None
    ai_opportunity: Optional[int] = None
    outreach_priority: Optional[int] = None
    overall_fit_score: Optional[float] = None
    fit_summary: Optional[str] = None
    recommended_action: Optional[Literal["prioritize", "nurture", "deprioritize"]] = None
    cached: bool = False  # Set by run_research_agent when result is served from cache


class VerificationError(BaseModel):
    verified: bool = False
    reason: str
    flagged_for_review: bool = True
