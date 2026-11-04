# ============================================================
# PPE_LOGIC
# ============================================================
#
# Turns raw YOLO boxes into:
#   1) flat detections (for drawing + the old-style detection list)
#   2) PER-WORKER compliance (which is what "PPE compliance %"
#      and "which worker is missing what" actually require)
#
# WHY PER-WORKER ASSOCIATION:
# The original version only produced a flat list of boxes and
# estimated compliance as 1 - (violation_count / person_count)
# across the whole frame. That over/under-counts whenever a
# frame has more than one worker, and it can't tell you WHICH
# worker is out of compliance. This version assigns each PPE
# box to the nearest person box, so compliance is computed per
# worker, matching what the brief actually asks for ("identify
# whether workers are following required PPE guidelines").
#
# THE "no_vest" GAP (and the extra-gear items below share it):
# The dataset (data.yaml) has a "vest" class but was never
# trained on a "no_vest" negative class - the model can only
# ever say "I see a vest", never "this person's vest is
# missing". Every other required item (helmet, gloves, boots)
# DOES have an explicit no_X class, so those violations come
# straight from the model. For vest specifically, we infer the
# violation: if a person is detected and no vest box is
# associated with them, we report it as a vest violation with
# source="inferred" (vs source="model" for the rest), so the
# distinction stays visible in the API instead of being quietly
# papered over. config.PRESENCE_ONLY_ITEMS generalizes this same
# handling to the "other industry" gear items (mask, ear
# protection, face shield) - the extra-gear model that detects
# them (see relabel_extra_gear_boxes below) has exactly the same
# presence-only limitation, for the same reason: no off-the-shelf
# model we could find trains an explicit "not wearing a mask"
# class either.
# ============================================================

import torch

from config import (
    PPE_COMPLIANT_CLASSES,
    PPE_EXPLICIT_VIOLATION_CLASSES,
    PPE_NEUTRAL_CLASSES,
    REQUIRED_PPE_ITEMS,
    PRESENCE_ONLY_ITEMS,
    EXTRA_GEAR_CLASS_MAP,
    INCIDENT_LABELS,
)

# Use the GPU when available, otherwise fall back to CPU
INFERENCE_DEVICE = 0 if torch.cuda.is_available() else "cpu"
# ============================================================
# CLASS NAME LOOKUP
# ============================================================

def class_name_from_model(model, class_id):

    names = model.names

    if isinstance(names, dict):
        class_name = names.get(class_id, str(class_id))
    else:
        class_name = names[class_id]

    return str(class_name).lower().strip()


# ============================================================
# PER-CAMERA WATCHED ITEMS
# ============================================================
#
# A camera can be configured to only care about a subset of PPE
# items (e.g. CAM-01 = helmet only, CAM-02 = vest only). The model
# itself always detects every class it was trained on - filtering
# happens here, downstream, so "watching only helmet" means "don't
# report/draw/alert on anything else", not "the model can't see it".
# ============================================================

def item_for_class(class_name):
    """Map a raw model class name to the PPE item it belongs to,
    e.g. 'no_helmet' -> 'helmet', 'goggles' -> 'goggles'. Returns
    None for classes that aren't a watchable PPE item at all
    (person / none), which should never be filtered out."""

    if class_name in ("person", "none"):
        return None

    item = class_name[3:] if class_name.startswith("no_") else class_name

    # Dataset quirk: the negative class is "no_goggle" (singular)
    # but the positive/compliant class is "goggles" (plural).
    if item == "goggle":
        item = "goggles"

    return item


def filter_boxes_by_watched_items(boxes, watched_items):
    """Keep 'person'/'none' boxes always, plus PPE item boxes whose
    item is in watched_items. watched_items=None means "no camera-
    level filter configured" - keep everything (safe default)."""

    if watched_items is None:
        return boxes

    watched = set(watched_items)

    return [
        box for box in boxes
        if item_for_class(box["class_name"]) is None
        or item_for_class(box["class_name"]) in watched
    ]


def filter_fire_boxes_by_watched_hazards(boxes, watched_hazards):
    """Same idea as filter_boxes_by_watched_items, but for the fire/
    smoke model - simpler, since a fire-model class name ('fire' /
    'smoke') already IS the hazard name with no 'no_' prefix to
    strip. watched_hazards=None means no camera-level filter
    configured - keep everything."""

    if watched_hazards is None:
        return boxes

    watched = set(watched_hazards)

    return [box for box in boxes if box["class_name"] in watched]


def filter_boxes_by_min_area(boxes, frame_w, frame_h, min_area_ratio):
    """Drops boxes smaller than min_area_ratio of the frame area - a
    weak, opt-in secondary filter (config.FIRE_MIN_BOX_AREA_RATIO,
    off by default) for sites where lighter/cigarette-sized false
    positives still get through after temporal confirmation alone.
    See that config value's comment for why it's off by default: a
    genuine small/early/distant fire is also small in-frame, so this
    is a blunt instrument."""

    if min_area_ratio <= 0:
        return boxes

    frame_area = frame_w * frame_h

    if frame_area <= 0:
        return boxes

    out = []

    for box in boxes:

        x1, y1, x2, y2 = box["bbox"]
        box_area = max(0, x2 - x1) * max(0, y2 - y1)

        if (box_area / frame_area) >= min_area_ratio:
            out.append(box)

    return out


def update_fire_confirmation(counts, boxes, confirm_frames):
    """Tracks, per hazard class ('fire' / 'smoke'), how many
    consecutive PROCESSED frames in a row that class has just been
    detected in. A class is only "confirmed" (promoted to an actual
    alert) once its streak reaches confirm_frames; a single miss
    resets it straight back to zero - see config.FIRE_CONFIRM_FRAMES
    for the reasoning (filtering a lighter flick / one-frame false
    positive without ever delaying a genuinely sustained fire).

    counts: {class_name: int}, the streak counts from the PREVIOUS
    cycle (this function doesn't mutate it - it returns a fresh dict,
    so callers just reassign, e.g.
    self._fire_confirm_counts, confirmed = update_fire_confirmation(...)).
    boxes: this cycle's raw (already watched-hazard-filtered) fire/
    smoke boxes.
    confirm_frames: config.FIRE_CONFIRM_FRAMES.

    Returns (new_counts, confirmed_boxes) - confirmed_boxes is the
    subset of `boxes` whose class's streak has now reached
    confirm_frames (i.e. what should actually go on to become a
    violation / trigger the siren-log-SMS pipeline)."""

    seen_classes = {b["class_name"] for b in boxes}
    new_counts = dict(counts)

    for class_name in seen_classes:
        new_counts[class_name] = min(
            new_counts.get(class_name, 0) + 1, confirm_frames
        )

    for class_name in list(new_counts.keys()):
        if class_name not in seen_classes:
            new_counts[class_name] = 0

    confirmed_boxes = [
        b for b in boxes
        if new_counts.get(b["class_name"], 0) >= confirm_frames
    ]

    return new_counts, confirmed_boxes


def relabel_extra_gear_boxes(boxes):
    """Boxes from the extra-gear ("other industry") model, raw. That
    model was trained on 17 classes (SH17 dataset) and we only care
    about 3 of them - config.EXTRA_GEAR_CLASS_MAP says which raw
    class name maps to which watchable item name. Everything else it
    detects (tools, glasses, regular shoes, medical/chemical suits,
    its own 'person' class, etc.) is dropped here.

    Boxes that survive are renamed to the item name the rest of the
    pipeline expects (e.g. 'face-mask' -> 'mask'), so they can be
    merged straight into the main PPE box list and flow through
    build_worker_compliance / flatten_ppe_detections exactly like a
    'vest' box: no explicit negative class, so a person with no
    matching box is still reported as a violation, the same
    "inferred" way vest already is (see PRESENCE_ONLY_ITEMS)."""

    out = []

    for box in boxes:

        item = EXTRA_GEAR_CLASS_MAP.get(box["class_name"])

        if item is None:
            continue

        out.append({**box, "class_name": item})

    return out


# ============================================================
# RUN A YOLO MODEL, RETURN FLAT BOXES
# ============================================================

def run_model(model, frame, confidence, inference_size):

    boxes_out = []

    results = model.predict(
        source=frame,
        conf=confidence,
        imgsz=inference_size,
       device=INFERENCE_DEVICE,
        verbose=False,
    )

    for result in results:

        if result.boxes is None:
            continue

        boxes = result.boxes

        for i in range(len(boxes)):

            xyxy = boxes.xyxy[i].cpu().numpy().astype(int)
            conf = float(boxes.conf[i].cpu().item())
            class_id = int(boxes.cls[i].cpu().item())
            class_name = class_name_from_model(model, class_id)

            x1, y1, x2, y2 = [int(v) for v in xyxy]

            boxes_out.append({
                "class_id": class_id,
                "class_name": class_name,
                "confidence": round(conf, 3),
                "bbox": [x1, y1, x2, y2],
            })

    return boxes_out


# ============================================================
# GEOMETRY HELPERS
# ============================================================

def box_center(bbox):

    x1, y1, x2, y2 = bbox

    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def pad_box(bbox, frame_w, frame_h, pad_ratio=0.25):
    """
    Pad a person box outward so PPE items that stick out of it
    (a helmet above the head, boots below a tight crop) still
    count as belonging to that person.
    """

    x1, y1, x2, y2 = bbox

    w = x2 - x1
    h = y2 - y1

    pad_x = w * pad_ratio
    pad_y = h * pad_ratio

    return [
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(frame_w, x2 + pad_x),
        min(frame_h, y2 + pad_y),
    ]


def point_in_box(point, bbox):

    px, py = point
    x1, y1, x2, y2 = bbox

    return x1 <= px <= x2 and y1 <= py <= y2


# ============================================================
# INCIDENT LABEL
# ============================================================

def incident_label(class_name):

    return INCIDENT_LABELS.get(
        class_name,
        class_name.replace("_", " ").title(),
    )


# ============================================================
# BUILD PER-WORKER PPE COMPLIANCE
# ============================================================

def build_worker_compliance(ppe_boxes, frame_w, frame_h, watched_items=None):
    """
    ppe_boxes: flat list from run_model() for the PPE model.
    watched_items: which PPE items THIS camera checks for. None
    defaults to REQUIRED_PPE_ITEMS (helmet/vest/gloves/boots),
    matching the original all-items behaviour. An empty list/set is
    a deliberate, distinct state - "this camera watches no PPE item"
    - and is honoured as such, not silently upgraded back to the
    default. Items not in this set are left out of `items`/
    `violations` entirely rather than reported as compliant or
    unclear.

    Returns a list of workers:
      {
        "worker_id": int,
        "bbox": [...],
        "confidence": float,        # person detection confidence
        "items": {
            "helmet": "compliant" | "violation" | "unclear",
            "vest":   "compliant" | "violation" | "unclear",
            "gloves": "compliant" | "violation" | "unclear",
            "boots":  "compliant" | "violation" | "unclear",
        },
        "violations": [
            {"item": "vest", "source": "inferred" | "model",
             "confidence": float}
        ],
        "compliant": bool,   # True only if every required item is compliant
      }
    """

    items_to_check = (
        list(REQUIRED_PPE_ITEMS) if watched_items is None else list(watched_items)
    )

    persons = [b for b in ppe_boxes if b["class_name"] == "person"]

    ppe_items = [
        b for b in ppe_boxes
        if b["class_name"] != "person" and b["class_name"] != "none"
    ]

    workers = []

    for idx, person in enumerate(persons):

        padded = pad_box(person["bbox"], frame_w, frame_h)

        assigned = [
            item for item in ppe_items
            if point_in_box(box_center(item["bbox"]), padded)
        ]

        # Best (highest-confidence) detection per class assigned
        # to this worker.
        best_by_class = {}

        for item in assigned:

            cname = item["class_name"]

            if (
                cname not in best_by_class
                or item["confidence"] > best_by_class[cname]["confidence"]
            ):
                best_by_class[cname] = item

        items_status = {}
        violations = []

        for required in items_to_check:

            # Direct model classes for this item, e.g. helmet / no_helmet.
            # Goggles is a dataset quirk: the negative class is spelled
            # "no_goggle" (singular) while everything else follows
            # "no_<item>" exactly, so it needs its own lookup.
            compliant_hit = best_by_class.get(required)

            if required == "goggles":
                violation_hit = (
                    best_by_class.get("no_goggle")
                    or best_by_class.get("no_goggles")
                )
            else:
                violation_hit = best_by_class.get(f"no_{required}")

            if violation_hit is not None:

                items_status[required] = "violation"

                violations.append({
                    "item": required,
                    "source": "model",
                    "confidence": violation_hit["confidence"],
                })

            elif compliant_hit is not None:

                items_status[required] = "compliant"

            elif required in PRESENCE_ONLY_ITEMS:

                # No explicit "no_<item>" class exists for this one -
                # vest, or one of the extra-gear items (mask, ear
                # protection, face shield) - see module docstring.
                # Infer the violation from absence.
                items_status[required] = "violation"

                violations.append({
                    "item": required,
                    "source": "inferred",
                    "confidence": person["confidence"],
                })

            else:

                # Not visible either way (occluded / out of frame) -
                # don't guess for items that DO have an explicit
                # negative class, to keep false-positive rate low.
                items_status[required] = "unclear"

        workers.append({
            "worker_id": idx,
            "bbox": person["bbox"],
            "confidence": person["confidence"],
            "items": items_status,
            "violations": violations,
            "compliant": len(violations) == 0,
        })

    return workers


# ============================================================
# FIRE / SMOKE CLASSIFICATION
# ============================================================

def classify_fire_boxes(fire_boxes):

    detections = []

    for box in fire_boxes:

        class_name = box["class_name"]

        if class_name == "fire":
            severity = "CRITICAL"
            detection_type = "fire"
        elif class_name == "smoke":
            severity = "HIGH"
            detection_type = "smoke"
        else:
            severity = "UNKNOWN"
            detection_type = "fire"

        detections.append({
            "type": detection_type,
            "class_id": box["class_id"],
            "class_name": class_name,
            "confidence": box["confidence"],
            "bbox": box["bbox"],
            "violation": class_name in ("fire", "smoke"),
            "severity": severity,
            "status": "VIOLATION" if class_name in ("fire", "smoke") else "UNKNOWN",
        })

    return detections


# ============================================================
# FLAT PPE DETECTIONS (for drawing + legacy /detections shape)
# ============================================================

def flatten_ppe_detections(ppe_boxes, workers):

    """
    Rebuilds a flat detection list like the original API shape,
    but now includes inferred vest violations (which have no
    box of their own - the box comes from the associated worker).
    """

    detections = []

    for box in ppe_boxes:

        class_name = box["class_name"]

        if class_name in PPE_EXPLICIT_VIOLATION_CLASSES:
            violation, severity, status = True, "HIGH", "VIOLATION"
        elif class_name in PPE_COMPLIANT_CLASSES:
            violation, severity, status = False, "NONE", "COMPLIANT"
        elif class_name in PPE_NEUTRAL_CLASSES:
            violation, severity, status = False, "NONE", "NEUTRAL"
        else:
            violation, severity, status = False, "UNKNOWN", "UNKNOWN"

        detections.append({
            "type": "ppe",
            "class_id": box["class_id"],
            "class_name": class_name,
            "confidence": box["confidence"],
            "bbox": box["bbox"],
            "violation": violation,
            "severity": severity,
            "status": status,
            "source": "model",
        })

    # Add inferred violations (vest) as synthetic detections so
    # existing consumers of the flat list (drawing, /detections,
    # SMS alerts) see them without special-casing.
    for worker in workers:

        for v in worker["violations"]:

            if v["source"] != "inferred":
                continue

            detections.append({
                "type": "ppe",
                "class_id": -1,
                "class_name": f"no_{v['item']}",
                "confidence": round(v["confidence"], 3),
                "bbox": worker["bbox"],
                "violation": True,
                "severity": "HIGH",
                "status": "VIOLATION",
                "source": "inferred",
            })

    return detections
