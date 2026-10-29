-- 0005 — 묶는 범위를 스캔 종류 하나에서 (스캔 종류, 센서) 하나로 (SPEC.md SL-CLU-3 · SL-MAP-1).
--
-- 같은 스캔 종류라도 센서가 다르면 신호의 기준과 모양이 다르다 — 섞어 묶으면 「센서가 달라서 다른」
-- 기록이 「모양이 달라서 다른」 기록처럼 보인다. 풀 행에 센서를 열로 꺼내 두고, 묶음 · 지도의 점에도
-- 같은 열을 남긴다. 기존 풀 행은 표시용 메타 사본(meta.sensor_id)에서 채운다.
-- 0005 이전 결과의 지도(maps 의 키가 스캔 종류뿐)는 이 판에서 읽히지 않는다 — 다시 묶으면 생긴다.

ALTER TABLE scan_records ADD COLUMN sensor_id text NOT NULL DEFAULT '';   -- 센서 없음 = ''
UPDATE scan_records SET sensor_id = meta->>'sensor_id' WHERE meta ? 'sensor_id' AND meta->>'sensor_id' IS NOT NULL;
CREATE INDEX scan_records_group_idx ON scan_records (set_name, scan_enum, sensor_id);

ALTER TABLE cluster_members ADD COLUMN sensor_id text NOT NULL DEFAULT '';
ALTER TABLE cluster_points  ADD COLUMN sensor_id text NOT NULL DEFAULT '';
DROP INDEX cluster_points_kind_idx;
CREATE INDEX cluster_points_group_idx ON cluster_points (run_id, scan_enum, sensor_id);
