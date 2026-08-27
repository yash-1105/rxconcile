"""Self-consistency across N extraction runs.

Implements the decision recorded in ``docs/DESIGN_DECISIONS.md`` section 2: the
model's own confidence score does not predict whether it can reproduce a field,
so **per-field agreement across N runs replaces it** as the reliability signal.

Two steps:

1. **Align.** Runs may return different item counts in different orders, so
   items are clustered across runs by their transcribed text. A cluster present
   in some runs but not all is recorded as an unstable line.
2. **Resolve.** For each field of each cluster, the modal value wins. Three
   distinct readings mean the model cannot read the field, and the value
   resolves to ``None`` rather than to a plurality of one.

Resolution table for N=3:

===================================  ==============  ===========
observations                         resolved value  agreement
===================================  ==============  ===========
``A, A, A``                          ``A``           1.00
``A, A, B``                          ``A``           0.67
``A, A, None``                       ``A``           0.67
``A, None, None``                    ``None``        0.67
``A, B, C``                          **None**        0.33
===================================  ==============  ===========

``None`` is an observation, not a missing datum: a run that read no drug name is
evidence about legibility, and it votes.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final, TypeVar

from pydantic import BaseModel
from rapidfuzz import fuzz

from rxconcile.normalize.drug_dictionary import normalize_name

logger: Final = logging.getLogger(__name__)

ItemT = TypeVar("ItemT", bound=BaseModel)

#: Similarity above which two runs' lines are considered the same prescribed or
#: billed line. High enough that different drugs never merge, loose enough to
#: absorb whitespace and punctuation drift between runs.
LINE_MATCH_THRESHOLD: Final[float] = 85.0

#: Float fields are compared at this precision, so 500.0 and 500.0000001 agree.
_FLOAT_PRECISION: Final[int] = 6

#: Intersection-over-union above which two runs are considered to have located
#: the same line.
#:
#: Exact equality is meaningless for coordinates -- three runs will never return
#: identical floats -- so agreement is judged by overlap. 0.5 is the conventional
#: detection threshold and is deliberately not loose: at 0.5 two boxes share more
#: area than either holds alone, which for single-line regions on a page means
#: they cannot be pointing at different lines. A looser value would let adjacent
#: lines count as agreement, which is exactly the failure a provenance highlight
#: must not make.
IOU_AGREEMENT_THRESHOLD: Final[float] = 0.5


class FieldResolution(BaseModel):
    """One field's resolved value and how much the runs agreed on it."""

    value: Any = None
    agreement: float | None = None


class ItemCluster(BaseModel):
    """One logical line, as observed across the runs.

    ``observations[i]`` is the item run ``i`` produced for this line, or None if
    that run did not produce it at all.
    """

    observations: list[Any]
    order: int

    @property
    def present(self) -> list[Any]:
        return [item for item in self.observations if item is not None]

    @property
    def present_count(self) -> int:
        return len(self.present)

    @property
    def run_count(self) -> int:
        return len(self.observations)

    @property
    def is_stable(self) -> bool:
        """True when every run produced this line."""
        return self.present_count == self.run_count

    @property
    def canonical_raw_text(self) -> str:
        """Modal raw_text, falling back to the first observation.

        Never resolved to None: raw_text is display evidence, and blanking it
        would remove what a reviewer needs in order to check the line. Its
        agreement ratio still records the disagreement.
        """
        texts = [str(getattr(item, "raw_text", "")) for item in self.present]
        if not texts:
            return ""
        return Counter(texts).most_common(1)[0][0]


def line_key(raw_text: str) -> str:
    """Comparison key for aligning the same line across runs."""
    return normalize_name(raw_text)


def _hashable(value: object) -> object:
    """Make a field value hashable and tolerant of float noise."""
    if isinstance(value, float):
        return round(value, _FLOAT_PRECISION)
    if isinstance(value, list):
        return tuple(_hashable(element) for element in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable(val)) for key, val in value.items()))
    return value


def resolve_values(values: Sequence[object], *, run_count: int) -> FieldResolution:
    """Resolve one field's observations into a value plus an agreement ratio.

    ``run_count`` is the denominator -- the number of runs in which the line
    appeared, so a line seen twice out of three runs is scored against 2.

    Returns agreement None when there is a single observation: one run has no
    agreement to report, and returning 1.0 would overstate the evidence.
    """
    if not values:
        return FieldResolution(value=None, agreement=None)
    if run_count <= 1:
        return FieldResolution(value=values[0], agreement=None)

    counts = Counter(_hashable(value) for value in values)
    top_key, top_count = counts.most_common(1)[0]

    if top_count == 1:
        # Every run read something different: there is no reproducible value.
        return FieldResolution(value=None, agreement=round(1 / len(values), 2))

    original = next(value for value in values if _hashable(value) == top_key)
    return FieldResolution(value=original, agreement=round(top_count / len(values), 2))


def resolve_field(cluster: ItemCluster, attribute: str) -> FieldResolution:
    """Resolve one attribute across every run that produced this line."""
    values = [getattr(item, attribute, None) for item in cluster.present]
    return resolve_values(values, run_count=cluster.present_count)


def align_items(runs: list[list[ItemT]]) -> list[ItemCluster]:
    """Cluster items across runs so the same line lines up.

    The run whose item count is modal seeds the ordering, so a run that dropped
    or invented a line does not dictate the output shape. Remaining runs are
    matched greedily against the seed by text similarity; anything unmatched
    becomes its own cluster and will be flagged as unstable.
    """
    if not runs:
        return []
    run_count = len(runs)
    if run_count == 1:
        return [
            ItemCluster(observations=[item], order=index)
            for index, item in enumerate(runs[0])
        ]

    counts = [len(run) for run in runs]
    modal_count = Counter(counts).most_common(1)[0][0]
    seed_index = counts.index(modal_count)

    clusters: list[ItemCluster] = [
        ItemCluster(observations=[None] * run_count, order=order)
        for order in range(len(runs[seed_index]))
    ]
    keys: list[str] = [line_key(str(getattr(item, "raw_text", ""))) for item in runs[seed_index]]
    for order, item in enumerate(runs[seed_index]):
        clusters[order].observations[seed_index] = item

    for run_index, run in enumerate(runs):
        if run_index == seed_index:
            continue
        taken: set[int] = set()
        for item in run:
            key = line_key(str(getattr(item, "raw_text", "")))
            best_slot: int | None = None
            best_score = LINE_MATCH_THRESHOLD
            for slot, cluster_key in enumerate(keys):
                if slot in taken or clusters[slot].observations[run_index] is not None:
                    continue
                score = float(fuzz.token_set_ratio(key, cluster_key))
                if score >= best_score:
                    best_score, best_slot = score, slot
            if best_slot is None:
                clusters.append(
                    ItemCluster(observations=[None] * run_count, order=len(clusters))
                )
                keys.append(key)
                clusters[-1].observations[run_index] = item
                taken.add(len(clusters) - 1)
            else:
                clusters[best_slot].observations[run_index] = item
                taken.add(best_slot)

    return clusters


def majority_threshold(run_count: int) -> int:
    """Minimum runs a line must appear in to be kept as an item."""
    return (run_count // 2) + 1


def split_clusters(
    clusters: list[ItemCluster], *, run_count: int
) -> tuple[list[ItemCluster], list[str]]:
    """Partition clusters into kept items and the text of unstable lines.

    A line is kept when a majority of runs produced it. Every line that did not
    appear in *all* runs is reported as unstable, whether or not it was kept --
    the engine decides what to do about it.
    """
    threshold = majority_threshold(run_count)
    kept = [cluster for cluster in clusters if cluster.present_count >= threshold]
    unstable = [
        cluster.canonical_raw_text
        for cluster in clusters
        if cluster.present_count < run_count and cluster.canonical_raw_text
    ]
    if unstable:
        logger.warning(
            "item-count instability: %d line(s) present in some runs only", len(unstable)
        )
    return kept, unstable


Box = tuple[float, float, float, float]


def _as_box(value: object) -> Box | None:
    """Coerce a model-supplied box, rejecting anything unusable."""
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not all(0.0 <= v <= 1.0 for v in (x0, y0, x1, y1)):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def iou(left: Box, right: Box) -> float:
    """Intersection over union of two normalised boxes."""
    ax0, ay0, ax1, ay1 = left
    bx0, by0, bx1, by1 = right
    inter_w = min(ax1, bx1) - max(ax0, bx0)
    inter_h = min(ay1, by1) - max(ay0, by0)
    if inter_w <= 0 or inter_h <= 0:
        return 0.0
    intersection = inter_w * inter_h
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - intersection
    return intersection / union if union > 0 else 0.0


def _mean_box(boxes: Sequence[Box]) -> Box:
    count = len(boxes)
    return (
        sum(b[0] for b in boxes) / count,
        sum(b[1] for b in boxes) / count,
        sum(b[2] for b in boxes) / count,
        sum(b[3] for b in boxes) / count,
    )


def resolve_bbox(values: Sequence[object], *, run_count: int) -> FieldResolution:
    """Resolve bounding boxes across runs by overlap rather than equality.

    A box is kept only when a majority of the runs that produced one placed it in
    substantially the same place (IoU >= :data:`IOU_AGREEMENT_THRESHOLD`); the
    mean of that agreeing group is returned. Boxes that scatter resolve to None:
    a location the model cannot reproduce is a guess, and a provenance highlight
    pointing at the wrong line is worse than no highlight.

    Agreement is scored against ``run_count``, so a box found by only one of three
    runs scores 0.33 and is discarded, exactly as a field read three different
    ways is.
    """
    boxes = [box for box in (_as_box(value) for value in values) if box is not None]
    if not boxes:
        return FieldResolution(value=None, agreement=None if run_count <= 1 else 0.0)
    if run_count <= 1:
        return FieldResolution(value=list(boxes[0]), agreement=None)

    best: list[Box] = []
    for candidate in boxes:
        group = [other for other in boxes if iou(candidate, other) >= IOU_AGREEMENT_THRESHOLD]
        if len(group) > len(best):
            best = group

    ratio = round(len(best) / run_count, 2)
    if len(best) < 2:
        # No two runs agreed on where this line is.
        return FieldResolution(value=None, agreement=ratio)
    return FieldResolution(value=list(_mean_box(best)), agreement=ratio)
