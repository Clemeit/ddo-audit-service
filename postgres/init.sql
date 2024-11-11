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
    group_id bigint,
    is_in_party boolean NOT NULL,
    is_recruiting boolean NOT NULL,
    is_anonymous boolean NOT NULL,
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
    id bigint NOT NULL,
    total_level jsonb NOT NULL DEFAULT '[]'::jsonb,
    location jsonb NOT NULL DEFAULT '[]'::jsonb,
    guild_name jsonb NOT NULL DEFAULT '[]'::jsonb,
    server_name jsonb NOT NULL DEFAULT '[]'::jsonb,
    is_online jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT character_activity_pkey PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public."character_activity"
    OWNER to pgadmin;