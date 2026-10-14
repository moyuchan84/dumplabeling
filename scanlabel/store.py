"""PostgreSQL 접근 — 라벨 트리(label_defs)와 기록 라벨(scan_labels). 원시 SQL, ORM 없음.

검증 실패는 `ValueError`(→ 422), 없는 행은 `KeyError`(→ 404) — fleet `Store` 와 같은 약속.
트리 규칙의 판단은 `tree.py` 에 있고, 여기는 그것을 DB 에 적용한다.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import migrate, timebase, tree as treemod

_DEF_COLS = ("id, parent_id, level, code, name, description, case_id, origin, retired, sort_no,"
             " author, created_at, updated_at")
_LABEL_COLS = ("id, servername, server_id, set_name, name, source, sig, scan_enum, meta, label_ids,"
               " author, feature_version, created_at, updated_at")

_NAME_MAX = 120
_TEXT_MAX = 2000


class SchemaError(RuntimeError):
    """DB 스키마가 코드와 다르다 — `python -m scanlabel.migrate up` 을 먼저 돌린다."""


def _clean_name(v: Any) -> str:
    s = " ".join(str(v or "").split())
    if not s:
        raise ValueError("이름이 비어 있다")
    if len(s) > _NAME_MAX:
        raise ValueError(f"이름은 {_NAME_MAX}자까지")
    return s


def _clean_text(v: Any, what: str, limit: int = _TEXT_MAX) -> str:
    s = str(v or "").strip()
    if len(s) > limit:
        raise ValueError(f"{what} 은 {limit}자까지")
    return s


class Store:
    def __init__(self, dsn: str, *, pool_max: int = 8):
        self._pool = ConnectionPool(dsn, min_size=1, max_size=pool_max,
                                    kwargs={"row_factory": dict_row}, open=True)
        with self._conn() as c:
            have, want = migrate.applied_version(c), migrate.latest_version()
        if have != want:
            self._pool.close()
            raise SchemaError(f"DB 스키마 {have:04d} ≠ 코드 {want:04d} — "
                              "python -m scanlabel.migrate up 을 먼저 돌린다")

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _conn(self):
        with self._pool.connection() as c:
            yield c

    # ─── 라벨 트리 ──────────────────────────────────────────────────────────

    def _tree(self, c) -> treemod.Tree:
        return treemod.Tree(c.execute(f"SELECT {_DEF_COLS} FROM label_defs").fetchall())

    def list_defs(self, *, include_retired: bool = False) -> list[dict]:
        """평평한 목록 + 경로 · 실효 내림 · 붙은 건수(직접 / 자손 포함). 트리 조립은 호출자가."""
        with self._conn() as c:
            t = self._tree(c)
            selections = [r["label_ids"] for r in c.execute("SELECT label_ids FROM scan_labels")]
        direct: dict[int, int] = {}
        total: dict[int, int] = {}
        for ids in selections:
            known = [int(i) for i in ids if int(i) in t]
            for i in known:
                direct[i] = direct.get(i, 0) + 1
            for node in t.closure(known):
                total[node] = total.get(node, 0) + 1
        out = []
        for i, r in t.rows.items():
            retired = t.retired(i)
            if retired and not include_retired:
                continue
            out.append({**r, "path": t.path(i), "retired_effective": retired,
                        "count": direct.get(i, 0), "count_total": total.get(i, 0)})
        out.sort(key=lambda r: (r["level"], r["sort_no"] if r["sort_no"] is not None else 1 << 30,
                                r["name"].lower(), r["id"]))
        return out

    def get_def(self, def_id: int) -> dict:
        with self._conn() as c:
            t = self._tree(c)
        if def_id not in t:
            raise KeyError(f"없는 라벨 id: {def_id}")
        return {**t.get(def_id), "path": t.path(def_id), "retired_effective": t.retired(def_id)}

    def create_def(self, *, parent_id: Optional[int], name: str, code: str = "",
                   description: str = "", case_id: Optional[int] = None,
                   origin: str = "added", sort_no: Optional[int] = None) -> dict:
        name = _clean_name(name)
        code = _clean_text(code, "코드", 40)
        description = _clean_text(description, "설명")
        if origin not in ("seed", "added"):
            raise ValueError(f"origin 은 seed | added: {origin}")
        now = timebase.now_iso()
        with self._conn() as c, c.transaction():
            # 같은 트랜잭션에서 트리를 읽고 넣는다 — 부모가 그새 내려졌어도 낡은 판단으로 넣지 않게.
            c.execute("LOCK TABLE label_defs IN SHARE ROW EXCLUSIVE MODE")
            t = self._tree(c)
            level = treemod.child_level(t, parent_id)
            try:
                row = c.execute(
                    f"INSERT INTO label_defs (parent_id, level, code, name, description, case_id,"
                    f" origin, sort_no, created_at, updated_at)"
                    f" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {_DEF_COLS}",
                    (parent_id, level, code, name, description, case_id, origin, sort_no, now, now),
                ).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError(self._dup_message(exc, name, code)) from exc
        return self.get_def(int(row["id"]))

    @staticmethod
    def _dup_message(exc: Exception, name: str, code: str) -> str:
        if "code_key" in str(exc):
            return f"이미 쓰이는 코드다: {code}"
        return f"같은 부모 아래에 이미 같은 이름이 있다: {name}"

    def find_child(self, parent_id: Optional[int], name: str) -> Optional[dict]:
        """같은 부모 아래 같은 이름(대소문자 · 공백 무시)의 노드 — 시드가 멱등이게."""
        key = " ".join(str(name).split()).lower()
        with self._conn() as c:
            row = c.execute(
                f"SELECT {_DEF_COLS} FROM label_defs WHERE COALESCE(parent_id, 0) = %s"
                f" AND lower(btrim(name)) = %s", (parent_id or 0, key)).fetchone()
        return row

    def update_def(self, def_id: int, fields: dict) -> dict:
        allowed = {"name", "code", "description", "case_id", "retired", "sort_no", "parent_id"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"고칠 수 없는 필드: {', '.join(sorted(unknown))}")
        sets: dict[str, Any] = {}
        if "name" in fields:
            sets["name"] = _clean_name(fields["name"])
        if "code" in fields:
            sets["code"] = _clean_text(fields["code"], "코드", 40)
        if "description" in fields:
            sets["description"] = _clean_text(fields["description"], "설명")
        for k in ("case_id", "sort_no", "parent_id"):
            if k in fields:
                sets[k] = fields[k]
        if "retired" in fields:
            sets["retired"] = bool(fields["retired"])
        with self._conn() as c, c.transaction():
            c.execute("LOCK TABLE label_defs IN SHARE ROW EXCLUSIVE MODE")
            t = self._tree(c)
            if def_id not in t:
                raise KeyError(f"없는 라벨 id: {def_id}")
            if "parent_id" in sets:
                treemod.check_move(t, def_id, sets["parent_id"])
            if sets:
                cols = ", ".join(f"{k} = %s" for k in sets)
                try:
                    c.execute(f"UPDATE label_defs SET {cols}, updated_at = %s WHERE id = %s",
                              (*sets.values(), timebase.now_iso(), def_id))
                except psycopg.errors.UniqueViolation as exc:
                    raise ValueError(self._dup_message(exc, sets.get("name", ""),
                                                       sets.get("code", ""))) from exc
        return self.get_def(def_id)

    # ─── 기록 라벨 ──────────────────────────────────────────────────────────

    def file_labels(self, servername: str, set_name: str, name: str) -> list[dict]:
        with self._conn() as c:
            t = self._tree(c)
            rows = c.execute(
                f"SELECT {_LABEL_COLS} FROM scan_labels WHERE servername = %s AND set_name = %s"
                f" AND name = %s ORDER BY source", (servername, set_name, name)).fetchall()
        return [self._label_out(r, t) for r in rows]

    @staticmethod
    def _label_out(r: dict, t: treemod.Tree) -> dict:
        ids = [int(i) for i in r["label_ids"]]
        return {**r, "label_ids": ids,
                "labels": [{"id": i, "level": int(t.get(i)["level"]), "path": t.path(i),
                            "retired": t.retired(i)} for i in ids if i in t]}

    def upsert_label(self, *, servername: str, server_id: int, set_name: str, name: str,
                     source: str, sig: str, scan_enum: str, meta: dict,
                     label_ids: Iterable[int], features: dict, feature_version: int) -> Optional[dict]:
        """라벨이 비면 행을 지우고 None. 아니면 검증 후 upsert 한 행."""
        ids = list(label_ids)
        if not ids:
            self.delete_label(servername, set_name, name, source)
            return None
        now = timebase.now_iso()
        with self._conn() as c, c.transaction():
            t = self._tree(c)
            ids = treemod.check_selection(t, ids)
            row = c.execute(
                f"INSERT INTO scan_labels (servername, server_id, set_name, name, source, sig,"
                f" scan_enum, meta, label_ids, feature_version, features, created_at, updated_at)"
                f" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                f" ON CONFLICT ON CONSTRAINT scan_labels_record_key DO UPDATE SET"
                f"  server_id = EXCLUDED.server_id, sig = EXCLUDED.sig, scan_enum = EXCLUDED.scan_enum,"
                f"  meta = EXCLUDED.meta, label_ids = EXCLUDED.label_ids,"
                f"  feature_version = EXCLUDED.feature_version, features = EXCLUDED.features,"
                f"  updated_at = EXCLUDED.updated_at"
                f" RETURNING {_LABEL_COLS}",
                (servername, server_id, set_name, name, source, sig, scan_enum, Jsonb(meta), ids,
                 feature_version, Jsonb(features), now, now)).fetchone()
        return self._label_out(row, t)

    def delete_label(self, servername: str, set_name: str, name: str, source: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM scan_labels WHERE servername = %s AND set_name = %s"
                            " AND name = %s AND source = %s", (servername, set_name, name, source))
            return cur.rowcount > 0

    def candidates(self, *, set_name: str, scan_enum: Optional[str] = None) -> list[dict]:
        """유사 비교 대상 — 특징 스냅샷 포함. scan_enum 을 주면 같은 스캔 종류만."""
        q = (f"SELECT {_LABEL_COLS}, features FROM scan_labels WHERE set_name = %s")
        args: list[Any] = [set_name]
        if scan_enum is not None:
            q += " AND scan_enum = %s"
            args.append(scan_enum)
        with self._conn() as c:
            rows = c.execute(q + " ORDER BY id", args).fetchall()
        for r in rows:
            r["label_ids"] = [int(i) for i in r["label_ids"]]
        return rows

    def tree(self) -> treemod.Tree:
        with self._conn() as c:
            return self._tree(c)

    def set_features(self, label_id: int, features: dict, feature_version: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE scan_labels SET features = %s, feature_version = %s, updated_at = %s"
                      " WHERE id = %s",
                      (Jsonb(features), feature_version, timebase.now_iso(), label_id))


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
