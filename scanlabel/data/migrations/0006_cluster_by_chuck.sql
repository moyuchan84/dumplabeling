-- 0006 — 묶는 범위를 (스캔 종류, 센서) 에서 (스캔 종류, 척, 센서) 로 (SPEC.md SL-CLU-3 · SL-MAP-1).
--
-- 같은 스캔 종류 · 같은 센서라도 척이 다르면 기준이 다르다 — 섞어 묶으면 「척이 달라서 다른」 기록이
-- 「어긋남이 달라서 다른」 기록처럼 보인다. 센서와 같은 방식으로 풀 행 · 묶음 · 지도의 점에 열을 둔다.
-- 기존 풀 행은 표시용 메타 사본(meta.chuck_id)에서 채운다.
-- 0006 이전 결과의 지도(maps 의 키가 「종류|센서」)는 이 판에서 읽히지 않는다 — 다시 묶으면 생긴다.

ALTER TABLE scan_records ADD COLUMN chuck_id text NOT NULL DEFAULT '';   -- 척 없음 = ''
UPDATE scan_records SET chuck_id = meta->>'chuck_id' WHERE meta ? 'chuck_id' AND meta->>'chuck_id' IS NOT NULL;
DROP INDEX scan_records_group_idx;
CREATE INDEX scan_records_group_idx ON scan_records (set_name, scan_enum, chuck_id, sensor_id);

ALTER TABLE cluster_members ADD COLUMN chuck_id text NOT NULL DEFAULT '';
ALTER TABLE cluster_points  ADD COLUMN chuck_id text NOT NULL DEFAULT '';
DROP INDEX cluster_points_group_idx;
CREATE INDEX cluster_points_group_idx ON cluster_points (run_id, scan_enum, chuck_id, sensor_id);
