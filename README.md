# 寻根溯源 — Genealogy Management System

A Chinese-style 家谱 management system covering all five deliverables of the
《数据库应用实践》 project.

- **RDBMS**: PostgreSQL **16.13** (Debian, official Docker image)
- **App**: Python **3.13.13** + Flask **3.1**
- **Driver**: psycopg **3** with connection pooling
- **Data**: 100,545 members across 10 genealogies (one with 52,425 members and 32 main-line generations)

---

## Quick start

### 1. Bring up PostgreSQL (Docker)
```bash
docker run -d \
  --name genealogy-pg \
  -e POSTGRES_USER=genealogy \
  -e POSTGRES_PASSWORD=genealogy_dev_pw \
  -e POSTGRES_DB=genealogy \
  -p 5432:5432 \
  -v "$PWD/pgdata:/var/lib/postgresql/data" \
  postgres:16

# enable pg_trgm
docker exec genealogy-pg psql -U genealogy -d genealogy \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

### 2. Apply schema and indexes
```bash
docker cp sql/01_schema.sql  genealogy-pg:/tmp/
docker cp sql/02_indexes.sql genealogy-pg:/tmp/
docker exec genealogy-pg psql -U genealogy -d genealogy -f /tmp/01_schema.sql
docker exec genealogy-pg psql -U genealogy -d genealogy -f /tmp/02_indexes.sql
```

### 3. Generate and load synthetic data
```bash
uv sync                       # install Python dependencies
uv run python data/generate_data.py   # writes data/csv/*.csv (~30s)
uv run python data/load_data.py        # COPY into PostgreSQL (~3s)
```

Alternatively, restore the included pg_dump:
```bash
docker cp backup/genealogy.dump genealogy-pg:/tmp/
docker exec genealogy-pg pg_restore -U genealogy -d genealogy --clean --if-exists /tmp/genealogy.dump
```

### 4. Run the web app
```bash
uv run python app/app.py
# open http://127.0.0.1:5000
# demo login:  user1 / password1   ... user5 / password5
```

---

## Repository layout

```
database/
├── README.md                 ← you are here
├── PROJECT_REQUIREMENTS.md   ← original spec (Chinese)
├── pyproject.toml            ← uv project + dependencies
├── .env                      ← DATABASE_URL, FLASK_SECRET_KEY
├── sql/
│   ├── 01_schema.sql         ← tables, types, triggers, constraints
│   ├── 02_indexes.sql        ← B-tree + GIN trgm indexes
│   ├── 04_queries.sql        ← the 5 required queries (single-statement each)
│   └── benchmark_4gen.sql    ← 4-generation EXPLAIN harness
├── data/
│   ├── generate_data.py      ← Faker-based generator
│   ├── load_data.py          ← psycopg COPY loader
│   └── csv/                  ← generated CSVs (gitignored)
├── app/
│   ├── app.py                ← Flask routes
│   ├── db.py                 ← psycopg connection pool helpers
│   ├── templates/            ← Jinja2 templates
│   └── static/style.css
├── docs/
│   ├── ER_diagram.md         ← Mermaid ER diagram + cardinalities
│   ├── schema_design.md      ← 3NF analysis, constraint catalog
│   └── index_strategy.md     ← EXPLAIN comparison (1573× speedup)
└── backup/
    ├── genealogy.dump        ← pg_dump custom format (binary)
    └── genealogy.schema.sql  ← schema-only SQL (human-readable)
```

---

## What each task delivers

| § | Requirement | Where |
|---|---|---|
| 1 | Application UI: login, register, dashboard, CRUD, fuzzy search, tree, ancestor query, kinship query | `app/` |
| 2 | ER diagram, relational schema in ≥3NF, PK/FK/CHECK constraints | `docs/ER_diagram.md`, `docs/schema_design.md`, `sql/01_schema.sql` |
| 3 | Generator + bulk import via `COPY` | `data/generate_data.py`, `data/load_data.py` |
| 4 | The 5 required SQL queries, each as a single statement | `sql/04_queries.sql` |
| 5 | Index strategy + EXPLAIN comparison (with vs. without) | `docs/index_strategy.md`, `sql/02_indexes.sql` |

---

## Demo data summary

```
genealogy_id | n_members | max_generation
-------------+-----------+----------------
           1 |    52,425 |      42        ← required ≥ 50K members and ≥ 30 gens
           2 |     6,111 |      20
           3 |     5,501 |      20
           4 |     5,322 |      20
           5 |     5,558 |      20
           6 |     5,236 |      19
           7 |     4,849 |      20
           8 |     4,970 |      20
           9 |     4,831 |      20
          10 |     5,742 |      20
total        |   100,545 |
```

The trigger correctly auto-computes `generation` (max 42 in g1 = trunk depth 32
+ side-branch overlap), and every member has at least one kinship edge.

---

## Reproducing the headline EXPLAIN benchmark

```bash
# drop indexes for a cold-start measurement
docker exec genealogy-pg psql -U genealogy -d genealogy -c "
  DROP INDEX IF EXISTS idx_members_father_id, idx_members_mother_id,
                       idx_members_genealogy_generation, idx_members_name_trgm,
                       idx_marriages_member2;
  ANALYZE;"

# baseline
docker exec genealogy-pg psql -U genealogy -d genealogy \
  -v ancestor_id=1 -f /tmp/benchmark_4gen.sql

# rebuild indexes
docker exec genealogy-pg psql -U genealogy -d genealogy -f /tmp/02_indexes.sql

# indexed run
docker exec genealogy-pg psql -U genealogy -d genealogy \
  -v ancestor_id=1 -f /tmp/benchmark_4gen.sql
```

Result: **523.9 ms → 0.33 ms** (≈ 1573× speedup) for the 4-generation
descendant query of a 52K-member genealogy. Full plans in
[`docs/index_strategy.md`](docs/index_strategy.md).
