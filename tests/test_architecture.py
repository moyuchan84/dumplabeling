"""Architecture guardrails — dependency direction points downward only (CLAUDE.md area map).

    processing  ←  data  ←  application        connectors  ←  application

`processing` is pure (stdlib + itself), `connectors` knows nothing of the other areas, `data` reaches only
`processing` and `timebase`, and `application` is the one place that composes them. The test fails the
moment an edit smuggles an upward import in — the structural boundary made executable (same idea as
ftpmodule's tests/test_architecture.py).
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

PKG = "scanlabel"
ROOT = pathlib.Path(__file__).resolve().parent.parent / PKG
AREAS = ("processing", "connectors", "data", "application")


def _imports(pyfile: pathlib.Path) -> set[str]:
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    mods: set[str] = set()
    pkg_parts = pyfile.relative_to(ROOT.parent).with_suffix("").parts      # ('scanlabel', 'data', 'x')
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                                                 # relative → absolute
                base = pkg_parts[: len(pkg_parts) - node.level]
                mod = ".".join(base) + ("." + node.module if node.module else "")
                mods.add(mod)
                # `from .. import timebase` names a module, not just a package
                mods.update(f"{mod}.{a.name}" for a in node.names)
            elif node.module:
                mods.add(node.module)
    return mods


def _files(area: str) -> list[pathlib.Path]:
    return [f for f in (ROOT / area).rglob("*.py")]


def _hits(area: str, allowed) -> list[str]:
    out = []
    for f in _files(area):
        for m in sorted(_imports(f)):
            if m.split(".")[0] in sys.stdlib_module_names:
                continue
            if not allowed(m):
                out.append(f"{f.relative_to(ROOT.parent)} -> {m}")
    return out


def _under(m: str, *prefixes: str) -> bool:
    return any(m == p or m.startswith(p + ".") for p in prefixes)


def test_every_module_lives_in_an_area():
    stray = sorted(p.name for p in ROOT.glob("*.py") if p.name not in ("__init__.py", "timebase.py"))
    assert not stray, f"modules outside an area (only __init__ and timebase may sit at the top): {stray}"
    for area in AREAS:
        assert (ROOT / area / "__init__.py").exists(), area


def test_processing_stays_pure():
    """processing may import ONLY stdlib and itself — no DB, no HTTP, no clock, no sibling area.

    An allowlist, not a denylist: a denylist of area names would silently admit a *new* module
    (e.g. `scanlabel.timebase`, whose clock read would make a score depend on when it was computed).
    """
    bad = _hits("processing", lambda m: _under(m, f"{PKG}.processing") or m == PKG)
    assert not bad, f"processing imported outside its allowlist: {bad}"


def test_connectors_know_nothing_of_the_other_areas():
    bad = _hits("connectors", lambda m: not _under(m, *(f"{PKG}.{a}" for a in AREAS if a != "connectors")))
    assert not bad, f"connectors imported another area: {bad}"


def test_data_reaches_only_processing_and_timebase():
    bad = _hits("data", lambda m: m == PKG or not m.startswith(PKG)
                or _under(m, f"{PKG}.data", f"{PKG}.processing", f"{PKG}.timebase"))
    assert not bad, f"data imported outside {{processing, timebase}}: {bad}"


def test_the_thin_adapters_do_not_call_each_other():
    """api.py and jobs.py both call usecases.py; neither imports the other (api once imported jobs)."""
    for me, other in (("api", "jobs"), ("jobs", "api")):
        mods = _imports(ROOT / "application" / f"{me}.py")
        assert f"{PKG}.application.{other}" not in mods, f"application/{me}.py imports application/{other}.py"


def test_wall_clock_reads_live_only_in_timebase():
    """SPEC 'Times people read are stored as KST' — the one place the wall clock is read is timebase.py.

    `time.monotonic()` (an interval, not a date) is allowed: connectors/fleet.py ages its cache with it.
    """
    pattern = re.compile(r"datetime\.now|utcnow|time\.time\(\)")
    offenders = [str(f.relative_to(ROOT.parent)) for f in ROOT.rglob("*.py")
                 if f.name != "timebase.py" and pattern.search(f.read_text(encoding="utf-8"))]
    assert not offenders, f"wall-clock reads outside timebase.py: {offenders}"


def test_migrations_sit_next_to_their_runner():
    """The ledger checksums file bytes, so moving a file between folders is free — but runner and SQL stay
    together (`data/migrate.py` finds `data/migrations/` by its own location)."""
    assert (ROOT / "data" / "migrate.py").exists() and (ROOT / "data" / "migrations").is_dir()
    assert not (ROOT / "migrations").exists()


@pytest.mark.parametrize("area", AREAS)
def test_every_area_has_its_spec(area):
    assert (ROOT / area / "__spec__.md").exists(), f"{area}/__spec__.md is the area's reference doc"
