-- =====================================================================
-- Genealogy Management System — Schema (PostgreSQL 16)
-- =====================================================================
-- Naming: snake_case, singular concept (table name plural for collections)
-- All FKs use ON DELETE CASCADE to avoid orphans.
-- Generation is stored (denormalized) for fast per-generation aggregates;
-- correctness is enforced by trigger when parents are set.
-- =====================================================================

BEGIN;

-- Idempotent re-run for development
DROP TABLE IF EXISTS member_links           CASCADE;
DROP TABLE IF EXISTS marriages              CASCADE;
DROP TABLE IF EXISTS genealogy_collaborators CASCADE;
DROP TABLE IF EXISTS members                CASCADE;
DROP TABLE IF EXISTS genealogies            CASCADE;
DROP TABLE IF EXISTS users                  CASCADE;
DROP TYPE  IF EXISTS gender_t               CASCADE;
DROP TYPE  IF EXISTS collab_role_t          CASCADE;

-- ---------- enums (UDT per requirement §六) ----------------------------
CREATE TYPE gender_t      AS ENUM ('M', 'F');
CREATE TYPE collab_role_t AS ENUM ('editor', 'viewer');

-- ---------- users -------------------------------------------------------
CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    username      VARCHAR(64)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    email         VARCHAR(255) UNIQUE,
    is_admin      BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT users_username_nonempty CHECK (length(trim(username)) > 0)
);

-- ---------- genealogies (family books) ---------------------------------
CREATE TABLE genealogies (
    id               BIGSERIAL PRIMARY KEY,
    name             VARCHAR(128) NOT NULL,        -- 谱名
    surname          VARCHAR(32)  NOT NULL,        -- 姓氏
    compilation_date DATE         NOT NULL DEFAULT CURRENT_DATE,
    owner_user_id    BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT genealogies_compilation_not_future
        CHECK (compilation_date <= CURRENT_DATE)
);

-- ---------- members ----------------------------------------------------
-- Generation is stored for performance (avg-by-generation queries are
-- very common in this schema). Maintained by trigger; root members
-- default to generation 1.
CREATE TABLE members (
    id            BIGSERIAL PRIMARY KEY,
    genealogy_id  BIGINT       NOT NULL REFERENCES genealogies(id) ON DELETE CASCADE,
    name          VARCHAR(64)  NOT NULL,
    gender        gender_t     NOT NULL,
    birth_year    INT,
    death_year    INT,
    biography     TEXT,
    father_id     BIGINT       REFERENCES members(id) ON DELETE SET NULL,
    mother_id     BIGINT       REFERENCES members(id) ON DELETE SET NULL,
    generation    INT          NOT NULL DEFAULT 1,
    CONSTRAINT members_name_nonempty
        CHECK (length(trim(name)) > 0),
    CONSTRAINT members_birth_year_sane
        CHECK (birth_year IS NULL
               OR (birth_year BETWEEN 1 AND EXTRACT(YEAR FROM CURRENT_DATE)::int)),
    CONSTRAINT members_death_after_birth
        CHECK (death_year IS NULL
               OR birth_year IS NULL
               OR death_year >= birth_year),
    CONSTRAINT members_lifespan_sane
        CHECK (death_year IS NULL
               OR birth_year IS NULL
               OR (death_year - birth_year) <= 130),
    CONSTRAINT members_generation_positive
        CHECK (generation >= 1),
    CONSTRAINT members_no_self_parent
        CHECK (id IS NULL OR (id <> father_id AND id <> mother_id))
);

-- ---------- marriages (M:N over members, canonical ordering) ----------
CREATE TABLE marriages (
    id            BIGSERIAL PRIMARY KEY,
    member1_id    BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    member2_id    BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    married_year  INT,
    divorced_year INT,
    CONSTRAINT marriages_distinct_partners CHECK (member1_id <> member2_id),
    CONSTRAINT marriages_canonical_order   CHECK (member1_id <  member2_id),
    CONSTRAINT marriages_unique_pair       UNIQUE (member1_id, member2_id),
    CONSTRAINT marriages_divorce_after_marriage
        CHECK (divorced_year IS NULL
               OR married_year IS NULL
               OR divorced_year >= married_year)
);

-- ---------- cross-genealogy "same person" links ----------------------
-- A real-world person may be recorded in multiple family books (a daughter
-- who marries out is kept in her birth family's book AND her husband's
-- family's book). Marriages and parents are constrained to one genealogy
-- by trigger, so we expose the cross-family connection through this table.
CREATE TABLE member_links (
    id           BIGSERIAL PRIMARY KEY,
    member_a_id  BIGINT       NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    member_b_id  BIGINT       NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    link_type    VARCHAR(32)  NOT NULL DEFAULT 'same_person',
    note         TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT member_links_distinct      CHECK (member_a_id <> member_b_id),
    CONSTRAINT member_links_canonical     CHECK (member_a_id <  member_b_id),
    CONSTRAINT member_links_unique_pair   UNIQUE (member_a_id, member_b_id),
    CONSTRAINT member_links_type_known    CHECK (link_type IN ('same_person', 'related'))
);

CREATE INDEX idx_member_links_a ON member_links (member_a_id);
CREATE INDEX idx_member_links_b ON member_links (member_b_id);

-- ---------- collaborators (M:N users ↔ genealogies) -------------------
CREATE TABLE genealogy_collaborators (
    genealogy_id BIGINT       NOT NULL REFERENCES genealogies(id) ON DELETE CASCADE,
    user_id      BIGINT       NOT NULL REFERENCES users(id)        ON DELETE CASCADE,
    role         collab_role_t NOT NULL DEFAULT 'editor',
    invited_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (genealogy_id, user_id)
);

-- =====================================================================
-- Triggers
--   1. Maintain members.generation from parent generations.
--   2. Enforce: parent's birth_year < child's birth_year (cross-row
--      check that a CHECK constraint cannot express).
--   3. Enforce: parent and child must belong to same genealogy.
--   4. Father must be male, mother must be female.
-- =====================================================================

CREATE OR REPLACE FUNCTION trg_members_validate_and_set_generation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
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

CREATE TRIGGER members_validate_biur
BEFORE INSERT OR UPDATE OF father_id, mother_id, birth_year, genealogy_id
ON members
FOR EACH ROW EXECUTE FUNCTION trg_members_validate_and_set_generation();

-- Marriages must be within the same genealogy
CREATE OR REPLACE FUNCTION trg_marriages_same_genealogy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
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

CREATE TRIGGER marriages_same_genealogy_biur
BEFORE INSERT OR UPDATE OF member1_id, member2_id ON marriages
FOR EACH ROW EXECUTE FUNCTION trg_marriages_same_genealogy();

COMMIT;
