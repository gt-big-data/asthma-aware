"""Private asynchronous task status; no inference or personalized results."""
from alembic import op

revision = "0004_personalization_tasks"
down_revision = "0003_live_forecast"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='asthmaaware_worker') THEN
        CREATE ROLE asthmaaware_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='asthmaaware_worker'
        AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb)) THEN
        RAISE EXCEPTION 'Existing worker role has unsafe privileges';
      END IF;
    END $$""")
    op.execute("GRANT asthmaaware_worker TO CURRENT_USER")
    op.execute("""CREATE TABLE app.personalization_tasks (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES app.profiles(user_id) ON DELETE CASCADE,
      log_id uuid NOT NULL, log_revision integer NOT NULL CHECK(log_revision>0),
      available_at timestamptz NOT NULL, status text NOT NULL CHECK(status IN
        ('pending_dispatch','queued','blocked_ml_unavailable','completed','failed')),
      reason text, attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), cloud_task_name text,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(log_id,log_revision),
      FOREIGN KEY(log_id,user_id) REFERENCES app.symptom_logs(id,user_id) ON DELETE CASCADE,
      FOREIGN KEY(log_id,log_revision) REFERENCES app.symptom_log_revisions(log_id,revision) ON DELETE CASCADE
    )""")
    op.execute("CREATE INDEX ix_personalization_tasks_owner ON app.personalization_tasks(user_id,created_at DESC)")
    op.execute("REVOKE ALL ON app.personalization_tasks FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA app TO asthmaaware_worker")
    op.execute("GRANT SELECT,INSERT,UPDATE ON app.personalization_tasks TO asthmaaware_api")
    op.execute("GRANT SELECT,UPDATE ON app.personalization_tasks TO asthmaaware_worker")
    op.execute("ALTER TABLE app.personalization_tasks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.personalization_tasks FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tasks_owner ON app.personalization_tasks TO asthmaaware_api
      USING (user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
      WITH CHECK (user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)""")
    op.execute("CREATE POLICY tasks_worker ON app.personalization_tasks TO asthmaaware_worker USING(true) WITH CHECK(true)")
    op.execute("""DO $$ DECLARE r text; BEGIN
      FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
          EXECUTE format('REVOKE ALL ON app.personalization_tasks FROM %I',r);
        END IF;
      END LOOP;
    END $$""")


def downgrade():
    op.execute("DROP TABLE app.personalization_tasks")
    # Preserve cluster role, which may have externally provisioned login members.
