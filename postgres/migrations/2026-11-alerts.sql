-- Alerts: rules users set up to be notified about things ("an LFM for quest X was posted", and later
-- server status, shard exchange listings, friends logging in, ...).
--
-- init.sql only runs when the database volume is first created, and this repo has no migration tool.
-- Run this file by hand against an existing database (psql -U pgadmin -d <db> -f this_file.sql).
-- It is safe to run more than once. The same statements are at the end of init.sql for fresh databases;
-- if you change one, change the other.
--
-- Tables:
--   alert_rules  one row per rule. `type` names the alert type (see sanic/business/alerts/types) and
--                `criteria` holds that type's settings, validated by the type's pydantic model.

CREATE TABLE IF NOT EXISTS public."alert_rules"
(
    id serial NOT NULL,
    user_id integer NOT NULL REFERENCES public."users"(id) ON DELETE CASCADE,
    type text NOT NULL,
    criteria jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled boolean NOT NULL DEFAULT true,
    expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL DEFAULT current_timestamp,
    updated_at timestamp with time zone NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."alert_rules"
    OWNER to pgadmin;

CREATE INDEX IF NOT EXISTS idx_alert_rules_user
    ON public."alert_rules" (user_id);

CREATE INDEX IF NOT EXISTS idx_alert_rules_type_enabled
    ON public."alert_rules" (type) WHERE enabled;
