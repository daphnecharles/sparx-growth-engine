from typing import Optional
from pydantic import BaseModel


class ApolloEnrollResult(BaseModel):
    success: bool
    contact_id: Optional[str] = None
    sequence_id: Optional[str] = None
    error: Optional[str] = None
