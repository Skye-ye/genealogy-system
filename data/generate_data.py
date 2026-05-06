"""Generate genealogy CSVs ready for COPY into PostgreSQL.

Output (in data/csv/):
    users.csv, genealogies.csv, members.csv, marriages.csv, collaborators.csv

Schema contract (must match sql/01_schema.sql):
    members are emitted in topological order (parents before children).
    Explicit ids are assigned so the COPY ordering satisfies FK and trigger
    constraints — birth_year(parent) < birth_year(child), gender of father
    is 'M', gender of mother is 'F', everyone in one genealogy.
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from pathlib import Path

from faker import Faker
from werkzeug.security import generate_password_hash

# ---------------------------------------------------------------------------
# Tunables — change here, not in the body of the script.
# ---------------------------------------------------------------------------
SEED = 20260506
NUM_USERS = 5
NUM_GENEALOGIES = 10
BIG_GENEALOGY_TARGET_SIZE = 55_000
BIG_GENEALOGY_TARGET_DEPTH = 32
SMALL_GENEALOGY_TARGET_SIZE = 6_500
SMALL_GENEALOGY_TARGET_DEPTH = 12

# Birth-year offsets between generations
PARENT_AGE_AT_CHILD = (18, 38)
SPOUSE_AGE_DIFF = (-3, 5)

# Children-per-couple distribution. Tuned for ~1.5× growth per generation
# so a 30-generation tree can reach 50K members. We rely on the "force at
# least one male heir" rule below to prevent extinction; additional children
# beyond the heir come from this distribution.
EXTRA_CHILDREN_DISTRIBUTION = [
    (0, 0.18),
    (1, 0.32),
    (2, 0.28),
    (3, 0.14),
    (4, 0.06),
    (5, 0.02),
]

# Lifespan & gender splits
LIFESPAN_RANGE = (35, 95)
P_MALE_CHILD = 0.55      # patrilineal genealogies tend to record more males
P_MALE_MARRIES = 0.95
P_FEMALE_MARRIES = 0.78  # only matters for marriage edges, not children

OUT_DIR = Path(__file__).parent / "csv"

CHINESE_SURNAMES = [
    "李", "王", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴",
]

# ---------------------------------------------------------------------------

fake = Faker("zh_CN")
Faker.seed(SEED)
random.seed(SEED)


@dataclass
class Member:
    id: int
    genealogy_id: int
    name: str
    gender: str
    birth_year: int
    death_year: int | None
    biography: str
    father_id: int | None
    mother_id: int | None


@dataclass
class Genealogy:
    id: int
    name: str
    surname: str
    compilation_date: str
    owner_user_id: int


@dataclass
class TreeBuildResult:
    members: list[Member] = field(default_factory=list)
    marriages: list[tuple[int, int, int | None]] = field(default_factory=list)
    max_generation: int = 1


def _weighted_choice(pairs: list[tuple[int, float]]) -> int:
    values, weights = zip(*pairs)
    return random.choices(values, weights=weights, k=1)[0]


def _given_name() -> str:
    # Faker.first_name returns a single given name
    return fake.first_name()[:8]


def _make_lifespan(birth_year: int) -> int | None:
    if random.random() < 0.05:
        return None  # still alive
    span = random.randint(*LIFESPAN_RANGE)
    return birth_year + span


def build_genealogy(
    *,
    genealogy_id: int,
    surname: str,
    target_size: int,
    target_depth: int,
    next_member_id: int,
    root_birth_year: int,
) -> tuple[TreeBuildResult, int]:
    """Build a patrilineal tree.

    Algorithm: BFS by generation. At each generation we materialize children
    from each currently-living couple. A male child gets a married-in wife
    (recorded as a member with no parents) so the next generation has parents.
    """
    res = TreeBuildResult()

    other_surnames = [s for s in CHINESE_SURNAMES if s != surname]

    def emit_member(
        gender: str,
        birth_year: int,
        father_id: int | None,
        mother_id: int | None,
        *,
        married_in: bool = False,
    ) -> Member:
        """A born-in member (has a parent OR is the founder) carries the
        genealogy's surname. A married-in spouse keeps their birth-family
        surname — picked from the other surnames in CHINESE_SURNAMES so
        every name in the dataset starts with a real Chinese surname."""
        nonlocal next_member_id
        member_surname = random.choice(other_surnames) if married_in else surname
        m = Member(
            id=next_member_id,
            genealogy_id=genealogy_id,
            name=member_surname + _given_name(),
            gender=gender,
            birth_year=birth_year,
            death_year=_make_lifespan(birth_year),
            biography=fake.sentence(nb_words=8)[:200],
            father_id=father_id,
            mother_id=mother_id,
        )
        next_member_id += 1
        res.members.append(m)
        return m

    # PHASE 1 — build the trunk: one couple per generation, deterministic.
    # This guarantees we hit target_depth even with small target_size.
    root_father = emit_member("M", root_birth_year, None, None)
    wife_birth = root_birth_year + random.randint(*SPOUSE_AGE_DIFF)
    root_mother = emit_member("F", wife_birth, None, None, married_in=True)
    res.marriages.append((root_father.id, root_mother.id, root_birth_year + 20))
    res.max_generation = 1
    main_line: list[tuple[Member, Member]] = [(root_father, root_mother)]

    for gen in range(2, target_depth + 1):
        father, mother = main_line[-1]
        base_year = max(father.birth_year, mother.birth_year)
        heir_birth = base_year + random.randint(*PARENT_AGE_AT_CHILD)
        heir = emit_member("M", heir_birth, father.id, mother.id)
        wife_birth_y = heir_birth + random.randint(*SPOUSE_AGE_DIFF)
        heir_wife = emit_member("F", wife_birth_y, None, None, married_in=True)
        res.marriages.append(
            (heir.id, heir_wife.id, heir_birth + random.randint(18, 28))
        )
        main_line.append((heir, heir_wife))
        res.max_generation = gen

    # PHASE 2 — fill with side branches off every main-line couple, using
    # remaining size budget. We grow each side branch a few generations deep
    # so members are tightly connected, not isolated leaves.
    def grow_side_branch(
        seed_father: Member,
        seed_mother: Member,
        max_size: int,
        max_extra_depth: int,
    ) -> None:
        if max_size <= 0:
            return
        # The seed couple is already in res.members from the main line; we
        # only count NEW members against max_size here.
        couples = [(seed_father, seed_mother)]
        size_so_far = 0
        for offset in range(1, max_extra_depth + 1):
            if size_so_far >= max_size or not couples:
                break
            next_couples: list[tuple[Member, Member]] = []
            for f, m in couples:
                if size_so_far >= max_size:
                    break
                base_y = max(f.birth_year, m.birth_year)
                # Forced heir keeps this side-branch lineage alive across
                # generations (otherwise random branching frequently dies
                # out and the budget is left unspent).
                if size_so_far + 2 <= max_size:
                    heir_birth = base_y + random.randint(*PARENT_AGE_AT_CHILD)
                    heir = emit_member("M", heir_birth, f.id, m.id)
                    size_so_far += 1
                    wb = heir_birth + random.randint(*SPOUSE_AGE_DIFF)
                    heir_wife = emit_member("F", wb, None, None, married_in=True)
                    size_so_far += 1
                    res.marriages.append(
                        (heir.id, heir_wife.id, heir_birth + random.randint(18, 28))
                    )
                    next_couples.append((heir, heir_wife))
                # Extra children
                n_kids = _weighted_choice(EXTRA_CHILDREN_DISTRIBUTION)
                for _ in range(n_kids):
                    if size_so_far >= max_size:
                        break
                    child_birth = base_y + random.randint(*PARENT_AGE_AT_CHILD)
                    gender = "M" if random.random() < P_MALE_CHILD else "F"
                    child = emit_member(gender, child_birth, f.id, m.id)
                    size_so_far += 1
                    if gender == "M" and random.random() < P_MALE_MARRIES:
                        if size_so_far >= max_size:
                            break
                        wb = child_birth + random.randint(*SPOUSE_AGE_DIFF)
                        wife = emit_member("F", wb, None, None, married_in=True)
                        size_so_far += 1
                        res.marriages.append(
                            (child.id, wife.id, child_birth + random.randint(18, 28))
                        )
                        next_couples.append((child, wife))
                    elif gender == "F" and random.random() < P_FEMALE_MARRIES:
                        if size_so_far >= max_size:
                            break
                        hb = child_birth + random.randint(*SPOUSE_AGE_DIFF)
                        husband = emit_member("M", hb, None, None, married_in=True)
                        size_so_far += 1
                        a, b = sorted([child.id, husband.id])
                        res.marriages.append(
                            (a, b, child_birth + random.randint(18, 28))
                        )
            couples = next_couples

    remaining_budget = max(0, target_size - len(res.members))
    if main_line and remaining_budget > 0:
        # Distribute budget across all main-line couples (skip root and
        # very-recent couples slightly so birth years stay sane).
        per_couple = max(4, remaining_budget // len(main_line))
        # Side-branch depth: deep enough that the budget is spent, but not
        # so deep that descendants would be born after CURRENT_YEAR.
        side_depth = 8 if target_size < 10_000 else 12
        for f, m in main_line:
            if len(res.members) >= target_size:
                break
            grow_side_branch(f, m, per_couple, side_depth)

    return res, next_member_id


def _ensure_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def _write_csv(name: str, rows, header: list[str]) -> Path:
    path = OUT_DIR / name
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)
    return path


def main() -> None:
    _ensure_dir()
    print(f"writing CSVs to {OUT_DIR}")

    # ---- users ----------------------------------------------------------
    # user1 is the seeded admin (see docs/index_strategy.md / app login).
    users = []
    for i in range(1, NUM_USERS + 1):
        username = f"user{i}"
        password_hash = generate_password_hash(f"password{i}")
        email = f"{username}@example.com"
        is_admin = (i == 1)
        users.append((i, username, password_hash, email, is_admin))
    _write_csv(
        "users.csv",
        users,
        ["id", "username", "password_hash", "email", "is_admin"],
    )
    print(f"  users: {len(users)} (admin: user1)")

    # ---- genealogies ----------------------------------------------------
    genealogies: list[Genealogy] = []
    for gid in range(1, NUM_GENEALOGIES + 1):
        surname = CHINESE_SURNAMES[(gid - 1) % len(CHINESE_SURNAMES)]
        owner = ((gid - 1) % NUM_USERS) + 1
        compilation = fake.date_between(start_date="-5y", end_date="today")
        genealogies.append(
            Genealogy(
                id=gid,
                name=f"{surname}氏宗谱",
                surname=surname,
                compilation_date=str(compilation),
                owner_user_id=owner,
            )
        )
    _write_csv(
        "genealogies.csv",
        [(g.id, g.name, g.surname, g.compilation_date, g.owner_user_id) for g in genealogies],
        ["id", "name", "surname", "compilation_date", "owner_user_id"],
    )
    print(f"  genealogies: {len(genealogies)}")

    # ---- members + marriages -------------------------------------------
    next_member_id = 1
    member_rows: list[tuple] = []
    marriage_rows: list[tuple] = []
    summary: list[tuple[int, int, int]] = []

    for g in genealogies:
        if g.id == 1:
            target_size = BIG_GENEALOGY_TARGET_SIZE
            target_depth = BIG_GENEALOGY_TARGET_DEPTH
            # Worst-case birth year = root + (target_depth + side_depth) * MAX
            # gap. With 32 + 12 = 44 generations × 38yr we need root ≤ 354 AD
            # to keep all descendants ≤ 2026.
            root_birth = random.randint(50, 300)
        else:
            target_size = SMALL_GENEALOGY_TARGET_SIZE
            target_depth = SMALL_GENEALOGY_TARGET_DEPTH
            # 12 + 8 = 20 generations × 38yr ≈ 760yr, so root ≤ 1266 AD.
            root_birth = random.randint(1100, 1230)

        result, next_member_id = build_genealogy(
            genealogy_id=g.id,
            surname=g.surname,
            target_size=target_size,
            target_depth=target_depth,
            next_member_id=next_member_id,
            root_birth_year=root_birth,
        )

        for m in result.members:
            member_rows.append((
                m.id, m.genealogy_id, m.name, m.gender,
                m.birth_year, m.death_year, m.biography,
                m.father_id if m.father_id is not None else "",
                m.mother_id if m.mother_id is not None else "",
            ))
        for a, b, year in result.marriages:
            marriage_rows.append((a, b, year if year is not None else ""))

        summary.append((g.id, len(result.members), result.max_generation))
        print(f"  genealogy {g.id} ({g.name}): {len(result.members)} members, depth {result.max_generation}")

    _write_csv(
        "members.csv",
        member_rows,
        ["id", "genealogy_id", "name", "gender", "birth_year", "death_year",
         "biography", "father_id", "mother_id"],
    )
    _write_csv(
        "marriages.csv",
        marriage_rows,
        ["member1_id", "member2_id", "married_year"],
    )
    print(f"  members total: {len(member_rows)}")
    print(f"  marriages total: {len(marriage_rows)}")

    # ---- collaborators (a couple of cross-invites for demo) ------------
    collabs = [
        (1, 2, "editor"),
        (1, 3, "viewer"),
        (2, 1, "editor"),
        (3, 4, "editor"),
    ]
    _write_csv(
        "collaborators.csv",
        collabs,
        ["genealogy_id", "user_id", "role"],
    )
    print(f"  collaborators: {len(collabs)}")

    # ---- summary check --------------------------------------------------
    total = len(member_rows)
    biggest = max(summary, key=lambda s: s[1])
    deepest = max(summary, key=lambda s: s[2])
    print()
    print("=== generation summary ===")
    print(f"  total members:    {total:>8,}    (need ≥ 100,000)")
    print(f"  largest genealogy: g{biggest[0]} = {biggest[1]:,}    (need ≥ 50,000)")
    print(f"  deepest genealogy: g{deepest[0]} = depth {deepest[2]}  (need ≥ 30)")


if __name__ == "__main__":
    main()
