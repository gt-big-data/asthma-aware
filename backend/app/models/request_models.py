# Defines the data model for incoming map requests, specifying the expected structure and default values for the request payload.

from pydantic import BaseModel


class MapRequest(BaseModel):
    city: str = "atlanta"