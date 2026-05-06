"""Connection pool helpers."""
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_POOL: ConnectionPool | None = None


def init_pool() -> None:
    global _POOL
    if _POOL is None:
        _POOL = ConnectionPool(
            os.environ["DATABASE_URL"],
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
        )
        _POOL.wait()


@contextmanager
def conn():
    if _POOL is None:
        init_pool()
    assert _POOL is not None
    with _POOL.connection() as c:
        yield c


@contextmanager
def cursor():
    with conn() as c:
        with c.cursor() as cur:
            yield cur


def fetchone(sql: str, params: tuple | dict = ()):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetchall(sql: str, params: tuple | dict = ()):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute(sql: str, params: tuple | dict = ()):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            try:
                row = cur.fetchone()
            except psycopg.ProgrammingError:
                row = None
        c.commit()
        return row
