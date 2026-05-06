"""Bulk-load generated CSVs into PostgreSQL via COPY.

Order matters: parents in users/genealogies/members go in before their FKs are
resolved. members are emitted in topological order by the generator (parent ids
are always strictly less than child ids), so the validating BEFORE trigger will
find each parent at the time the child row is processed.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
DSN = os.environ["DATABASE_URL"]
CSV_DIR = Path(__file__).parent / "csv"


SEED_USER_MAX_ID = 5  # ids 1..5 are seeded by generate_data.py


def _truncate(cur: psycopg.Cursor) -> None:
    """Wipe synthetic data without destroying user-registered accounts.

    Genealogies (and everything that depends on them) are blown away — they
    are entirely synthetic. Users with id > SEED_USER_MAX_ID were created
    via the web app's /register flow and must survive a re-load.
    """
    cur.execute(
        "TRUNCATE genealogies, members, marriages, genealogy_collaborators "
        "RESTART IDENTITY CASCADE"
    )
    # Recreate the seed users from CSV: drop the old seed rows, leave the
    # rest. After COPY we advance users_id_seq so future BIGSERIAL inserts
    # don't collide with anyone.
    cur.execute("DELETE FROM users WHERE id <= %s", (SEED_USER_MAX_ID,))


def _copy_table(cur: psycopg.Cursor, table: str, columns: list[str], csv_path: Path) -> int:
    cols = ", ".join(columns)
    copy_sql = (
        f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '')"
    )
    with csv_path.open("rb") as f, cur.copy(copy_sql) as copy:
        # Stream the file in chunks (psycopg copy.write accepts bytes).
        while chunk := f.read(1024 * 1024):
            copy.write(chunk)
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def _resync_sequences(cur: psycopg.Cursor) -> None:
    # We assigned explicit ids in CSVs; advance BIGSERIAL sequences past max
    # so future inserts via the app don't collide.
    for table, col, seq in [
        ("users", "id", "users_id_seq"),
        ("genealogies", "id", "genealogies_id_seq"),
        ("members", "id", "members_id_seq"),
        ("marriages", "id", "marriages_id_seq"),
    ]:
        cur.execute(f"SELECT setval('{seq}', COALESCE((SELECT MAX({col}) FROM {table}), 1))")


def main() -> None:
    print(f"loading from {CSV_DIR}")
    if not (CSV_DIR / "members.csv").exists():
        raise SystemExit("CSVs missing — run data/generate_data.py first.")

    t0 = time.time()
    with psycopg.connect(DSN) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            print("  truncating existing data...")
            _truncate(cur)

            print("  COPY users.csv ...")
            n = _copy_table(cur, "users",
                            ["id", "username", "password_hash", "email", "is_admin"],
                            CSV_DIR / "users.csv")
            print(f"    -> {n} rows")

            print("  COPY genealogies.csv ...")
            n = _copy_table(cur, "genealogies",
                            ["id", "name", "surname", "compilation_date", "owner_user_id"],
                            CSV_DIR / "genealogies.csv")
            print(f"    -> {n} rows")

            print("  COPY members.csv ...")
            n = _copy_table(cur, "members",
                            ["id", "genealogy_id", "name", "gender", "birth_year",
                             "death_year", "biography", "father_id", "mother_id"],
                            CSV_DIR / "members.csv")
            print(f"    -> {n} rows")

            print("  COPY marriages.csv ...")
            n = _copy_table(cur, "marriages",
                            ["member1_id", "member2_id", "married_year"],
                            CSV_DIR / "marriages.csv")
            print(f"    -> {n} rows")

            print("  COPY collaborators.csv ...")
            n = _copy_table(cur, "genealogy_collaborators",
                            ["genealogy_id", "user_id", "role"],
                            CSV_DIR / "collaborators.csv")
            print(f"    -> {n} rows")

            print("  resyncing sequences...")
            _resync_sequences(cur)

            print("  ANALYZE ...")
            cur.execute("ANALYZE")

        conn.commit()

    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
