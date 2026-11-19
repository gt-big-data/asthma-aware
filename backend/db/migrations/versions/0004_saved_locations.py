"""Private owner-scoped Georgia saved cities.

Revision ID: 0004_saved_locations
Revises: 0003_personalization
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_saved_locations"
down_revision = "0003_personalization"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("saved_locations",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app.profiles.user_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("city_id", sa.String(7), primary_key=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("city_name", sa.String(160), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("city_id ~ '^13[0-9]{5}$'", name=op.f("ck_saved_locations_georgia_city_id")),
        sa.CheckConstraint("length(trim(label)) BETWEEN 1 AND 80", name=op.f("ck_saved_locations_label_valid")),
        sa.CheckConstraint("latitude BETWEEN 30 AND 36 AND longitude BETWEEN -86 AND -80", name=op.f("ck_saved_locations_coordinates_valid")),
        schema="app")
    op.execute("REVOKE ALL ON app.saved_locations FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.saved_locations TO asthmaaware_api")
    op.execute("ALTER TABLE app.saved_locations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.saved_locations FORCE ROW LEVEL SECURITY")
    owner = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
    op.execute(f"CREATE POLICY owner_access ON app.saved_locations TO asthmaaware_api USING ({owner}) WITH CHECK ({owner})")
    op.execute("""DO $$ DECLARE r text; BEGIN
      FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
          EXECUTE format('REVOKE ALL ON app.saved_locations FROM %I', r);
        END IF;
      END LOOP;
    END $$""")


def downgrade():
    op.drop_table("saved_locations", schema="app")
