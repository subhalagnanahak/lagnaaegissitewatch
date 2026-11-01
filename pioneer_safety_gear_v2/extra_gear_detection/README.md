# Extra-gear ("other industry") PPE model

Detects three items the construction-PPE model was never trained on:
**mask/respirator**, **ear protection (earmuffs)**, and **face shield**.
This is a completely separate YOLO model, not a config change to the
main PPE model - see `backend/config.py`'s `EXTRA_GEAR_MODEL_PATH`
comment for why.

## Where it came from

`weights/sh17_yolov8n.pt` is the nano checkpoint from the
[SH17 dataset](https://github.com/ahmadmughees/SH17dataset)
(Ahmad, Mughees et al.), an academic PPE dataset covering 17 classes
across construction, manufacturing, and medical/lab settings - built
specifically to go beyond a single industry's gear. Downloaded as-is
from that repository's `v1` release; not retrained or fine-tuned here.

- **License: CC BY-NC-SA 4.0** (non-commercial, share-alike) - fine
  for this hackathon submission, but *not* redistributable commercially
  as-is. If this project is ever taken beyond a hackathon demo, either
  get a commercial license from the dataset authors or retrain on
  data you have full rights to.
- Dataset paper / details: see the repo above and its Kaggle mirror
  (`mugheesahmad/sh17-dataset-for-ppe-detection`).

## Classes we actually use

The model detects all 17 SH17 classes; `backend/config.py`'s
`EXTRA_GEAR_CLASS_MAP` only picks out the 3 we expose in the
dashboard:

| Raw model class | Exposed as        |
|------------------|--------------------|
| `face-mask`      | `mask`             |
| `ear-mufs`       | `ear_protection`   |
| `face-guard`     | `face_shield`      |

Everything else it can detect (tools, glasses, regular shoes,
medical-suit, safety-suit, its own `person`/`head`/`hands`/`foot`
classes, etc.) is intentionally ignored - `ppe_logic.relabel_extra_gear_boxes`
drops any box that isn't one of the three above.

## Known gap: safety harness

**Safety harness detection was requested but isn't included here.**
We looked for a small, freely-downloadable, off-the-shelf YOLO
checkpoint that detects a "harness"/"working at height" class the
same way SH17 covers mask/ear-protection/face-shield, and didn't find
one that met the same bar (open weights, permissive enough license,
compatible architecture). Two honest paths forward if harness
detection is needed for real:

1. **Train one.** A handful of Roboflow Universe projects have
   harness-labeled datasets (search "safety harness" there); training
   a nano YOLO model on one would take an afternoon on a decent GPU,
   longer on CPU (see the main README's note on how slow this
   project's own training runs were).
2. **Keep looking** for a hosted/pretrained model as more open PPE
   datasets get published - this space is moving fast.

This is called out here rather than silently shipped as "supported,"
matching this project's existing "Design notes / known limitations"
section in the main README (e.g. the `no_vest` gap).

## Same "presence-only" limitation as vest

Like the construction-PPE model's `vest` class, SH17 only ever learned
to say "I see a mask" - not "this person isn't wearing one." There's
no `no_mask`/`no_ear_protection`/`no_face_shield` class to detect
directly. `backend/ppe_logic.py` handles this exactly the way it
already handles vest: if a watched item has no box associated with a
detected person, it's reported as a violation with `source: "inferred"`
rather than `source: "model"` - see `config.PRESENCE_ONLY_ITEMS`.

## Off by default, per camera

None of these three items are in `REQUIRED_PPE_ITEMS` - a camera only
checks for one if an operator explicitly turns it on via the
dashboard's "Other Industry Gear" pills (or `PUT
/cameras/{id}/watched-items`). A camera that never opts in never even
runs this model on its frames (see `cameras.py`'s `_loop()`), so it
costs nothing on a site that's construction-only.
