"""Private application records, separate from the historical public data schema."""

import datetime as dt
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin

SCHEMA = "app"


class Profile(TimestampMixin, Base):
    __tablename__ = "profiles"
    __table_args__ = {"schema": SCHEMA}

    # Same UUID as the verified Supabase subject. Managed auth tables are not migrated here.
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/New_York")


class SymptomLog(TimestampMixin, Base):
    __tablename__ = "symptom_logs"
    __table_args__ = (
        UniqueConstraint("user_id", "log_date", name="uq_symptom_logs_user_date"),
        UniqueConstraint("id", "user_id", name="uq_symptom_logs_id_user"),
        CheckConstraint("severity BETWEEN 1 AND 5", name="severity_range"),
        CheckConstraint("latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180", name="coordinates_valid"),
        CheckConstraint("location_source IN ('current', 'selected')", name="location_source_valid"),
        CheckConstraint("revision > 0", name="revision_positive"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.profiles.user_id", ondelete="CASCADE"), nullable=False)
    log_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    location_source: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    # Automatic finality is time-based, not a scheduler-updated status flag.
    finalizes_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SymptomLogRevision(TimestampMixin, Base):
    """Audit history, not extra active observations for training."""

    __tablename__ = "symptom_log_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["log_id", "user_id"], ["app.symptom_logs.id", "app.symptom_logs.user_id"], ondelete="CASCADE"),
        CheckConstraint("revision > 0 AND severity BETWEEN 1 AND 5", name="revision_severity_valid"),
        {"schema": SCHEMA},
    )
    log_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Exact location, local day, timezone and finalization boundary of this revision.
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ExposureSnapshot(TimestampMixin, Base):
    """Immutable inputs for a particular log revision; a future worker writes these."""

    __tablename__ = "exposure_snapshots"
    __table_args__ = (
        UniqueConstraint("log_id", "log_revision", name="uq_exposures_log_revision"),
        ForeignKeyConstraint(["log_id", "user_id"], ["app.symptom_logs.id", "app.symptom_logs.user_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["log_id", "log_revision"], ["app.symptom_log_revisions.log_id", "app.symptom_log_revisions.revision"], ondelete="CASCADE"),
        CheckConstraint("log_revision > 0", name="log_revision_positive"),
        CheckConstraint("window_end > window_start", name="window_ordered"),
        {"schema": SCHEMA},
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.profiles.user_id", ondelete="CASCADE"), nullable=False)
    log_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    log_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(128), nullable=False)
    # Feature names/units/values and source object/run identities; not guessed exposures.
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)


class PersonalizationRevision(TimestampMixin, Base):
    __tablename__ = "personalization_revisions"
    __table_args__ = (
        UniqueConstraint("user_id", "revision", name="uq_personalization_user_revision"),
        CheckConstraint("revision > 0", name="revision_positive"),
        {"schema": SCHEMA},
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.profiles.user_id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    population_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(128), nullable=False)
    weights: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Exact log IDs/revisions used, rather than an unversioned user-history query.
    training_inputs: Mapped[list] = mapped_column(JSONB, nullable=False)


class OutboxEvent(TimestampMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("log_id", "log_revision", name="uq_outbox_log_revision"),
        ForeignKeyConstraint(["log_id", "user_id"], ["app.symptom_logs.id", "app.symptom_logs.user_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["log_id", "log_revision"], ["app.symptom_log_revisions.log_id", "app.symptom_log_revisions.revision"], ondelete="CASCADE"),
        CheckConstraint("log_revision > 0", name="log_revision_positive"),
        Index("ix_app_outbox_due", "available_at", postgresql_where=text("dispatched_at IS NULL")),
        {"schema": SCHEMA},
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.profiles.user_id", ondelete="CASCADE"), nullable=False)
    log_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    log_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="symptom_log.changed")
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dispatched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
