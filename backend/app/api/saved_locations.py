"""Public Georgia city search and authenticated account bookmarks."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.auth import get_current_user_id
from app.dependencies import get_user_session
from app.services.location_catalog import city_by_id, search_cities, location_coverage
from db.repositories import saved_locations as repository

router = APIRouter(prefix="/v1/locations", tags=["Saved locations"])
User = Annotated[UUID, Depends(get_current_user_id)]
Database = Annotated[Session, Depends(get_user_session, scope="function")]


class LocationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=80)

    @field_validator("label")
    @classmethod
    def label_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Label cannot be blank")
        return value.strip() if value is not None else None


def render(record):
    city = city_by_id(record.city_id)
    return {"city_id": record.city_id, "label": record.label, "city_name": record.city_name,
            "latitude": record.latitude, "longitude": record.longitude, "state": "GA",
            "forecast_supported": city["forecast_supported"]}


@router.get("/cities")
def cities(q: str = Query("", max_length=80)):
    return {"items": search_cities(q)}


@router.get("/coverage")
def coverage(lat: float = Query(ge=-90, le=90, allow_inf_nan=False),
             lon: float = Query(ge=-180, le=180, allow_inf_nan=False)):
    return location_coverage(lat, lon)


@router.get("/cities/{city_id}")
def city(city_id: str):
    try:
        return city_by_id(city_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@router.get("")
def locations(user: User, session: Database):
    return {"items": [render(row) for row in repository.list_locations(session, user)]}


@router.put("/{city_id}")
def save_location(city_id: str, body: LocationWrite, user: User, session: Database):
    try:
        selected = city_by_id(city_id)
        return render(repository.save_location(session, user, selected, body.label))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.delete("/{city_id}", status_code=204)
def delete_location(city_id: str, user: User, session: Database):
    repository.remove_location(session, user, city_id)
    return Response(status_code=204)
