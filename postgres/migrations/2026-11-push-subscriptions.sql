-- Web Push subscriptions: one row per browser/device a user turned push notifications on for.
--
-- init.sql only runs when the database volume is first created, and this repo has no migration tool.
-- Run this file by hand against an existing database (psql -U pgadmin -d <db> -f this_file.sql).
-- It is safe to run more than once. The same statements are at the end of init.sql for fresh databases;
-- if you change one, change the other.
--
-- Tables:
--   push_subscriptions  the browser's PushSubscription (endpoint + encryption keys). The endpoint is
--                       unique: a browser that logs in as someone else moves to that user. Rows are
--                       deleted when the push service says the subscription is gone (404/410) or after
--                       repeated failures.

CREATE TABLE IF NOT EXISTS public."push_subscriptions"
(
    id serial NOT NULL,
    user_id integer NOT NULL REFERENCES public."users"(id) ON DELETE CASCADE,
    endpoint text NOT NULL,
    p256dh text NOT NULL,
    auth text NOT NULL,
    user_agent text,
    failure_count integer NOT NULL DEFAULT 0,
    created_at timestamp with time zone NOT NULL DEFAULT current_timestamp,
    updated_at timestamp with time zone NOT NULL DEFAULT current_timestamp,
    last_success_at timestamp with time zone,
    PRIMARY KEY (id),
    UNIQUE (endpoint)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."push_subscriptions"
    OWNER to pgadmin;

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user
    ON public."push_subscriptions" (user_id);
