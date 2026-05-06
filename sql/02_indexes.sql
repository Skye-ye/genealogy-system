-- =====================================================================
-- Index strategy
-- =====================================================================
-- Goals (per spec §五):
--   * "Fuzzy search by name"      -> GIN trgm index on members.name
--   * "Find children by parent"   -> B-tree on father_id and mother_id
--   * "Members of a genealogy"    -> B-tree on (genealogy_id, generation)
--                                    (composite supports both single-column
--                                     and per-generation queries)
--   * "Spouse lookup"             -> already covered by UNIQUE(member1_id,
--                                     member2_id) plus a complementary index
--                                     on member2_id for the reverse lookup.
-- =====================================================================

CREATE INDEX IF NOT EXISTS idx_members_father_id
    ON members (father_id) WHERE father_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_members_mother_id
    ON members (mother_id) WHERE mother_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_members_genealogy_generation
    ON members (genealogy_id, generation);

-- pg_trgm supports LIKE '%substr%' and trigram similarity for fuzzy match.
-- Prerequisite extension is created in 00_extensions.sql / by bootstrap.
CREATE INDEX IF NOT EXISTS idx_members_name_trgm
    ON members USING GIN (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_marriages_member2
    ON marriages (member2_id);

-- Refresh planner stats after building indexes
ANALYZE members;
ANALYZE marriages;
