"""Allow owner-scoped derived personalization writes.

Revision ID: 0003_personalization
Revises: 0002_application
"""
from alembic import op

revision = "0003_personalization"
down_revision = "0002_application"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("GRANT INSERT ON app.exposure_snapshots, app.personalization_revisions TO asthmaaware_api")
    op.execute("GRANT UPDATE (dispatched_at, updated_at) ON app.outbox_events TO asthmaaware_api")


def downgrade():
    op.execute("REVOKE INSERT ON app.exposure_snapshots, app.personalization_revisions FROM asthmaaware_api")
    op.execute("REVOKE UPDATE (dispatched_at, updated_at) ON app.outbox_events FROM asthmaaware_api")
