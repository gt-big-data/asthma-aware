"""Preserve immutable symptom exposures across downscaling pipeline versions."""
from alembic import op
import sqlalchemy as sa

revision = "0005_exposure_pipelines"
down_revision = "0004_saved_locations"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_exposures_log_revision", "exposure_snapshots", schema="app", type_="unique")
    op.create_unique_constraint("uq_exposures_log_revision_pipeline", "exposure_snapshots",
        ["log_id", "log_revision", "pipeline_version"], schema="app")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM app.exposure_snapshots GROUP BY log_id, log_revision HAVING count(*) > 1)")):
        raise RuntimeError("Multiple immutable exposure pipelines exist; downgrade would discard history")
    op.drop_constraint("uq_exposures_log_revision_pipeline", "exposure_snapshots", schema="app", type_="unique")
    op.create_unique_constraint("uq_exposures_log_revision", "exposure_snapshots", ["log_id", "log_revision"], schema="app")
