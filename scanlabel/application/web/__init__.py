"""화면 문서(HTML) — 서버가 직접 내주는 읽는 법. 숫자 자리 `{{이름}}` 은 지금 쓰는 `shape` 상수로 채운다."""
from __future__ import annotations

import pathlib

from ...processing import shape

GUIDE = pathlib.Path(__file__).with_name("guide.html")
FLOW = pathlib.Path(__file__).with_name("guide_flow.html")


def guide_values() -> dict[str, str]:
    """읽는 법(`guide.html`)의 {{이름}} 자리 — 지금 쓰는 `shape` 상수. 문서의 숫자가 코드와 갈라지지 않게."""
    pct = lambda x: f"{round(x * 100):d}"
    area = shape.PCA_MAT_W["area"]
    vals = {n: getattr(shape, n) for n in (
        "GRID_N", "PATH_N", "CLUSTER_K_MAX", "CLUSTER_MIN_SIZE", "CLUSTER_MAX_N", "SKEW_FLOOR",
        "W_1D", "W_2D", "W_SEQ", "PCA_PATH", "PCA_MAX_K", "MAP_LANDMARKS", "PROFILE_MIN", "MIN_OVERLAP",
        "DRAW_THIN", "PCA_FLOOR", "PCA_GRID")}
    vals.update(CLUSTER_AGREE_PCT=pct(shape.CLUSTER_AGREE), PCA_VAR_PCT=pct(shape.PCA_VAR),
                PCA_W_SHAPE=shape.PCA_W["shape"], PCA_W_SKEW=shape.PCA_W["skew"], PCA_W_AMP=shape.PCA_W["amp"],
                PCA_MAT_X=area["x"], PCA_MAT_Z=area["z"], PCA_MAT_GRID=area["grid"], PCA_MAT_PATH=area["path"])
    return {k: str(v) for k, v in vals.items()}


def render_guide(path: pathlib.Path = GUIDE) -> str:
    html = path.read_text(encoding="utf-8")
    for k, v in guide_values().items():
        html = html.replace("{{" + k + "}}", v)
    return html
