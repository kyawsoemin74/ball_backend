--
-- PostgreSQL database dump
--

\restrict gzqcWFK1752POxtRndA0AFU0TzJBu4m4yZIl62PvPN1Zl3ifVxyMdQVuFF61GxC

-- Dumped from database version 17.9
-- Dumped by pg_dump version 17.9

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: avatarsource; Type: TYPE; Schema: public; Owner: fover_user
--

CREATE TYPE public.avatarsource AS ENUM (
    'default',
    'google',
    'upload'
);


ALTER TYPE public.avatarsource OWNER TO fover_user;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ad_configs; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.ad_configs (
    id integer NOT NULL,
    is_enabled boolean DEFAULT false NOT NULL,
    banner_android character varying(255),
    banner_ios character varying(255),
    interstitial_android character varying(255),
    interstitial_ios character varying(255),
    rewarded_android character varying(255),
    rewarded_ios character varying(255),
    app_open_android character varying(255),
    app_open_ios character varying(255),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone
);


ALTER TABLE public.ad_configs OWNER TO fover_user;

--
-- Name: ad_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.ad_configs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ad_configs_id_seq OWNER TO fover_user;

--
-- Name: ad_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.ad_configs_id_seq OWNED BY public.ad_configs.id;


--
-- Name: ads; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.ads (
    id integer NOT NULL,
    title character varying(255) NOT NULL,
    image_url character varying(500),
    link_url character varying(500),
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


ALTER TABLE public.ads OWNER TO fover_user;

--
-- Name: ads_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.ads_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ads_id_seq OWNER TO fover_user;

--
-- Name: ads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.ads_id_seq OWNED BY public.ads.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO fover_user;

--
-- Name: allowed_leagues; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.allowed_leagues (
    league_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.allowed_leagues OWNER TO fover_user;

--
-- Name: allowed_leagues_league_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.allowed_leagues_league_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.allowed_leagues_league_id_seq OWNER TO fover_user;

--
-- Name: allowed_leagues_league_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.allowed_leagues_league_id_seq OWNED BY public.allowed_leagues.league_id;


--
-- Name: coaches; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.coaches (
    coach_id integer NOT NULL,
    provider character varying(50) DEFAULT 'api-football'::character varying NOT NULL,
    provider_id character varying(100),
    name character varying(255) NOT NULL,
    normalized_name character varying(255) NOT NULL,
    nationality character varying(100),
    photo text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.coaches OWNER TO fover_user;

--
-- Name: coaches_coach_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.coaches_coach_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.coaches_coach_id_seq OWNER TO fover_user;

--
-- Name: coaches_coach_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.coaches_coach_id_seq OWNED BY public.coaches.coach_id;


--
-- Name: countries; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.countries (
    country_id integer NOT NULL,
    name character varying(255) NOT NULL,
    code character varying(10),
    flag character varying(500),
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.countries OWNER TO fover_user;

--
-- Name: countries_country_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.countries_country_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.countries_country_id_seq OWNER TO fover_user;

--
-- Name: countries_country_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.countries_country_id_seq OWNED BY public.countries.country_id;


--
-- Name: league_seasons; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.league_seasons (
    id integer NOT NULL,
    league_id integer NOT NULL,
    season character varying(20) NOT NULL,
    provider character varying(50),
    provider_id character varying(100),
    start_date timestamp with time zone,
    end_date timestamp with time zone,
    current boolean,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.league_seasons OWNER TO fover_user;

--
-- Name: league_seasons_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.league_seasons_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.league_seasons_id_seq OWNER TO fover_user;

--
-- Name: league_seasons_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.league_seasons_id_seq OWNED BY public.league_seasons.id;


--
-- Name: leagues; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.leagues (
    league_id integer NOT NULL,
    name character varying(255) NOT NULL,
    country character varying(255),
    logo character varying(500),
    season character varying(10),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    is_featured boolean DEFAULT false NOT NULL,
    display_order integer DEFAULT 999 NOT NULL,
    country_code character varying(20),
    provider character varying(50) DEFAULT 'api-football'::character varying NOT NULL,
    type character varying(50),
    "national" boolean,
    country_id integer
);


ALTER TABLE public.leagues OWNER TO fover_user;

--
-- Name: leagues_league_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.leagues_league_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.leagues_league_id_seq OWNER TO fover_user;

--
-- Name: leagues_league_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.leagues_league_id_seq OWNED BY public.leagues.league_id;


--
-- Name: lineup_refresh_state; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.lineup_refresh_state (
    match_id integer NOT NULL,
    last_refreshed_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.lineup_refresh_state OWNER TO fover_user;

--
-- Name: match_events; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.match_events (
    id integer NOT NULL,
    match_id integer NOT NULL,
    time_elapsed integer NOT NULL,
    time_extra integer,
    team_id integer NOT NULL,
    team_name character varying(255),
    player_id integer,
    player_name character varying(255),
    assist_id integer,
    assist_name character varying(255),
    type character varying(50) NOT NULL,
    detail character varying(255),
    comments text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.match_events OWNER TO fover_user;

--
-- Name: match_events_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.match_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.match_events_id_seq OWNER TO fover_user;

--
-- Name: match_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.match_events_id_seq OWNED BY public.match_events.id;


--
-- Name: match_h2h; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.match_h2h (
    id integer NOT NULL,
    h2h_key character varying(50) NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.match_h2h OWNER TO fover_user;

--
-- Name: match_h2h_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.match_h2h_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.match_h2h_id_seq OWNER TO fover_user;

--
-- Name: match_h2h_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.match_h2h_id_seq OWNED BY public.match_h2h.id;


--
-- Name: match_lineups; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.match_lineups (
    id integer NOT NULL,
    match_id integer NOT NULL,
    data json NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.match_lineups OWNER TO fover_user;

--
-- Name: match_lineups_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.match_lineups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.match_lineups_id_seq OWNER TO fover_user;

--
-- Name: match_lineups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.match_lineups_id_seq OWNED BY public.match_lineups.id;


--
-- Name: match_statistics; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.match_statistics (
    match_id integer NOT NULL,
    data jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.match_statistics OWNER TO fover_user;

--
-- Name: matches; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.matches (
    fixture_id integer NOT NULL,
    league_id integer NOT NULL,
    league_name character varying(255),
    league_logo character varying(500),
    country_name character varying(255),
    country_logo character varying(500),
    match_time timestamp with time zone NOT NULL,
    status character varying(10) NOT NULL,
    elapsed integer,
    home_team character varying(255) NOT NULL,
    home_team_logo character varying(500),
    away_team character varying(255) NOT NULL,
    away_team_logo character varying(500),
    home_score integer NOT NULL,
    away_score integer NOT NULL,
    venue_name character varying(255),
    venue_city character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    home_team_id integer,
    away_team_id integer,
    season integer
);


ALTER TABLE public.matches OWNER TO fover_user;

--
-- Name: matches_fixture_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.matches_fixture_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.matches_fixture_id_seq OWNER TO fover_user;

--
-- Name: matches_fixture_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.matches_fixture_id_seq OWNED BY public.matches.fixture_id;


--
-- Name: news; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.news (
    id integer NOT NULL,
    title character varying(255) NOT NULL,
    content text NOT NULL,
    category character varying(50) NOT NULL,
    published_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


ALTER TABLE public.news OWNER TO fover_user;

--
-- Name: news_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.news_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.news_id_seq OWNER TO fover_user;

--
-- Name: news_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.news_id_seq OWNED BY public.news.id;


--
-- Name: odds; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.odds (
    id integer NOT NULL,
    fixture_id integer NOT NULL,
    bookmaker_name character varying(255),
    market_name character varying(255) NOT NULL,
    selection character varying(255) NOT NULL,
    odd_value character varying(50) NOT NULL,
    last_updated timestamp with time zone DEFAULT now() NOT NULL,
    myanmar_odd character varying(20)
);


ALTER TABLE public.odds OWNER TO fover_user;

--
-- Name: odds_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.odds_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.odds_id_seq OWNER TO fover_user;

--
-- Name: odds_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.odds_id_seq OWNED BY public.odds.id;


--
-- Name: players; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.players (
    player_id integer NOT NULL,
    provider character varying(50) DEFAULT 'api-football'::character varying NOT NULL,
    provider_id character varying(100) NOT NULL,
    first_name character varying(100),
    last_name character varying(100),
    name character varying(255) NOT NULL,
    nationality character varying(100),
    birth_date timestamp without time zone,
    birth_place character varying(255),
    birth_country character varying(100),
    height integer,
    weight integer,
    "position" character varying(50),
    preferred_foot character varying(20),
    photo text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.players OWNER TO fover_user;

--
-- Name: players_player_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.players_player_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.players_player_id_seq OWNER TO fover_user;

--
-- Name: players_player_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.players_player_id_seq OWNED BY public.players.player_id;


--
-- Name: referees; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.referees (
    referee_id integer NOT NULL,
    provider character varying(50) DEFAULT 'api-football'::character varying NOT NULL,
    provider_id character varying(100),
    name character varying(255) NOT NULL,
    normalized_name character varying(255) NOT NULL,
    nationality character varying(100),
    photo text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.referees OWNER TO fover_user;

--
-- Name: referees_referee_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.referees_referee_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.referees_referee_id_seq OWNER TO fover_user;

--
-- Name: referees_referee_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.referees_referee_id_seq OWNED BY public.referees.referee_id;


--
-- Name: standings; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.standings (
    id integer NOT NULL,
    league_id integer NOT NULL,
    season character varying(10) NOT NULL,
    team_id integer NOT NULL,
    "position" integer NOT NULL,
    points integer NOT NULL,
    played integer NOT NULL,
    won integer NOT NULL,
    drawn integer NOT NULL,
    lost integer NOT NULL,
    goals_for integer NOT NULL,
    goals_against integer NOT NULL,
    goal_difference integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    team_name character varying(255),
    team_logo character varying(1024),
    group_name character varying(50),
    form character varying(20),
    description character varying(255)
);


ALTER TABLE public.standings OWNER TO fover_user;

--
-- Name: standings_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.standings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.standings_id_seq OWNER TO fover_user;

--
-- Name: standings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.standings_id_seq OWNED BY public.standings.id;


--
-- Name: teams; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.teams (
    team_id integer NOT NULL,
    name character varying(255) NOT NULL,
    country character varying(255),
    logo character varying(500),
    stadium character varying(255),
    founded integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    current_league_id integer,
    current_season character varying(10),
    provider character varying(50) DEFAULT 'api-football'::character varying NOT NULL,
    provider_id character varying(100),
    country_id integer
);


ALTER TABLE public.teams OWNER TO fover_user;

--
-- Name: teams_team_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.teams_team_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.teams_team_id_seq OWNER TO fover_user;

--
-- Name: teams_team_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.teams_team_id_seq OWNED BY public.teams.team_id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    email character varying(255) NOT NULL,
    hashed_password character varying(255) NOT NULL,
    role character varying(20) DEFAULT 'user'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    google_id character varying(255),
    display_name character varying(100),
    avatar_url character varying(2048),
    avatar_source public.avatarsource DEFAULT 'default'::public.avatarsource NOT NULL
);


ALTER TABLE public.users OWNER TO fover_user;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO fover_user;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: venues; Type: TABLE; Schema: public; Owner: fover_user
--

CREATE TABLE public.venues (
    venue_id integer NOT NULL,
    provider character varying(50) DEFAULT 'api-football'::character varying NOT NULL,
    provider_id character varying(100) NOT NULL,
    name character varying(255) NOT NULL,
    city character varying(255),
    country character varying(100),
    country_code character varying(2),
    capacity integer,
    surface character varying(50),
    image text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.venues OWNER TO fover_user;

--
-- Name: venues_venue_id_seq; Type: SEQUENCE; Schema: public; Owner: fover_user
--

CREATE SEQUENCE public.venues_venue_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.venues_venue_id_seq OWNER TO fover_user;

--
-- Name: venues_venue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: fover_user
--

ALTER SEQUENCE public.venues_venue_id_seq OWNED BY public.venues.venue_id;


--
-- Name: ad_configs id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.ad_configs ALTER COLUMN id SET DEFAULT nextval('public.ad_configs_id_seq'::regclass);


--
-- Name: ads id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.ads ALTER COLUMN id SET DEFAULT nextval('public.ads_id_seq'::regclass);


--
-- Name: allowed_leagues league_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.allowed_leagues ALTER COLUMN league_id SET DEFAULT nextval('public.allowed_leagues_league_id_seq'::regclass);


--
-- Name: coaches coach_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.coaches ALTER COLUMN coach_id SET DEFAULT nextval('public.coaches_coach_id_seq'::regclass);


--
-- Name: countries country_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.countries ALTER COLUMN country_id SET DEFAULT nextval('public.countries_country_id_seq'::regclass);


--
-- Name: league_seasons id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.league_seasons ALTER COLUMN id SET DEFAULT nextval('public.league_seasons_id_seq'::regclass);


--
-- Name: leagues league_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.leagues ALTER COLUMN league_id SET DEFAULT nextval('public.leagues_league_id_seq'::regclass);


--
-- Name: match_events id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_events ALTER COLUMN id SET DEFAULT nextval('public.match_events_id_seq'::regclass);


--
-- Name: match_h2h id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_h2h ALTER COLUMN id SET DEFAULT nextval('public.match_h2h_id_seq'::regclass);


--
-- Name: match_lineups id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_lineups ALTER COLUMN id SET DEFAULT nextval('public.match_lineups_id_seq'::regclass);


--
-- Name: matches fixture_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.matches ALTER COLUMN fixture_id SET DEFAULT nextval('public.matches_fixture_id_seq'::regclass);


--
-- Name: news id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.news ALTER COLUMN id SET DEFAULT nextval('public.news_id_seq'::regclass);


--
-- Name: odds id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.odds ALTER COLUMN id SET DEFAULT nextval('public.odds_id_seq'::regclass);


--
-- Name: players player_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.players ALTER COLUMN player_id SET DEFAULT nextval('public.players_player_id_seq'::regclass);


--
-- Name: referees referee_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.referees ALTER COLUMN referee_id SET DEFAULT nextval('public.referees_referee_id_seq'::regclass);


--
-- Name: standings id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.standings ALTER COLUMN id SET DEFAULT nextval('public.standings_id_seq'::regclass);


--
-- Name: teams team_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.teams ALTER COLUMN team_id SET DEFAULT nextval('public.teams_team_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: venues venue_id; Type: DEFAULT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.venues ALTER COLUMN venue_id SET DEFAULT nextval('public.venues_venue_id_seq'::regclass);


--
-- Data for Name: ad_configs; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.ad_configs (id, is_enabled, banner_android, banner_ios, interstitial_android, interstitial_ios, rewarded_android, rewarded_ios, app_open_android, app_open_ios, created_at, updated_at) FROM stdin;
2	t	validation-banner-android	validation-banner-ios	validation-interstitial-android	validation-interstitial-ios	validation-rewarded-android	validation-rewarded-ios	validation-app-open-android	validation-app-open-ios	2026-07-30 10:43:38.935227+06:30	\N
3	f	\N	\N	\N	\N	\N	\N	\N	\N	2026-07-30 10:50:12.058957+06:30	\N
4	t	validation-banner-android	validation-banner-ios	validation-interstitial-android	validation-interstitial-ios	validation-rewarded-android	validation-rewarded-ios	validation-app-open-android	validation-app-open-ios	2026-07-30 12:33:25.326399+06:30	\N
5	t	validation-banner-android	validation-banner-ios	validation-interstitial-android	validation-interstitial-ios	validation-rewarded-android	validation-rewarded-ios	validation-app-open-android	validation-app-open-ios	2026-07-30 12:44:33.725941+06:30	\N
6	t	validation-banner-android	validation-banner-ios	validation-interstitial-android	validation-interstitial-ios	validation-rewarded-android	validation-rewarded-ios	validation-app-open-android	validation-app-open-ios	2026-07-30 12:45:09.709475+06:30	\N
7	t	validation-banner-android	validation-banner-ios	validation-interstitial-android	validation-interstitial-ios	validation-rewarded-android	validation-rewarded-ios	validation-app-open-android	validation-app-open-ios	2026-07-30 12:47:07.61466+06:30	\N
8	t	validation-banner-android	validation-banner-ios	validation-interstitial-android	validation-interstitial-ios	validation-rewarded-android	validation-rewarded-ios	validation-app-open-android	validation-app-open-ios	2026-07-30 12:47:56.368303+06:30	\N
9	f	ca-app-pub-3940256099942544/6300978111	ca-app-pub-3940256099942544/2934735716	ca-app-pub-3940256099942544/1033173712	ca-app-pub-3940256099942544/4411468910	ca-app-pub-3940256099942544/5224354917	ca-app-pub-3940256099942544/1712485313	ca-app-pub-3940256099942544~3347511713	ca-app-pub-3940256099942544~1458002511	2026-07-30 12:50:47.692529+06:30	\N
\.


--
-- Data for Name: ads; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.ads (id, title, image_url, link_url, active, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.alembic_version (version_num) FROM stdin;
c5d6e7f8a9b0
\.


--
-- Data for Name: allowed_leagues; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.allowed_leagues (league_id, created_at) FROM stdin;
71	2026-07-30 10:09:42.69383+06:30
\.


--
-- Data for Name: coaches; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.coaches (coach_id, provider, provider_id, name, normalized_name, nationality, photo, created_at, updated_at) FROM stdin;
1	api-football	129	Rogério Ceni	rogerio ceni	Brazil	https://media.api-sports.io/football/coachs/129.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
2	api-football	3056	Cláudio Prates	claudio prates	Brazil	https://media.api-sports.io/football/coachs/3056.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
3	api-football	7590	Bruno Lopes	bruno lopes	Portugal	https://media.api-sports.io/football/coachs/7590.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
4	api-football	10313	Preto Casagrande	preto casagrande	Brazil	https://media.api-sports.io/football/coachs/10313.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
5	api-football	25694	Ceni Rogerio	ceni rogerio	\N	https://media.api-sports.io/football/coachs/25694.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
6	api-football	2082	Roger Machado	roger machado	Brazil	https://media.api-sports.io/football/coachs/2082.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
7	api-football	10396	Falcão	falcao	\N	https://media.api-sports.io/football/coachs/10396.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
8	api-football	15576	Pablo Fernandez	pablo fernandez	Brazil	https://media.api-sports.io/football/coachs/15576.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
9	api-football	25744	Paulo Pezzolano	paulo pezzolano	\N	https://media.api-sports.io/football/coachs/25744.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
10	api-football	2085	Bruno Lazaroni	bruno lazaroni	Brazil	https://media.api-sports.io/football/coachs/2085.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
11	api-football	2813	Renato Paiva	renato paiva	Portugal	https://media.api-sports.io/football/coachs/2813.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
12	api-football	2820	Ricardo Resende	ricardo resende	Brazil	https://media.api-sports.io/football/coachs/2820.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
13	api-football	13244	Flávio Tenius	flavio tenius	Brazil	https://media.api-sports.io/football/coachs/13244.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
14	api-football	15178	Carlos Leiria	carlos leiria	Brazil	https://media.api-sports.io/football/coachs/15178.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
15	api-football	25742	Martin Anselmi	martin anselmi	\N	https://media.api-sports.io/football/coachs/25742.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
16	api-football	28286	Pablo de Muner	pablo de muner	\N	https://media.api-sports.io/football/coachs/28286.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
17	api-football	28348	Rodrigo Dias Bellao	rodrigo dias bellao	\N	https://media.api-sports.io/football/coachs/28348.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
18	api-football	28558	Franclim Carvalho	franclim carvalho	\N	https://media.api-sports.io/football/coachs/28558.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
19	api-football	1103	Abel Ferreira	abel ferreira	Portugal	https://media.api-sports.io/football/coachs/1103.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
20	api-football	7371	Andrey Lopes	andrey lopes	Brazil	https://media.api-sports.io/football/coachs/7371.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
21	api-football	141	Renato Gaúcho	renato gaucho	Brazil	https://media.api-sports.io/football/coachs/141.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
22	api-football	143	Abel Braga	abel braga	Brazil	https://media.api-sports.io/football/coachs/143.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
23	api-football	846	L. Zubeldía	l. zubeldia	Argentina	https://media.api-sports.io/football/coachs/846.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
24	api-football	1643	Oswaldo de Oliveira	oswaldo de oliveira	Brazil	https://media.api-sports.io/football/coachs/1643.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
25	api-football	2201	Marcão	marcao	Brazil	https://media.api-sports.io/football/coachs/2201.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
26	api-football	27998	Machado Roger	machado roger	\N	https://media.api-sports.io/football/coachs/27998.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
27	api-football	2579	Marcelo Salles	marcelo salles	Brazil	https://media.api-sports.io/football/coachs/2579.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
28	api-football	10314	Jayme de Almeida	jayme de almeida	Brazil	https://media.api-sports.io/football/coachs/10314.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
29	api-football	23648	Filipe Luís	filipe luis	Brazil	https://media.api-sports.io/football/coachs/23648.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
31	api-football	852	J. Vojvoda	j. vojvoda	Argentina	https://media.api-sports.io/football/coachs/852.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
32	api-football	1686	Pedro Caixinha	pedro caixinha	Portugal	https://media.api-sports.io/football/coachs/1686.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
33	api-football	3052	Serginho Chulapa	serginho chulapa	Brazil	https://media.api-sports.io/football/coachs/3052.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
34	api-football	14736	Leandro Zago	leandro zago	Brazil	https://media.api-sports.io/football/coachs/14736.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
35	api-football	24955	César Sampaio	cesar sampaio	Brazil	https://media.api-sports.io/football/coachs/24955.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
36	api-football	25051	Cléber Xavier	cleber xavier	Brazil	https://media.api-sports.io/football/coachs/25051.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
38	api-football	2083	Mano Menezes	mano menezes	Brazil	https://media.api-sports.io/football/coachs/2083.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
39	api-football	9646	James Freitas	james freitas	Brazil	https://media.api-sports.io/football/coachs/9646.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
40	api-football	16544	Cesinha	cesinha	Brazil	https://media.api-sports.io/football/coachs/16544.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
41	api-football	25743	Luis Castro	luis castro	\N	https://media.api-sports.io/football/coachs/25743.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
42	api-football	128	Vanderlei Luxemburgo	vanderlei luxemburgo	Brazil	https://media.api-sports.io/football/coachs/128.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
43	api-football	136	Fernando Diniz	fernando diniz	Brazil	https://media.api-sports.io/football/coachs/136.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
44	api-football	3059	Dorival Júnior	dorival junior	Brazil	https://media.api-sports.io/football/coachs/3059.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
45	api-football	3370	R. Díaz	r. diaz	Argentina	https://media.api-sports.io/football/coachs/3370.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
46	api-football	16599	Fernando Lázaro	fernando lazaro	Brazil	https://media.api-sports.io/football/coachs/16599.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
47	api-football	18467	Orlando Ribeiro	orlando ribeiro	Brazil	https://media.api-sports.io/football/coachs/18467.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
48	api-football	19893	Danilo	danilo	Brazil	https://media.api-sports.io/football/coachs/19893.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
49	api-football	22626	Raphael Laruccia	raphael laruccia	Brazil	https://media.api-sports.io/football/coachs/22626.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
50	api-football	234	Bolívar	bolivar	Brazil	https://media.api-sports.io/football/coachs/234.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
51	api-football	3063	Gilmar Dall Pozzo	gilmar dall pozzo	Brazil	https://media.api-sports.io/football/coachs/3063.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
52	api-football	9801	Caio Júnior	caio junior	\N	https://media.api-sports.io/football/coachs/9801.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
53	api-football	14485	Yan	yan	Brazil	https://media.api-sports.io/football/coachs/14485.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
54	api-football	27360	Lacerda Rafael	lacerda rafael	\N	https://media.api-sports.io/football/coachs/27360.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
55	api-football	137	Fábio Carille	fabio carille	Brazil	https://media.api-sports.io/football/coachs/137.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
56	api-football	2106	Pedro Emanuel	pedro emanuel	Portugal	https://media.api-sports.io/football/coachs/2106.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
57	api-football	2577	Marcos Valadares	marcos valadares	Brazil	https://media.api-sports.io/football/coachs/2577.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
58	api-football	14735	Felipe	felipe	Brazil	https://media.api-sports.io/football/coachs/14735.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
59	api-football	17432	Emílio	emilio	Brazil	https://media.api-sports.io/football/coachs/17432.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
60	api-football	22397	Rafael Paiva	rafael paiva	Brazil	https://media.api-sports.io/football/coachs/22397.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
61	api-football	27955	Gaucho Renato	gaucho renato	\N	https://media.api-sports.io/football/coachs/27955.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
62	api-football	126	Odair Hellmann	odair hellmann	Brazil	https://media.api-sports.io/football/coachs/126.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
63	api-football	232	Maurício Barbieri	mauricio barbieri	Brazil	https://media.api-sports.io/football/coachs/232.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
64	api-football	18085	L. González	l. gonzalez	Argentina	https://media.api-sports.io/football/coachs/18085.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
65	api-football	18810	Juca	juca	Brazil	https://media.api-sports.io/football/coachs/18810.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
66	api-football	25104	Joao Correia	joao correia	Portugal	https://media.api-sports.io/football/coachs/28644.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
30	api-football	27985	Leonardo Jardim	leonardo jardim	Portugal	https://media.api-sports.io/football/coachs/22.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
67	api-football	333	Tite	tite	Brazil	https://media.api-sports.io/football/coachs/333.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
68	api-football	26601	Jorge Artur	jorge artur	\N	https://media.api-sports.io/football/coachs/26601.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
69	api-football	240	Carlos Amadeu	carlos amadeu	Brazil	https://media.api-sports.io/football/coachs/240.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
70	api-football	241	Thiago Carpini	thiago carpini	Brazil	https://media.api-sports.io/football/coachs/241.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
71	api-football	245	Geninho	geninho	Brazil	https://media.api-sports.io/football/coachs/245.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
72	api-football	2416	Carpegiani	carpegiani	Brazil	https://media.api-sports.io/football/coachs/2416.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
73	api-football	10315	Flavio Tanajura	flavio tanajura	\N	https://media.api-sports.io/football/coachs/10315.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
74	api-football	10319	D. Petkovic	d. petkovic	Serbia	https://media.api-sports.io/football/coachs/10319.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
75	api-football	25777	Ventura Jair	ventura jair	\N	https://media.api-sports.io/football/coachs/25777.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
76	api-football	217	Jorginho	jorginho	Brazil	https://media.api-sports.io/football/coachs/217.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
77	api-football	2819	Robson Gomes	robson gomes	Brazil	https://media.api-sports.io/football/coachs/2819.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
78	api-football	10316	Pachequinho	pachequinho	Brazil	https://media.api-sports.io/football/coachs/10316.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
79	api-football	10397	Marcio Goiano	marcio goiano	\N	https://media.api-sports.io/football/coachs/10397.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
80	api-football	13746	Mozart	mozart	\N	https://media.api-sports.io/football/coachs/13746.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
81	api-football	20175	Thiago Kosloski	thiago kosloski	Brazil	https://media.api-sports.io/football/coachs/20175.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
82	api-football	25692	Seabra Fernando	seabra fernando	\N	https://media.api-sports.io/football/coachs/25692.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
83	api-football	2423	Vágner Mancini	vagner mancini	Brazil	https://media.api-sports.io/football/coachs/2423.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
84	api-football	16570	Fernando Seabra	fernando seabra	Brazil	https://media.api-sports.io/football/coachs/16570.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
85	api-football	2244	Luiz Felipe Scolari	luiz felipe scolari	Brazil	https://media.api-sports.io/football/coachs/2244.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
37	api-football	28282	Cuca	cuca	Brazil	https://media.api-sports.io/football/coachs/2417.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
86	api-football	14037	Lucas Gonçalves	lucas goncalves	Brazil	https://media.api-sports.io/football/coachs/14037.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
87	api-football	25668	Eduardo Dominguez	eduardo dominguez	\N	https://media.api-sports.io/football/coachs/25668.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
88	api-football	140	Rodrigo Santana	rodrigo santana	Brazil	https://media.api-sports.io/football/coachs/140.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
89	api-football	3068	Daniel Paulista	daniel paulista	Brazil	https://media.api-sports.io/football/coachs/3068.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
90	api-football	5163	Paulo Bonamigo	paulo bonamigo	Brazil	https://media.api-sports.io/football/coachs/5163.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
91	api-football	25886	Juan Osorio	juan osorio	\N	https://media.api-sports.io/football/coachs/25886.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
92	api-football	27997	Conde Leo	conde leo	\N	https://media.api-sports.io/football/coachs/27997.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
93	api-football	14040	Rafael Guanaes	rafael guanaes	Brazil	https://media.api-sports.io/football/coachs/14040.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
94	api-football	24574	Ivan Baitello	ivan baitello	Brazil	https://media.api-sports.io/football/coachs/24574.png	2026-08-16 18:48:14.481181+06:30	2026-08-16 18:48:29.497452+06:30
\.


--
-- Data for Name: countries; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.countries (country_id, name, code, flag, created_at, updated_at) FROM stdin;
1851761881	Brazil	\N	\N	2026-08-15 22:15:04.199771+06:30	2026-08-15 22:15:04.199771+06:30
1955094987	England	GB	\N	2026-08-15 22:15:04.199771+06:30	2026-08-15 22:15:04.199771+06:30
\.


--
-- Data for Name: league_seasons; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.league_seasons (id, league_id, season, provider, provider_id, start_date, end_date, current, created_at, updated_at) FROM stdin;
1	39	2026	api-football	39	\N	\N	t	2026-08-15 22:36:29.600346+06:30	2026-08-15 22:36:29.600346+06:30
2	71	2026	api-football	71	\N	\N	t	2026-08-15 22:36:29.600346+06:30	2026-08-15 22:36:29.600346+06:30
\.


--
-- Data for Name: leagues; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.leagues (league_id, name, country, logo, season, created_at, updated_at, is_featured, display_order, country_code, provider, type, "national", country_id) FROM stdin;
39	Premier League	England	https://media.api-sports.io/football/leagues/39.png	2026	2026-07-30 10:05:00.672157+06:30	2026-08-15 22:24:59.263022+06:30	f	999	GB	api-football	\N	\N	1955094987
71	Serie A	Brazil	https://media.api-sports.io/football/leagues/71.png	2026	2026-07-30 10:07:04.487869+06:30	2026-08-15 22:24:59.263022+06:30	f	999	BZ	api-football	\N	\N	1851761881
\.


--
-- Data for Name: lineup_refresh_state; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.lineup_refresh_state (match_id, last_refreshed_at, updated_at) FROM stdin;
\.


--
-- Data for Name: match_events; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.match_events (id, match_id, time_elapsed, time_extra, team_id, team_name, player_id, player_name, assist_id, assist_name, type, detail, comments, created_at, updated_at) FROM stdin;
1	1492315	11	\N	124	Fluminense	10017	Ignacio	\N	\N	Card	Yellow Card	Tripping	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.506199+06:30
2	1492315	42	\N	124	Fluminense	6337	J. Freytes	259	Thiago Silva	subst	Substitution 1	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.506529+06:30
3	1492315	49	\N	118	Bahia	303127	Erick Pulga	\N	\N	Card	Yellow Card	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.506624+06:30
4	1492315	62	\N	124	Fluminense	50763	L. Acosta	51214	J. Savarino	subst	Substitution 2	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.506704+06:30
5	1492315	62	\N	124	Fluminense	259	Thiago Silva	9872	Igor Rabello	subst	Substitution 3	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.506785+06:30
6	1492315	62	\N	124	Fluminense	280245	Martinelli	266267	Hercules	subst	Substitution 4	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.506857+06:30
7	1492315	65	\N	118	Bahia	9854	Ademir	10168	Everton Ribeiro	subst	Substitution 1	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.506927+06:30
8	1492315	65	\N	118	Bahia	197383	Luciano Juba	51701	Michel Araujo	subst	Substitution 2	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.507023+06:30
9	1492315	74	\N	118	Bahia	9994	Jean Lucas	510204	David Martins	subst	Substitution 3	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.507089+06:30
10	1492315	75	\N	118	Bahia	303127	Erick Pulga	525667	Kaue Junior	subst	Substitution 4	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.507154+06:30
11	1492315	75	\N	124	Fluminense	12705	Hulk	13523	G. Cano	subst	Substitution 5	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.507234+06:30
12	1492315	88	\N	118	Bahia	311344	A. Veliz	47319	Willian Jose	subst	Substitution 5	\N	2026-07-30 10:10:44.913211+06:30	2026-07-30 10:10:47.507318+06:30
13	1492319	14	\N	121	Palmeiras	9218	Marlon Freitas	\N	\N	Card	Yellow Card	Roughing	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.10816+06:30
14	1492319	27	\N	121	Palmeiras	106485	Mauricio	\N	\N	Card	Yellow Card	Elbowing	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108331+06:30
15	1492319	32	\N	136	Vitoria	16637	Emmanuel Martinez	\N	\N	Card	Yellow Card	Tripping	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108406+06:30
16	1492319	37	\N	136	Vitoria	10085	Caca	\N	\N	Card	Red Card	Tripping	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108474+06:30
17	1492319	40	\N	136	Vitoria	9906	Luan Candido	\N	\N	Card	Red Card	Unsportsmanlike conduct	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108541+06:30
18	1492319	41	\N	136	Vitoria	16637	Emmanuel Martinez	288230	Ze Vitor	subst	Substitution 1	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108605+06:30
19	1492319	45	1	121	Palmeiras	106485	Mauricio	13708	J. Arias	Goal	Normal Goal	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.10867+06:30
20	1492319	45	6	121	Palmeiras	187920	Fabiano	\N	\N	Goal	Own Goal	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108732+06:30
21	1492319	46	\N	136	Vitoria	442404	Rene	6242	T. Pochettino	subst	Substitution 2	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108795+06:30
22	1492319	46	\N	121	Palmeiras	2502	G. Gomez	295513	J. Lopez	subst	Substitution 1	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108858+06:30
23	1492319	46	\N	121	Palmeiras	9218	Marlon Freitas	153083	E. Martinez	subst	Substitution 2	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.10892+06:30
24	1492319	46	\N	121	Palmeiras	106485	Mauricio	10125	Khellven	subst	Substitution 3	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.108982+06:30
25	1492319	46	\N	136	Vitoria	114436	Matheuzinho	455047	D. Tarzia	subst	Substitution 3	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.109042+06:30
26	1492319	56	\N	136	Vitoria	187920	Fabiano	\N	\N	Card	Yellow Card	Tripping	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.109101+06:30
27	1492319	65	\N	121	Palmeiras	5933	A. Barboza	21095	Lucas Evangelista	subst	Substitution 4	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.109166+06:30
28	1492319	66	\N	136	Vitoria	6078	E. Britez	\N	\N	Card	Yellow Card	Holding	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.109224+06:30
29	1492319	67	\N	136	Vitoria	187920	Fabiano	41357	Jamerson	subst	Substitution 4	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.109282+06:30
30	1492319	70	\N	121	Palmeiras	13708	J. Arias	51466	J. Piquerez	Goal	Normal Goal	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.10934+06:30
31	1492319	74	\N	121	Palmeiras	13708	J. Arias	340279	Vitor Roque	subst	Substitution 5	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.109395+06:30
32	1492319	74	\N	136	Vitoria	9460	Baralhas	25387	Walace	subst	Substitution 5	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.109449+06:30
33	1492319	84	\N	121	Palmeiras	196298	R. Sosa	\N	\N	Goal	Normal Goal	\N	2026-07-30 10:10:47.543797+06:30	2026-07-30 10:10:50.109503+06:30
34	1492316	27	\N	119	Internacional	10147	Vitinho	\N	\N	Goal	Normal Goal	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.653883+06:30
35	1492316	46	\N	127	Flamengo	10124	Leo Pereira	618	Danilo	subst	Substitution 1	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.655328+06:30
36	1492316	46	\N	127	Flamengo	332654	Evertton Araujo	2560	E. Pulgar	subst	Substitution 2	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.655653+06:30
37	1492316	63	\N	127	Flamengo	5994	J. Carrascal	10180	Bruno Henrique	subst	Substitution 3	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.655914+06:30
38	1492316	64	\N	127	Flamengo	860	Alex Sandro	1566	Emerson Royal	subst	Substitution 4	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.656155+06:30
39	1492316	64	\N	127	Flamengo	9909	Vitao	\N	\N	Card	Yellow Card	Tripping	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.656798+06:30
40	1492316	68	\N	119	Internacional	9921	Bruno Henrique	96353	Paulinho Paula	subst	Substitution 1	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.657221+06:30
41	1492316	68	\N	119	Internacional	2553	G. Maripan	9912	Juninho	subst	Substitution 2	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.658052+06:30
42	1492316	79	\N	127	Flamengo	126936	Samuel Lino	10180	Bruno Henrique	Goal	Normal Goal	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.658323+06:30
43	1492316	81	\N	119	Internacional	692	Alan Patrick	266658	Calebe	subst	Substitution 3	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.658533+06:30
44	1492316	84	\N	119	Internacional	13691	J. Carbonero	9893	Alerrandro	subst	Substitution 4	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.658726+06:30
45	1492316	84	\N	119	Internacional	6519	R. Villagra	63964	F. Torres	subst	Substitution 5	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.658921+06:30
46	1492316	84	\N	127	Flamengo	2289	Jorginho	5995	N. de la Cruz	subst	Substitution 5	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.659113+06:30
47	1492316	90	4	127	Flamengo	2560	E. Pulgar	\N	\N	Card	Yellow Card	Elbowing	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.659332+06:30
48	1492316	90	5	119	Internacional	2044	G. Mercado	\N	\N	Card	Yellow Card	Unsportsmanlike conduct	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.659801+06:30
49	1492316	90	7	127	Flamengo	5995	N. de la Cruz	\N	\N	Card	Yellow Card	\N	2026-07-30 10:11:12.594089+06:30	2026-07-30 10:11:13.660001+06:30
50	1492317	17	\N	7848	Mirassol	156191	Igor Formiga	\N	\N	Card	Yellow Card	Tripping	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.047886+06:30
51	1492317	21	\N	7848	Mirassol	357888	Gabriel Knesowitsch	\N	\N	Card	Yellow Card	Holding	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.04798+06:30
52	1492317	30	\N	7848	Mirassol	10232	Marllon	\N	\N	Goal	Own Goal	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048021+06:30
53	1492317	45	1	1198	Remo	5211	L. Picco	\N	\N	Goal	Normal Goal	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048058+06:30
54	1492317	46	\N	7848	Mirassol	357888	Gabriel Knesowitsch	80654	Willian Machado	subst	Substitution 1	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048094+06:30
55	1492317	54	\N	7848	Mirassol	115525	Joao Victor	9946	Reinaldo	Goal	Normal Goal	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.04813+06:30
56	1492317	59	\N	1198	Remo	268877	Marcelinho	9709	Matheus Alexandre	subst	Substitution 1	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048164+06:30
57	1492317	59	\N	1198	Remo	10581	Yago Pikachu	54894	Jaja	subst	Substitution 2	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048198+06:30
58	1492317	60	\N	7848	Mirassol	9620	Gustavo Silva	10025	Shaylon	subst	Substitution 2	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048234+06:30
59	1492317	72	\N	1198	Remo	9709	Matheus Alexandre	\N	\N	Card	Yellow Card	Delay of game	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048268+06:30
60	1492317	72	\N	1198	Remo	10114	Ze Ivaldo	\N	\N	Card	Yellow Card	Holding	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048301+06:30
61	1492317	75	\N	1198	Remo	10310	Ze Ricardo	355193	David Braga	subst	Substitution 3	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048335+06:30
62	1492317	77	\N	7848	Mirassol	44348	Eduardo	96385	G. Cazonatti	subst	Substitution 3	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048368+06:30
63	1492317	81	\N	1198	Remo	96343	Alef Manga	10141	Gabriel Poveda	subst	Substitution 4	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.0484+06:30
64	1492317	81	\N	1198	Remo	80273	Gabriel Taliari	9962	Vitor Bueno	subst	Substitution 5	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048434+06:30
65	1492317	85	\N	7848	Mirassol	54238	Edson Carioca	520668	Carlos Eduardo	subst	Substitution 4	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048466+06:30
66	1492317	85	\N	7848	Mirassol	405353	Japa	80474	Chico Kim	subst	Substitution 5	\N	2026-07-30 10:11:13.700264+06:30	2026-07-30 10:11:16.048499+06:30
\.


--
-- Data for Name: match_h2h; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.match_h2h (id, h2h_key, data, updated_at) FROM stdin;
\.


--
-- Data for Name: match_lineups; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.match_lineups (id, match_id, data, updated_at) FROM stdin;
\.


--
-- Data for Name: match_statistics; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.match_statistics (match_id, data, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: matches; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.matches (fixture_id, league_id, league_name, league_logo, country_name, country_logo, match_time, status, elapsed, home_team, home_team_logo, away_team, away_team_logo, home_score, away_score, venue_name, venue_city, created_at, updated_at, home_team_id, away_team_id, season) FROM stdin;
1492315	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-30 07:00:00+06:30	FT	90	Fluminense	https://media.api-sports.io/football/teams/124.png	Bahia	https://media.api-sports.io/football/teams/118.png	0	0	Estádio do Maracanã	Rio de Janeiro	2026-07-30 10:10:43.098545+06:30	\N	124	118	2026
1492319	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-30 07:00:00+06:30	FT	90	Vitoria	https://media.api-sports.io/football/teams/136.png	Palmeiras	https://media.api-sports.io/football/teams/121.png	0	4	Estádio Manoel Barradas	Salvador	2026-07-30 10:10:47.529124+06:30	\N	136	121	2026
1492313	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-31 05:00:00+06:30	NS	0	Corinthians	https://media.api-sports.io/football/teams/131.png	Atletico Paranaense	https://media.api-sports.io/football/teams/134.png	0	0	Neo Quimica Arena	Sao Paulo	2026-07-30 10:10:50.121529+06:30	\N	131	134	2026
1492310	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-30 02:30:00+06:30	PST	0	Atletico-MG	https://media.api-sports.io/football/teams/1062.png	RB Bragantino	https://media.api-sports.io/football/teams/794.png	0	0	MRV Arena	Belo Horizonte	2026-07-30 10:11:03.697727+06:30	\N	1062	794	2026
1492311	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-30 02:30:00+06:30	PST	0	Botafogo	https://media.api-sports.io/football/teams/120.png	Gremio	https://media.api-sports.io/football/teams/130.png	0	0	Estadio Olimpico Nilton Santos	Rio de Janeiro	2026-07-30 10:11:08.187637+06:30	\N	120	130	2026
1492312	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-30 02:30:00+06:30	PST	0	Chapecoense-sc	https://media.api-sports.io/football/teams/132.png	Vasco DA Gama	https://media.api-sports.io/football/teams/133.png	0	0	\N	\N	2026-07-30 10:11:09.261029+06:30	\N	132	133	2026
1492318	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-30 02:30:00+06:30	PST	0	Sao Paulo	https://media.api-sports.io/football/teams/126.png	Santos	https://media.api-sports.io/football/teams/128.png	0	0	Estadio Do MorumBIS	Sao Paulo	2026-07-30 10:11:11.465909+06:30	\N	126	128	2026
1492316	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-30 05:00:00+06:30	FT	90	Internacional	https://media.api-sports.io/football/teams/119.png	Flamengo	https://media.api-sports.io/football/teams/127.png	1	1	Estádio Beira-Rio	Porto Alegre	2026-07-30 10:11:12.581774+06:30	\N	119	127	2026
1492317	71	Serie A	https://media.api-sports.io/football/leagues/71.png	Brazil	https://media.api-sports.io/flags/br.svg	2026-07-30 05:00:00+06:30	FT	90	Mirassol	https://media.api-sports.io/football/teams/7848.png	Remo	https://media.api-sports.io/football/teams/1198.png	2	1	Estádio José Maria de Campos Maia	Mirassol	2026-07-30 10:11:13.682898+06:30	\N	7848	1198	2026
\.


--
-- Data for Name: news; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.news (id, title, content, category, published_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: odds; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.odds (id, fixture_id, bookmaker_name, market_name, selection, odd_value, last_updated, myanmar_odd) FROM stdin;
\.


--
-- Data for Name: players; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.players (player_id, provider, provider_id, first_name, last_name, name, nationality, birth_date, birth_place, birth_country, height, weight, "position", preferred_foot, photo, created_at, updated_at) FROM stdin;
1	api-football	259	\N	\N	Thiago Silva	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
2	api-football	692	\N	\N	Alan Patrick	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
3	api-football	860	\N	\N	Alex Sandro	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
4	api-football	2044	\N	\N	G. Mercado	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
5	api-football	2289	\N	\N	Jorginho	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
6	api-football	2502	\N	\N	G. Gomez	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
7	api-football	2553	\N	\N	G. Maripan	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
8	api-football	2560	\N	\N	E. Pulgar	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
9	api-football	5211	\N	\N	L. Picco	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
10	api-football	5933	\N	\N	A. Barboza	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
11	api-football	5994	\N	\N	J. Carrascal	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
12	api-football	5995	\N	\N	N. de la Cruz	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
13	api-football	6078	\N	\N	E. Britez	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
14	api-football	6337	\N	\N	J. Freytes	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
15	api-football	6519	\N	\N	R. Villagra	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
16	api-football	9218	\N	\N	Marlon Freitas	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
17	api-football	9460	\N	\N	Baralhas	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
18	api-football	9620	\N	\N	Gustavo Silva	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
19	api-football	9709	\N	\N	Matheus Alexandre	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
20	api-football	9854	\N	\N	Ademir	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
21	api-football	9906	\N	\N	Luan Candido	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
22	api-football	9909	\N	\N	Vitao	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
23	api-football	9921	\N	\N	Bruno Henrique	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
24	api-football	9994	\N	\N	Jean Lucas	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
25	api-football	10017	\N	\N	Ignacio	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
26	api-football	10085	\N	\N	Caca	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
27	api-football	10114	\N	\N	Ze Ivaldo	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
28	api-football	10124	\N	\N	Leo Pereira	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
29	api-football	10147	\N	\N	Vitinho	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
30	api-football	10232	\N	\N	Marllon	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
31	api-football	10310	\N	\N	Ze Ricardo	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
32	api-football	10581	\N	\N	Yago Pikachu	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
33	api-football	12705	\N	\N	Hulk	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
34	api-football	13691	\N	\N	J. Carbonero	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
35	api-football	13708	\N	\N	J. Arias	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
36	api-football	16637	\N	\N	Emmanuel Martinez	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
37	api-football	44348	\N	\N	Eduardo	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
38	api-football	50763	\N	\N	L. Acosta	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
39	api-football	54238	\N	\N	Edson Carioca	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
40	api-football	80273	\N	\N	Gabriel Taliari	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
41	api-football	96343	\N	\N	Alef Manga	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
42	api-football	106485	\N	\N	Mauricio	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
43	api-football	114436	\N	\N	Matheuzinho	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
44	api-football	115525	\N	\N	Joao Victor	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
45	api-football	126936	\N	\N	Samuel Lino	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
46	api-football	156191	\N	\N	Igor Formiga	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
47	api-football	187920	\N	\N	Fabiano	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
48	api-football	196298	\N	\N	R. Sosa	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
49	api-football	197383	\N	\N	Luciano Juba	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
50	api-football	268877	\N	\N	Marcelinho	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
51	api-football	280245	\N	\N	Martinelli	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
52	api-football	303127	\N	\N	Erick Pulga	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
53	api-football	311344	\N	\N	A. Veliz	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
54	api-football	332654	\N	\N	Evertton Araujo	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
55	api-football	357888	\N	\N	Gabriel Knesowitsch	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
56	api-football	405353	\N	\N	Japa	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
57	api-football	442404	\N	\N	Rene	\N	\N	\N	\N	\N	\N	\N	\N	\N	2026-08-15 23:45:20.282671+06:30	2026-08-15 23:45:20.282671+06:30
\.


--
-- Data for Name: referees; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.referees (referee_id, provider, provider_id, name, normalized_name, nationality, photo, created_at, updated_at) FROM stdin;
1	api-football	\N	Paulo Cesar Zanovelli da Silva, Brazil	paulo cesar zanovelli da silva, brazil	\N	\N	2026-08-16 18:19:46.165521+06:30	2026-08-16 18:20:11.764349+06:30
2	api-football	\N	R. Claus	r. claus	\N	\N	2026-08-16 18:19:46.165521+06:30	2026-08-16 18:20:11.764349+06:30
3	api-football	\N	Matheus Delgado Candançan, Brazil	matheus delgado candancan, brazil	\N	\N	2026-08-16 18:19:46.165521+06:30	2026-08-16 18:20:11.764349+06:30
4	api-football	\N	Lucas Paulo Torezin, Brazil	lucas paulo torezin, brazil	\N	\N	2026-08-16 18:19:46.165521+06:30	2026-08-16 18:20:11.764349+06:30
5	api-football	\N	A. Gomes Stefano	a. gomes stefano	\N	\N	2026-08-16 18:19:46.165521+06:30	2026-08-16 18:20:11.764349+06:30
\.


--
-- Data for Name: standings; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.standings (id, league_id, season, team_id, "position", points, played, won, drawn, lost, goals_for, goals_against, goal_difference, created_at, updated_at, team_name, team_logo, group_name, form, description) FROM stdin;
1	71	2026	121	1	47	21	14	5	2	38	16	22	2026-07-30 10:10:50.14104+06:30	\N	Palmeiras	https://media.api-sports.io/football/teams/121.png	Serie A	WLWWW	LIBC CL group stage
2	71	2026	127	2	39	20	11	6	3	37	18	19	2026-07-30 10:10:50.14104+06:30	\N	Flamengo	https://media.api-sports.io/football/teams/127.png	Serie A	DDWWL	LIBC CL group stage
3	71	2026	134	3	36	20	11	3	6	28	19	9	2026-07-30 10:10:50.14104+06:30	\N	Atletico Paranaense	https://media.api-sports.io/football/teams/134.png	Serie A	WWWWD	LIBC CL group stage
4	71	2026	124	4	34	21	9	7	5	30	25	5	2026-07-30 10:10:50.14104+06:30	\N	Fluminense	https://media.api-sports.io/football/teams/124.png	Serie A	DDDDL	LIBC CL group stage
5	71	2026	118	5	32	21	8	8	5	29	25	4	2026-07-30 10:10:50.14104+06:30	\N	Bahia	https://media.api-sports.io/football/teams/118.png	Serie A	DDDWW	LIBC Play-offs
6	71	2026	794	6	31	20	9	4	7	26	20	6	2026-07-30 10:10:50.14104+06:30	\N	RB Bragantino	https://media.api-sports.io/football/teams/794.png	Serie A	DDWWW	Copa Sudamericana Group Stage
7	71	2026	120	7	29	20	8	5	7	34	32	2	2026-07-30 10:10:50.14104+06:30	\N	Botafogo	https://media.api-sports.io/football/teams/120.png	Serie A	WDWLD	Copa Sudamericana Group Stage
8	71	2026	1062	8	28	20	8	4	8	25	25	0	2026-07-30 10:10:50.14104+06:30	\N	Atletico-MG	https://media.api-sports.io/football/teams/1062.png	Serie A	WDWLW	Copa Sudamericana Group Stage
9	71	2026	131	9	28	20	7	7	6	22	20	2	2026-07-30 10:10:50.14104+06:30	\N	Corinthians	https://media.api-sports.io/football/teams/131.png	Serie A	DWWWL	Copa Sudamericana Group Stage
10	71	2026	147	10	27	20	7	6	7	25	27	-2	2026-07-30 10:10:50.14104+06:30	\N	Coritiba	https://media.api-sports.io/football/teams/147.png	Serie A	DLLWW	Copa Sudamericana Group Stage
11	71	2026	135	11	27	20	7	6	7	26	30	-4	2026-07-30 10:10:50.14104+06:30	\N	Cruzeiro	https://media.api-sports.io/football/teams/135.png	Serie A	LWDWD	Copa Sudamericana Group Stage
12	71	2026	126	12	26	20	7	5	8	25	23	2	2026-07-30 10:10:50.14104+06:30	\N	Sao Paulo	https://media.api-sports.io/football/teams/126.png	Serie A	DLLDL	\N
13	71	2026	136	13	26	21	7	5	9	22	31	-9	2026-07-30 10:10:50.14104+06:30	\N	Vitoria	https://media.api-sports.io/football/teams/136.png	Serie A	LLDWL	\N
14	71	2026	7848	14	23	20	6	5	9	23	27	-4	2026-07-30 10:10:50.14104+06:30	\N	Mirassol	https://media.api-sports.io/football/teams/7848.png	Serie A	WDWLW	\N
15	71	2026	128	15	22	20	5	7	8	29	33	-4	2026-07-30 10:10:50.14104+06:30	\N	Santos	https://media.api-sports.io/football/teams/128.png	Serie A	DLWLL	\N
16	71	2026	119	16	22	21	5	7	9	23	27	-4	2026-07-30 10:10:50.14104+06:30	\N	Internacional	https://media.api-sports.io/football/teams/119.png	Serie A	DLLLL	\N
17	71	2026	130	17	22	20	5	7	8	22	26	-4	2026-07-30 10:10:50.14104+06:30	\N	Gremio	https://media.api-sports.io/football/teams/130.png	Serie A	DLLWD	Relegation
18	71	2026	133	18	21	20	5	6	9	23	31	-8	2026-07-30 10:10:50.14104+06:30	\N	Vasco DA Gama	https://media.api-sports.io/football/teams/133.png	Serie A	DLLLL	Relegation
19	71	2026	1198	19	21	21	5	6	10	24	34	-10	2026-07-30 10:10:50.14104+06:30	\N	Remo	https://media.api-sports.io/football/teams/1198.png	Serie A	LWLWL	Relegation
20	71	2026	22722	20	10	20	1	7	12	19	41	-22	2026-07-30 10:10:50.14104+06:30	\N	Chapecoense B	https://media.api-sports.io/football/teams/22722.png	Serie A	\N	Relegation
\.


--
-- Data for Name: teams; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.teams (team_id, name, country, logo, stadium, founded, created_at, updated_at, current_league_id, current_season, provider, provider_id, country_id) FROM stdin;
124	Fluminense	\N	https://media.api-sports.io/football/teams/124.png	\N	\N	2026-07-30 10:10:43.098545+06:30	2026-07-30 10:10:43.098545+06:30	71	2026	api-football	124	1851761881
118	Bahia	\N	https://media.api-sports.io/football/teams/118.png	\N	\N	2026-07-30 10:10:43.098545+06:30	2026-07-30 10:10:43.098545+06:30	71	2026	api-football	118	1851761881
136	Vitoria	\N	https://media.api-sports.io/football/teams/136.png	\N	\N	2026-07-30 10:10:47.529124+06:30	2026-07-30 10:10:47.529124+06:30	71	2026	api-football	136	1851761881
121	Palmeiras	\N	https://media.api-sports.io/football/teams/121.png	\N	\N	2026-07-30 10:10:47.529124+06:30	2026-07-30 10:10:47.529124+06:30	71	2026	api-football	121	1851761881
131	Corinthians	\N	https://media.api-sports.io/football/teams/131.png	\N	\N	2026-07-30 10:10:50.121529+06:30	2026-07-30 10:10:50.121529+06:30	71	2026	api-football	131	1851761881
134	Atletico Paranaense	\N	https://media.api-sports.io/football/teams/134.png	\N	\N	2026-07-30 10:10:50.121529+06:30	2026-07-30 10:10:50.121529+06:30	71	2026	api-football	134	1851761881
147	Coritiba	\N	https://media.api-sports.io/football/teams/147.png	\N	\N	2026-07-30 10:10:50.14104+06:30	\N	\N	\N	api-football	147	1851761881
135	Cruzeiro	\N	https://media.api-sports.io/football/teams/135.png	\N	\N	2026-07-30 10:10:50.14104+06:30	\N	\N	\N	api-football	135	1851761881
22722	Chapecoense B	\N	https://media.api-sports.io/football/teams/22722.png	\N	\N	2026-07-30 10:10:50.14104+06:30	\N	\N	\N	api-football	22722	1851761881
1062	Atletico-MG	\N	https://media.api-sports.io/football/teams/1062.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:03.697727+06:30	71	2026	api-football	1062	1851761881
794	RB Bragantino	\N	https://media.api-sports.io/football/teams/794.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:03.697727+06:30	71	2026	api-football	794	1851761881
120	Botafogo	\N	https://media.api-sports.io/football/teams/120.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:08.187637+06:30	71	2026	api-football	120	1851761881
130	Gremio	\N	https://media.api-sports.io/football/teams/130.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:08.187637+06:30	71	2026	api-football	130	1851761881
132	Chapecoense-sc	\N	https://media.api-sports.io/football/teams/132.png	\N	\N	2026-07-30 10:11:09.261029+06:30	2026-07-30 10:11:09.261029+06:30	71	2026	api-football	132	1851761881
133	Vasco DA Gama	\N	https://media.api-sports.io/football/teams/133.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:09.261029+06:30	71	2026	api-football	133	1851761881
126	Sao Paulo	\N	https://media.api-sports.io/football/teams/126.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:11.465909+06:30	71	2026	api-football	126	1851761881
128	Santos	\N	https://media.api-sports.io/football/teams/128.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:11.465909+06:30	71	2026	api-football	128	1851761881
119	Internacional	\N	https://media.api-sports.io/football/teams/119.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:12.581774+06:30	71	2026	api-football	119	1851761881
127	Flamengo	\N	https://media.api-sports.io/football/teams/127.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:12.581774+06:30	71	2026	api-football	127	1851761881
7848	Mirassol	\N	https://media.api-sports.io/football/teams/7848.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:13.682898+06:30	71	2026	api-football	7848	1851761881
1198	Remo	\N	https://media.api-sports.io/football/teams/1198.png	\N	\N	2026-07-30 10:10:50.14104+06:30	2026-07-30 10:11:13.682898+06:30	71	2026	api-football	1198	1851761881
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.users (id, username, email, hashed_password, role, is_active, created_at, updated_at, google_id, display_name, avatar_url, avatar_source) FROM stdin;
1	admin	user@example.com	$2b$12$R43lcFXJBQOxzmOIrRsz0e43Rx22W8kkMxsSWsV.pRicF2jvseb6.	admin	t	2026-07-29 22:54:25.924015+06:30	\N	\N	\N	\N	default
3	kyawseomin	kyawsoemin242374@gmail.com	$2b$12$6mBGqNvfzMBZdxevj7ekNuOqWxmokvrLJp9ZQrETao32zz0dSvrlm	admin	t	2026-08-06 10:48:19.238816+06:30	\N	\N	\N	\N	default
4	kyawseomin2	kyawsoemin473242@gmail.com	$2b$12$jE6EqO.ChRTCK9FzRkh1hu31KZgkmiBjnhYzF.NX/GidupiK0doS2	admin	t	2026-08-06 11:02:33.766017+06:30	\N	\N	\N	\N	default
5	kyawseomin3	kyawsoemin7777777@gmail.com	$2b$12$wJwpj08NGX03XImuuwarOOURDoObkkHR0mK7IMQQa1VVW1acIVRoG	admin	t	2026-08-06 11:42:27.578923+06:30	\N	\N	\N	\N	default
6	audit	audit@example.com	$2b$12$Fz9tlld168YfBnGpEjAytOs7qBFyy4u7H9/zm3FeClLFRSlCIXSfy	user	t	2026-08-06 11:47:57.61306+06:30	\N	audit-google-id	Audit User	https://example.com/avatar.jpg	google
7	kyawseomin4	kyawsoemin77777777@gmail.com	$2b$12$5hbYveeCXF4k.VU07TNfNOLxAxvm5JZaGuGevYAhkjRKa/kjAWoRS	admin	t	2026-08-06 11:54:40.569783+06:30	\N	\N	\N	\N	default
\.


--
-- Data for Name: venues; Type: TABLE DATA; Schema: public; Owner: fover_user
--

COPY public.venues (venue_id, provider, provider_id, name, city, country, country_code, capacity, surface, image, created_at, updated_at) FROM stdin;
1	api-football	244	Estádio Beira-Rio	Porto Alegre	\N	\N	\N	\N	\N	2026-08-16 00:00:08.005412+06:30	2026-08-16 00:20:54.481835+06:30
2	api-football	204	Estádio do Maracanã	Rio de Janeiro	\N	\N	\N	\N	\N	2026-08-16 00:00:08.005412+06:30	2026-08-16 00:20:54.481835+06:30
3	api-football	269	Estadio Do MorumBIS	Sao Paulo	\N	\N	\N	\N	\N	2026-08-16 00:00:08.005412+06:30	2026-08-16 00:20:54.481835+06:30
4	api-football	5676	Estádio José Maria de Campos Maia	Mirassol	\N	\N	\N	\N	\N	2026-08-16 00:00:08.005412+06:30	2026-08-16 00:20:54.481835+06:30
5	api-football	281	Estádio Manoel Barradas	Salvador	\N	\N	\N	\N	\N	2026-08-16 00:00:08.005412+06:30	2026-08-16 00:20:54.481835+06:30
6	api-football	218	Estadio Olimpico Nilton Santos	Rio de Janeiro	\N	\N	\N	\N	\N	2026-08-16 00:00:08.005412+06:30	2026-08-16 00:20:54.481835+06:30
7	api-football	21427	MRV Arena	Belo Horizonte	\N	\N	\N	\N	\N	2026-08-16 00:00:08.005412+06:30	2026-08-16 00:20:54.481835+06:30
8	api-football	11531	Neo Quimica Arena	Sao Paulo	\N	\N	\N	\N	\N	2026-08-16 00:00:08.005412+06:30	2026-08-16 00:20:54.481835+06:30
\.


--
-- Name: ad_configs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.ad_configs_id_seq', 9, true);


--
-- Name: ads_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.ads_id_seq', 1, false);


--
-- Name: allowed_leagues_league_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.allowed_leagues_league_id_seq', 1, false);


--
-- Name: coaches_coach_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.coaches_coach_id_seq', 94, true);


--
-- Name: countries_country_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.countries_country_id_seq', 1, false);


--
-- Name: league_seasons_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.league_seasons_id_seq', 2, true);


--
-- Name: leagues_league_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.leagues_league_id_seq', 1, false);


--
-- Name: match_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.match_events_id_seq', 66, true);


--
-- Name: match_h2h_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.match_h2h_id_seq', 1, false);


--
-- Name: match_lineups_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.match_lineups_id_seq', 1, false);


--
-- Name: matches_fixture_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.matches_fixture_id_seq', 1, false);


--
-- Name: news_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.news_id_seq', 1, false);


--
-- Name: odds_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.odds_id_seq', 1, false);


--
-- Name: players_player_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.players_player_id_seq', 57, true);


--
-- Name: referees_referee_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.referees_referee_id_seq', 5, true);


--
-- Name: standings_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.standings_id_seq', 20, true);


--
-- Name: teams_team_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.teams_team_id_seq', 1, false);


--
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.users_id_seq', 7, true);


--
-- Name: venues_venue_id_seq; Type: SEQUENCE SET; Schema: public; Owner: fover_user
--

SELECT pg_catalog.setval('public.venues_venue_id_seq', 8, true);


--
-- Name: ad_configs ad_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.ad_configs
    ADD CONSTRAINT ad_configs_pkey PRIMARY KEY (id);


--
-- Name: ads ads_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.ads
    ADD CONSTRAINT ads_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: coaches coaches_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.coaches
    ADD CONSTRAINT coaches_pkey PRIMARY KEY (coach_id);


--
-- Name: countries countries_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.countries
    ADD CONSTRAINT countries_pkey PRIMARY KEY (country_id);


--
-- Name: league_seasons league_seasons_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.league_seasons
    ADD CONSTRAINT league_seasons_pkey PRIMARY KEY (id);


--
-- Name: leagues leagues_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.leagues
    ADD CONSTRAINT leagues_pkey PRIMARY KEY (league_id);


--
-- Name: lineup_refresh_state lineup_refresh_state_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.lineup_refresh_state
    ADD CONSTRAINT lineup_refresh_state_pkey PRIMARY KEY (match_id);


--
-- Name: match_h2h match_h2h_h2h_key_key; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_h2h
    ADD CONSTRAINT match_h2h_h2h_key_key UNIQUE (h2h_key);


--
-- Name: match_h2h match_h2h_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_h2h
    ADD CONSTRAINT match_h2h_pkey PRIMARY KEY (id);


--
-- Name: match_lineups match_lineups_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_lineups
    ADD CONSTRAINT match_lineups_pkey PRIMARY KEY (id);


--
-- Name: matches matches_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_pkey PRIMARY KEY (fixture_id);


--
-- Name: news news_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.news
    ADD CONSTRAINT news_pkey PRIMARY KEY (id);


--
-- Name: odds odds_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.odds
    ADD CONSTRAINT odds_pkey PRIMARY KEY (id);


--
-- Name: allowed_leagues pk_allowed_leagues; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.allowed_leagues
    ADD CONSTRAINT pk_allowed_leagues PRIMARY KEY (league_id);


--
-- Name: match_events pk_match_events; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_events
    ADD CONSTRAINT pk_match_events PRIMARY KEY (id);


--
-- Name: match_statistics pk_match_statistics; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_statistics
    ADD CONSTRAINT pk_match_statistics PRIMARY KEY (match_id);


--
-- Name: players players_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.players
    ADD CONSTRAINT players_pkey PRIMARY KEY (player_id);


--
-- Name: referees referees_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.referees
    ADD CONSTRAINT referees_pkey PRIMARY KEY (referee_id);


--
-- Name: standings standings_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.standings
    ADD CONSTRAINT standings_pkey PRIMARY KEY (id);


--
-- Name: teams teams_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_pkey PRIMARY KEY (team_id);


--
-- Name: coaches uq_coaches_provider_normalized_name; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.coaches
    ADD CONSTRAINT uq_coaches_provider_normalized_name UNIQUE (provider, normalized_name);


--
-- Name: coaches uq_coaches_provider_provider_id; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.coaches
    ADD CONSTRAINT uq_coaches_provider_provider_id UNIQUE (provider, provider_id);


--
-- Name: league_seasons uq_league_seasons_league_id_season; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.league_seasons
    ADD CONSTRAINT uq_league_seasons_league_id_season UNIQUE (league_id, season);


--
-- Name: leagues uq_leagues_name; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.leagues
    ADD CONSTRAINT uq_leagues_name UNIQUE (name);


--
-- Name: odds uq_odds_fixture_bookmaker_market_selection; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.odds
    ADD CONSTRAINT uq_odds_fixture_bookmaker_market_selection UNIQUE (fixture_id, bookmaker_name, market_name, selection);


--
-- Name: players uq_players_provider_provider_id; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.players
    ADD CONSTRAINT uq_players_provider_provider_id UNIQUE (provider, provider_id);


--
-- Name: referees uq_referees_provider_normalized_name; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.referees
    ADD CONSTRAINT uq_referees_provider_normalized_name UNIQUE (provider, normalized_name);


--
-- Name: referees uq_referees_provider_provider_id; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.referees
    ADD CONSTRAINT uq_referees_provider_provider_id UNIQUE (provider, provider_id);


--
-- Name: standings uq_standings_league_id_season_team_id; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.standings
    ADD CONSTRAINT uq_standings_league_id_season_team_id UNIQUE (league_id, season, team_id);


--
-- Name: teams uq_teams_name; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT uq_teams_name UNIQUE (name);


--
-- Name: teams uq_teams_provider_provider_id; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT uq_teams_provider_provider_id UNIQUE (provider, provider_id);


--
-- Name: users uq_users_email; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_email UNIQUE (email);


--
-- Name: users uq_users_username; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_username UNIQUE (username);


--
-- Name: venues uq_venues_provider_provider_id; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.venues
    ADD CONSTRAINT uq_venues_provider_provider_id UNIQUE (provider, provider_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: venues venues_pkey; Type: CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.venues
    ADD CONSTRAINT venues_pkey PRIMARY KEY (venue_id);


--
-- Name: ix_ad_configs_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_ad_configs_id ON public.ad_configs USING btree (id);


--
-- Name: ix_allowed_leagues_league_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_allowed_leagues_league_id ON public.allowed_leagues USING btree (league_id);


--
-- Name: ix_coaches_coach_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_coaches_coach_id ON public.coaches USING btree (coach_id);


--
-- Name: ix_coaches_name; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_coaches_name ON public.coaches USING btree (name);


--
-- Name: ix_coaches_normalized_name; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_coaches_normalized_name ON public.coaches USING btree (normalized_name);


--
-- Name: ix_coaches_provider_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_coaches_provider_id ON public.coaches USING btree (provider_id);


--
-- Name: ix_countries_country_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_countries_country_id ON public.countries USING btree (country_id);


--
-- Name: ix_countries_name; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE UNIQUE INDEX ix_countries_name ON public.countries USING btree (name);


--
-- Name: ix_league_seasons_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_league_seasons_id ON public.league_seasons USING btree (id);


--
-- Name: ix_league_seasons_league_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_league_seasons_league_id ON public.league_seasons USING btree (league_id);


--
-- Name: ix_league_seasons_season; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_league_seasons_season ON public.league_seasons USING btree (season);


--
-- Name: ix_leagues_country_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_leagues_country_id ON public.leagues USING btree (country_id);


--
-- Name: ix_leagues_display_order; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_leagues_display_order ON public.leagues USING btree (display_order);


--
-- Name: ix_leagues_is_featured; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_leagues_is_featured ON public.leagues USING btree (is_featured);


--
-- Name: ix_lineup_refresh_state_match_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_lineup_refresh_state_match_id ON public.lineup_refresh_state USING btree (match_id);


--
-- Name: ix_match_events_match_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_match_events_match_id ON public.match_events USING btree (match_id);


--
-- Name: ix_match_h2h_h2h_key; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_match_h2h_h2h_key ON public.match_h2h USING btree (h2h_key);


--
-- Name: ix_match_lineups_match_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE UNIQUE INDEX ix_match_lineups_match_id ON public.match_lineups USING btree (match_id);


--
-- Name: ix_matches_league_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_matches_league_id ON public.matches USING btree (league_id);


--
-- Name: ix_matches_league_id_season; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_matches_league_id_season ON public.matches USING btree (league_id, season);


--
-- Name: ix_odds_fixture_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_odds_fixture_id ON public.odds USING btree (fixture_id);


--
-- Name: ix_odds_market_name; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_odds_market_name ON public.odds USING btree (market_name);


--
-- Name: ix_odds_selection; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_odds_selection ON public.odds USING btree (selection);


--
-- Name: ix_players_name; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_players_name ON public.players USING btree (name);


--
-- Name: ix_players_player_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_players_player_id ON public.players USING btree (player_id);


--
-- Name: ix_players_provider_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_players_provider_id ON public.players USING btree (provider_id);


--
-- Name: ix_referees_name; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_referees_name ON public.referees USING btree (name);


--
-- Name: ix_referees_normalized_name; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_referees_normalized_name ON public.referees USING btree (normalized_name);


--
-- Name: ix_referees_provider_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_referees_provider_id ON public.referees USING btree (provider_id);


--
-- Name: ix_referees_referee_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_referees_referee_id ON public.referees USING btree (referee_id);


--
-- Name: ix_standings_league_id_season_position; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_standings_league_id_season_position ON public.standings USING btree (league_id, season, "position");


--
-- Name: ix_teams_country_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_teams_country_id ON public.teams USING btree (country_id);


--
-- Name: ix_teams_provider_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_teams_provider_id ON public.teams USING btree (provider_id);


--
-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_users_email ON public.users USING btree (email);


--
-- Name: ix_users_username; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_users_username ON public.users USING btree (username);


--
-- Name: ix_venues_name; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_venues_name ON public.venues USING btree (name);


--
-- Name: ix_venues_provider_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_venues_provider_id ON public.venues USING btree (provider_id);


--
-- Name: ix_venues_venue_id; Type: INDEX; Schema: public; Owner: fover_user
--

CREATE INDEX ix_venues_venue_id ON public.venues USING btree (venue_id);


--
-- Name: league_seasons fk_league_seasons_league_id; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.league_seasons
    ADD CONSTRAINT fk_league_seasons_league_id FOREIGN KEY (league_id) REFERENCES public.leagues(league_id);


--
-- Name: leagues fk_leagues_country_id; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.leagues
    ADD CONSTRAINT fk_leagues_country_id FOREIGN KEY (country_id) REFERENCES public.countries(country_id);


--
-- Name: match_events fk_match_events_match_id_matches; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_events
    ADD CONSTRAINT fk_match_events_match_id_matches FOREIGN KEY (match_id) REFERENCES public.matches(fixture_id);


--
-- Name: match_statistics fk_match_statistics_match_id_matches; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_statistics
    ADD CONSTRAINT fk_match_statistics_match_id_matches FOREIGN KEY (match_id) REFERENCES public.matches(fixture_id);


--
-- Name: matches fk_matches_away_team; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT fk_matches_away_team FOREIGN KEY (away_team_id) REFERENCES public.teams(team_id);


--
-- Name: matches fk_matches_home_team; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT fk_matches_home_team FOREIGN KEY (home_team_id) REFERENCES public.teams(team_id);


--
-- Name: odds fk_odds_fixture_id_matches; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.odds
    ADD CONSTRAINT fk_odds_fixture_id_matches FOREIGN KEY (fixture_id) REFERENCES public.matches(fixture_id);


--
-- Name: standings fk_standings_league_id_leagues; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.standings
    ADD CONSTRAINT fk_standings_league_id_leagues FOREIGN KEY (league_id) REFERENCES public.leagues(league_id);


--
-- Name: standings fk_standings_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.standings
    ADD CONSTRAINT fk_standings_team_id_teams FOREIGN KEY (team_id) REFERENCES public.teams(team_id);


--
-- Name: teams fk_teams_country_id; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT fk_teams_country_id FOREIGN KEY (country_id) REFERENCES public.countries(country_id);


--
-- Name: lineup_refresh_state lineup_refresh_state_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.lineup_refresh_state
    ADD CONSTRAINT lineup_refresh_state_match_id_fkey FOREIGN KEY (match_id) REFERENCES public.matches(fixture_id);


--
-- Name: match_lineups match_lineups_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fover_user
--

ALTER TABLE ONLY public.match_lineups
    ADD CONSTRAINT match_lineups_match_id_fkey FOREIGN KEY (match_id) REFERENCES public.matches(fixture_id);


--
-- PostgreSQL database dump complete
--

\unrestrict gzqcWFK1752POxtRndA0AFU0TzJBu4m4yZIl62PvPN1Zl3ifVxyMdQVuFF61GxC

