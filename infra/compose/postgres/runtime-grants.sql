-- Run as the schema owner with psql -v app_role=<existing runtime login>.
-- The login must not own the database/schema or inherit the migration role.
-- Reapply after later migrations deliberately add application tables.
GRANT USAGE ON SCHEMA public TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON
    public.admins, public.vpn_users, public.servers, public.server_access,
    public.devices, public.device_credentials, public.server_config_revisions,
    public.admin_sessions
    TO :"app_role";
GRANT SELECT, INSERT ON public.audit_events TO :"app_role";
