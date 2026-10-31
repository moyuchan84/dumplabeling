-- 0008 — 이어서 묶기 (SPEC.md SL-CLU-13).
--
-- 다시 묶어도 닮음이 그대로인 묶음은 지난 번호를 이어받는다. 새 묶음은 쓴 적 없는 번호를 받으므로
-- 판마다 「다음에 줄 번호」를 남기고, 지난 판과 견준 변화(유지 · 옮김 · 합류 …)를 함께 남긴다.

ALTER TABLE cluster_runs ADD COLUMN next_no int NOT NULL DEFAULT 1;
ALTER TABLE cluster_runs ADD COLUMN changes jsonb;          -- shape.run_changes — 지난 판이 없으면 NULL

UPDATE cluster_runs r SET next_no = COALESCE(
    (SELECT max(m.cluster_no) + 1 FROM cluster_members m WHERE m.run_id = r.id), 1);
