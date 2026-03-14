from typing import Optional
from pydantic import BaseModel


class CellDetailResponse(BaseModel):
    cell_id: str
    risk_score: float
    risk_level: str
    explanation: str
    predicted_risk_score: Optional[float] = None
    predicted_risk_level: Optional[str] = None