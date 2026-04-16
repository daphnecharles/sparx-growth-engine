from datetime import datetime
from typing import List

from pydantic import BaseModel

from models.prospect import ProspectProfile


class PipelineRunResult(BaseModel):
    total_prospects_found: int
    total_researched: int
    total_prioritized: int        # overall_fit_score >= 4.0
    total_deprioritized: int
    total_flagged_for_review: int
    total_skipped_duplicates: int
    profiles: List[ProspectProfile]
    attio_synced: int = 0
    run_timestamp: datetime
    run_duration_seconds: float
