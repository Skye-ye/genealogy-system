--
-- PostgreSQL database dump
--

\restrict 8AF6ACNM5Uo8vCPhe9GhmLgsjXmyJa1M1ojYfChyQwGhQEbJKLHOQjsPfvjeuG5

-- Dumped from database version 16.13 (Debian 16.13-1.pgdg13+1)
-- Dumped by pg_dump version 16.13 (Debian 16.13-1.pgdg13+1)

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

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: collab_role_t; Type: TYPE; Schema: public; Owner: genealogy
--

CREATE TYPE public.collab_role_t AS ENUM (
    'editor',
    'viewer'
);


ALTER TYPE public.collab_role_t OWNER TO genealogy;

--
-- Name: gender_t; Type: TYPE; Schema: public; Owner: genealogy
--

CREATE TYPE public.gender_t AS ENUM (
    'M',
    'F'
);


ALTER TYPE public.gender_t OWNER TO genealogy;

--
-- Name: trg_marriages_same_genealogy(); Type: FUNCTION; Schema: public; Owner: genealogy
--

CREATE FUNCTION public.trg_marriages_same_genealogy() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    g1 BIGINT;
    g2 BIGINT;
BEGIN
    SELECT genealogy_id INTO g1 FROM members WHERE id = NEW.member1_id;
    SELECT genealogy_id INTO g2 FROM members WHERE id = NEW.member2_id;
    IF g1 IS DISTINCT FROM g2 THEN
        RAISE EXCEPTION 'spouses must belong to the same genealogy (% vs %)', g1, g2;
    END IF;
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.trg_marriages_same_genealogy() OWNER TO genealogy;

--
-- Name: trg_members_validate_and_set_generation(); Type: FUNCTION; Schema: public; Owner: genealogy
--

CREATE FUNCTION public.trg_members_validate_and_set_generation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    father_gen INT;
    mother_gen INT;
    father_birth INT;
    mother_birth INT;
    father_gender gender_t;
    mother_gender gender_t;
    father_genealogy BIGINT;
    mother_genealogy BIGINT;
    new_gen INT := 1;
BEGIN
    IF NEW.father_id IS NOT NULL THEN
        SELECT generation, birth_year, gender, genealogy_id
          INTO father_gen, father_birth, father_gender, father_genealogy
          FROM members WHERE id = NEW.father_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'father_id % does not exist', NEW.father_id;
        END IF;
        IF father_gender <> 'M' THEN
            RAISE EXCEPTION 'father_id % is not male (gender=%)', NEW.father_id, father_gender;
        END IF;
        IF father_genealogy IS DISTINCT FROM NEW.genealogy_id THEN
            RAISE EXCEPTION 'father_id % belongs to a different genealogy', NEW.father_id;
        END IF;
        IF father_birth IS NOT NULL AND NEW.birth_year IS NOT NULL
           AND father_birth >= NEW.birth_year THEN
            RAISE EXCEPTION 'father birth_year (%) must be earlier than child birth_year (%)',
                            father_birth, NEW.birth_year;
        END IF;
        new_gen := GREATEST(new_gen, father_gen + 1);
    END IF;

    IF NEW.mother_id IS NOT NULL THEN
        SELECT generation, birth_year, gender, genealogy_id
          INTO mother_gen, mother_birth, mother_gender, mother_genealogy
          FROM members WHERE id = NEW.mother_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'mother_id % does not exist', NEW.mother_id;
        END IF;
        IF mother_gender <> 'F' THEN
            RAISE EXCEPTION 'mother_id % is not female (gender=%)', NEW.mother_id, mother_gender;
        END IF;
        IF mother_genealogy IS DISTINCT FROM NEW.genealogy_id THEN
            RAISE EXCEPTION 'mother_id % belongs to a different genealogy', NEW.mother_id;
        END IF;
        IF mother_birth IS NOT NULL AND NEW.birth_year IS NOT NULL
           AND mother_birth >= NEW.birth_year THEN
            RAISE EXCEPTION 'mother birth_year (%) must be earlier than child birth_year (%)',
                            mother_birth, NEW.birth_year;
        END IF;
        new_gen := GREATEST(new_gen, mother_gen + 1);
    END IF;

    NEW.generation := new_gen;
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.trg_members_validate_and_set_generation() OWNER TO genealogy;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: genealogies; Type: TABLE; Schema: public; Owner: genealogy
--

CREATE TABLE public.genealogies (
    id bigint NOT NULL,
    name character varying(128) NOT NULL,
    surname character varying(32) NOT NULL,
    compilation_date date DEFAULT CURRENT_DATE NOT NULL,
    owner_user_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT genealogies_compilation_not_future CHECK ((compilation_date <= CURRENT_DATE))
);


ALTER TABLE public.genealogies OWNER TO genealogy;

--
-- Name: genealogies_id_seq; Type: SEQUENCE; Schema: public; Owner: genealogy
--

CREATE SEQUENCE public.genealogies_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.genealogies_id_seq OWNER TO genealogy;

--
-- Name: genealogies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: genealogy
--

ALTER SEQUENCE public.genealogies_id_seq OWNED BY public.genealogies.id;


--
-- Name: genealogy_collaborators; Type: TABLE; Schema: public; Owner: genealogy
--

CREATE TABLE public.genealogy_collaborators (
    genealogy_id bigint NOT NULL,
    user_id bigint NOT NULL,
    role public.collab_role_t DEFAULT 'editor'::public.collab_role_t NOT NULL,
    invited_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.genealogy_collaborators OWNER TO genealogy;

--
-- Name: marriages; Type: TABLE; Schema: public; Owner: genealogy
--

CREATE TABLE public.marriages (
    id bigint NOT NULL,
    member1_id bigint NOT NULL,
    member2_id bigint NOT NULL,
    married_year integer,
    divorced_year integer,
    CONSTRAINT marriages_canonical_order CHECK ((member1_id < member2_id)),
    CONSTRAINT marriages_distinct_partners CHECK ((member1_id <> member2_id)),
    CONSTRAINT marriages_divorce_after_marriage CHECK (((divorced_year IS NULL) OR (married_year IS NULL) OR (divorced_year >= married_year)))
);


ALTER TABLE public.marriages OWNER TO genealogy;

--
-- Name: marriages_id_seq; Type: SEQUENCE; Schema: public; Owner: genealogy
--

CREATE SEQUENCE public.marriages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.marriages_id_seq OWNER TO genealogy;

--
-- Name: marriages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: genealogy
--

ALTER SEQUENCE public.marriages_id_seq OWNED BY public.marriages.id;


--
-- Name: members; Type: TABLE; Schema: public; Owner: genealogy
--

CREATE TABLE public.members (
    id bigint NOT NULL,
    genealogy_id bigint NOT NULL,
    name character varying(64) NOT NULL,
    gender public.gender_t NOT NULL,
    birth_year integer,
    death_year integer,
    biography text,
    father_id bigint,
    mother_id bigint,
    generation integer DEFAULT 1 NOT NULL,
    CONSTRAINT members_birth_year_sane CHECK (((birth_year IS NULL) OR ((birth_year >= 1) AND (birth_year <= (EXTRACT(year FROM CURRENT_DATE))::integer)))),
    CONSTRAINT members_death_after_birth CHECK (((death_year IS NULL) OR (birth_year IS NULL) OR (death_year >= birth_year))),
    CONSTRAINT members_generation_positive CHECK ((generation >= 1)),
    CONSTRAINT members_lifespan_sane CHECK (((death_year IS NULL) OR (birth_year IS NULL) OR ((death_year - birth_year) <= 130))),
    CONSTRAINT members_name_nonempty CHECK ((length(TRIM(BOTH FROM name)) > 0)),
    CONSTRAINT members_no_self_parent CHECK (((id IS NULL) OR ((id <> father_id) AND (id <> mother_id))))
);


ALTER TABLE public.members OWNER TO genealogy;

--
-- Name: members_id_seq; Type: SEQUENCE; Schema: public; Owner: genealogy
--

CREATE SEQUENCE public.members_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.members_id_seq OWNER TO genealogy;

--
-- Name: members_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: genealogy
--

ALTER SEQUENCE public.members_id_seq OWNED BY public.members.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: genealogy
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    username character varying(64) NOT NULL,
    password_hash character varying(255) NOT NULL,
    email character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_username_nonempty CHECK ((length(TRIM(BOTH FROM username)) > 0))
);


ALTER TABLE public.users OWNER TO genealogy;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: genealogy
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO genealogy;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: genealogy
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: genealogies id; Type: DEFAULT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.genealogies ALTER COLUMN id SET DEFAULT nextval('public.genealogies_id_seq'::regclass);


--
-- Name: marriages id; Type: DEFAULT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.marriages ALTER COLUMN id SET DEFAULT nextval('public.marriages_id_seq'::regclass);


--
-- Name: members id; Type: DEFAULT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.members ALTER COLUMN id SET DEFAULT nextval('public.members_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: genealogies genealogies_pkey; Type: CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.genealogies
    ADD CONSTRAINT genealogies_pkey PRIMARY KEY (id);


--
-- Name: genealogy_collaborators genealogy_collaborators_pkey; Type: CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.genealogy_collaborators
    ADD CONSTRAINT genealogy_collaborators_pkey PRIMARY KEY (genealogy_id, user_id);


--
-- Name: marriages marriages_pkey; Type: CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.marriages
    ADD CONSTRAINT marriages_pkey PRIMARY KEY (id);


--
-- Name: marriages marriages_unique_pair; Type: CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.marriages
    ADD CONSTRAINT marriages_unique_pair UNIQUE (member1_id, member2_id);


--
-- Name: members members_pkey; Type: CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT members_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: idx_marriages_member2; Type: INDEX; Schema: public; Owner: genealogy
--

CREATE INDEX idx_marriages_member2 ON public.marriages USING btree (member2_id);


--
-- Name: idx_members_father_id; Type: INDEX; Schema: public; Owner: genealogy
--

CREATE INDEX idx_members_father_id ON public.members USING btree (father_id) WHERE (father_id IS NOT NULL);


--
-- Name: idx_members_genealogy_generation; Type: INDEX; Schema: public; Owner: genealogy
--

CREATE INDEX idx_members_genealogy_generation ON public.members USING btree (genealogy_id, generation);


--
-- Name: idx_members_mother_id; Type: INDEX; Schema: public; Owner: genealogy
--

CREATE INDEX idx_members_mother_id ON public.members USING btree (mother_id) WHERE (mother_id IS NOT NULL);


--
-- Name: idx_members_name_trgm; Type: INDEX; Schema: public; Owner: genealogy
--

CREATE INDEX idx_members_name_trgm ON public.members USING gin (name public.gin_trgm_ops);


--
-- Name: marriages marriages_same_genealogy_biur; Type: TRIGGER; Schema: public; Owner: genealogy
--

CREATE TRIGGER marriages_same_genealogy_biur BEFORE INSERT OR UPDATE OF member1_id, member2_id ON public.marriages FOR EACH ROW EXECUTE FUNCTION public.trg_marriages_same_genealogy();


--
-- Name: members members_validate_biur; Type: TRIGGER; Schema: public; Owner: genealogy
--

CREATE TRIGGER members_validate_biur BEFORE INSERT OR UPDATE OF father_id, mother_id, birth_year, genealogy_id ON public.members FOR EACH ROW EXECUTE FUNCTION public.trg_members_validate_and_set_generation();


--
-- Name: genealogies genealogies_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.genealogies
    ADD CONSTRAINT genealogies_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: genealogy_collaborators genealogy_collaborators_genealogy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.genealogy_collaborators
    ADD CONSTRAINT genealogy_collaborators_genealogy_id_fkey FOREIGN KEY (genealogy_id) REFERENCES public.genealogies(id) ON DELETE CASCADE;


--
-- Name: genealogy_collaborators genealogy_collaborators_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.genealogy_collaborators
    ADD CONSTRAINT genealogy_collaborators_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: marriages marriages_member1_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.marriages
    ADD CONSTRAINT marriages_member1_id_fkey FOREIGN KEY (member1_id) REFERENCES public.members(id) ON DELETE CASCADE;


--
-- Name: marriages marriages_member2_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.marriages
    ADD CONSTRAINT marriages_member2_id_fkey FOREIGN KEY (member2_id) REFERENCES public.members(id) ON DELETE CASCADE;


--
-- Name: members members_father_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT members_father_id_fkey FOREIGN KEY (father_id) REFERENCES public.members(id) ON DELETE SET NULL;


--
-- Name: members members_genealogy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT members_genealogy_id_fkey FOREIGN KEY (genealogy_id) REFERENCES public.genealogies(id) ON DELETE CASCADE;


--
-- Name: members members_mother_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: genealogy
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT members_mother_id_fkey FOREIGN KEY (mother_id) REFERENCES public.members(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict 8AF6ACNM5Uo8vCPhe9GhmLgsjXmyJa1M1ojYfChyQwGhQEbJKLHOQjsPfvjeuG5

