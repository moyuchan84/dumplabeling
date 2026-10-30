-- 0007 — 묶음 잣대를 둘로: 채널 간 편차(skew) · 주성분(pca) (SPEC.md SL-CLU-10).
--
-- 같은 풀을 두 잣대로 묶어 set · 잣대마다 최신 결과 하나씩 남긴다 — 화면이 잣대를 골라 본다.
-- 지도의 투영식은 cluster_runs.maps(한 칸의 jsonb)에서 범위마다 한 행으로 옮긴다: 주성분 투영식은 범위마다
-- 수천 개의 수라, 결과를 읽을 때마다 전부 끌어오지 않고 그 범위의 것만 읽는다.

ALTER TABLE cluster_runs ADD COLUMN method text NOT NULL DEFAULT 'skew'
    CHECK (method IN ('skew', 'pca'));

CREATE TABLE cluster_maps (                        -- 묶음 범위 하나의 지도 투영식
    run_id    bigint NOT NULL REFERENCES cluster_runs(id) ON DELETE CASCADE,
    group_key text   NOT NULL,                     -- shape.group_key — 「종류|척|센서」
    proj      jsonb  NOT NULL,
    PRIMARY KEY (run_id, group_key)
);

INSERT INTO cluster_maps (run_id, group_key, proj)
SELECT r.id, m.key, m.value FROM cluster_runs r, jsonb_each(r.maps) m;

ALTER TABLE cluster_runs DROP COLUMN maps;
