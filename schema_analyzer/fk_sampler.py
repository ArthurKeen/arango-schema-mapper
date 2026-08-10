"""AQL value-containment sampler — the one genuinely ArangoDB-specific piece of FK inference.

``relational_schema_analyzer`` already abstracts the only DB-touching step of its FK engine
behind ``Sampler = Callable[[str, str, str, str], float | None]``, with five implementations
(PostgreSQL, MySQL, SQL Server, Databricks, CSV). This is the sixth, and the reason the rest
of the engine ports unchanged.

Kept in its own module so ``fk_inference`` stays free of any database import and can be
tested without one.
"""

from __future__ import annotations

import logging
from typing import Any

from .defaults import FK_MAX_PROBES, FK_PROBE_SAMPLE_SIZE

logger = logging.getLogger(__name__)

# One round trip per candidate: sample distinct non-null referencing values, then count how
# many resolve in the referenced collection. LIMIT 1 inside the subquery makes each lookup an
# existence check rather than a scan, and `_key` lookups ride the primary index.
_PROBE_AQL = """
LET vals = (
  FOR d IN @@localCollection
    FILTER d[@localField] != null
    LIMIT @sampleSize
    RETURN DISTINCT {value}
)
LET hits = LENGTH(
  FOR v IN vals
    FILTER LENGTH(FOR t IN @@foreignCollection FILTER t[@foreignField] == v LIMIT 1 RETURN 1) > 0
    RETURN 1
)
RETURN LENGTH(vals) == 0 ? null : hits / LENGTH(vals)
"""

# ARANGO: `_key` and `_id` are always strings, but a reference imported from a relational
# source is typically numeric. Comparing them raw makes every probe return 0.0, which — with
# `overlap_veto_on_zero` — silently vetoes every genuine candidate (measured against Chinook:
# recall dropped from 0.818 to 0.0). Casting the *sampled* value once keeps `t._key == v` an
# indexed primary lookup rather than forcing a scan with a per-document cast.
_IDENTITY_FIELDS = ("_key", "_id")


class ArangoValueSampler:
    """Measures what fraction of a field's sampled values exist in a target collection.

    Returns ``None`` for "could not evaluate" — an empty collection, a missing collection,
    or a failed query — which makes the engine skip the overlap signal rather than treat it
    as a veto. That distinction matters: a zero ratio is evidence against a candidate, while
    no data is no evidence at all.

    Enforces its own probe budget. On exhaustion it returns ``None`` and records the count in
    :attr:`skipped`, so the caller can surface ``foreignKeyStatus = "degraded"`` instead of
    silently reporting fewer relationships (PRD §3.4 transparency rule).
    """

    def __init__(
        self,
        db: Any,
        *,
        sample_size: int = FK_PROBE_SAMPLE_SIZE,
        max_probes: int = FK_MAX_PROBES,
    ) -> None:
        self._db = db
        self._sample_size = sample_size
        self._max_probes = max_probes
        self.probes = 0
        self.skipped = 0

    @property
    def budget_exhausted(self) -> bool:
        return self.probes >= self._max_probes

    def __call__(
        self,
        local_collection: str,
        local_field: str,
        foreign_collection: str,
        foreign_field: str,
    ) -> float | None:
        if self.budget_exhausted:
            self.skipped += 1
            return None

        self.probes += 1
        bind_vars = {
            "@localCollection": local_collection,
            "@foreignCollection": foreign_collection,
            "localField": local_field,
            "foreignField": foreign_field,
            "sampleSize": self._sample_size,
        }
        value_expr = "TO_STRING(d[@localField])" if foreign_field in _IDENTITY_FIELDS else "d[@localField]"
        try:
            cursor = self._db.aql.execute(_PROBE_AQL.format(value=value_expr), bind_vars=bind_vars)
            rows = list(cursor)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "fk containment probe failed for %s.%s -> %s.%s: %s",
                local_collection,
                local_field,
                foreign_collection,
                foreign_field,
                err,
            )
            return None

        if not rows or rows[0] is None:
            return None
        try:
            return max(0.0, min(1.0, float(rows[0])))
        except (TypeError, ValueError):
            return None

    def status(self) -> dict[str, Any]:
        """Probe accounting for ``metadata.foreignKeyStatus``."""
        degraded = self.skipped > 0
        return {
            "status": "degraded" if degraded else "ok",
            "probes": self.probes,
            "maxProbes": self._max_probes,
            "unprobedCandidates": self.skipped,
            "reason": (
                f"probe budget of {self._max_probes} exhausted; {self.skipped} candidate(s) "
                "were not measured and are reported on name evidence alone"
                if degraded
                else None
            ),
        }
