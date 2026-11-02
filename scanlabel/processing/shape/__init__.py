"""모양 특징과 유사도 — 순수 함수, 표준 라이브러리만 (SPEC.md 「모양 특징」·「유사도」·「제안」).

기록 한 건 = dump raw 스캔 하나(`value{채널: [..]}` + `xyz{x,z}`). 채널마다

- **측정 순서 곡선**: 스캔 순서 배열을 `PATH_N` 점으로 줄인 것(구간 평균; 짧으면 선형 보간).
- **칸**: (x, z) 를 0–1 평면에 놓고 `GRID_N`×`GRID_N` 칸 평균(빈 칸은 None).

을 **정규화 전** 값으로 스냅샷에 남긴다. 비교 재료 고르기 · 정규화(z-score) · 비교는 `similarity` 가
그때그때 한다 — 그 방식을 바꾸는 변경은 원본 없이 스냅샷만으로 다시 계산된다.

- 면 스캔(칸이 있다): 1D = **X 단면 · Z 단면**(칸의 열 · 행 평균), 2D = 칸. 셋을 **각자** 정규화한다.
  측정 순서 곡선은 쓰지 않는다 — 면을 한 줄로 펴면 행이 꺾이는 자리가 섞여 모양이 뭉개진다.
- 선 스캔(칸이 없다): 1D = 측정 순서 곡선.

묶음 · 지도는 모양 대신 **채널 간 편차(skew)**로 잰다 — 같은 자리(칸 · 단면 칸 · 경로 칸)에서 채널마다
(제 정규화 값 − 채널 평균). dump 오류는 한 채널의 모양보다 채널(tr · txh · tyh …)끼리 어긋나는 자리와 방향으로
갈린다(SL-CLU-9). 면 스캔의 편차에는 **측정 순서 곡선도** 넣는다(SL-CLU-11) — 채널은 `xyz` 를 함께 쓰므로 같은
인덱스는 같은 점이고, 경로 칸 하나에 행이 꺾이는 자리가 섞여도 모든 채널에 똑같이 섞여 채널끼리의 어긋남은
남는다. 라벨 제안(`rank`)은 그대로 모양 유사도다(모양에는 측정 순서 곡선을 쓰지 않는다, SL-SIM-1).

문턱값 · 가중치는 **휴리스틱**이다. `evaluate`(leave-one-out 적중률)로 재고 여기서만 고친다.
"""
from __future__ import annotations

from .constants import (  # noqa: F401
    CASE_TOP,
    CLUSTER_AGREE,
    CLUSTER_K_MAX,
    CLUSTER_MAX_N,
    CLUSTER_MIN_SIZE,
    EVIDENCE_MIN,
    FEATURE_VERSION,
    FLAT_REL,
    GRID_N,
    LEVEL_TOP,
    MAP_LANDMARKS,
    MARGIN,
    METHODS,
    MIN_OVERLAP,
    PATH_N,
    PCA_FLOOR,
    PCA_GRID,
    PCA_ITER,
    PCA_MAT_W,
    PCA_MAX_K,
    PCA_PATH,
    PCA_VAR,
    PCA_W,
    PROFILE_MIN,
    SKEW_FLOOR,
    SUGGEST_MIN,
    W_1D,
    W_2D,
    W_SEQ,
)
from .features import (  # noqa: F401
    _grid,
    _resample,
    _stats,
    channel_features,
    num,
    record_features,
)
from .similarity import (  # noqa: F401
    _SEQ_MIN,
    _channel_score,
    _corr,
    _dot,
    _flat,
    _mean,
    _pre,
    _prep_channel,
    _scale,
    _score_seq,
    _zcorr,
    _znone,
    normalized_profiles,
    prepare,
    profiles,
    similarity,
    zscore,
)
from .skew import (  # noqa: F401
    _SKEW_MATS,
    _base_fits,
    _skew_dev,
    _skew_norm,
    _skew_pack,
    _skew_score,
    prepare_skew,
    skew_baseline,
    skew_profiles,
    skew_similarity,
)
from .pca import (  # noqa: F401
    _PCA_MATS,
    _down,
    _down_grid,
    _jacobi,
    _model_mats,
    _orth,
    _pca_raw,
    _pca_resid,
    _pca_split,
    _shares,
    _top_pcs,
    pca_axes,
    pca_coords,
    pca_model,
    pca_parts,
    pca_similarity,
)
from .ranking import (  # noqa: F401
    rank,
    similar_cases,
)
from .clustering import (  # noqa: F401
    _cut,
    _linkage,
    _order_key,
    _scorer,
    _silhouette,
    _txt,
    best_cut,
    check_method,
    cluster,
    group_key,
    group_of,
    run_changes,
)
from .drawing import (  # noqa: F401
    DRAW_THIN,
    _quantile,
    draw_shape,
    mean_shape,
    thin_shape,
)
from .cluster_map import (  # noqa: F401
    _dist,
    _top2,
    _xy,
    cluster_map,
    project,
    project_features,
)
from .evaluation import (  # noqa: F401
    cluster_category,
    cluster_quality,
    evaluate,
    partition_agreement,
)
from .walkthrough import (  # noqa: F401
    WALK_CLUSTERS,
    WALK_SAMPLE,
    _rl,
    _walk_cut,
    walk_sample,
    walkthrough,
)
