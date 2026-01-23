-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE TABLE IF NOT EXISTS public."areas"
(
    id integer NOT NULL,
    name text NOT NULL,
    is_public boolean default true,
    is_wilderness boolean default false,
    region text,
    PRIMARY KEY (id)
)

TABLESPACE pg_default;

CREATE TABLE IF NOT EXISTS public."quests"
(
    id integer NOT NULL,
    alt_id integer,
    area_id integer REFERENCES public."areas"(id),
    name text NOT NULL,
    heroic_normal_cr smallint,
    epic_normal_cr smallint,
    is_free_to_vip boolean DEFAULT false,
    required_adventure_pack text,
    adventure_area text,
    quest_journal_area text,
    group_size text,
    patron text,
    xp jsonb,
    length smallint,
    tip text,
    PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."quests"
    OWNER to pgadmin;

ALTER TABLE IF EXISTS public."areas"
    OWNER to pgadmin;
    CREATE INDEX idx_quests_group_size ON public."quests" (group_size);

CREATE TABLE IF NOT EXISTS public."characters"
(
    id bigint NOT NULL,
    name text COLLATE pg_catalog."default",
    gender text COLLATE pg_catalog."default",
    race text COLLATE pg_catalog."default" NOT NULL,
    total_level smallint NOT NULL,
    classes jsonb NOT NULL,
    location_id integer REFERENCES public."areas"(id),
    guild_name text COLLATE pg_catalog."default",
    server_name text COLLATE pg_catalog."default" NOT NULL,
    home_server_name text COLLATE pg_catalog."default",
    is_anonymous boolean NOT NULL,
    last_update timestamp with time zone NOT NULL,
    last_save timestamp with time zone NOT NULL DEFAULT current_timestamp,
    auditing_flags jsonb,
    CONSTRAINT character_pkey PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."characters"
    OWNER to pgadmin;
    CREATE INDEX idx_name_server_name ON public."characters" (LOWER(name), LOWER(server_name));

CREATE TABLE IF NOT EXISTS public."character_report_status"
(
    character_id bigint PRIMARY KEY REFERENCES public."characters"(id) ON DELETE CASCADE,
    active boolean NOT NULL DEFAULT false,
    active_checked_at timestamp with time zone,
    updated_at timestamp with time zone NOT NULL DEFAULT current_timestamp
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."character_report_status"
    OWNER to pgadmin;

CREATE INDEX crs_active_idx ON public."character_report_status" (active);
CREATE INDEX crs_checked_at_idx ON public."character_report_status" (active_checked_at);

CREATE TABLE IF NOT EXISTS public."game_info"
(
    id serial NOT NULL,
    "timestamp" timestamp with time zone NOT NULL DEFAULT current_timestamp,
    data jsonb,
    PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."game_info"
    OWNER to pgadmin;

CREATE TABLE IF NOT EXISTS public."character_activity"
(
	"timestamp" timestamp with time zone NOT NULL,
	character_id BIGINT NOT NULL,
	activity_type TEXT,
	data jsonb
);

SELECT create_hypertable('character_activity', 'timestamp');

-- Add a retention policy to delete data older than 180 days
SELECT add_retention_policy('character_activity', INTERVAL '180 days');

CREATE INDEX ON public."character_activity" (character_id);

-- Index optimized for quest session worker queries using composite checkpoint
-- Supports tuple comparison for composite checkpoint: (timestamp, character_id) > (last_timestamp, max_character_id)
CREATE INDEX idx_character_activity_by_timestamp
ON public."character_activity" (timestamp, character_id)
WHERE activity_type IN ('location', 'status');

ALTER TABLE IF EXISTS public."character_activity"
    OWNER to pgadmin;

CREATE TABLE IF NOT EXISTS public."quest_sessions"
(
    id serial NOT NULL,
    character_id bigint NOT NULL REFERENCES public."characters"(id) ON DELETE CASCADE,
    quest_id integer NOT NULL REFERENCES public."quests"(id) ON DELETE CASCADE,
    entry_timestamp timestamp with time zone NOT NULL,
    exit_timestamp timestamp with time zone,
    duration_seconds numeric,
    created_at timestamp with time zone NOT NULL DEFAULT current_timestamp,
    entry_total_level integer,
    entry_classes jsonb,
    entry_group_id integer,
    PRIMARY KEY (id)
)
TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."quest_sessions"
    OWNER to pgadmin;

-- Unique constraint to ensure idempotent reprocessing of quest sessions
-- Unique index to ensure idempotent reprocessing of quest sessions
-- Same (character_id, quest_id, entry_timestamp, exit_timestamp) tuple won't be inserted twice
-- This allows safe reprocessing of activities without duplicate sessions
CREATE UNIQUE INDEX IF NOT EXISTS uq_quest_sessions_activity ON public."quest_sessions" (character_id, quest_id, entry_timestamp, exit_timestamp);

-- Indexes for quest_sessions
CREATE INDEX idx_quest_sessions_character_id ON public."quest_sessions" (character_id);
CREATE INDEX idx_quest_sessions_quest_id ON public."quest_sessions" (quest_id);
CREATE INDEX idx_quest_sessions_entry_timestamp ON public."quest_sessions" (entry_timestamp);
CREATE INDEX idx_quest_sessions_character_entry ON public."quest_sessions" (character_id, entry_timestamp);
CREATE INDEX idx_quest_sessions_quest_entry ON public."quest_sessions" (quest_id, entry_timestamp);
CREATE INDEX idx_quest_sessions_active ON public."quest_sessions" (character_id, quest_id) WHERE exit_timestamp IS NULL;

-- Optimized index for quest analytics queries that filter on completed sessions
-- This covers the most expensive queries: quest_id + entry_timestamp + exit_timestamp IS NOT NULL + duration_seconds IS NOT NULL
-- The INCLUDE clause adds duration_seconds as a covering index to avoid table lookups
CREATE INDEX idx_quest_sessions_analytics ON public."quest_sessions" (quest_id, entry_timestamp) 
    INCLUDE (duration_seconds, exit_timestamp)
    WHERE exit_timestamp IS NOT NULL AND duration_seconds IS NOT NULL;

-- Function to calculate duration_seconds when exit_timestamp is set
CREATE OR REPLACE FUNCTION calculate_quest_session_duration()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.exit_timestamp IS NOT NULL AND NEW.entry_timestamp IS NOT NULL THEN
        NEW.duration_seconds := EXTRACT(EPOCH FROM (NEW.exit_timestamp - NEW.entry_timestamp));
    ELSE
        NEW.duration_seconds := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically calculate duration_seconds
CREATE TRIGGER quest_session_duration_trigger
    BEFORE INSERT OR UPDATE ON public."quest_sessions"
    FOR EACH ROW
    EXECUTE FUNCTION calculate_quest_session_duration();

CREATE TABLE IF NOT EXISTS public."news"
(
    id serial NOT NULL,
    date timestamp with time zone NOT NULL DEFAULT current_timestamp,
    message text NOT NULL,
    PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."news"
    OWNER to pgadmin;

CREATE TABLE IF NOT EXISTS public."page_messages"
(
    id serial NOT NULL,
    message text NOT NULL,
    affected_pages jsonb NOT NULL,
    dismissable boolean NOT NULL DEFAULT false,
    type text NOT NULL DEFAULT 'info',
    start_date timestamp with time zone NOT NULL DEFAULT current_timestamp,
    end_date timestamp with time zone NOT NULL DEFAULT current_timestamp + INTERVAL '1 day',
    PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."page_messages"
    OWNER to pgadmin;

CREATE TABLE IF NOT EXISTS public."access_tokens"
(
    character_id bigint NOT NULL,
    access_token text NOT NULL,
    PRIMARY KEY (character_id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."access_tokens"
    OWNER to pgadmin;

CREATE TABLE IF NOT EXISTS public."feedback"
(
    id serial NOT NULL,
    date timestamp with time zone NOT NULL DEFAULT current_timestamp,
    message text,
    contact text,
    ticket text,
    user_id text,
    session_id text,
    commit_hash text,
    response text,
    resolved boolean NOT NULL DEFAULT false,
    PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."feedback"
    OWNER to pgadmin;

CREATE TABLE IF NOT EXISTS public."logs"
(
    id serial NOT NULL,
    message text NOT NULL,
    level text NOT NULL,
    "timestamp" timestamp with time zone NOT NULL DEFAULT current_timestamp,
    component text,
    action text,
    metadata jsonb,
    session_id text,
    user_id text,
    user_agent text,
    browser text,
    browser_version text,
    os text,
    screen_resolution text,
    viewport_size text,
    url text,
    page_title text,
    referrer text,
    route text,
    ip_address text,
    country text,
    is_internal boolean,
    commit_hash text,
    PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."logs"
    OWNER to pgadmin;

CREATE TABLE IF NOT EXISTS public."config"
(
    key text NOT NULL,
    value text,
    description text,
    is_enabled boolean NOT NULL DEFAULT true,
    created_date timestamp with time zone NOT NULL DEFAULT current_timestamp,
    modified_date timestamp with time zone NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (key)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."config"
    OWNER to pgadmin;

-- Note: removed index on non-existent column "category" to avoid init errors.

-- Create an index on is_enabled for quick feature toggles
CREATE INDEX idx_config_enabled ON public."config" (is_enabled);

CREATE TABLE IF NOT EXISTS public."quest_metrics"
(
    quest_id integer NOT NULL,
    heroic_xp_per_minute_relative numeric,
    epic_xp_per_minute_relative numeric,
    heroic_popularity_relative numeric,
    epic_popularity_relative numeric,
    analytics_data jsonb,
    updated_at timestamp with time zone NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (quest_id),
    CONSTRAINT fk_quest_metrics_quest FOREIGN KEY (quest_id) REFERENCES public."quests"(id) ON DELETE CASCADE
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."quest_metrics"
    OWNER to pgadmin;

CREATE INDEX idx_quest_metrics_updated_at ON public."quest_metrics" (updated_at);
