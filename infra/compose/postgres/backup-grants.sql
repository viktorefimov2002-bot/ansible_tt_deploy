-- Run as migration/schema owner in the selected database.
-- Create the non-superuser login/password separately via protected DBA tooling.
-- psql -v backup_role=<existing reader> -f backup-grants.sql
GRANT USAGE ON SCHEMA public TO :"backup_role";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"backup_role";
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO :"backup_role";
-- Default privileges are per object creator; run for the migration owner.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"backup_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO :"backup_role";
