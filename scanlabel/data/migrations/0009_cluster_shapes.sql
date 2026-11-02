-- 0009 — 묶음의 평균 그림 (SL-DRAW-2) · 문턱 없는 묶기 (SL-CLU-2, 2026-11-02)
--
-- 묶음마다 구성원의 평균 그림(채널별 측정 순서 곡선 + 띠 + 칸, shape.mean_shape)을 판과 함께 남긴다 — 목록 · 지도 ·
-- 묶음 화면이 구성원 수백 건의 특징을 매번 다시 읽지 않게.
-- 묶기에 닮음 문턱(CLUSTER_MIN)이 없어졌으므로 cluster_runs.threshold 는 그 전 판에만 값이 있다.

CREATE TABLE cluster_shapes (
    run_id     bigint NOT NULL REFERENCES cluster_runs(id) ON DELETE CASCADE,
    cluster_no int    NOT NULL,
    shape      jsonb  NOT NULL,                    -- shape.mean_shape
    PRIMARY KEY (run_id, cluster_no)
);

ALTER TABLE cluster_runs ALTER COLUMN threshold DROP NOT NULL;
