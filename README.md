# scanlabel

dump 기록(스캔 한 건)에 **대분류 › 중분류 › 소분류 라벨**을 붙이고, 화면에 띄운 기록과 **모양이 닮은
라벨된 기록**을 찾아 그 라벨을 단계별로 제안하는 서비스입니다. 라벨이 없는 기록까지 **모양으로 묶어(클러스터)**
묶음 단위로 분류하고, 닮은 기록들이 가리키는 **유사 사례**(fleet `acs_case`)를 모아 보여 줍니다.

- fleet(ftpmodule)의 공개 HTTP API 를 **캐시로만** 읽습니다 — FTP 를 일으키지 않고, fleet DB 를 읽지 않습니다.
- 화면은 poc_viewer 의 Dump → 「사례」 패널 → 「이 기록 라벨」 탭입니다.
- 규칙과 결정: `SPEC.md` · 호출 계약: `README.api.md` · 작업 규칙: `CLAUDE.md`.

## 실행 (이 PC)

```powershell
.\.local\pg.ps1 start                                   # 워크스페이스 루트 — PostgreSQL 16, 포트 5433
cd scanlabel
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
$env:SCANLABEL_DB_DSN = 'postgresql://postgres@127.0.0.1:5433/scanlabel'
$env:FLEET_API_URL    = 'http://localhost:8787'        # 목(poc_viewer npm run mock) 또는 fleet 8000
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python -m scanlabel.data.migrate up
.\.venv\Scripts\python -m scanlabel.application.jobs seed-labels labels.seed.example.json   # 임시 트리
.\.venv\Scripts\python -m uvicorn scanlabel.application.api:app --port 8100
```

DB 가 없으면 한 번 만듭니다: `psql -h 127.0.0.1 -p 5433 -U postgres -c "CREATE DATABASE scanlabel"`.
VS Code 에서는 워크스페이스의 `scanlabel API (8100)` 구성(또는 compound 「풀스택」)으로 띄웁니다.

## 라벨 트리 넣기

**실제 분류표 ID-0603**(워크스페이스의 `0603.md` — error_no › category › error)은 마이그레이션 `0003` 이
`migrate up` 때 넣습니다. 트리는 대 › 중 › 소 **3단계로 고정**이고, `IFM-MIOB` · `Air shower-air balance` 처럼
이름에 묶음 접두어가 있는 항목도 4단계로 나누지 않고 그 이름 그대로 소분류입니다(SPEC `SL-TREE-11`).
`labels.seed.example.json` 은 개발용 임시 트리라 운영 DB 에는 넣지 않습니다.

다른 분류표가 더 오면 엑셀에서 CSV(UTF-8)로 저장해 넣으면 됩니다. 열은 아래 순서이고, 머리 행은 있어도 됩니다.

```csv
대분류,중분류,소분류,코드,설명,사례번호
신호 이상,피크,단일 피크,A-01-1,한 자리에서만 튄다,7
신호 이상,레벨 저하,,A-02,,
```

- 소분류를 비우면 중분류에서 끝나는 가지가 됩니다. 코드 · 설명 · 사례번호는 그 행의 **가장 깊은** 노드에 붙습니다.
- 여러 번 넣어도 됩니다(멱등) — 이미 있는 노드는 그대로 쓰고 빈 코드 · 설명 · 사례만 채웁니다.
- `사례번호` 는 fleet `acs_case.case_id` 입니다.

## 기록 풀과 묶음

묶음(클러스터)은 scanlabel 이 **본 기록 전부**(라벨 없는 것 포함)를 대상으로 합니다. 기록은 세 길로 풀에 들어옵니다.

- 화면에서 Dump 파일을 열면(그 파일의 제안을 물을 때) 그 파일의 기록 전부.
- 라벨을 저장하면 그 기록.
- `jobs index` — fleet 이 해석해 둔 dump 파일을 `/generations` 커서로 따라가며 넣습니다(캐시만, FTP 없음).
  주기적으로 돌리면 아무도 열지 않은 파일도 묶음에 들어갑니다.

```powershell
.\.venv\Scripts\python -m scanlabel.application.jobs index           # 이어서 읽는다 (커서는 DB 의 index_cursors)
.\.venv\Scripts\python -m scanlabel.application.jobs cluster         # 화면의 「다시 묶기」와 같은 일
```

묶기는 스캔 종류마다 최근 기록 `CLUSTER_MAX_N`(800)개까지입니다 — 모든 쌍을 비교하므로(800개에 약 6초) 상한이
있고, 넘친 수는 결과에 `truncated` 로 나옵니다.

## 측정

```powershell
.\.venv\Scripts\python -m scanlabel.application.jobs eval            # leave-one-out 단계별 적중률
.\.venv\Scripts\python -m scanlabel.application.jobs refeature       # FEATURE_VERSION 이 바뀐 뒤
```

라벨이 수십 건 쌓이면 `eval` 로 「닮은 모양 = 같은 원인」이 실제로 맞는지부터 확인합니다. 문턱값 · 가중치는
`scanlabel/processing/shape.py` 의 상수 한 곳에만 있습니다. `refeature` 는 라벨된 기록과 기록 풀을 함께 새 판으로
바꿉니다 — 그 뒤 `cluster` 를 다시 돌립니다.

## 테스트

```powershell
$env:SCANLABEL_TEST_DSN = 'postgresql://postgres@127.0.0.1:5433/scanlabel_test'
.\.venv\Scripts\python -m pytest
```

실제 PostgreSQL 이 필요하고, DSN 이 없으면 조용히 건너뛰지 않고 실패합니다. fleet 은 `httpx.MockTransport` 로 흉내 냅니다.
