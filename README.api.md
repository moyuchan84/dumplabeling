# scanlabel HTTP API

poc_viewer 는 same-origin `/api/scanlabel/...` 로 부르고(`next.config.ts` rewrite → `SCANLABEL_API_URL`),
기본 포트는 **8100** 입니다. 인증은 없습니다(fleet 과 같이 네트워크 위치로 막습니다).

## 에러

| status | 뜻 |
|---|---|
| `422` | 검증 실패(트리 규칙 · 이름 중복 · 내려진 라벨 · 조상+자손 동시 선택) **또는** fleet 이 아직 그 파일을 해석해 두지 않음 — 본문 `detail` 에 사람이 읽는 문장 |
| `404` | 없는 라벨 id · 그 파일에 없는 기록(`source`) · fleet 이 모르는 서버 |
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

`source` 를 빼면 파일의 모든 기록에 대해 제안 하나씩:

```json
{ "sig": "…", "records": [ { "source": "…", "suggestion": { "label_id": 3, "level": 3, "path": "…", "score": 0.93, "…": "…" } },
                           { "source": "…", "suggestion": null } ] }
```

## `GET /health`

`{ "db": true, "fleet": true, "fleet_url": "http://localhost:8000", "feature_version": 1 }`
