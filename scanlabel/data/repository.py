"""PostgreSQL 접근 — 라벨 트리(label_defs)와 기록 라벨(scan_labels). 원시 SQL, ORM 없음.

검증 실패는 `ValueError`(→ 422), 없는 행은 `KeyError`(→ 404) — fleet `Store` 와 같은 약속.
트리 규칙의 판단은 `processing/tree.py` 에 있고, 여기는 그것을 DB 에 적용한다.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .. import timebase
from ..processing import tree as treemod
from . import migrate

_DEF_COLS = ("id, parent_id, level, code, name, description, case_id, origin, retired, sort_no,"
             " author, created_at, updated_at")
_LABEL_COLS = ("id, servername, server_id, set_name, name, source, sig, scan_enum, meta, label_ids,"
               " author, feature_version, created_at, updated_at")

_NAME_MAX = 120
_TEXT_MAX = 2000


class SchemaError(RuntimeError):
    """DB 스키마가 코드와 다르다 — `python -m scanlabel.data.migrate up` 을 먼저 돌린다."""


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
                              "python -m scanlabel.data.migrate up 을 먼저 돌린다")

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

    def labels_by_record(self, *, set_name: str) -> dict[tuple[str, str, str], list[int]]:
        """(servername, name, source) → 붙은 라벨 id — 묶음의 분류를 셀 때 쓴다."""
        with self._conn() as c:
            rows = c.execute("SELECT servername, name, source, label_ids FROM scan_labels"
                             " WHERE set_name = %s", (set_name,)).fetchall()
        return {(r["servername"], r["name"], r["source"]): [int(i) for i in r["label_ids"]] for r in rows}

    def labels_by_record_id(self, *, set_name: str) -> dict[int, list[int]]:
        """풀의 기록 id → 붙은 라벨 id — 묶음 결과를 라벨로 잴 때(`shape.cluster_quality`)."""
        with self._conn() as c:
            rows = c.execute("SELECT r.id, l.label_ids FROM scan_records r JOIN scan_labels l"
                             " ON l.servername = r.servername AND l.set_name = r.set_name"
                             " AND l.name = r.name AND l.source = r.source WHERE r.set_name = %s",
                             (set_name,)).fetchall()
        return {int(r["id"]): [int(i) for i in r["label_ids"]] for r in rows}

    # ─── 기록 풀 (SL-POOL) ──────────────────────────────────────────────────

    def upsert_records(self, rows: Iterable[dict]) -> int:
        """본 기록들의 특징을 풀에 넣거나 갈아 끼운다. 키 = (servername, set_name, name, source)."""
        rows = list(rows)
        if not rows:
            return 0
        now = timebase.now_iso()
        with self._conn() as c, c.transaction(), c.cursor() as cur:
            cur.executemany(
                "INSERT INTO scan_records (servername, server_id, set_name, name, source, sig, scan_enum,"
                " chuck_id, sensor_id, meta, feature_version, features, seen_at, updated_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT ON CONSTRAINT scan_records_record_key DO UPDATE SET"
                "  server_id = EXCLUDED.server_id, sig = EXCLUDED.sig, scan_enum = EXCLUDED.scan_enum,"
                "  chuck_id = EXCLUDED.chuck_id, sensor_id = EXCLUDED.sensor_id,"
                "  meta = EXCLUDED.meta, feature_version = EXCLUDED.feature_version,"
                "  features = EXCLUDED.features, updated_at = EXCLUDED.updated_at,"
                "  seq = nextval('scan_records_seq')"
                # 같은 세대 · 같은 특징 판이면 모양이 같다 — 쓰지 않는다(묶음이 낡았다고 말하지 않게).
                " WHERE (scan_records.sig, scan_records.feature_version, scan_records.scan_enum,"
                "        scan_records.chuck_id, scan_records.sensor_id)"
                "  IS DISTINCT FROM (EXCLUDED.sig, EXCLUDED.feature_version, EXCLUDED.scan_enum, EXCLUDED.chuck_id,"
                "                    EXCLUDED.sensor_id)",
                [(r["servername"], r["server_id"], r["set_name"], r["name"], r["source"], r["sig"],
                  r["scan_enum"], r.get("chuck_id", ""), r.get("sensor_id", ""), Jsonb(r["meta"]), r["feature_version"], Jsonb(r["features"]), now, now)
                 for r in rows])
        return len(rows)

    def pool(self, *, set_name: str, per_scan_limit: int) -> tuple[list[dict], int, int, int]:
        """묶을 기록 — 묶음 범위(스캔 종류, 척, 센서)마다 **라벨된 기록 전부** + 라벨 없는 기록 최근 것부터
        `per_scan_limit` 개 (SL-CLU-12). 라벨된 기록은 오래돼도 묶음 · 지도에서 빠지지 않는다.

        (행들, 전체 수, 잘린 수, 풀의 최대 seq). 한 트랜잭션에서 읽어 넷이 서로 맞는다. 행마다 `labelled`.
        """
        with self._conn() as c, c.transaction():
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            head = c.execute("SELECT count(*) AS n, COALESCE(max(seq), 0) AS seq FROM scan_records"
                             " WHERE set_name = %s", (set_name,)).fetchone()
            rows = c.execute(
                "SELECT id, scan_enum, chuck_id, sensor_id, features, labelled FROM ("
                "  SELECT *, row_number() OVER"
                "   (PARTITION BY scan_enum, chuck_id, sensor_id, labelled ORDER BY seq DESC, id DESC) AS rn FROM ("
                "    SELECT r.id, r.scan_enum, r.chuck_id, r.sensor_id, r.features, r.seq,"
                "     EXISTS (SELECT 1 FROM scan_labels l WHERE l.servername = r.servername"
                "      AND l.set_name = r.set_name AND l.name = r.name AND l.source = r.source) AS labelled"
                "    FROM scan_records r WHERE r.set_name = %s) a) t"
                " WHERE labelled OR rn <= %s ORDER BY id",
                (set_name, per_scan_limit)).fetchall()
        total = int(head["n"])
        return rows, total, total - len(rows), int(head["seq"])

    def stale_pool(self, set_name: str, feature_version: int) -> dict[tuple[int, str], set[str]]:
        """특징 판이 다른 풀 기록 — (server_id, 파일) → source 들."""
        with self._conn() as c:
            rows = c.execute("SELECT server_id, name, source FROM scan_records WHERE set_name = %s"
                             " AND feature_version <> %s", (set_name, feature_version)).fetchall()
        out: dict[tuple[int, str], set[str]] = {}
        for r in rows:
            out.setdefault((int(r["server_id"]), r["name"]), set()).add(r["source"])
        return out

    def pool_count(self, set_name: str) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT count(*) AS n FROM scan_records WHERE set_name = %s",
                                 (set_name,)).fetchone()["n"])

    def features_of(self, record_ids: Iterable[int]) -> dict[int, dict]:
        ids = list(record_ids)
        if not ids:
            return {}
        with self._conn() as c:
            rows = c.execute("SELECT id, features FROM scan_records WHERE id = ANY(%s)", (ids,)).fetchall()
        return {int(r["id"]): r["features"] for r in rows}

    def pool_changed_since(self, *, set_name: str, seq: int) -> bool:
        """묶은 뒤에 풀에 새로 들어오거나 모양이 바뀐 기록이 있나."""
        with self._conn() as c:
            return c.execute("SELECT EXISTS (SELECT 1 FROM scan_records WHERE set_name = %s"
                             " AND seq > %s) AS x", (set_name, seq)).fetchone()["x"]

    def cursor(self, set_name: str) -> int:
        with self._conn() as c:
            row = c.execute("SELECT after_id FROM index_cursors WHERE set_name = %s", (set_name,)).fetchone()
        return int(row["after_id"]) if row else 0

    def set_cursor(self, set_name: str, after_id: int) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO index_cursors (set_name, after_id, updated_at) VALUES (%s,%s,%s)"
                      " ON CONFLICT (set_name) DO UPDATE SET after_id = EXCLUDED.after_id,"
                      " updated_at = EXCLUDED.updated_at", (set_name, after_id, timebase.now_iso()))

    # ─── 묶음 (SL-CLU) ──────────────────────────────────────────────────────

    def save_cluster_run(self, *, set_name: str, result: dict, n_records: int, truncated: int,
                         pool_seq: int, threshold: float, min_size: int, feature_version: int,
                         method: str = "skew", maps: Optional[dict] = None,
                         points: Optional[list[tuple]] = None) -> int:
        """묶음 결과 한 벌을 저장한다. 그 set · 잣대(`method`)의 이전 결과는 지운다 — 최신 것 하나만 있다.

        묶음 번호는 결과의 `no`(이어받은 번호, SL-CLU-13 — 없으면 크기 순), 판에 `next_no` · `changes` 를 남긴다.
        `maps` = 묶음 범위 키(`shape.group_key`) → 투영식, `points` = (record_id, scan_enum, chuck_id, sensor_id, x, y)
        — 묶음 지도(SL-MAP).
        """
        nos = [int(cl.get("no") or k) for k, cl in enumerate(result["clusters"], start=1)]
        with self._conn() as c, c.transaction():
            c.execute("DELETE FROM cluster_runs WHERE set_name = %s AND method = %s", (set_name, method))
            run_id = int(c.execute(
                "INSERT INTO cluster_runs (set_name, method, threshold, min_size, feature_version, n_records,"
                " n_clusters, n_unclustered, truncated, pool_seq, created_at, next_no, changes)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (set_name, method, threshold, min_size, feature_version, n_records, len(result["clusters"]),
                 len(result["unclustered"]), truncated, pool_seq, timebase.now_iso(),
                 int(result.get("next_no") or max(nos, default=0) + 1),
                 Jsonb(result["changes"]) if result.get("changes") is not None else None)).fetchone()["id"])
            if maps:
                with c.cursor() as cur:
                    cur.executemany("INSERT INTO cluster_maps (run_id, group_key, proj) VALUES (%s,%s,%s)",
                                    [(run_id, k, Jsonb(v)) for k, v in maps.items()])
            if points:
                with c.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO cluster_points (run_id, record_id, scan_enum, chuck_id, sensor_id, x, y)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        [(run_id, *p) for p in points])
            with c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO cluster_members (run_id, cluster_no, record_id, scan_enum, chuck_id, sensor_id, score,"
                    " representative) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(run_id, no, m["id"], cl["scan_enum"], cl.get("chuck_id", ""), cl.get("sensor_id", ""), m["score"], m["representative"])
                     for no, cl in zip(nos, result["clusters"]) for m in cl["members"]])
        return run_id

    def carry_of(self, set_name: str, method: str) -> Optional[dict]:
        """그 잣대의 최신 판을 `shape.cluster(previous=…)` 의 모양으로 — 없으면 None (SL-CLU-13)."""
        run = self.latest_run(set_name, method)
        if run is None:
            return None
        with self._conn() as c:
            rows = c.execute("SELECT cluster_no, record_id, representative FROM cluster_members WHERE run_id = %s"
                             " ORDER BY cluster_no, representative DESC, record_id", (run["id"],)).fetchall()
        by: dict[int, dict] = {}
        for r in rows:
            p = by.setdefault(int(r["cluster_no"]), {"no": int(r["cluster_no"]), "representative": None, "members": []})
            p["members"].append(int(r["record_id"]))
            if r["representative"]:
                p["representative"] = int(r["record_id"])
        return {"run_id": int(run["id"]), "next_no": int(run["next_no"]), "clusters": list(by.values())}

    def latest_run(self, set_name: str, method: str = "skew") -> Optional[dict]:
        with self._conn() as c:
            return c.execute("SELECT * FROM cluster_runs WHERE set_name = %s AND method = %s"
                             " ORDER BY id DESC LIMIT 1", (set_name, method)).fetchone()

    def run_map(self, run_id: int, group_key: str) -> Optional[dict]:
        """묶음 범위 하나의 지도 투영식 — 없으면 None (기록이 너무 적었던 범위)."""
        with self._conn() as c:
            row = c.execute("SELECT proj FROM cluster_maps WHERE run_id = %s AND group_key = %s",
                            (run_id, group_key)).fetchone()
        return row["proj"] if row else None

    def run_members(self, run_id: int, *, cluster_no: Optional[int] = None,
                    with_features: bool = False) -> list[dict]:
        """묶음 구성원 + 풀의 기록 정보. 묶음 번호 → 대표 먼저 → 점수 순."""
        cols = ("m.cluster_no, m.scan_enum, m.chuck_id, m.sensor_id, m.score, m.representative, r.id AS record_id, r.servername,"
                " r.server_id, r.set_name, r.name, r.source, r.sig, r.meta")
        if with_features:
            cols += ", r.features"
        q = (f"SELECT {cols} FROM cluster_members m JOIN scan_records r ON r.id = m.record_id"
             f" WHERE m.run_id = %s")
        args: list[Any] = [run_id]
        if cluster_no is not None:
            q += " AND m.cluster_no = %s"
            args.append(cluster_no)
        with self._conn() as c:
            return c.execute(q + " ORDER BY m.cluster_no, m.representative DESC, m.score DESC, r.id",
                             args).fetchall()

    def map_points(self, run_id: int, scan_enum: str, chuck_id: str, sensor_id: str) -> list[dict]:
        """지도의 점 — 자리 · 묶음 번호(없으면 None) · 라벨 여부 · 기록 머리."""
        with self._conn() as c:
            return c.execute(
                "SELECT p.record_id, p.x, p.y, m.cluster_no, r.servername, r.server_id, r.name, r.source,"
                " r.set_name, EXISTS (SELECT 1 FROM scan_labels l WHERE l.servername = r.servername"
                "   AND l.set_name = r.set_name AND l.name = r.name AND l.source = r.source) AS labelled"
                " FROM cluster_points p JOIN scan_records r ON r.id = p.record_id"
                " LEFT JOIN cluster_members m ON m.run_id = p.run_id AND m.record_id = p.record_id"
                " WHERE p.run_id = %s AND p.scan_enum = %s AND p.chuck_id = %s AND p.sensor_id = %s ORDER BY p.record_id",
                (run_id, scan_enum, chuck_id, sensor_id)).fetchall()

    def map_kinds(self, run_id: int) -> list[dict]:
        with self._conn() as c:
            return c.execute("SELECT scan_enum, chuck_id, sensor_id, count(*) AS n FROM cluster_points WHERE run_id = %s"
                             " GROUP BY scan_enum, chuck_id, sensor_id"
                             " ORDER BY count(*) DESC, scan_enum, chuck_id, sensor_id",
                             (run_id,)).fetchall()

    def find_record(self, *, set_name: str, servername: str, name: str, source: str) -> Optional[dict]:
        with self._conn() as c:
            return c.execute(
                "SELECT id AS record_id, servername, server_id, set_name, name, source, sig, scan_enum, chuck_id, sensor_id,"
                " meta,"
                " features FROM scan_records WHERE set_name = %s AND servername = %s AND name = %s"
                " AND source = %s", (set_name, servername, name, source)).fetchone()

    def get_record(self, record_id: int) -> dict:
        with self._conn() as c:
            row = c.execute(
                "SELECT id AS record_id, servername, server_id, set_name, name, source, sig, scan_enum, chuck_id, sensor_id,"
                " meta,"
                " features, seen_at, updated_at FROM scan_records WHERE id = %s", (record_id,)).fetchone()
        if row is None:
            raise KeyError(f"없는 기록 id: {record_id}")
        return row

    def record_point(self, run_id: int, record_id: int) -> Optional[dict]:
        with self._conn() as c:
            return c.execute(
                "SELECT p.x, p.y, p.scan_enum, p.chuck_id, p.sensor_id, m.cluster_no FROM cluster_points p"
                " LEFT JOIN cluster_members m ON m.run_id = p.run_id AND m.record_id = p.record_id"
                " WHERE p.run_id = %s AND p.record_id = %s", (run_id, record_id)).fetchone()

    def label_of(self, *, set_name: str, servername: str, name: str, source: str) -> list[int]:
        with self._conn() as c:
            row = c.execute("SELECT label_ids FROM scan_labels WHERE set_name = %s AND servername = %s"
                            " AND name = %s AND source = %s", (set_name, servername, name, source)).fetchone()
        return [int(i) for i in row["label_ids"]] if row else []

    def record_cluster(self, *, set_name: str, servername: str, name: str, source: str,
                       method: str = "skew") -> Optional[dict]:
        """이 기록이 그 잣대의 최신 묶음 결과에서 어느 묶음에 들었나 — 없으면 None."""
        with self._conn() as c:
            return c.execute(
                "SELECT m.run_id, m.cluster_no FROM cluster_members m"
                " JOIN scan_records r ON r.id = m.record_id"
                " JOIN cluster_runs k ON k.id = m.run_id"
                " WHERE k.set_name = %s AND k.method = %s AND r.servername = %s AND r.name = %s AND r.source = %s"
                " ORDER BY m.run_id DESC LIMIT 1", (set_name, method, servername, name, source)).fetchone()

    def set_features(self, label_id: int, features: dict, feature_version: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE scan_labels SET features = %s, feature_version = %s, updated_at = %s"
                      " WHERE id = %s",
                      (Jsonb(features), feature_version, timebase.now_iso(), label_id))


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
