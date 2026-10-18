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
  "target_paths": { "A": [ -0.41, … 128개 ] },
  "matches": [
    { "id": 7, "servername": "scanner-c", "server_id": 3, "name": "server3.tdf", "source": "…",
      "scan_enum": "FINEHORVERT_SCAN", "meta": { … },
      "labels": [ { "id": 3, "level": 3, "path": "신호 이상 › 피크 › 단일 피크", "case_id": 1 } ],
      "similarity": { "score": 0.91, "d1": 0.94, "d2": 0.88, "scale": 1.8,
                      "by_channel": { "A": { "score": 0.95, "d1": 0.97, "d2": 0.93, "scale": 1.7 } } },
      "paths": { "A": [ … 128개, z-score ] } } ],
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
- `same_scan_only: true`(기본)면 같은 `scan_enum` 의 기록끼리만 비교합니다 — 스캔 경로가 다르면 1D 비교가 무의미합니다.

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
- **`cluster` = 이 기록이 든 묶음**(최신 묶음 결과 기준, 없으면 `null`). 모양은 아래 `GET /clusters` 의 한 칸과 같습니다.
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

라벨이 있든 없든 scanlabel 이 **본 기록 전부**(기록 풀)를 모양으로 묶습니다. 같은 스캔 종류끼리만 묶고,
구성원 모두가 묶음의 **대표 기록**과 직접 0.8 이상 닮았습니다. 3건보다 작은 무리는 「묶이지 않음」입니다.
분류(`category`)는 그 묶음의 **라벨된 구성원**이 합의한 가장 깊은 단계입니다(60% 이상 + 2위보다 많음 + 위 단계의
자식) — 라벨된 구성원이 없거나 갈리면 `null`. 이 판단도 서버가 하고, 화면은 옮겨 적습니다.

### `GET /clusters?set_name=dump&scan_enum=`

```json
{ "run": { "id": 4, "created_at": "2026-10-15T11:40:02+09:00", "records": 312, "clusters": 9,
           "unclustered": 41, "truncated": 0, "threshold": 0.8, "min_size": 3, "stale": false },
  "pool": 312,
  "clusters": [
    { "cluster_no": 1, "scan_enum": "FINEHORVERT_SCAN", "size": 58, "min_score": 0.81,
      "representative": { "record_id": 12, "servername": "scanner-a", "server_id": 1, "name": "…",
                          "source": "…", "sig": "…", "scan_enum": "…", "meta": { … } },
      "channel": "A", "path": [ … 128개, z-score ],
      "labelled": 6, "unlabelled": 52,
      "levels": { "1": [ { "label_id": 1, "path": "신호 이상", "count": 6, "share": 1.0, "case_id": null, "agreed": true } ], "2": [ … ], "3": [ … ] },
      "category": { "label_id": 3, "level": 3, "path": "…", "count": 5, "share": 0.833, "case_id": 1, "agreed": true } } ] }
```

- 아직 묶은 적이 없으면 `run: null` · `clusters: []`. `pool` 은 지금 풀의 기록 수.
- `stale: true` = 묶은 뒤에 풀에 새 기록이 들어왔거나 모양이 바뀌었다 — 「다시 묶기」를 권할 자리. 같은 파일을
  다시 연 것은 바뀐 것이 아닙니다.
- 묶음 번호는 **그 결과 안에서만** 뜻이 있습니다(크기 순). 다시 묶으면 번호가 바뀌고 `run.id` 가 새로 납니다.

### `POST /clusters/run` · `{ "set_name": "dump" }`

다시 묶기를 **시작**하고 끝나기를 기다리지 않습니다 → `202 { "job": { "state": "running", "started_at": "…",
"finished_at": null, "error": null } }`. 이미 돌고 있으면 그 작업을 그대로 돌려줍니다(두 번 돌지 않음).
진행은 `GET /clusters` 의 `job` 으로 봅니다 — `running` → `done` 이면 목록이 새 결과, `failed` 면 `error` 에 이유가
있고 목록은 이전 결과입니다. 스캔 종류마다 최근 800건까지 — 2,400건에 20초 안팎. 같은 일을
`python -m scanlabel.application.jobs cluster` 로도 돌립니다(그 진행은 API 의 `job` 에 보이지 않고, 결과만 보입니다).

> 요청 하나에 기다리게 하던 첫 판은 18초에서 앞단 프록시가 연결을 끊었습니다(2026-10-15) — 그래서 뒤에서 돕니다.

### `GET /clusters/map?set_name=dump&scan_enum=&server_id=&name=&source=` — 묶음 지도

스캔 종류 하나의 기록들을 2D 에 놓은 것 — **가까울수록 닮았습니다**(근사: 기준점 24개에 대한 거리로 놓는다).
`server_id`·`name`·`source` 를 주면 그 기록(지금 화면의 기록)의 자리도 줍니다. `scan_enum` 을 빼면 그 기록의 종류,
그것도 없으면 가장 많은 종류.

```json
{ "run_id": 4, "scan_enum": "FINEHORVERT_SCAN", "has_map": true,
  "kinds": [ { "scan_enum": "FINEHORVERT_SCAN", "n": 800 }, { "scan_enum": "COARSEHORVERT_SCAN", "n": 800 } ],
  "points": [ { "id": 12, "x": -0.2573, "y": 0.1102, "c": 2, "l": true } ],
  "clusters": [ { "cluster_no": 2, "size": 534, "category": "ID-0603 › WS › IFM-MIOB" } ],
  "target": { "x": 0.5604, "y": 0.0, "cluster_no": null, "record_id": 1, "in_run": false },
  "target_kind": "FINEHORVERT_SCAN" }
```

- 점 = `id`(풀의 기록 id — `GET /records/{id}`) · 자리 · `c` 묶음 번호(묶이지 않음 = `null`) · `l` 라벨 있음.
- `target.in_run: false` = 이번 묶음 뒤에 본 기록이거나 상한에 걸려 빠진 기록 — 같은 투영식으로 놓았고 묶음 번호는 없다.
  그 기록의 종류가 고른 지도와 다르면 `target: null`(`target_kind` 로 알 수 있다).
- `has_map: false` = 0004 이전에 묶은 결과 — 다시 묶으면 생깁니다. 아직 묶은 적이 없으면 404.

### `GET /records/{record_id}?set_name=dump&vs_server_id=&vs_name=&vs_source=` — 기록 상세

지도에서 누른 점의 기록. `vs_*` 를 주면 그 기록(지금 화면의 기록)과의 유사도와 두 곡선이 `vs` 에 옵니다.

```json
{ "record_id": 12, "servername": "…", "server_id": 1, "name": "…", "source": "…", "sig": "…", "scan_enum": "…",
  "meta": { … }, "seen_at": "…", "labels": [ … ], "cluster_no": 2, "run_id": 4,
  "paths": { "A": [ … 128개, z-score ] },
  "vs": { "similarity": { "score": 0.88, "d1": 0.85, "d2": 0.9, "scale": 1.3, "by_channel": { … } },
          "paths": { "A": [ … ] } } }
```

없는 id 는 404.

### `GET /clusters/{cluster_no}?set_name=dump&run_id=`

묶음 하나의 구성원 — 대표 먼저, 그다음 대표와의 점수 순. 구성원마다 `labels`(붙은 라벨)와 겹쳐 그릴 `path`
(묶음의 `channel`, z-score 128점).

```json
{ "run_id": 4, "cluster_no": 2, "scan_enum": "…", "size": 17, "channel": "A",
  "labelled": 5, "unlabelled": 12, "levels": { … }, "category": { … },
  "members": [ { "record_id": 12, "servername": "…", "server_id": 1, "name": "…", "source": "…", "sig": "…",
                 "scan_enum": "…", "meta": { … }, "score": 1.0, "representative": true,
                 "labels": [ … ], "path": [ … ] } ] }
```

`run_id` 가 최신 결과와 다르면 422(「그새 묶음이 다시 계산됐다」), 묶은 적이 없거나 없는 번호면 404.

### `POST /clusters/{cluster_no}/apply` — 묶음에 라벨 한 번에 붙이기

```json
{ "set_name": "dump", "run_id": 4, "label_ids": [3], "record_ids": null, "only_unlabelled": true }
```

→ `{ "applied": 12, "skipped": 5 }`

- `record_ids` 를 주면 그 구성원만, 빼면 묶음 전체. `only_unlabelled: true`(기본)면 이미 라벨된 구성원은 건너뜁니다.
- 라벨은 `PUT /labels` 와 같은 규칙으로 검증하고, 하나라도 어기면 **하나도 쓰지 않고** 422.
- 풀에 저장된 특징을 그대로 씁니다 — fleet 을 부르지 않습니다.
- `run_id` 가 최신 결과와 다르면 422 — 다른 사람이 다시 묶은 뒤의 번호로 엉뚱한 기록에 붙이지 않게.

## `GET /health`

`{ "db": true, "fleet": true, "fleet_url": "http://localhost:8000", "feature_version": 1 }`
