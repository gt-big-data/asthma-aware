"""Authenticated location lookup; no client-controlled upstream URL or field mask."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth import get_current_user_id
from app.services import places

router = APIRouter(prefix="/v1/places", tags=["Places"])


class PlaceSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)

    @model_validator(mode="after")
    def pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude")
        return self


@router.post("/search")
def search(body: PlaceSearch, request: Request, response: Response, user_id: UUID = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    settings = request.app.state.settings
    if not settings.places_enabled or not settings.places_api_key:
        raise HTTPException(503, "Places integration is disabled")
    try:
        places.limiter.allow(user_id, settings.places_requests_per_minute)
        return places.search(settings, body.query, latitude=body.latitude, longitude=body.longitude)
    except places.PlacesRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from None
    except places.PlacesUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
