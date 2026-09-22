"""Evaluate immutable model predictions against explicitly confirmed review frames."""
from collections import defaultdict, deque
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import tempfile

from review_io import load_review_rows


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@lru_cache(maxsize=8)
def source_fingerprint(path, modified_ns, size):
    """Avoid rereading a whole video's predictions after every frame confirmation."""
    return fingerprint(json.loads(Path(path).read_text(encoding='utf-8')))


def frame_fingerprint(rows, number):
    # CSV round trips turn numbers into strings. Ignore unrelated CSV columns.
    keys = ('frame_number', 'x1', 'y1', 'x2', 'y2', 'species', 'review_status',
            'annotation_source', 'annotation_id')
    return fingerprint([{k: str(r.get(k, '')) for k in keys}
                        for r in rows if int(r['frame_number']) == number])


def read_confirmation(root):
    path = Path(root) / 'review_completion.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def confirm_review_frame(root, rows, number):
    """Record a full-frame review only after its CSV edits have been saved."""
    root = Path(root)
    original_path = root / 'original_detections.json'
    if not original_path.exists():
        return  # Legacy runs cannot support reliable full-frame evaluation.
    stat = original_path.stat()
    source = source_fingerprint(str(original_path.resolve()), stat.st_mtime_ns, stat.st_size)
    data = read_confirmation(root)
    if data.get('source') != source:
        data = {'source': source, 'frames': {}}
    data['frames'][str(number)] = frame_fingerprint(rows, number)
    fd, temporary = tempfile.mkstemp(dir=root, suffix='.json.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / 'review_completion.json')
    finally:
        Path(temporary).unlink(missing_ok=True)


def box_key(box):
    return tuple(round(float(v), 2) for v in box)


def evaluate(original, rows, completion, legacy_frames=(), trained_species=None):
    """Count fish instances per frame; species corrections are FP + FN by class."""
    if trained_species is None:
        trained_species = original.get('class_names')
    trained = None if not trained_species else {name.strip() for name in trained_species}
    trained_detected = trained_correct = trained_missed = 0
    outside_detected = outside_missed = 0
    total = int(original.get('total_frames') or round(original['fps'] * original['duration']))
    by_frame = {int(f['frame_number']): f['detections'] for f in original['frames']}
    grouped = defaultdict(list)
    for row in rows:
        grouped[int(row['frame_number'])].append(row)
    confirmed = set()
    source = fingerprint(original)
    if completion.get('source') == source:
        for key, digest in completion.get('frames', {}).items():
            number = int(key)
            if (1 <= number <= total and digest == frame_fingerprint(grouped[number], number)
                    and all(r['review_status'] in ('confirmed', 'rejected') for r in grouped[number])):
                confirmed.add(number)
    verified_count = len(confirmed)
    legacy = set()
    # Earlier versions saved a rendered image after confirming a frame, but no
    # completion fingerprint. Use that evidence only for provisional scores.
    if not completion or completion.get('source') == source:
        recorded = completion.get('frames', {})
        for number in legacy_frames:
            if (str(number) not in recorded and 1 <= number <= total and grouped[number]
                    and all(r['review_status'] in ('confirmed', 'rejected')
                            and r.get('reviewed') == 'yes' for r in grouped[number])):
                legacy.add(number)
    confirmed.update(legacy)
    counts = dict(correct=0, corrected=0, rejected=0, missed=0)
    species = defaultdict(lambda: dict(tp=0, fp=0, fn=0))
    for number in sorted(confirmed):
        lookup = defaultdict(deque)
        seen_manual = set()
        for row in grouped[number]:
            if row.get('annotation_source') == 'manual':
                if row['review_status'] == 'confirmed':
                    identity = row.get('annotation_id')
                    if not identity or identity in seen_manual:
                        raise ValueError('Manual annotations must have distinct IDs.')
                    seen_manual.add(identity)
                    counts['missed'] += 1
                    actual = row['species'].strip()
                    if not actual:
                        raise ValueError('A confirmed fish has no species name.')
                    if trained is not None:
                        if actual in trained:
                            trained_missed += 1
                            species[actual]['fn'] += 1
                        else:
                            outside_missed += 1
            else:
                lookup[box_key(row[k] for k in ('x1', 'y1', 'x2', 'y2'))].append(row)
        for detection in by_frame.get(number, []):
            predicted = detection['species'].strip()
            matches = lookup.get(box_key(detection['bbox']))
            review = matches.popleft() if matches else None
            if review and review['review_status'] == 'rejected':
                counts['rejected'] += 1
                if trained is not None and predicted in trained:
                    species[predicted]['fp'] += 1
            else:
                actual = review['species'].strip() if review else predicted
                if not actual:
                    raise ValueError('A confirmed fish has no species name.')
                if trained is not None:
                    if actual in trained:
                        trained_detected += 1
                        trained_correct += int(actual == predicted)
                    else:
                        outside_detected += 1
                if actual == predicted:
                    counts['correct'] += 1
                    if trained is not None and predicted in trained:
                        species[predicted]['tp'] += 1
                else:
                    counts['corrected'] += 1
                    if trained is not None and predicted in trained:
                        species[predicted]['fp'] += 1
                    if trained is not None and actual in trained:
                        species[actual]['fn'] += 1
        if any(lookup.values()):
            raise ValueError('Some review decisions do not match the original predictions.')
    correct, corrected, rejected, missed = (counts[k] for k in ('correct', 'corrected', 'rejected', 'missed'))
    return dict(counts=counts, reviewed_frames=len(confirmed), total_frames=total,
                legacy_frames=len(legacy), verified_frames=verified_count,
                complete=total > 0 and verified_count == total,
                class_list_available=trained is not None,
                detection=dict(precision=(correct+corrected)/(correct+corrected+rejected)
                               if correct+corrected+rejected else None,
                               recall=trained_detected/(trained_detected+trained_missed)
                               if trained_detected+trained_missed else None,
                               detected=correct+corrected, predictions=correct+corrected+rejected,
                               trained_detected=trained_detected, trained_missed=trained_missed),
                species_accuracy=trained_correct/trained_detected if trained_detected else None,
                species_correct=trained_correct,
                outside=dict(detected=outside_detected, missed=outside_missed),
                species={name: metrics(**values) for name, values in sorted(species.items())})


def metrics(tp, fp, fn):
    return dict(tp=tp, fp=fp, fn=fn, precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None,
                f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)


def evaluate_run(root, csv_path):
    root = Path(root)
    source = root / 'original_detections.json'
    if not source.exists():
        raise ValueError('This older run has no original predictions. Run inference again to enable evaluation. '
                         'Existing review data is preserved.')
    rows, _ = load_review_rows(csv_path)
    legacy_frames = []
    for image in (root / 'reviewed_frames').glob('frame_*.jpg'):
        suffix = image.stem.removeprefix('frame_')
        if suffix.isdigit() and image.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            legacy_frames.append(int(suffix))
    original = json.loads(source.read_text(encoding='utf-8'))
    classes_path = root / 'species_names.json'
    trained = original.get('class_names')
    if trained is None and classes_path.exists():
        trained = json.loads(classes_path.read_text(encoding='utf-8'))
    if trained is not None:
        if not isinstance(trained, list) or any(not isinstance(name, str) or not name.strip() for name in trained):
            raise ValueError('The saved model class list is invalid.')
        trained = trained or None
    return evaluate(original, rows, read_confirmation(root), legacy_frames, trained)
