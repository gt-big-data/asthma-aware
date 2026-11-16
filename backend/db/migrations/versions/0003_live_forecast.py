"""Shared latest NASA forecast, isolated from private symptom records."""
from alembic import op

revision = "0003_live_forecast"
down_revision = "0002_application"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA forecast")
    op.execute("REVOKE ALL ON SCHEMA forecast FROM PUBLIC")
    op.execute("""DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='asthmaaware_forecast_writer') THEN
        CREATE ROLE asthmaaware_forecast_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='asthmaaware_forecast_writer'
        AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb)) THEN
        RAISE EXCEPTION 'Existing forecast writer role has unsafe privileges';
      END IF;
    END $$""")
    op.execute("GRANT asthmaaware_forecast_writer TO CURRENT_USER")
    op.execute("""CREATE TABLE forecast.current (
      singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
      initialized_at timestamptz NOT NULL,
      payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
      published_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("REVOKE ALL ON forecast.current FROM PUBLIC")
    op.execute("""DO $$ DECLARE r text; BEGIN
      FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
          EXECUTE format('REVOKE ALL ON SCHEMA forecast FROM %I', r);
          EXECUTE format('REVOKE ALL ON forecast.current FROM %I', r);
        END IF;
      END LOOP;
    END $$""")
    op.execute("GRANT USAGE ON SCHEMA forecast TO asthmaaware_api, asthmaaware_forecast_writer")
    op.execute("GRANT SELECT ON forecast.current TO asthmaaware_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON forecast.current TO asthmaaware_forecast_writer")
    op.execute("ALTER TABLE forecast.current ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE forecast.current FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY forecast_read ON forecast.current FOR SELECT TO asthmaaware_api USING (true)")
    op.execute("CREATE POLICY forecast_publish ON forecast.current TO asthmaaware_forecast_writer USING (true) WITH CHECK (true)")


def downgrade():
    op.execute("DROP TABLE forecast.current")
    op.execute("DROP SCHEMA forecast")
    # Keep cluster-wide role: it may have externally provisioned login members.
