"""합성 dump 기록 — 실제 TDF 표본이 없어서(SPEC.md 「한계」) 모양을 손으로 만든다."""
from __future__ import annotations

import math
from typing import Callable


def scan(shape: Callable[[float, float], float], *, cols: int = 30, rows: int = 20,
         gain: float = 1.0, offset: float = 0.0, channels=("A", "B"),
         source: str = "s.tdf::run_log1_IFXAxDATA_ARR_PHYS_SCAN_RAW_DATA_STRUCT.dd") -> dict:
    """raster 스캔 하나. 채널 A 는 shape, B 는 shape 를 뒤집은 것 — 채널마다 모양이 다르게."""
    xs, zs, a, b = [], [], [], []
    for r in range(rows):
        for c in range(cols):
            x, z = c / (cols - 1), r / (rows - 1)
            xs.append(x * 10.0)
            zs.append(z * 4.0 - 2.0)
            v = shape(x, z)
            a.append(offset + gain * v)
            b.append(offset - gain * v * 0.5)
    value = {"A": a, "B": b}
    return {"source": source, "kind": "SCAN_RAW_DATA_STRUCT@v1",
            "value": {k: value[k] for k in channels},
            "xyz": {"x": xs, "y": [0.0] * len(xs), "z": zs}}


def meta(pair: str, scan_enum: str = "FINEHORVERT_SCAN") -> dict:
    return {"source": pair.replace("RAW_DATA", "SPEC"), "kind": "SCAN_SPEC_META@v1", "pair": pair,
            "scan_enum": scan_enum, "chuck_id": "CH1", "sensor_id": 2,
            "scan_queued": "2026-08-11T00:57:20+09:00", "timestamp": "2026-08-11T00:59:03+09:00",
            "results": []}


def peak(cx: float = 0.5, cz: float = 0.5, w: float = 0.08) -> Callable[[float, float], float]:
    return lambda x, z: math.exp(-((x - cx) ** 2 + (z - cz) ** 2) / (2 * w * w))


def two_peaks(w: float = 0.07) -> Callable[[float, float], float]:
    p, q = peak(0.25, 0.5, w), peak(0.75, 0.5, w)
    return lambda x, z: p(x, z) + q(x, z)


def edge_drop() -> Callable[[float, float], float]:
    return lambda x, z: 1.0 if x < 0.75 else 1.0 - (x - 0.75) * 3.5


def ramp() -> Callable[[float, float], float]:
    return lambda x, z: x + 0.3 * z
