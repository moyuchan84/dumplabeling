# scanlabel — 개발자 README (구조 · 실행 · 선택 테스트 지도)

모듈을 **수정·확장하는 사람**을 위한 문서입니다. 화면·호출만 하는 사람은 [`README.md`](./README.md)(실행·운영)와
[`README.api.md`](./README.api.md)(HTTP 계약)를 봅니다. 요구사항·결정은 [`SPEC.md`](./SPEC.md)(영문),
AI 작업 규칙은 [`CLAUDE.md`](./CLAUDE.md)(영문).

## 구조 — 네 영역

```
scanlabel/
├── timebase.py          벽시계를 읽는 유일한 곳 + KST 변환
├── processing/          순수 계산 (표준 라이브러리만)   → processing/__spec__.md
│   ├── shape.py           특징 · 유사도 · 제안 · 묶음 · 지도 · 평가 — 문턱값은 여기 상수에만
│   └── tree.py            라벨 트리 규칙
├── connectors/          fleet 과의 경계                  → connectors/__spec__.md
│   └── fleet.py           fleet 공개 HTTP API 클라이언트 (캐시만 읽는다)
├── data/                저장 · 스키마                    → data/__spec__.md
│   ├── repository.py      PostgreSQL Store (원시 SQL)
│   ├── migrate.py         번호 붙은 전진 전용 SQL 실행기
│   └── migrations/        0001 … (적용된 파일은 불변)
└── application/         유스케이스와 두 어댑터           → application/__spec__.md
    ├── usecases.py        API 와 CLI 가 함께 쓰는 일
    ├── api.py             FastAPI 라우트
    └── jobs.py            CLI (seed-labels · eval · refeature · index · cluster)
```

의존 방향은 한쪽뿐입니다: `processing` ← `data` ← `application`, `connectors` ← `application`.
`tests/test_architecture.py` 가 어기는 순간 실패합니다 — 새 모듈은 영역 안에 두고, 위쪽을 import 하지 않습니다.

## 실행

설치 · DB · 환경 변수 · 서버 기동은 [`README.md`](./README.md)의 「실행 (이 PC)」가 정본입니다.
진입점은 영역 경로입니다.

```powershell
python -m scanlabel.data.migrate up            # 스키마 (status: 미적용이 있으면 exit 1)
python -m scanlabel.application.jobs eval      # CLI
python -m uvicorn scanlabel.application.api:app --port 8100
```

## 확장 — 어디를 고치나

[`CLAUDE.md`](./CLAUDE.md) 의 「Change → what to touch」 표가 정본입니다. 자주 하는 것만:

- **문턱값 · 가중치를 바꿈** → `processing/shape.py` 상수 한 곳. 동작 변경이므로 실제 라벨에 `jobs eval` 을 돌려
  숫자를 커밋 메시지에 적습니다.
- **스냅샷 모양을 바꿈** → `FEATURE_VERSION` 을 올리고, 배포한 뒤 `jobs refeature` → `jobs cluster`.
- **라우트 · 응답 · 에러** → `application/api.py` 와 `README.api.md` 를 같은 변경에서.
- **스키마** → `data/migrations/` 에 새 번호 파일. 적용된 파일은 고치지 않습니다(체크섬 불일치는 하드 에러).
- **fleet 이 안 주는 데이터가 필요** → fleet 에 요청합니다. fleet 코드 import · fleet DB 읽기는 하지 않습니다.

## 테스트

```powershell
$env:SCANLABEL_TEST_DSN = 'postgresql://postgres@127.0.0.1:5433/scanlabel_test'
.\.venv\Scripts\python -m pytest
```

실제 PostgreSQL 이 필요하고, DSN 이 없으면 조용히 건너뛰지 않고 실패합니다. fleet 은
`tests/conftest.py::FakeFleet`(`httpx.MockTransport`)이 흉내 냅니다.

### 선택 테스트 지도 — "고친 곳만 돌린다"

개발 루프에서는 고친 곳에 해당하는 테스트만 돌리고, 머지 전에 전체를 한 번 돌립니다. 표에 없는 결합이 의심되면
전체를 돌립니다.

| 고친 곳 | 돌릴 테스트 |
|---|---|
| `processing/shape.py` | `tests/test_shape.py` + `tests/test_architecture.py` — 상수를 바꿨다면 `jobs eval` 숫자도 |
| `processing/shape.py` 의 `cluster` · `cluster_map` · `cluster_category` | 위 + `tests/test_cluster.py` (저장된 묶음 · 지도 · 일괄 라벨) |
| `processing/tree.py` | `tests/test_tree.py` + `tests/test_api.py` (트리 라우트 · 라벨 검증) |
| `connectors/fleet.py` | `tests/test_api.py` + `tests/test_cluster.py` (`FakeFleet` 을 통과하는 전부 — 캐시 · 502 · 색인 커서) |
| `data/repository.py` | `tests/test_api.py` + `tests/test_cluster.py` + `tests/test_taxonomy.py` |
| `data/migrations/` 의 새 파일 | 위 `data/` 줄 전체 — `conftest` 가 빈 DB 에 처음부터 적용한다. 데이터를 넣는 마이그레이션은 `tests/test_taxonomy.py` 처럼 다시 돌려 멱등을 핀으로 잡는다 |
| `application/usecases.py` | `tests/test_api.py` (seed · eval) + `tests/test_cluster.py` (index · 묶기) |
| `application/api.py` | `tests/test_api.py` + `tests/test_cluster.py` — `README.api.md` 도 같은 변경에서 |
| `application/jobs.py` | 인자 · 출력만이므로 `tests/test_api.py` 의 `run_eval` / `seed_labels` 와 수동 실행 |
| 모듈 추가 · 이동 | `tests/test_architecture.py` (영역 밖 모듈 · 위쪽 import · 시계 읽기 · `__spec__.md`) |

## 문서 지도

| 파일 | 독자 | 언어 |
|---|---|---|
| `README.md` | 운영자 · 사용자 — 실행 · 라벨 넣기 · 기록 풀 · 측정 | 한국어 |
| `README.api.md` | 호출자 — HTTP 계약 | 한국어 |
| `README.dev.md` | 개발자 — 구조 · 선택 테스트 지도 (이 문서) | 한국어 |
| `SPEC.md` | 요구사항 · 불변식 (`SL-*` ID) | 영문 |
| `CLAUDE.md` | 작업 규칙 · 영역 지도 · 변경 라우팅 | 영문 |
| `scanlabel/<영역>/__spec__.md` | 영역 내부 · 결정 기록 · 테스트 지도 | 영문 |
