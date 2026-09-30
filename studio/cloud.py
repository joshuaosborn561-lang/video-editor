"""YouTube rows and footage on the restricted database login.

The server uses SUPABASE_DB_URL only. That login can use schema youtube.
It cannot read the lead tables, and the service role is never used.
Footage is a Postgres large object so a Storage API key is not required.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

_CHUNK = 1024 * 1024


def enabled() -> bool:
    return bool(os.environ.get("SUPABASE_DB_URL"))


def upsert_project(project: dict) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into youtube.projects (id, payload, updated_at)
            values (%s, %s, %s)
            on conflict (id) do update
              set payload = excluded.payload,
                  updated_at = excluded.updated_at
            """,
            (project["id"], Json(project), project["updated_at"]),
        )


def fetch_project(project_id: str) -> dict | None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("select payload from youtube.projects where id = %s", (project_id,))
        row = cur.fetchone()
    if not row:
        return None
    return row[0]


def list_summaries() -> list[dict]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, updated_at, payload
            from youtube.projects
            order by updated_at desc
            """
        )
        rows = cur.fetchall()
    found = []
    for project_id, updated_at, payload in rows:
        payload = payload or {}
        brief = payload.get("brief") or {}
        found.append(
            {
                "id": project_id,
                "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
                "topic": brief.get("topic") or "Untitled",
                "approved": bool(payload.get("approved")),
            }
        )
    return found


def upload(project_id: str, path: Path) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select oid from youtube.files where project_id = %s and name = %s",
            (project_id, path.name),
        )
        previous = cur.fetchone()
        previous_oid = previous[0] if previous else None
        new_oid = _write_large_object(conn, path)
        cur.execute(
            """
            insert into youtube.files (project_id, name, oid)
            values (%s, %s, %s)
            on conflict (project_id, name) do update set oid = excluded.oid
            """,
            (project_id, path.name, new_oid),
        )
        if previous_oid and previous_oid != new_oid:
            cur.execute("select lo_unlink(%s)", (previous_oid,))


def download(project_id: str, filename: str) -> bytes | None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select oid from youtube.files where project_id = %s and name = %s",
            (project_id, filename),
        )
        row = cur.fetchone()
        if not row:
            return None
        large = conn.lobject(row[0], "rb")
        try:
            chunks = []
            while True:
                chunk = large.read(_CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            large.close()
    return b"".join(chunks)


def _write_large_object(conn, path: Path) -> int:
    large = conn.lobject(0, "wb")
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                large.write(chunk)
        return large.oid
    finally:
        large.close()


@contextmanager
def _conn():
    try:
        conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=20, application_name="youtube-desk")
    except psycopg2.Error as exc:
        raise RuntimeError(f"supabase connect failed: {_safe(exc)}") from exc
    try:
        with conn.cursor() as cur:
            cur.execute("set search_path to youtube")
        yield conn
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        raise RuntimeError(f"supabase query failed: {_safe(exc)}") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _safe(exc: BaseException) -> str:
    text = str(exc).splitlines()[0][:240]
    if "://" in text or "password" in text.lower():
        return "database error"
    return text
