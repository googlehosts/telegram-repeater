--
-- PostgreSQL database dump
--

-- Dumped from database version 12.4
-- Dumped by pg_dump version 12.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: answer_history; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.answer_history (
    id integer NOT NULL,
    user_id integer NOT NULL,
    body text NOT NULL,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.answer_history OWNER TO postgres;

--
-- Name: answer_history_aid_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.answer_history_aid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.answer_history_aid_seq OWNER TO postgres;

--
-- Name: answer_history_aid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.answer_history_aid_seq OWNED BY public.answer_history.id;


--
-- Name: auth_user; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.auth_user (
    uid bigint NOT NULL,
    authorized boolean DEFAULT false NOT NULL,
    muted boolean DEFAULT false NOT NULL,
    whitelist boolean DEFAULT false NOT NULL
);


ALTER TABLE public.auth_user OWNER TO postgres;

--
-- Name: banlist; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.banlist (
    id bigint NOT NULL
);


ALTER TABLE public.banlist OWNER TO postgres;

--
-- Name: exam_user_session; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.exam_user_session (
    user_id bigint NOT NULL,
    problem_version integer NOT NULL,
    problem_id integer,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    baned boolean DEFAULT false NOT NULL,
    bypass boolean DEFAULT false NOT NULL,
    passed boolean DEFAULT false NOT NULL,
    unlimited boolean DEFAULT false NOT NULL,
    retries integer DEFAULT 0 NOT NULL
);


ALTER TABLE public.exam_user_session OWNER TO postgres;

--
-- Name: msg_id; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.msg_id (
    msg_id integer NOT NULL,
    target_id integer DEFAULT 0 NOT NULL,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    user_id bigint
);


ALTER TABLE public.msg_id OWNER TO postgres;

--
-- Name: reasons; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.reasons (
    id integer NOT NULL,
    user_id bigint,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    text text NOT NULL,
    msg_id integer
);


ALTER TABLE public.reasons OWNER TO postgres;

--
-- Name: reasons_rid_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.reasons_rid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.reasons_rid_seq OWNER TO postgres;

--
-- Name: reasons_rid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.reasons_rid_seq OWNED BY public.reasons.id;


--
-- Name: tickets; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tickets (
    id integer NOT NULL,
    user_id bigint DEFAULT 0 NOT NULL,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    hash character varying(32) NOT NULL,
    origin_msg text,
    section character varying(20),
    status character varying(10)
);


ALTER TABLE public.tickets OWNER TO postgres;

--
-- Name: tickets_tid_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.tickets_tid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tickets_tid_seq OWNER TO postgres;

--
-- Name: tickets_tid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.tickets_tid_seq OWNED BY public.tickets.id;


--
-- Name: tickets_user; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tickets_user (
    user_id integer NOT NULL,
    create_time timestamp without time zone,
    last_time timestamp without time zone,
    banned boolean DEFAULT false NOT NULL,
    last_msg_sent timestamp without time zone,
    step smallint DEFAULT 0 NOT NULL,
    section character varying(20)
);


ALTER TABLE public.tickets_user OWNER TO postgres;

--
-- Name: username; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.username (
    user_id bigint NOT NULL,
    hash character varying(64) DEFAULT NULL::character varying,
    channel_msg_id integer NOT NULL
);


ALTER TABLE public.username OWNER TO postgres;

--
-- Name: answer_history id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.answer_history ALTER COLUMN id SET DEFAULT nextval('public.answer_history_aid_seq'::regclass);


--
-- Name: reasons id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.reasons ALTER COLUMN id SET DEFAULT nextval('public.reasons_rid_seq'::regclass);


--
-- Name: tickets id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tickets ALTER COLUMN id SET DEFAULT nextval('public.tickets_tid_seq'::regclass);


--
-- Name: answer_history answer_history_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.answer_history
    ADD CONSTRAINT answer_history_pk PRIMARY KEY (id);


--
-- Name: auth_user auth_user_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.auth_user
    ADD CONSTRAINT auth_user_pk PRIMARY KEY (uid);


--
-- Name: banlist banlist_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.banlist
    ADD CONSTRAINT banlist_pk PRIMARY KEY (id);


--
-- Name: exam_user_session exam_user_session_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.exam_user_session
    ADD CONSTRAINT exam_user_session_pk PRIMARY KEY (user_id);


--
-- Name: msg_id msg_id_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.msg_id
    ADD CONSTRAINT msg_id_pk PRIMARY KEY (msg_id);


--
-- Name: reasons reasons_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.reasons
    ADD CONSTRAINT reasons_pk PRIMARY KEY (id);


--
-- Name: tickets tickets_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tickets
    ADD CONSTRAINT tickets_pk PRIMARY KEY (id);


--
-- Name: tickets_user tickets_user_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tickets_user
    ADD CONSTRAINT tickets_user_pk PRIMARY KEY (user_id);


--
-- Name: username username_pk; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.username
    ADD CONSTRAINT username_pk PRIMARY KEY (user_id);


--
-- Name: tickets_hash_uindex; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX tickets_hash_uindex ON public.tickets USING btree (hash);


--
-- PostgreSQL database dump complete
--

