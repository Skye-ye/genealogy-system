-- =====================================================================
-- Benchmark: find all great-grandchildren (4-generation descendant) of a
-- given member. Run before and after building 02_indexes.sql.
--
-- Usage:  psql ... -v ancestor_id=<id> -f benchmark_4gen.sql
-- =====================================================================
\timing on
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
WITH RECURSIVE descendants (id, name, gender, depth, path) AS (
    -- start at the chosen ancestor (depth 0)
    SELECT id, name, gender, 0 AS depth, ARRAY[id]
    FROM   members WHERE id = :'ancestor_id'

    UNION ALL

    -- one generation down per recursive step; stop after 4 generations
    SELECT c.id, c.name, c.gender, d.depth + 1, d.path || c.id
    FROM   descendants d
    JOIN   members     c
           ON  c.father_id = d.id OR c.mother_id = d.id
    WHERE  d.depth < 4
       AND c.id <> ALL (d.path)   -- cycle guard (defensive; tree has no cycles)
)
SELECT id, name, gender, depth
FROM   descendants
WHERE  depth = 4;     -- leaf great-grandchildren only
