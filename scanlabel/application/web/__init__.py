"""화면 문서(HTML) — 서버가 직접 내주는 읽는 법. 숫자 자리 `{{이름}}` 은 지금 쓰는 `shape` 상수로 채운다."""
from __future__ import annotations

import pathlib

from ...processing import shape

GUIDE = pathlib.Path(__file__).with_name("guide.html")
FLOW = pathlib.Path(__file__).with_name("guide_flow.html")
DB = pathlib.Path(__file__).with_name("guide_db.html")


def guide_values() -> dict[str, str]:
    """읽는 법(`guide.html`)의 {{이름}} 자리 — 지금 쓰는 `shape` 상수. 문서의 숫자가 코드와 갈라지지 않게."""
    pct = lambda x: f"{round(x * 100):d}"
    area = shape.PCA_MAT_W["area"]
    vals = {n: getattr(shape, n) for n in (
        "FEATURE_VERSION", "GRID_N", "PATH_N", "CLUSTER_K_MAX", "CLUSTER_MIN_SIZE", "CLUSTER_MAX_N", "SKEW_FLOOR",
        "W_1D", "W_2D", "W_SEQ", "PCA_PATH", "PCA_MAX_K", "MAP_LANDMARKS", "PROFILE_MIN", "MIN_OVERLAP",
        "DRAW_THIN", "PCA_FLOOR", "PCA_GRID")}
    vals.update(CLUSTER_AGREE_PCT=pct(shape.CLUSTER_AGREE), PCA_VAR_PCT=pct(shape.PCA_VAR),
                PCA_W_SHAPE=shape.PCA_W["shape"], PCA_W_SKEW=shape.PCA_W["skew"], PCA_W_AMP=shape.PCA_W["amp"],
                PCA_MAT_X=area["x"], PCA_MAT_Z=area["z"], PCA_MAT_GRID=area["grid"], PCA_MAT_PATH=area["path"])
    return {k: str(v) for k, v in vals.items()}


# 읽는 법의 탭 — (이름, 글자). 세 페이지가 같은 줄을 달고, 지금 보는 것만 표시한다.
TABS = (("guide", "읽는 법"), ("flow", "그림으로 따라가기 (분석 흐름)"), ("db", "저장 구조 (DB)"))
TABS_CSS = """
.gtabs { background: var(--plane); border-bottom: 1px solid var(--line); }
.gtabs .in { max-width: 1000px; margin: 0 auto; padding: 0 16px; display: flex; flex-wrap: wrap; gap: 0 4px; }
.gtabs a { padding: 11px 14px; font-size: 14px; font-weight: 500; color: var(--ink-2); text-decoration: none; border-bottom: 3px solid transparent; }
.gtabs a:hover { color: var(--ink); }
.gtabs a[aria-current="page"] { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
"""
# /guide 는 한 단계 위, /guide/flow · /guide/db 는 같은 단계라 상대 주소가 다르다(앞단 프록시의 접두어를 건드리지 않게 상대로 둔다).
_HREF = {"guide": {"guide": "guide", "flow": "guide/flow", "db": "guide/db"},
         "flow": {"guide": "../guide", "flow": "flow", "db": "db"},
         "db": {"guide": "../guide", "flow": "flow", "db": "db"}}


def tabs_html(active: str) -> str:
    links = "".join(f'<a href="{_HREF[active][k]}"' + (' aria-current="page"' if k == active else "") + f">{label}</a>"
                    for k, label in TABS)
    return f'<nav class="gtabs" aria-label="읽는 법"><div class="in">{links}</div></nav>'


def render_guide(path: pathlib.Path = GUIDE, active: str = "guide") -> str:
    html = path.read_text(encoding="utf-8")
    html = html.replace("{{TABS_CSS}}", TABS_CSS).replace("{{TABS}}", tabs_html(active))
    for k, v in guide_values().items():
        html = html.replace("{{" + k + "}}", v)
    return html
