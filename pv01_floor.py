"""M002 floor for the sequence trigger, with PV01 counted as floor stock.

Same order the walk already uses: RM01 + PS01 + PV01, then COGI, then the
quantity earlier sequences already took, then open STO. Whatever is still
short is the W004 RM01 trigger. PV01 is never a pick source.

PSS rounding is the same rule as ``picklist_engine.pick_quantity``.
"""
import math
from dataclasses import dataclass
from typing import Tuple

EPS = 1e-9


def _ceil(x: float) -> int:
    return int(math.ceil(x - EPS)) if x > EPS else 0


def opening_floor(rm01: float, ps01: float, pv01: float, cogi: float) -> float:
    """max(0, RM01 + PS01 + PV01 − COGI). PV01 is included only here."""
    return max(0.0, float(rm01) + float(ps01) + float(pv01) - float(cogi))


def pss_round(trigger_sum: float, pss_qty: int) -> Tuple[int, int]:
    """(pick qty, box count): ceil(trigger / PSS) × PSS, or (0, 0) when nothing triggered."""
    if trigger_sum <= EPS:
        return 0, 0
    if pss_qty <= 0:
        qty = _ceil(trigger_sum)
        return qty, qty
    boxes = _ceil(trigger_sum / pss_qty)
    return boxes * pss_qty, boxes


@dataclass(frozen=True)
class FloorStoW004Take:
    floor_taken: float
    sto_taken: float
    w004_taken: float
    shortfall: float
    floor_after: float
    sto_after: float
    w004_after: float


def take_floor_sto_w004(
    request: float, floor: float, open_sto: float, w004: float
) -> FloorStoW004Take:
    """Spend floor, then open STO, then W004 RM01. Trigger is the W004 take.

    A zero W004 balance leaves the trigger at 0 and the unmet request as
    shortfall. That shortfall is not taken from PV01.
    """
    rest = float(request)
    remaining = [float(floor), float(open_sto), float(w004)]
    taken = []
    for i in range(3):
        piece = min(rest, remaining[i])
        remaining[i] -= piece
        rest -= piece
        taken.append(piece)
    shortfall = rest if rest > EPS else 0.0
    return FloorStoW004Take(
        floor_taken=taken[0],
        sto_taken=taken[1],
        w004_taken=taken[2],
        shortfall=shortfall,
        floor_after=remaining[0],
        sto_after=remaining[1],
        w004_after=remaining[2],
    )


@dataclass(frozen=True)
class FloorTrigger:
    floor_opening: float
    floor_remaining: float
    floor_taken: float
    sto_taken: float
    trigger: float
    shortfall: float
    need_pick: int
    pss_count: int


def decide_trigger(
    request: float,
    rm01: float = 0.0,
    ps01: float = 0.0,
    pv01: float = 0.0,
    cogi: float = 0.0,
    prior_sequence_drain: float = 0.0,
    open_sto: float = 0.0,
    w004: float = 0.0,
    pss_qty: int = 1,
) -> FloorTrigger:
    """One sequence: floor after COGI and earlier sequences, then STO, then W004, then PSS."""
    floor_opening = opening_floor(rm01, ps01, pv01, cogi)
    floor_remaining = max(0.0, floor_opening - float(prior_sequence_drain))
    step = take_floor_sto_w004(request, floor_remaining, open_sto, w004)
    need_pick, pss_count = pss_round(step.w004_taken, pss_qty)
    return FloorTrigger(
        floor_opening=floor_opening,
        floor_remaining=floor_remaining,
        floor_taken=step.floor_taken,
        sto_taken=step.sto_taken,
        trigger=step.w004_taken,
        shortfall=step.shortfall,
        need_pick=need_pick,
        pss_count=pss_count,
    )
