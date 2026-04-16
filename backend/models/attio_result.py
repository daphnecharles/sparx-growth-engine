from typing import Optional

from pydantic import BaseModel


class AttioSyncResult(BaseModel):
    success: bool
    person_record_id: Optional[str] = None
    company_record_id: Optional[str] = None
    note_id: Optional[str] = None
    error: Optional[str] = None
