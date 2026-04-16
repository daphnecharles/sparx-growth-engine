from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ProspectLead(BaseModel):
    prospect_name: Optional[str] = None
    prospect_url: str
    company: Optional[str] = None
    context_snippet: Optional[str] = None


class ProspectBatch(BaseModel):
    prospects: List[ProspectLead]
    total_found: int
    total_skipped_duplicates: int
    queries_run: List[str]
    run_timestamp: datetime
