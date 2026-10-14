"""벽시계를 읽는 유일한 곳 — 사람이 읽는 시각은 KST ISO 문자열로 저장한다 (fleet 과 같은 규칙)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# zoneinfo 는 Windows 에서 tzdata 패키지가 있어야 Asia/Seoul 을 읽는다. KST 는 서머타임이 없으니
# 고정 오프셋으로 충분하고, 의존성을 하나 덜 진다.
KST = timezone(timedelta(hours=9), "KST")


def now() -> datetime:
    return datetime.now(KST)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")
