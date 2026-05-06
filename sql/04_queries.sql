-- =====================================================================
-- Required Core Queries (Project Spec §四)
-- Each query is a SINGLE SQL statement.
-- Use psql variables for parameters:  psql -v member_id=123 -f 04_queries.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- Q1 (basic): Given a member, return spouse(s) + all biological children.
-- ---------------------------------------------------------------------
-- A member can have multiple marriages; we surface every spouse and every
-- child whose father OR mother is the input member.
\echo '--- Q1: spouse(s) and children of a member ---'
SELECT
    rel.kind          AS relation,
    other.id,
    other.name,
    other.gender,
    other.birth_year,
    other.death_year
FROM (
    -- spouses
    SELECT CASE WHEN m.member1_id = :'member_id' THEN m.member2_id
                ELSE m.member1_id END AS other_id,
           'spouse' AS kind,
           m.married_year AS yr
    FROM marriages m
    WHERE m.member1_id = :'member_id' OR m.member2_id = :'member_id'

    UNION ALL

    -- children
    SELECT c.id AS other_id, 'child' AS kind, c.birth_year AS yr
    FROM members c
    WHERE c.father_id = :'member_id' OR c.mother_id = :'member_id'
) rel
JOIN members other ON other.id = rel.other_id
ORDER BY (rel.kind = 'spouse') DESC, rel.yr NULLS LAST, other.id;


-- ---------------------------------------------------------------------
-- Q2 (recursive CTE): All ancestors of a given member, with the path.
-- ---------------------------------------------------------------------
-- Walks both father_id and mother_id parents. Tracks the relationship
-- chain (e.g., 'father → father → mother') so the caller can render a
-- tree. Cycle protection via array-membership check on the path.
\echo '--- Q2: all ancestors of a member (recursive CTE) ---'
WITH RECURSIVE ancestors (id, name, gender, birth_year, death_year,
                          relation, depth, path_ids) AS (
    -- anchor: parents of the input member
    SELECT p.id, p.name, p.gender, p.birth_year, p.death_year,
           CASE WHEN m.father_id = p.id THEN 'father' ELSE 'mother' END,
           1 AS depth,
           ARRAY[p.id]
    FROM   members m
    JOIN   members p ON p.id = m.father_id OR p.id = m.mother_id
    WHERE  m.id = :'member_id'

    UNION ALL

    -- recursive step: for each known ancestor, add their parents
    SELECT pp.id, pp.name, pp.gender, pp.birth_year, pp.death_year,
           a.relation || ' -> ' ||
              CASE WHEN a2.father_id = pp.id THEN 'father' ELSE 'mother' END,
           a.depth + 1,
           a.path_ids || pp.id
    FROM   ancestors a
    JOIN   members  a2 ON a2.id = a.id
    JOIN   members  pp ON pp.id = a2.father_id OR pp.id = a2.mother_id
    WHERE  pp.id <> ALL (a.path_ids)             -- cycle guard
       AND a.depth < 50                          -- depth guard
)
SELECT depth, id, name, gender, birth_year, death_year, relation
FROM   ancestors
ORDER  BY depth, id;


-- ---------------------------------------------------------------------
-- Q3 (statistics): Generation with the longest avg lifespan (for a given genealogy).
-- ---------------------------------------------------------------------
-- Lifespan = death_year - birth_year. Members with NULL death (still alive)
-- are excluded. Tie-breaker: lower generation number wins.
\echo '--- Q3: generation with longest average lifespan ---'
SELECT generation,
       count(*)                                            AS members_with_known_lifespan,
       round(avg(death_year - birth_year)::numeric, 2)     AS avg_lifespan
FROM   members
WHERE  genealogy_id = :'genealogy_id'
  AND  birth_year IS NOT NULL
  AND  death_year IS NOT NULL
GROUP  BY generation
ORDER  BY avg_lifespan DESC, generation ASC
LIMIT  1;


-- ---------------------------------------------------------------------
-- Q4: Males >50 years old who have no marriage record.
-- ---------------------------------------------------------------------
-- Age = (current year) - birth_year. We use the alive subset (no death year)
-- to match the natural reading "currently older than 50 and unmarried".
\echo '--- Q4: unmarried males over 50 ---'
SELECT m.id, m.name, m.genealogy_id, m.birth_year,
       (EXTRACT(YEAR FROM CURRENT_DATE)::int - m.birth_year) AS age
FROM   members m
WHERE  m.gender = 'M'
  AND  m.birth_year IS NOT NULL
  AND  (EXTRACT(YEAR FROM CURRENT_DATE)::int - m.birth_year) > 50
  AND  m.death_year IS NULL                                 -- still alive
  AND  NOT EXISTS (
        SELECT 1 FROM marriages mar
        WHERE  mar.member1_id = m.id OR mar.member2_id = m.id
       )
ORDER  BY age DESC, m.id
LIMIT  100;       -- preview; remove for the full set


-- ---------------------------------------------------------------------
-- Q5: Members born earlier than the average birth year of their generation.
-- ---------------------------------------------------------------------
-- Per genealogy AND per generation. A window function gives the avg without
-- a self-join; the outer filter keeps members below their generation's avg.
\echo '--- Q5: members born before their generation avg ---'
SELECT id, name, genealogy_id, generation, birth_year, gen_avg_birth
FROM (
    SELECT id, name, genealogy_id, generation, birth_year,
           round(avg(birth_year) OVER (PARTITION BY genealogy_id, generation), 1)
               AS gen_avg_birth
    FROM   members
    WHERE  birth_year IS NOT NULL
) t
WHERE  birth_year < gen_avg_birth
ORDER  BY genealogy_id, generation, birth_year
LIMIT  100;       -- preview; remove for the full set
