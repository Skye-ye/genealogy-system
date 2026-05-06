# Index Strategy & EXPLAIN Analysis

RDBMS: **PostgreSQL 16.13**
Source: [`sql/02_indexes.sql`](../sql/02_indexes.sql)
Benchmark script: [`sql/benchmark_4gen.sql`](../sql/benchmark_4gen.sql)

---

## 1. Indexes created

| Index | Table | Type | Purpose |
|---|---|---|---|
| `members_pkey` | `members` | B-tree (auto) | PK lookup |
| `idx_members_father_id` | `members(father_id) WHERE NOT NULL` | Partial B-tree | "Find children given a father" — drives the recursive descendant query |
| `idx_members_mother_id` | `members(mother_id) WHERE NOT NULL` | Partial B-tree | Same as above for matrilineal hops |
| `idx_members_genealogy_generation` | `members(genealogy_id, generation)` | Composite B-tree | Per-genealogy and per-generation aggregates (Q3, Q5) |
| `idx_members_name_trgm` | `members(name gin_trgm_ops)` | GIN (pg_trgm) | Fuzzy `LIKE '%substr%'` search |
| `idx_marriages_member2` | `marriages(member2_id)` | B-tree | Reverse spouse lookup (the UNIQUE constraint already covers `member1_id`) |

The `WHERE NOT NULL` partial-index clauses on `father_id`/`mother_id` keep the
indexes small (≈ half the rows have a parent — the wives/husbands "married in"
have NULL parents).

---

## 2. The headline benchmark — 4-generation descendant query

> *"Find all great-great-grandchildren of a given member"* (project spec §五).

Query: a recursive CTE that walks 4 generations down via `father_id`/`mother_id`.
See [`sql/benchmark_4gen.sql`](../sql/benchmark_4gen.sql).
Target: `ancestor_id = 1` (root of the 52,425-member 李氏宗谱), 31 leaf
descendants at depth = 4.

### Without indexes
```
Execution Time: 523.941 ms
Plan:
  CTE Scan on descendants (rows=31)
  ->  Recursive Union  (rows=68)
        ->  Index Scan using members_pkey (anchor)
        ->  Nested Loop
              Join Filter: ((c.father_id = d.id) OR (c.mother_id = d.id))
              Rows Removed by Join Filter: 744 020
              ->  Seq Scan on members c  (rows=100545, loops=5)   ← FULL SCAN
              ->  WorkTable Scan on descendants d
Buffers: shared hit=8323
```

### With indexes
```
Execution Time: 0.333 ms
Plan:
  CTE Scan on descendants (rows=31)
  ->  Recursive Union  (rows=68)
        ->  Index Scan using members_pkey (anchor)
        ->  Nested Loop
              ->  WorkTable Scan on descendants d
              ->  Bitmap Heap Scan on members c
                    Recheck Cond: ((father_id = d.id) OR (mother_id = d.id))
                    ->  BitmapOr
                          ->  Bitmap Index Scan on idx_members_father_id
                          ->  Bitmap Index Scan on idx_members_mother_id
Buffers: shared hit=172 read=8
```

### Comparison
| Metric | No indexes | Indexed | Improvement |
|---|---|---|---|
| Execution time | 523.94 ms | **0.33 ms** | **≈ 1573×** |
| Buffer hits | 8,323 | 180 | 46× fewer |
| Strategy | 5× full Seq Scan of 100,545-row table | BitmapOr over `idx_members_father_id` ∪ `idx_members_mother_id` |

**Why the gap is so big**: each recursive step needs the children of every row
in the work table. Without an index on `father_id`/`mother_id` the planner must
do a full sequential scan of `members` for each iteration of the recursion (5
iterations, depth 0→4). With the partial B-trees, each lookup is `O(log n)`
plus a small heap fetch.

---

## 3. Fuzzy-name search — pg_trgm trade-off

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, name FROM members WHERE name LIKE '%李建%' LIMIT 50;
```

For our 100K-row Chinese dataset the planner often **prefers Seq Scan** even
with the GIN trigram index in place. Reason: short CJK substrings (e.g.,
`'李建'`) decompose into many overlapping trigrams that match a large fraction
of names; the index recheck step then has to filter most matches out, and the
index path becomes slower than a single sequential pass.

| Pattern | Plan | Time |
|---|---|---|
| `name LIKE '%李建%' LIMIT 50` | Seq Scan | ~0.2 ms (LIMIT short-circuits) |
| `count(*) WHERE name LIKE '%淑芬%'` | Seq Scan | 7.0 ms |
| Same, with `enable_seqscan=off` (forces GIN) | Bitmap Heap Scan | 19.9 ms |

The trigram index still pays off for:
- Longer patterns (≥3 CJK characters) where the trigram set selects far fewer rows.
- Trigram similarity ranking (`name % 'query'` and `<->` distance).
- Datasets where Latin-script names dominate.

For production-grade CJK fuzzy search the proper extension would be
`pg_bigm` (bigram on CJK), but that is outside the scope of this assignment.

---

## 4. Composite index `(genealogy_id, generation)`

This index supports two distinct query shapes from the requirements:

```sql
-- Q3: per-generation aggregate within one genealogy
SELECT generation, avg(death_year - birth_year)
FROM   members WHERE genealogy_id = 1 GROUP BY generation;
```

```sql
-- Dashboard: members per genealogy
SELECT count(*) FROM members WHERE genealogy_id = 5;
```

Because PostgreSQL B-tree composite indexes are usable for **prefix** queries,
the same structure serves both `(genealogy_id)` lookups and
`(genealogy_id, generation)` range/group queries.

---

## 5. How to reproduce

```bash
# fresh start
docker exec genealogy-pg psql -U genealogy -d genealogy -c "DROP INDEX IF EXISTS idx_members_father_id, idx_members_mother_id, idx_members_genealogy_generation, idx_members_name_trgm, idx_marriages_member2; ANALYZE;"

# baseline (no index)
docker exec genealogy-pg psql -U genealogy -d genealogy -v ancestor_id=1 -f /work/sql/benchmark_4gen.sql

# build indexes
docker exec genealogy-pg psql -U genealogy -d genealogy -f /work/sql/02_indexes.sql

# indexed run
docker exec genealogy-pg psql -U genealogy -d genealogy -v ancestor_id=1 -f /work/sql/benchmark_4gen.sql
```

Save both EXPLAIN outputs side-by-side for the report (already captured above).
