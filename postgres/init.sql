-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE TABLE IF NOT EXISTS public."characters"
(
    id bigint NOT NULL,
    name character varying(25) COLLATE pg_catalog."default",
    gender character varying(10) COLLATE pg_catalog."default",
    race character varying(25) COLLATE pg_catalog."default" NOT NULL,
    total_level smallint NOT NULL,
    classes jsonb NOT NULL,
    location jsonb NOT NULL,
    guild_name character varying(50) COLLATE pg_catalog."default",
    server_name character varying(25) COLLATE pg_catalog."default" NOT NULL,
    home_server_name character varying(25) COLLATE pg_catalog."default",
    is_anonymous boolean NOT NULL,
    last_updated timestamp with time zone NOT NULL,
    last_saved timestamp with time zone NOT NULL DEFAULT current_timestamp,
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
	timestamp TIMESTAMPTZ NOT NULL,
	id BIGINT NOT NULL,
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
    start_date timestamp with time zone NOT NULL,
    end_date timestamp with time zone NOT NULL,
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

CREATE TABLE IF NOT EXISTS public."quests"
(
    id integer NOT NULL,
    alt_id integer,
    area_id integer,
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

ALTER TABLE IF EXISTS public."areas"
    OWNER to pgadmin;
    CREATE INDEX idx_quests_group_size ON public."quests" (group_size);