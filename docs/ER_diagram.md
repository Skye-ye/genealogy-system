# ER Diagram

```mermaid
erDiagram
    users ||--o{ genealogies : "owns"
    users }o--o{ genealogies : "collaborates on"
    genealogies ||--o{ members : "contains"
    members }o--o{ members : "married to"
    members }o--|| members : "father of"
    members }o--|| members : "mother of"

    users {
        BIGINT  id PK
        VARCHAR username UK
        VARCHAR password_hash
        VARCHAR email UK
        TIMESTAMPTZ created_at
    }

    genealogies {
        BIGINT  id PK
        VARCHAR name
        VARCHAR surname
        DATE    compilation_date
        BIGINT  owner_user_id FK
        TIMESTAMPTZ created_at
    }

    members {
        BIGINT  id PK
        BIGINT  genealogy_id FK
        VARCHAR name
        gender_t gender
        INT     birth_year
        INT     death_year
        TEXT    biography
        BIGINT  father_id FK
        BIGINT  mother_id FK
        INT     generation
    }

    marriages {
        BIGINT  id PK
        BIGINT  member1_id FK
        BIGINT  member2_id FK
        INT     married_year
        INT     divorced_year
    }

    genealogy_collaborators {
        BIGINT  genealogy_id PK,FK
        BIGINT  user_id PK,FK
        collab_role_t role
        TIMESTAMPTZ invited_at
    }
```

## Cardinality summary

| Source | Type | Target | Notes |
|---|---|---|---|
| `users` | 1 : N | `genealogies` | `genealogies.owner_user_id` |
| `users` | M : N | `genealogies` | via `genealogy_collaborators` |
| `genealogies` | 1 : N | `members` | a member belongs to exactly one genealogy |
| `members` | 1 : N | `members` | self-ref via `father_id` (each child has at most one father) |
| `members` | 1 : N | `members` | self-ref via `mother_id` (each child has at most one mother) |
| `members` | M : N | `members` | via `marriages`, with canonical ordering `member1_id < member2_id` |

## Constraint highlights

- Primary keys: surrogate `BIGSERIAL` on every entity (composite for the `genealogy_collaborators` junction).
- Foreign keys: `ON DELETE CASCADE` for ownership chains; parent FKs use `ON DELETE SET NULL` so descendants stay visible if a parent is removed.
- CHECK constraints (single-row): birth-year sanity, death ≥ birth, lifespan ≤ 130, marriage canonical ordering, no self-parent.
- Cross-row constraints (triggers): father/mother must exist with the right gender and same genealogy; parent's birth year must precede child's; `generation` is auto-computed as `1 + max(parent.generation)`.

See [`schema_design.md`](schema_design.md) for the 3NF analysis.
