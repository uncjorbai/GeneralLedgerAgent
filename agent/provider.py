"""Read-only access to the GL data the investigator inspects.

This is the Phase-2 analog of Phase-1's PURE/IMPURE split (see agent/audit.py):
the investigation *logic* must be testable on a laptop with no cluster, so all
data access goes through this thin provider. Two implementations, one interface:

  * LocalGLProvider  — reads parquet/CSV from a local ``data_root`` (a committed
                       fixture, or a local export of the Generator's Bronze).
                       This is what every unit test and the offline dev harness
                       use. No Spark, no network.
  * (Spark provider) — the live Databricks reader over ``fin_close.bronze.*``.
                       DEFERRED to the cluster session, exactly like
                       audit.write_delta(); stubbed below so it is never mistaken
                       for verified.

GUARDRAIL #4 (answer-key isolation) is enforced *here*, at the only door to the
data: the provider resolves the GL / COA / dimension tables and NOTHING else. Any
attempt to reach an answer-key artifact (``run_manifest.json`` / the ``_qa``
volume) raises AnswerKeyAccessError. The scorer reads the answer key; the
investigator never can, because the path it would use does not exist on this
surface.
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Protocol

import pandas as pd

CLEAN = "clean"  # the baseline scenario name (Generator convention)
_ANSWER_KEY_MARKERS = ("run_manifest", "_qa")  # never resolvable through a provider


class AnswerKeyAccessError(RuntimeError):
    """Raised if anything tries to read the answer key through the data surface.

    Defense-in-depth for guardrail #4. The investigator is *scored* against the
    answer key; it must never see it during investigation.
    """


class ProviderError(RuntimeError):
    """A requested table/scenario could not be resolved."""


class GLProvider(Protocol):
    """The read-only surface the tool layer is built on. Deliberately tiny."""

    def failing_table(self) -> pd.DataFrame: ...
    def clean_baseline(self) -> pd.DataFrame: ...
    def chart_of_accounts(self) -> pd.DataFrame: ...
    def departments(self) -> pd.DataFrame: ...


def _guard(name: str) -> None:
    lowered = name.lower()
    if any(marker in lowered for marker in _ANSWER_KEY_MARKERS):
        raise AnswerKeyAccessError(
            f"Refusing to resolve '{name}': the answer key (run_manifest / _qa) is "
            "off-limits to the investigator (guardrail #4). It belongs to the scorer."
        )


class LocalGLProvider:
    """Read GL/COA/dimension data from a local ``data_root``. Offline, testable.

    Expected layout under ``data_root`` (mirrors the Generator's ``out/``):

        {data_root}/{scenario}/gl_journal_lines/*.parquet   # the failing table
        {data_root}/clean/gl_journal_lines/*.parquet        # the clean baseline
        {data_root}/chart_of_accounts.csv
        {data_root}/departments.csv

    Frames are read once and cached, so repeated tool calls in one investigation
    do not re-hit disk. The provider is read-only: it exposes no write method.
    """

    def __init__(self, data_root: str | Path, scenario: str):
        _guard(scenario)
        self.data_root = Path(data_root)
        self.scenario = scenario
        self._cache: dict[str, pd.DataFrame] = {}

    # --- the GLProvider surface -------------------------------------------
    def failing_table(self) -> pd.DataFrame:
        return self._gl(self.scenario)

    def clean_baseline(self) -> pd.DataFrame:
        return self._gl(CLEAN)

    def chart_of_accounts(self) -> pd.DataFrame:
        return self._csv("chart_of_accounts.csv")

    def departments(self) -> pd.DataFrame:
        return self._csv("departments.csv")

    # --- internals --------------------------------------------------------
    def _gl(self, scenario: str) -> pd.DataFrame:
        _guard(scenario)
        key = f"gl:{scenario}"
        if key not in self._cache:
            pattern = str(self.data_root / scenario / "gl_journal_lines" / "**" / "*.parquet")
            files = sorted(glob.glob(pattern, recursive=True))
            if not files:
                raise ProviderError(
                    f"No GL parquet under {self.data_root / scenario / 'gl_journal_lines'} "
                    f"(pattern: {pattern})."
                )
            self._cache[key] = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
        return self._cache[key].copy()

    def _csv(self, filename: str) -> pd.DataFrame:
        _guard(filename)
        key = f"csv:{filename}"
        if key not in self._cache:
            path = self.data_root / filename
            if not path.exists():
                raise ProviderError(f"Reference file not found: {path}")
            self._cache[key] = pd.read_csv(path)
        return self._cache[key].copy()


def spark_provider(*args, **kwargs):
    """LIVE path — a GLProvider backed by ``fin_close.bronze.*`` Spark tables.

    DEFERRED to the Databricks session (cluster-only), the same discipline as
    audit.write_delta(). Intended shape: read ``fin_close.bronze.gl_journal_lines``
    (baseline) and ``...__{scenario}`` (failing) via ``spark.table(...)``, plus
    ``chart_of_accounts`` / the departments dimension, returning pandas frames so
    the tool layer above is identical online and offline. Left as an explicit stub
    so it is never shipped as if it worked.
    """
    raise NotImplementedError(
        "spark_provider is the cluster-session task. Use LocalGLProvider(data_root, "
        "scenario) for offline investigation and tests."
    )
