"""Owner-scoped saved cities; profile lock serializes the ten-place limit."""
from sqlalchemy import delete, func, select

from db.models.application import SavedLocation
from db.repositories.application import ensure_profile


def list_locations(session, user):
    return list(session.scalars(select(SavedLocation).where(SavedLocation.user_id == user)
                               .order_by(SavedLocation.created_at, SavedLocation.city_id)))


def save_location(session, user, city, label):
    ensure_profile(session, user, lock=True)
    record = session.get(SavedLocation, (user, city["id"]))
    if record is None:
        count = session.scalar(select(func.count()).select_from(SavedLocation).where(SavedLocation.user_id == user))
        if count >= 10:
            raise ValueError("You can save up to 10 locations; remove one before adding another")
        record = SavedLocation(user_id=user, city_id=city["id"])
        session.add(record)
    record.label = label or city["name"][:80]
    record.city_name = city["name"]
    record.latitude, record.longitude = city["latitude"], city["longitude"]
    session.flush()
    return record


def remove_location(session, user, city_id):
    session.execute(delete(SavedLocation).where(SavedLocation.user_id == user, SavedLocation.city_id == city_id))
