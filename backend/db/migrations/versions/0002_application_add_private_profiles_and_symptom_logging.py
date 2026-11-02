"""add private profiles and symptom logging

Revision ID: 0002_application
Revises: 0001_initial_schema
Create Date: 2026-11-01 22:49:23.459886

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0002_application'
down_revision: Union[str, Sequence[str], None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA app")
    op.execute("REVOKE ALL ON SCHEMA app FROM PUBLIC")
    op.create_table('profiles',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_profiles')),
    schema='app'
    )
    op.create_table('personalization_revisions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('population_model_version', sa.String(length=128), nullable=False),
    sa.Column('feature_schema_version', sa.String(length=128), nullable=False),
    sa.Column('normalization_version', sa.String(length=128), nullable=False),
    sa.Column('weights', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('training_inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('revision > 0', name=op.f('ck_personalization_revisions_revision_positive')),
    sa.ForeignKeyConstraint(['user_id'], ['app.profiles.user_id'], name=op.f('fk_personalization_revisions_user_id_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_personalization_revisions')),
    sa.UniqueConstraint('user_id', 'revision', name='uq_personalization_user_revision'),
    schema='app'
    )
    op.create_table('symptom_logs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('log_date', sa.Date(), nullable=False),
    sa.Column('severity', sa.Integer(), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=False),
    sa.Column('longitude', sa.Float(), nullable=False),
    sa.Column('location_source', sa.String(length=16), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('finalizes_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("location_source IN ('current', 'selected')", name=op.f('ck_symptom_logs_location_source_valid')),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180', name=op.f('ck_symptom_logs_coordinates_valid')),
    sa.CheckConstraint('revision > 0', name=op.f('ck_symptom_logs_revision_positive')),
    sa.CheckConstraint('severity BETWEEN 1 AND 5', name=op.f('ck_symptom_logs_severity_range')),
    sa.ForeignKeyConstraint(['user_id'], ['app.profiles.user_id'], name=op.f('fk_symptom_logs_user_id_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_symptom_logs')),
    sa.UniqueConstraint('id', 'user_id', name='uq_symptom_logs_id_user'),
    sa.UniqueConstraint('user_id', 'log_date', name='uq_symptom_logs_user_date'),
    schema='app'
    )
    op.create_table('symptom_log_revisions',
    sa.Column('log_id', sa.Uuid(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('severity', sa.Integer(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('revision > 0 AND severity BETWEEN 1 AND 5', name=op.f('ck_symptom_log_revisions_revision_severity_valid')),
    sa.ForeignKeyConstraint(['log_id', 'user_id'], ['app.symptom_logs.id', 'app.symptom_logs.user_id'], name=op.f('fk_symptom_log_revisions_log_id_symptom_logs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('log_id', 'revision', name=op.f('pk_symptom_log_revisions')),
    schema='app'
    )
    op.create_table('exposure_snapshots',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('log_id', sa.Uuid(), nullable=False),
    sa.Column('log_revision', sa.Integer(), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('window_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('feature_schema_version', sa.String(length=128), nullable=False),
    sa.Column('pipeline_version', sa.String(length=128), nullable=False),
    sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('log_revision > 0', name=op.f('ck_exposure_snapshots_log_revision_positive')),
    sa.CheckConstraint('window_end > window_start', name=op.f('ck_exposure_snapshots_window_ordered')),
    sa.ForeignKeyConstraint(['log_id', 'log_revision'], ['app.symptom_log_revisions.log_id', 'app.symptom_log_revisions.revision'], name=op.f('fk_exposure_snapshots_log_id_symptom_log_revisions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['log_id', 'user_id'], ['app.symptom_logs.id', 'app.symptom_logs.user_id'], name=op.f('fk_exposure_snapshots_log_id_symptom_logs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['app.profiles.user_id'], name=op.f('fk_exposure_snapshots_user_id_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exposure_snapshots')),
    sa.UniqueConstraint('log_id', 'log_revision', name='uq_exposures_log_revision'),
    schema='app'
    )
    op.create_table('outbox_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('log_id', sa.Uuid(), nullable=False),
    sa.Column('log_revision', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('log_revision > 0', name=op.f('ck_outbox_events_log_revision_positive')),
    sa.ForeignKeyConstraint(['log_id', 'log_revision'], ['app.symptom_log_revisions.log_id', 'app.symptom_log_revisions.revision'], name=op.f('fk_outbox_events_log_id_symptom_log_revisions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['log_id', 'user_id'], ['app.symptom_logs.id', 'app.symptom_logs.user_id'], name=op.f('fk_outbox_events_log_id_symptom_logs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['app.profiles.user_id'], name=op.f('fk_outbox_events_user_id_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox_events')),
    sa.UniqueConstraint('log_id', 'log_revision', name='uq_outbox_log_revision'),
    schema='app'
    )
    op.create_index('ix_app_outbox_due', 'outbox_events', ['available_at'], unique=False, schema='app', postgresql_where=sa.text('dispatched_at IS NULL'))
    # Group role: the deployed login is provisioned separately, with membership
    # in this role. Passwords and Supabase-managed auth objects do not belong here.
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'asthmaaware_api') THEN
            CREATE ROLE asthmaaware_api NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'asthmaaware_api'
                     AND (rolsuper OR rolbypassrls OR rolcanlogin)) THEN
            RAISE EXCEPTION 'asthmaaware_api must be an unprivileged NOLOGIN group';
          END IF;
        END $$
    """)
    op.execute("GRANT asthmaaware_api TO CURRENT_USER")
    op.execute("GRANT USAGE ON SCHEMA app TO asthmaaware_api")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA app FROM PUBLIC")
    # Explicitly prevent access by Supabase Data API roles, if present.
    op.execute("""
        DO $$ DECLARE r text; BEGIN
          FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
              EXECUTE format('REVOKE ALL ON SCHEMA app FROM %I', r);
              EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA app FROM %I', r);
            END IF;
          END LOOP;
        END $$
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.profiles, app.symptom_logs TO asthmaaware_api")
    op.execute("GRANT SELECT, INSERT ON app.symptom_log_revisions, app.outbox_events TO asthmaaware_api")
    op.execute("GRANT SELECT ON app.exposure_snapshots, app.personalization_revisions TO asthmaaware_api")
    for table in ("profiles", "symptom_logs", "symptom_log_revisions", "outbox_events", "exposure_snapshots", "personalization_revisions"):
        op.execute(f'ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY')
        owner = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
        op.execute(f'CREATE POLICY owner_access ON app.{table} TO asthmaaware_api USING ({owner}) WITH CHECK ({owner})')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_app_outbox_due', table_name='outbox_events', schema='app', postgresql_where=sa.text('dispatched_at IS NULL'))
    op.drop_table('outbox_events', schema='app')
    op.drop_table('exposure_snapshots', schema='app')
    op.drop_table('symptom_log_revisions', schema='app')
    op.drop_table('symptom_logs', schema='app')
    op.drop_table('personalization_revisions', schema='app')
    op.drop_table('profiles', schema='app')
    op.execute("DROP SCHEMA app")
    # The group role may have external login members or be used by another DB.
    # Leave its lifecycle to the operator instead of dropping a cluster-wide role.
