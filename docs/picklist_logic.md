# Hourly sequence picklist logic

One walk runs right after each SAP pull (MB52, COGI, STO) or ScaleX plan change. The screen only reads what that walk stored. Code: [`picklist_engine.py`](../picklist_engine.py). Tests: [`test_picklist_engine.py`](../test_picklist_engine.py).

## Backend walk (`run_walk`)

1. Sequences: ScaleX batches with `Status == "PLAN"` (HOLD is never walked), ordered by `ScaleX_Sequence_No`, then line. Both lines draw from the same balance per material.
2. Each sequence is exploded to materials with the app's BOM recursion. A child listed in `OFFICIAL_PSS_MASTER` is a handled unit and is not dissolved. Need = unit qty × batch count.
3. Balance per material, set once:
   - plant = max(0, M002 RM01 + PS01 − COGI)
   - sto = open STO (W004 ➡️ M002)
   - w004 = W004 RM01
   COGI is not used again inside the loop.
4. For each sequence, for each material: take the need from plant, then sto, then w004, never more than the column still holds. Trigger is the w004 part only.
   - COVERED: plant and sto met the need, trigger 0
   - TRIGGERED: the rest came from w004
   - PARTIAL: w004 gave some of the rest and then hit 0
   - RED: need still unmet and w004 was already 0, trigger 0
5. One row per sequence per material is stored in `picklist_walk`, with and without a trigger.

Not in the walk: the 30-piece buffer, `preceding_drain`, `classify_material_decision`, the Experimental Additive mode, and any time-based cutoff.

## Frontend consolidation (`consolidate`)

Same steps with and without a sequence filter. No filter means every sequence in the walk.

1. Per material, take the stored rows for the selected sequences.
2. qty = sum of trigger.
3. Ship 0 if qty is 0, else `ceil(qty / PSS) × PSS`. Boxes = ship / PSS.
4. Colour from the stored statuses, not from a new stock check:
   - RED: every selected row that needed w004 (TRIGGERED, PARTIAL, RED) is RED
   - YELLOW: any selected row is PARTIAL or RED and the trigger sum is above 0
   - none otherwise

`Transfer Status` reuses the existing labels: RED → `W004 Out of Stock`, YELLOW → `Partial Transfer Available`, none with a pick → `Ready for Full Transfer`, trigger 0 → `Sufficient M002 Stock`.

## Worked examples

### a, b, c

Need 30 per sequence, plant 40, sto 0, w004 plenty.

| Seq | Plant taken | W004 taken (trigger) | Status |
|---|---|---|---|
| a | 30 | 0 | COVERED |
| b | 10 | 20 | TRIGGERED |
| c | 0 | 30 | TRIGGERED |

Triggers 0, 20, 30.

### Sequences 1–10

Need 30 per sequence, plant 40, sto 0, w004 200.

- S1: plant covers, trigger 0.
- S2: plant 10, w004 20.
- S3–S8: 30 each from w004, 180 in total. W004 is now 0.
- S9–S10: RED, trigger 0.

Total trigger 200. Selecting only S9–S10 is RED with a pick of 0. Selecting S8–S9 is YELLOW: trigger 30, ship 50 with PSS 50.

### PSS rounding

Trigger 30 per sequence, PSS 50:

| Selected sequences | Trigger sum | Ship | Boxes |
|---|---|---|---|
| 1 | 30 | 50 | 1 |
| 2 | 60 | 100 | 2 |
| 3 | 90 | 100 | 2 |
| 4 | 120 | 150 | 3 |
| 5 | 150 | 150 | 3 |

## Logs

- `picklist_walk_run`: one row per walk (walk run id, snapshot id, batch fingerprint, source, timestamp, sequence count, material count, total trigger).
- `picklist_walk`: every walk row for that walk run id.
- `material_decision_audit` and the audit vault (`audit_vault/runs_json/<id>.json`, `audit_vault/runs_csv/<id>.csv`): `audit_run_id = walk_run_id`.

## Audit rule: one audit per picklist

A picklist is one snapshot id (MB52, COGI, STO) plus one ScaleX plan fingerprint. Code: [`walk_store.py`](../walk_store.py).

- Nothing is logged until MB52, COGI, and STO are all loaded. Uploading them one at a time shows a preview walk but writes no walk log, no audit rows, and no vault file. When the third dump arrives, exactly one JSON and one CSV are written.
- If `picklist_walk_run` already holds the snapshot id plus plan fingerprint, the app reuses that walk_run_id and its stored rows and skips the audit. Page refreshes, new sessions, and other clients with the same data write nothing.
- Sequence and line filters only re-consolidate the stored walk. They never write a file or create a walk_run_id.
- The audit rows describe the full walk: every PLAN sequence on both lines, whatever is selected on screen.
- Replacing any of the three dumps, or a real ScaleX plan change, is a new picklist: one new walk_run_id with one new JSON and CSV. Older files stay.

## Decisions not in the agreed algorithm

- W004 is RM01 only. The earlier engine fell back to the W004 plant total when RM01 had no row; the walk does not. The existing LX02 fallback inside `extract_detailed_stock_maps` still applies.
- The line filter restricts the selected sequences to that line before consolidation. The walk itself always covers both lines.
- `Sequence_Order`, `Starvation_Mins`, and `Priority` come from the earliest selected sequence. `Sequences_Covered` and `Vehicle_SKU` span every selected sequence, as in the earlier engine. Starvation minutes are counted from the first selected sequence on each line.
- Fractional BOM quantities are kept in the walk. `BOM Demand` and `Demand to Fulfil` are rounded up for display.
- Audit codes map to the codes the RCA already reads: Ready and Partial → `ACCEPTED_DEFICIT_FULFILLED`, RED → `REJECTED_W004_STOCKOUT`, trigger 0 → `REJECTED_COVERED_BY_STO` when STO was used, otherwise `REJECTED_SUFFICIENT_M002`.
