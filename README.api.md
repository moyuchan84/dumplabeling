# scanlabel HTTP API

poc_viewer 는 same-origin `/api/scanlabel/...` 로 부르고(`next.config.ts` rewrite → `SCANLABEL_API_URL`),
기본 포트는 **8100** 입니다. 인증은 없습니다(fleet 과 같이 네트워크 위치로 막습니다).

## 에러

| status | 뜻 |
|---|---|
| `422` | 검증 실패(트리 규칙 · 이름 중복 · 내려진 라벨 · 조상+자손 동시 선택 · 낡은 묶음 `run_id`) **또는** fleet 이 아직 그 파일을 해석해 두지 않음 — 본문 `detail` 에 사람이 읽는 문장 |
| `404` | 없는 라벨 id · 그 파일에 없는 기록(`source`) · fleet 이 모르는 서버 · 없는 묶음 번호 · 아직 묶은 적 없음 |
| `502` | fleet 에 닿지 않음 |

## 라벨 트리

### `GET /label-defs?include_retired=false`

```json
{ "defs": [
  { "id": 3, "parent_id": 2, "level": 3, "code": "A-01-1", "name": "단일 피크",
    "path": "신호 이상 › 피크 › 단일 피크", "description": "한 자리에서만 튄다",
    "case_id": 1, "origin": "seed", "retired": false, "retired_effective": false,
    "sort_no": 2, "count": 4, "count_total": 4, "author": "",
    "created_at": "2026-10-14T15:00:00+09:00", "updated_at": "..." } ] }
```

- **평평한 목록**입니다. 트리는 `parent_id` 로 조립하세요. 정렬은 `level` → `sort_no` → 이름.
- `count` = 이 노드에 **직접** 붙은 기록 수, `count_total` = 자손 포함.
- `retired_effective` = 자기나 조상이 내려짐. 기본 응답에서는 빠지고 `include_retired=true` 일 때만 옵니다.

### `POST /label-defs` → 201

```json
{ "parent_id": 2, "name": "삼중 피크", "code": "", "description": "", "case_id": null }
```

`level` 은 부모가 정합니다(`parent_id: null` = 대분류). 소분류 아래 · 내려진 부모 아래 · 형제와 같은 이름은 422.

### `PUT /label-defs/{id}`

보낸 필드만 고칩니다: `name` · `code` · `description` · `case_id` · `retired` · `sort_no` · `parent_id`
(같은 단계의 부모로만 옮길 수 있음). 응답은 고친 노드.

## 기록 라벨

### `GET /labels?server_id=1&name=server1.tdf&set_name=dump`

이 파일의 라벨된 기록들 — 목록 표시용이라 특징 스냅샷은 싣지 않습니다.

```json
{ "labels": [
  { "id": 1, "servername": "scanner-a", "server_id": 1, "set_name": "dump", "name": "server1.tdf",
    "source": "server1.tdf::…RAW_DATA_STRUCT.dd", "sig": "20260811T005720-100",
    "scan_enum": "FINEHORVERT_SCAN", "meta": { "chuck_id": "CH1", "sensor_id": 2, "…": "…" },
    "label_ids": [3], "labels": [ { "id": 3, "level": 3, "path": "신호 이상 › 피크 › 단일 피크", "retired": false } ],
    "feature_version": 1, "created_at": "…", "updated_at": "…" } ] }
```

### `PUT /labels`

```json
{ "server_id": 1, "set_name": "dump", "name": "server1.tdf", "source": "…", "label_ids": [3] }
```

scanlabel 이 fleet 에서 그 기록을 **캐시로만** 읽어(`source:"cache"`, FTP 없음) 특징을 계산해 저장합니다.
`label_ids: []` 이면 지우고 `{ "label": null }`. 응답 `{ "label": <위와 같은 행> }`.

### `DELETE /labels?server_id=&name=&source=&set_name=dump`

## 유사 · 제안

### `POST /labels/similar`

```json
{ "server_id": 1, "set_name": "dump", "name": "server1.tdf", "source": "…", "k": 10, "same_scan_only": true }
```

`source` 를 주면 그 기록 하나:

```json
{ "sig": "…", "source": "…", "scan_enum": "FINEHORVERT_SCAN", "compared": 12,
  "target_profiles": { "A": { "x": [ -0.41, … 16개 ], "z": [ … 16개 ] } },
  "matches": [
    { "id": 7, "servername": "scanner-c", "server_id": 3, "name": "server3.tdf", "source": "…",
      "scan_enum": "FINEHORVERT_SCAN", "meta": { … },
      "labels": [ { "id": 3, "level": 3, "path": "신호 이상 › 피크 › 단일 피크", "case_id": 1 } ],
      "similarity": { "score": 0.91, "d1": 0.94, "d2": 0.88, "scale": 1.8,
                      "by_channel": { "A": { "score": 0.95, "d1": 0.97, "d2": 0.93,
                                               "dx": 0.98, "dz": 0.96, "scale": 1.7 } } },
      "profiles": { "A": { "x": [ … 16개 ], "z": [ … 16개 ] } } } ],
  "levels": {
    "1": [ { "label_id": 1, "path": "신호 이상", "score": 0.93, "count": 6, "case_id": null, "suggested": true } ],
    "2": [ { "label_id": 2, "path": "신호 이상 › 피크", "score": 0.88, "count": 4, "case_id": null, "suggested": true } ],
    "3": [ { "label_id": 3, "path": "…단일 피크", "score": 0.71, "count": 2, "case_id": 1, "suggested": false },
           { "label_id": 4, "path": "…이중 피크", "score": 0.69, "count": 1, "case_id": null, "suggested": false } ] },
  "suggestion": { "label_id": 2, "level": 2, "path": "신호 이상 › 피크", "score": 0.88, "count": 4,
                  "case_id": null, "suggested": true } }
```

- `score` 는 -1 ~ 1 (상관계수). `scale` = 상대 기록의 크기 / 이 기록의 크기 — 점수에는 섞지 않습니다.
- **`suggested` 판단은 서버가 합니다.** 화면은 문턱값을 모릅니다. 단계 L 은 위 단계가 확실할 때만 확실할 수 있고,
  `suggestion` 은 확실한 가장 깊은 단계입니다(없으면 `null`).
- `same_scan_only: true`(기본)면 같은 `scan_enum` 의 기록끼리만 비교합니다 — 스캔 종류가 다르면 다른 측정입니다(선 스캔은 경로가 다르면 곡선 비교가 무의미).
- 면 스캔(16×16 칸이 있는 채널)의 `d1` 은 **X 단면(열 평균) · Z 단면(행 평균)** 상관의 평균(`dx` · `dz`), `d2` 는 칸
  상관입니다. 셋은 **각자** z-score 합니다. 선 스캔은 `d1` = 측정 순서 곡선(128점) 하나, `d2` 없음. 면과 선은 비교하지
  않습니다. `profiles` · `profile` 은 그 재료를 각자 정규화한 것입니다 — 면 스캔은 `{x, z}`(기록 상세만 `grid` 까지),
  선 스캔은 `{line}`. 빈 자리는 `null`.

같은 응답에 두 칸이 더 옵니다.

```json
"cases": [ { "case_id": 1, "score": 0.91, "count": 3, "paths": ["신호 이상 › 피크 › 단일 피크"] } ],
"cluster": { "run_id": 4, "cluster_no": 2, "size": 17, "scan_enum": "FINEHORVERT_SCAN",
             "labelled": 5, "unlabelled": 12, "levels": { "1": [ … ], "2": [ … ], "3": [ … ] },
             "category": { "label_id": 3, "level": 3, "path": "…", "count": 4, "share": 0.8,
                           "case_id": 1, "agreed": true } }
```

- **`cases` = 유사 사례.** 닮은 기록(점수 ≥ 0.6)들의 라벨이 가리키는 사례(fleet `acs_case.case_id`)를 사례 단위로
  모은 것 — `score` 는 그 사례로 온 기록의 최고 점수, `count` 는 그런 기록 수, 최대 5건. 사례의 제목 · 본문은
  fleet `/cases/{id}` 에서 읽습니다.
- **`cluster` = 이 기록이 든 묶음**(채널 간 편차(`skew`) 잣대의 최신 묶음 결과 기준, 없으면 `null`). 모양은 아래 `GET /clusters` 의 한 칸과 같습니다.
- 파일을 연 것으로 보고 그 기록을 **기록 풀**에 넣습니다(묶음의 재료).

`source` 를 빼면 파일의 모든 기록에 대해 제안 하나씩:

```json
{ "sig": "…", "records": [ { "source": "…", "suggestion": { "label_id": 3, "level": 3, "path": "…", "score": 0.93, "…": "…" } },
                           { "source": "…", "suggestion": null } ] }
```

이 호출도 그 파일의 기록 전부를 기록 풀에 넣습니다 — 화면이 파일을 열 때 부르는 것이 이것입니다.

### `GET /labels/by-case/{case_id}?set_name=dump&limit=50`

이 사례를 가리키는 라벨(노드 자신이나 조상의 `case_id`)이 붙은 기록들 — **사례에서 기록으로** 거꾸로 찾기.
최근 것 먼저.

```json
{ "case_id": 1, "total": 2,
  "records": [ { "id": 7, "servername": "scanner-c", "server_id": 3, "name": "server3.tdf", "source": "…",
                 "sig": "…", "scan_enum": "…", "meta": { … }, "updated_at": "…",
                 "labels": [ { "id": 3, "level": 3, "path": "…", "case_id": 1 } ] } ] }
```

## 묶음 (클러스터)

라벨이 있든 없든 scanlabel 이 **본 기록 전부**(기록 풀)를 **채널 간 편차(skew)** 로 묶습니다. **같은 스캔 종류 · 같은 척 ·
같은 센서**(`scan_enum` + `chuck_id` + `sensor_id`)끼리만 묶고, 구성원 모두가 묶음의 **대표 기록**과 직접 0.8 이상 닮았습니다.

채널 간 편차 = 같은 자리(16×16 칸 · X 단면 · Z 단면 · 측정 순서 128칸, 선 스캔은 측정 순서 곡선)에서 채널(`value` 의 키 — tr · txh · tyh …)마다
「제 표준편차로 잰 값 − 채널 평균」입니다. 그 범위의 기록들이 **평소 어긋나는 만큼**(자리마다 중앙값, 기준선)을 빼고, 남은
어긋남의 자리 · 방향 · 크기가 같을수록 1 에 가깝습니다. 면 스캔 점수 = 단면 · 칸 · 측정 순서를 1/3 씩(SL-CLU-11 —
측정 순서의 가로는 원래 인덱스가 아니라 스캔 진행률: 기록마다 점 개수가 달라서). 채널이 둘 미만인 기록은 묶이지 않습니다. 라벨 제안
(`/labels/similar`)은 지금처럼 모양 유사도입니다. 3건보다 작은 무리는 「묶이지 않음」입니다.
분류(`category`)는 그 묶음의 **라벨된 구성원**이 합의한 가장 깊은 단계입니다(60% 이상 + 2위보다 많음 + 위 단계의
자식) — 라벨된 구성원이 없거나 갈리면 `null`. 이 판단도 서버가 하고, 화면은 옮겨 적습니다.

**잣대는 둘입니다** — `method=skew`(기본, 위의 채널 간 편차) · `method=pca`(주성분). 「다시 묶기」 한 번이 두 잣대로 다
묶어 따로 저장하고, 아래 묶음 · 지도 · 기록 라우트는 모두 `method` 를 받아 그 잣대의 최신 결과를 봅니다(다른 값은 422).
주성분 = 범위 안에서 기록마다 「채널별 모양 · 채널 간 편차 · 채널별 크기(log 표준편차)가 범위의 평소(자리마다 중앙값)와
다른 정도」를 한 줄로 늘어놓고 주성분으로 줄인 좌표입니다. 점수는 그 좌표로 채널 간 편차와 같은 꼴(1 = 자리 · 방향 · 크기가
같음)이고, 범위에서 가장 흔한 채널 집합이 아닌 기록은 이 잣대로는 묶이지도 지도에 놓이지도 않습니다. 크기도 재료라 진폭만
다른 기록들은 주성분에서 갈립니다.

### `GET /clusters?set_name=dump&scan_enum=&method=skew`

```json
{ "run": { "id": 4, "method": "skew", "created_at": "2026-10-15T11:40:02+09:00", "records": 312, "clusters": 9,
           "unclustered": 41, "truncated": 0, "threshold": 0.8, "min_size": 3, "stale": false },
  "pool": 312,
  "clusters": [
    { "cluster_no": 1, "scan_enum": "FINEHORVERT_SCAN", "chuck_id": "CH1", "sensor_id": "2", "size": 58, "min_score": 0.81,
      "representative": { "record_id": 12, "servername": "scanner-a", "server_id": 1, "name": "…",
                          "source": "…", "sig": "…", "scan_enum": "…", "meta": { … } },
      "channel": "A", "profile": { "x": [ … 16개 ], "z": [ … 16개 ] },
      "skew": { "tr": { "x": [ … 16개 ], "z": [ … 16개 ], "line": [ … 128개 ] }, "txh": { … }, "tyh": { … } },
      "labelled": 6, "unlabelled": 52,
      "levels": { "1": [ { "label_id": 1, "path": "신호 이상", "count": 6, "share": 1.0, "case_id": null, "agreed": true } ], "2": [ … ], "3": [ … ] },
      "category": { "label_id": 3, "level": 3, "path": "…", "count": 5, "share": 0.833, "case_id": 1, "agreed": true } } ] }
```

- 아직 묶은 적이 없으면 `run: null` · `clusters: []`. `pool` 은 지금 풀의 기록 수.
- `stale: true` = 묶은 뒤에 풀에 새 기록이 들어왔거나 모양이 바뀌었다 — 「다시 묶기」를 권할 자리. 같은 파일을
  다시 연 것은 바뀐 것이 아닙니다.
- 다시 묶으면 **지난 판을 이어서** 묶습니다(SL-CLU-13) — 대표와 여전히 닮은 구성원은 같은 묶음 번호에 남고, 새 기록은
  닮은 묶음에 합류하고, 새 묶음은 쓴 적 없는 번호를 받습니다. 그래서 번호는 크기 순이 아니고, 목록은 큰 묶음부터 옵니다.
  `run.id` 는 판마다 새로 납니다. `run.changes` = 지난 판과 견준 기록 수 `{ "kept", "moved", "joined", "released", "out",
  "new_clusters", "closed_clusters" }`(처음 묶은 판은 `null`).
- 범위마다 **라벨된 기록은 전부**, 라벨 없는 기록은 최근 것부터 800건까지 묶습니다(SL-CLU-12). `truncated` 는 잘린 라벨 없는 기록 수.
- `min_score` · 구성원의 `score` 는 대표와의 **채널 간 편차 점수**입니다. `skew` 는 대표 기록의 채널별 편차 곡선(기준선을 뺀 것,
  채널 표준편차 단위) — 채널이 하나뿐이면 `{}`. `profile` 은 지금처럼 `channel` 하나의 모양입니다.

### `POST /clusters/run` · `{ "set_name": "dump" }`

두 잣대(`skew` · `pca`)를 모두 묶어 각각 저장합니다.

다시 묶기를 **시작**하고 끝나기를 기다리지 않습니다 → `202 { "job": { "state": "running", "started_at": "…",
"finished_at": null, "error": null } }`. 이미 돌고 있으면 그 작업을 그대로 돌려줍니다(두 번 돌지 않음).
진행은 `GET /clusters` 의 `job` 으로 봅니다 — `running` → `done` 이면 목록이 새 결과, `failed` 면 `error` 에 이유가
있고 목록은 이전 결과입니다. 묶음 범위(스캔 종류 · 척 · 센서)마다 최근 800건까지 — 2,400건에 20초 안팎. 같은 일을
`python -m scanlabel.application.jobs cluster` 로도 돌립니다(그 진행은 API 의 `job` 에 보이지 않고, 결과만 보입니다).
`jobs cluster --method skew|pca` 는 하나만 묶고, 잣대마다 라벨된 기록으로 잰 단계별 순도 · 덮음과 두 잣대가 얼마나 같게
갈랐는지(adjusted Rand, `agreement`)를 찍습니다.

> 요청 하나에 기다리게 하던 첫 판은 18초에서 앞단 프록시가 연결을 끊었습니다(2026-10-15) — 그래서 뒤에서 돕니다.

### `GET /clusters/map?set_name=dump&scan_enum=&chuck_id=&sensor_id=&server_id=&name=&source=&method=skew` — 묶음 지도

묶음 범위(스캔 종류 · 척 · 센서) 하나의 기록들을 2D 에 놓은 것 — **채널 간 편차가 가까울수록 닮았습니다**(묶음과 같은 잣대,
근사: 기준점 24개에 대한 거리로 놓는다). `server_id`·`name`·`source` 를 주면 그 기록(지금 화면의 기록)의 자리도 줍니다.
`scan_enum`(+`chuck_id` · `sensor_id`, 빼면 없음 `""`)을 빼면 그 기록의 범위, 그것도 없으면 가장 많은 범위.

```json
{ "run_id": 4, "method": "skew", "scan_enum": "FINEHORVERT_SCAN", "chuck_id": "CH1", "sensor_id": "2", "has_map": true,
  "kinds": [ { "scan_enum": "FINEHORVERT_SCAN", "chuck_id": "CH1", "sensor_id": "2", "n": 800 },
             { "scan_enum": "FINEHORVERT_SCAN", "chuck_id": "CH2", "sensor_id": "2", "n": 412 } ],
  "points": [ { "id": 12, "x": -0.2573, "y": 0.1102, "c": 2, "l": true } ],
  "clusters": [ { "cluster_no": 2, "size": 534, "category": "ID-0603 › WS › IFM-MIOB" } ],
  "target": { "x": 0.5604, "y": 0.0, "cluster_no": null, "record_id": 1, "in_run": false },
  "target_kind": "FINEHORVERT_SCAN", "target_chuck": "CH1", "target_sensor": "2", "axes": null }
```

`method=pca` 면 자리 = 첫째 · 둘째 주성분 좌표 그대로(근사가 아니라 투영)이고, `axes` 가 축마다 무엇을 보는지 줍니다:

```json
"axes": { "kept": 4, "explained": 0.91, "n": 800,
          "axes": [ { "axis": 1, "explained": 0.52,
                      "parts": [ { "part": "skew", "channel": "txh", "share": 0.61 }, … ],
                      "loadings": { "shape": { "tr": { "x": [ … ], "z": [ … ] } }, "skew": { "txh": { … } } },
                      "amp": { "tr": 0.12, "txh": -0.4, "tyh": 0.03 } }, { "axis": 2, … } ] }
```

- `parts` = 그 축의 무게를 (재료 `shape` · `skew` · `amp`, 채널)로 나눈 몫, 큰 것부터. `loadings` = 1D 재료(면: X · Z 단면과
  측정 순서 `line`, 선: `line`)의 무게 곡선 — 큰 자리일수록 그 축을 따라 기록이 갈린다. 칸은 몫에만 들어갑니다.

- 점 = `id`(풀의 기록 id — `GET /records/{id}`) · 자리 · `c` 묶음 번호(묶이지 않음 = `null`) · `l` 라벨 있음.
- `target.in_run: false` = 이번 묶음 뒤에 본 기록이거나 상한에 걸려 빠진 기록 — 같은 투영식으로 놓았고 묶음 번호는 없다.
  그 기록의 범위가 고른 지도와 다르면 `target: null`(`target_kind` · `target_chuck` · `target_sensor` 로 알 수 있다).
- `has_map: false` = 0006 이전에 묶은 결과(지도가 척 없이 나뉘었다)이거나 그 범위의 기록이 너무 적었다 — 다시 묶으면
  생깁니다. 아직 묶은 적이 없으면 404.

### `GET /records/{record_id}?set_name=dump&vs_server_id=&vs_name=&vs_source=&method=skew` — 기록 상세

지도에서 누른 점의 기록. `vs_*` 를 주면 그 기록(지금 화면의 기록)과의 유사도(모양 `similarity` · 채널 간 편차
`skew_similarity` — 묶음 · 지도가 본 점수)와 그 기록의 단면 · 칸 · 편차가 `vs` 에 옵니다. 여기서만 `profiles` 에 16×16 칸
(`grid`, 256개, 행 = Z · 열 = X)이 함께 옵니다.

그림 재료(`profiles` · `skew`, `vs.profiles` · `vs.skew`)는 **기록마다 제 것만으로** 정규화합니다(`skew` 는 그 범위의 기준선을
뺀 것). 그래서 같은 범위 안에서 다른 점을 열어도 `vs` 쪽(지금 기록)의 그림 재료는 바뀌지 않습니다 — 비교는 점수에만 있습니다.

```json
{ "record_id": 12, "servername": "…", "server_id": 1, "name": "…", "source": "…", "sig": "…", "scan_enum": "…",
  "meta": { … }, "seen_at": "…", "labels": [ … ], "cluster_no": 2, "run_id": 4, "method": "skew",
  "chuck_id": "CH1", "sensor_id": "2", "pca": null,
  "profiles": { "A": { "x": [ … 16개 ], "z": [ … 16개 ], "grid": [ … 256개 ] } },
  "skew": { "A": { "x": [ … 16개 ], "z": [ … 16개 ], "line": [ … 128개 ] }, "B": { … } },
  "vs": { "similarity": { "score": 0.88, "d1": 0.85, "d2": 0.9, "scale": 1.3, "by_channel": { … } },
          "skew_similarity": { "score": 0.83, "d1": 0.8, "d2": 0.86, "dx": 0.78, "dz": 0.82, "dp": 0.71, "channels": [ "A", "B" ] },
          "pca_similarity": null,
          "profiles": { "A": { "x": [ … ], "z": [ … ], "grid": [ … ] } },
          "skew": { "A": { "x": [ … ], "z": [ … ] }, "B": { … } } } }
```

`method=pca` 면 `cluster_no` · `run_id` 가 주성분 결과의 것이고, `pca` = 이 기록이 범위의 평소와 얼마나(`distance`, 값 하나당
RMS) · 어디서(`parts`, (재료, 채널)별 몫) 다른지, `vs.pca_similarity` = `{ "score": 0.86 }`(주성분 점수). 범위의 채널 집합과
다른 기록이면 둘 다 `null`.

없는 id 는 404.

### `GET /clusters/{cluster_no}?set_name=dump&run_id=&method=skew`

묶음 하나의 구성원 — 대표 먼저, 그다음 대표와의 점수 순. 구성원마다 `labels`(붙은 라벨)와 겹쳐 그릴 `profile`
(묶음의 `channel` 의 X · Z 단면).

```json
{ "run_id": 4, "method": "skew", "cluster_no": 2, "scan_enum": "…", "size": 17, "channel": "A",
  "labelled": 5, "unlabelled": 12, "levels": { … }, "category": { … },
  "members": [ { "record_id": 12, "servername": "…", "server_id": 1, "name": "…", "source": "…", "sig": "…",
                 "scan_enum": "…", "meta": { … }, "score": 1.0, "representative": true,
                 "labels": [ … ], "profile": { "x": [ … ], "z": [ … ] } } ] }
```

`run_id` 가 그 잣대의 최신 결과와 다르면 422(「그새 묶음이 다시 계산됐다」), 묶은 적이 없거나 없는 번호면 404.

### `POST /clusters/{cluster_no}/apply` — 묶음에 라벨 한 번에 붙이기

```json
{ "set_name": "dump", "method": "skew", "run_id": 4, "label_ids": [3], "record_ids": null, "only_unlabelled": true }
```

→ `{ "applied": 12, "skipped": 5 }`

- `record_ids` 를 주면 그 구성원만, 빼면 묶음 전체. `only_unlabelled: true`(기본)면 이미 라벨된 구성원은 건너뜁니다.
- 라벨은 `PUT /labels` 와 같은 규칙으로 검증하고, 하나라도 어기면 **하나도 쓰지 않고** 422.
- 풀에 저장된 특징을 그대로 씁니다 — fleet 을 부르지 않습니다.
- `run_id` 가 `method` 잣대의 최신 결과와 다르면 422 — 다른 사람이 다시 묶은 뒤의 번호로 엉뚱한 기록에 붙이지 않게.
  화면이 본 결과의 잣대를 그대로 보냅니다(기본 `skew`).

- `skew_similarity` 의 `d1` = 단면(`dx` · `dz` 평균) · `d2` = 칸 · `dp` = 측정 순서(면 스캔만, 선 스캔은 `null`) 점수 —
  `score` 는 이 셋의 가중 평균입니다. `skew` 의 `line` = 측정 순서 곡선의 채널 간 편차(면 스캔은 `x` · `z` 옆에, 선 스캔은 이것만).

## `GET /guide` — 묶음 화면 읽는 법

리뷰하는 사람용 HTML 문서(SL-DOC-1). 어떤 데이터를 가져와서 어떤 순서로 묶음이라는 결론이 나는지, 기록 상세의 그림 하나하나가
무엇이고 점수에 쓰이는지를 설명합니다. 문턱 · 가중치는 요청할 때 지금 상수로 채웁니다. 절마다 고정 앵커가 있어
(`#flow` · `#data` · `#materials` · `#skew` · `#pca` · `#cluster` · `#map` · `#detail` · `#path` · `#faq`) 화면이 그 절로 바로 겁니다.

## `GET /health`

`{ "db": true, "fleet": true, "fleet_url": "http://localhost:8000", "feature_version": 1 }`
