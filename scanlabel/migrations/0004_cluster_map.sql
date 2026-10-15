-- 0004 — 묶음 지도: 묶을 때 기록마다 2D 자리를 함께 남긴다 (SPEC.md 「묶음 지도」, SL-MAP).
--
-- 자리는 스캔 종류마다 따로 정해진다(종류가 다르면 1D 비교가 무의미 — SL-SIM-2). 투영식(maps)을 남겨
-- 두므로 그 결과에 없던 기록(새로 본 기록)도 같은 지도에 놓을 수 있다. 0004 이전에 묶은 결과에는
-- 자리가 없다 — 다시 묶으면 생긴다.

ALTER TABLE cluster_runs
    ADD COLUMN maps jsonb NOT NULL DEFAULT '{}';   -- {scan_enum: {landmarks: [record_id…], pinv, mean}}

CREATE TABLE cluster_points (                      -- 묶어 본 기록 전부(묶이지 않은 것 포함)의 자리
    run_id    bigint NOT NULL REFERENCES cluster_runs(id) ON DELETE CASCADE,
    record_id bigint NOT NULL REFERENCES scan_records(id) ON DELETE CASCADE,
    scan_enum text   NOT NULL,
    x         real   NOT NULL,
    y         real   NOT NULL,
    PRIMARY KEY (run_id, record_id)
);
CREATE INDEX cluster_points_kind_idx ON cluster_points (run_id, scan_enum);
