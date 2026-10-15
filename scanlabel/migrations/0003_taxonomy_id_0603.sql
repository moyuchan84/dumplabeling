-- 0003 — 실제 분류표 ID-0603 (워크스페이스 0603.md) 를 라벨 트리로 넣는다.
--
-- 대분류 = error_no · 중분류 = category · 소분류 = error. 트리는 **3단계로 고정**이다(SL-TREE-11):
-- 「IFM-MIOB」·「Air shower-air balance」처럼 이름 안에 묶음 접두어가 있는 항목도 4단계로 쪼개지 않고
-- 그 이름 그대로 소분류 하나다. error 가 빈 행(SW/Firmware)은 중분류에서 끝나는 가지다.
--
-- 멱등: 같은 부모 아래 같은 이름(대소문자 · 앞뒤 공백 무시)이 이미 있으면 건너뛴다
-- (label_defs_sibling_name_key 와 같은 비교). jobs seed-labels 로 같은 표를 넣어 둔 DB 에서도 안전하다.
-- 기존 개발용 임시 트리(labels.seed.example.json)는 건드리지 않는다.

CREATE TEMP TABLE _taxonomy (
    sort_no int  NOT NULL,     -- 0603.md 의 행 순서
    l1      text NOT NULL,     -- error_no
    l2      text NOT NULL,     -- category
    l3      text NOT NULL      -- error ('' = 중분류에서 끝남)
) ON COMMIT DROP;

INSERT INTO _taxonomy (sort_no, l1, l2, l3) VALUES
    (1, 'ID-0603', 'dynamic', 'TSPE'),
    (2, 'ID-0603', 'dynamic', 'TADS'),
    (3, 'ID-0603', 'dynamic', 'SWLF(LFF)'),
    (4, 'ID-0603', 'RS', 'Nothing filter in MC'),
    (5, 'ID-0603', 'RS', 'HERB board'),
    (6, 'ID-0603', 'RS', 'Encoder fiber'),
    (7, 'ID-0603', 'WS', 'Cable Slab'),
    (8, 'ID-0603', 'WS', 'Pressure sensor'),
    (9, 'ID-0603', 'WS', 'temp sensor'),
    (10, 'ID-0603', 'WS', 'IFM-reboot AOM laser'),
    (11, 'ID-0603', 'WS', 'IFM-MIOB'),
    (12, 'ID-0603', 'WS', 'IFM-NC5'),
    (13, 'ID-0603', 'WS', 'IFM-IFSR rack'),
    (14, 'ID-0603', 'WS', 'reference beam mirror'),
    (15, 'ID-0603', 'illumination', 'LICB'),
    (16, 'ID-0603', 'illumination', 'TCB'),
    (17, 'ID-0603', 'illumination', 'DCB'),
    (18, 'ID-0603', 'illumination', 'DCCB'),
    (19, 'ID-0603', 'illumination', 'Lens elements'),
    (20, 'ID-0603', 'illumination', 'DICB'),
    (21, 'ID-0603', 'illumination', 'laser'),
    (22, 'ID-0603', 'alignment', 'Aarepro'),
    (23, 'ID-0603', 'alignment', 'AAAR'),
    (24, 'ID-0603', 'alignment', 'AA verify queue'),
    (25, 'ID-0603', 'alignment', 'ISIB'),
    (26, 'ID-0603', 'alignment', 'ADC'),
    (27, 'ID-0603', 'C&T', 'Air shower-air balance'),
    (28, 'ID-0603', 'C&T', 'Air shower-leakage or damage'),
    (29, 'ID-0603', 'C&T', 'Hoses'),
    (30, 'ID-0603', 'C&T', 'Purge hood'),
    (31, 'ID-0603', 'C&T', 'Exhaust'),
    (32, 'ID-0603', 'C&T', 'Covers'),
    (33, 'ID-0603', 'C&T', 'Immersion Hood'),
    (34, 'ID-0603', 'TIS', 'TIS sensor'),
    (35, 'ID-0603', 'TIS', 'TIS Flex Cable'),
    (36, 'ID-0603', 'TIS', 'Electric&board'),
    (37, 'ID-0603', 'TIS', 'Machine constants'),
    (38, 'ID-0603', 'SW/Firmware', ''),
    (39, 'ID-0603', 'Metrology', 'TIS Position(BMAA)'),
    (40, 'ID-0603', 'Metrology', 'Calibration'),
    (41, 'ID-0603', 'Metrology', 'KMTR'),
    (42, 'ID-0603', 'Metrology', 'WSA');

CREATE TEMP TABLE _now ON COMMIT DROP AS
SELECT to_char(now() AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD"T"HH24:MI:SS') || '+09:00' AS t;

-- 대분류 (error_no)
INSERT INTO label_defs (parent_id, level, name, origin, sort_no, created_at, updated_at)
SELECT NULL, 1, s.l1, 'seed', min(s.sort_no), n.t, n.t
FROM _taxonomy s CROSS JOIN _now n
GROUP BY s.l1, n.t
ON CONFLICT (COALESCE(parent_id, 0), lower(btrim(name))) DO NOTHING;

-- 중분류 (category)
INSERT INTO label_defs (parent_id, level, name, origin, sort_no, created_at, updated_at)
SELECT p.id, 2, s.l2, 'seed', min(s.sort_no), n.t, n.t
FROM _taxonomy s
JOIN label_defs p ON p.parent_id IS NULL AND lower(btrim(p.name)) = lower(btrim(s.l1))
CROSS JOIN _now n
GROUP BY p.id, s.l2, n.t
ON CONFLICT (COALESCE(parent_id, 0), lower(btrim(name))) DO NOTHING;

-- 소분류 (error)
INSERT INTO label_defs (parent_id, level, name, origin, sort_no, created_at, updated_at)
SELECT m.id, 3, s.l3, 'seed', min(s.sort_no), n.t, n.t
FROM _taxonomy s
JOIN label_defs p ON p.parent_id IS NULL AND lower(btrim(p.name)) = lower(btrim(s.l1))
JOIN label_defs m ON m.parent_id = p.id AND lower(btrim(m.name)) = lower(btrim(s.l2))
CROSS JOIN _now n
WHERE btrim(s.l3) <> ''
GROUP BY m.id, s.l3, n.t
ON CONFLICT (COALESCE(parent_id, 0), lower(btrim(name))) DO NOTHING;
