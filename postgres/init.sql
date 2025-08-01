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
    name character varying(25) COLLATE pg_catalog."default",
    gender character varying(10) COLLATE pg_catalog."default",
    race character varying(25) COLLATE pg_catalog."default" NOT NULL,
    total_level smallint NOT NULL,
    classes jsonb NOT NULL,
    location_id integer REFERENCES public."areas"(id),
    guild_name character varying(50) COLLATE pg_catalog."default",
    server_name character varying(25) COLLATE pg_catalog."default" NOT NULL,
    home_server_name character varying(25) COLLATE pg_catalog."default",
    is_anonymous boolean NOT NULL,
    last_update timestamp with time zone NOT NULL,
    last_save timestamp with time zone NOT NULL DEFAULT current_timestamp,
    CONSTRAINT character_pkey PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."characters"
    OWNER to pgadmin;
    CREATE INDEX idx_name_server_name ON public."characters" (LOWER(name), LOWER(server_name));

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
	timestamp with time zone NOT NULL,
	character_id BIGINT NOT NULL,
	activity_type TEXT,
	data jsonb
);

SELECT create_hypertable('character_activity', 'timestamp');

-- Add a retention policy to delete data older than 90 days
SELECT add_retention_policy('character_activity', INTERVAL '90 days');

-- Add an index on the id column
CREATE INDEX ON public."character_activity" (id);

ALTER TABLE IF EXISTS public."character_activity"
    OWNER to pgadmin;

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
    timestamp timestamp with time zone NOT NULL DEFAULT current_timestamp,
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
    url text NOT NULL,
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
    value_type text NOT NULL DEFAULT 'string',
    description text,
    category text,
    is_enabled boolean NOT NULL DEFAULT true,
    created_date timestamp with time zone NOT NULL DEFAULT current_timestamp,
    updated_date timestamp with time zone NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (key)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."config"
    OWNER to pgadmin;

-- Create an index on category for faster filtering
CREATE INDEX idx_config_category ON public."config" (category);

-- Create an index on is_enabled for quick feature toggles
CREATE INDEX idx_config_enabled ON public."config" (is_enabled);
