# Schema Design — 3NF Analysis

RDBMS: **PostgreSQL 16.13** (Debian)
Source DDL: [`sql/01_schema.sql`](../sql/01_schema.sql)

---

## 1. Entities and relationships (ER summary)

| Entity | Purpose |
|---|---|
| `users` | system accounts |
| `genealogies` | a family book; owned by one user |
| `members` | a person inside a genealogy |
| `marriages` | M:N spouse relationship between two `members` |
| `genealogy_collaborators` | M:N invitations: users ↔ genealogies |

```
users (1) ─< owns >─ (N) genealogies (1) ─< contains >─ (N) members
  (M) ─< collaborates on, role >─ (N) genealogies
members (M) ─< married to >─ (N) members         -- via marriages
members (1) ─< father_id >─ (N) members          -- self-ref, parent → child
members (1) ─< mother_id >─ (N) members          -- self-ref, parent → child
```

Cardinalities:
- users → genealogies: **1:N** (a user owns many genealogies)
- genealogies → members: **1:N**
- members ↔ members (parent/child): two **1:N** self-references (father, mother)
- members ↔ members (marriage): **M:N** via `marriages`
- users ↔ genealogies (collaborator): **M:N** via `genealogy_collaborators`

---

## 2. Functional dependencies and normal-form check

For each table I list the FDs and the highest normal form satisfied.

### `users`
- PK: `id`
- FDs: `id → username, password_hash, email, created_at`
       `username → id` (UNIQUE), `email → id` (UNIQUE)
- All non-key attributes depend only on `id`. No transitive deps.
- **BCNF** ✓

### `genealogies`
- PK: `id`
- FDs: `id → name, surname, compilation_date, owner_user_id, created_at`
- All attributes are simple atomic values that describe the genealogy itself.
  `surname` does not determine `name` (multiple genealogies can share a surname);
  `owner_user_id` does not determine the rest.
- **BCNF** ✓

### `members`
- PK: `id`
- FDs: `id → genealogy_id, name, gender, birth_year, death_year,
              biography, father_id, mother_id, generation`
- `generation` is **derivable** (`= 1 + max(parent.generation)`). Storing it is a
  *controlled denormalization*: enforced by trigger (see §4) so it cannot drift.
  Without it, every per-generation aggregate query would require recursive CTE
  traversal of the entire family tree — unacceptable for the required
  "average lifespan per generation" query at 100K rows.
- All other non-key attributes depend solely on `id`. No transitive deps.
- **3NF** ✓ (BCNF too if we treat `generation` as derived rather than as part
  of the schema; see note below)

### `marriages`
- PK: `id`; UNIQUE`(member1_id, member2_id)` is a candidate key.
- FDs: `id → member1_id, member2_id, married_year, divorced_year`
       `(member1_id, member2_id) → id, married_year, divorced_year`
- We enforce `member1_id < member2_id` so a marriage has exactly one canonical
  representation — prevents the duplicate-pair anomaly.
- **BCNF** ✓

### `genealogy_collaborators`
- Composite PK: `(genealogy_id, user_id)`
- FDs: `(genealogy_id, user_id) → role, invited_at`
- Pure junction table. No partial dependencies (every non-key attribute depends
  on the *full* composite key).
- **BCNF** ✓

### Summary
| Table | Highest NF | Notes |
|---|---|---|
| `users` | BCNF | |
| `genealogies` | BCNF | |
| `members` | 3NF | `generation` is derived but materialized for performance |
| `marriages` | BCNF | canonical ordering eliminates duplicate-pair anomaly |
| `genealogy_collaborators` | BCNF | |

The schema **meets the requirement of ≥3NF** (4/5 tables are BCNF; `members`
is 3NF due to the deliberate `generation` denormalization).

---

## 3. Key constraints

### Primary keys
Every table has a single-column surrogate PK (`BIGSERIAL`) except
`genealogy_collaborators`, which uses a composite PK because it has no
attributes of its own that warrant a surrogate.

### Foreign keys (with delete semantics)
| FK | Targets | On delete |
|---|---|---|
| `genealogies.owner_user_id` | `users.id` | CASCADE — drop orphan genealogies |
| `members.genealogy_id` | `genealogies.id` | CASCADE |
| `members.father_id`, `members.mother_id` | `members.id` | SET NULL — keep the descendants visible |
| `marriages.member{1,2}_id` | `members.id` | CASCADE |
| `genealogy_collaborators.{genealogy_id,user_id}` | parent | CASCADE |

### CHECK constraints
- `users.username` non-empty after trim.
- `genealogies.compilation_date <= CURRENT_DATE`.
- `members.birth_year` is between 1 and current year, inclusive.
- `members.death_year >= birth_year` and `death_year - birth_year <= 130`.
- `members.generation >= 1`.
- `members.id <> father_id AND id <> mother_id` — no self-parent.
- `marriages.member1_id <> member2_id` and `member1_id < member2_id`
  (canonical ordering, with UNIQUE pair).
- `marriages.divorced_year >= married_year`.

### Cross-row constraints (enforced by trigger)
A `CHECK` constraint can only see one row, so the following rules are enforced
by `trg_members_validate_and_set_generation` (BEFORE INSERT/UPDATE):

1. Father must exist and have `gender = 'M'`; mother must exist and have `gender = 'F'`.
2. Parent's `birth_year` must be strictly less than the child's `birth_year`.
3. Both parents must belong to the **same** genealogy as the child.
4. `members.generation` is computed automatically as
   `1 + max(parent.generation)`.

`trg_marriages_same_genealogy` enforces that both spouses share a genealogy.

---

## 4. Why store `generation`?

The *naïve* relational model has `members(id, father_id, mother_id, ...)` and
nothing else; generation is implicit. But every query in §四 of the
requirements that aggregates "by generation" (avg lifespan per generation,
"members born before their generation's avg birth year") would force a
recursive CTE traversal of the *entire* genealogy on every call.

By materializing `generation` we trade:
- **+** O(1) per-generation grouping (a regular `GROUP BY generation`)
- **+** A natural index target (`(genealogy_id, generation)`)
- **−** A small write cost (one trigger per insert)
- **−** A schema attribute that is technically derivable

Drift is impossible because no application code writes the column directly —
the trigger always overrides it on INSERT and on any UPDATE that touches
`father_id`, `mother_id`, or `genealogy_id`.

This is the canonical "compute-on-write, read-cheap" pattern used in
production ancestry systems.
