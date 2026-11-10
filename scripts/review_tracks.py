"""Confirmed manual tracks propose nearby AI anchors and unconfirmed gap boxes."""
from uuid import uuid4

from review_io import review_box, original_box, box_iou, model_review_row, box_identity

# Pending boxes generated for a track: by the lookahead (between two matches, or copied
# after the last) or by SAM's video tracker.
INTERPOLATED, COPIED, VIDEO = 'sam_pending', 'sam_copy', 'sam_video'
GENERATED = (INTERPOLATED, COPIED, VIDEO)


def create_track(rows, source):
    """Start a track from a saved box: a manual annotation or an AI detection.

    ``source`` is the row itself, or a manual annotation's id.
    """
    if not isinstance(source, dict):
        source = next((r for r in rows if r.get('annotation_id') == source), None)
    elif not any(r is source for r in rows):
        source = None
    if source is None or source.get('manual_track_id') or source.get('review_status') == 'rejected':
        raise ValueError('Select a saved, untracked annotation.')
    review_box(source)
    start = dict(source, manual_track_id=str(uuid4()), track_keyframe='')
    return [start if r is source else r for r in rows], start['manual_track_id']


def stop_track(rows, track_id, frame_number):
    """Retain rejection markers, preventing earlier confirmations restarting a track.

    Keep row order and identities stable so deletion undo can restore every change.
    Existing saved tracks are readable; stopping one also removes its later boxes.
    """
    return [dict(r, review_status='rejected', reviewed='yes')
            if r.get('manual_track_id') == track_id and int(r['frame_number']) >= frame_number
            else r for r in rows]


def _same_species(a, b):
    return a['species'].strip().casefold() == b['species'].strip().casefold()


def _frame_candidates(rows, number, detections, fields):
    candidates = [r for r in rows if int(r['frame_number']) == number]
    represented = {box_identity(original_box(r)) for r in candidates
                   if r.get('annotation_source') != 'manual'}
    for detection in detections:
        key = box_identity(detection['bbox'])
        if key not in represented:
            candidates.append(model_review_row(detection, number, fields))
            represented.add(key)
    return candidates


def _match_anchor(candidates, source, box, distance):
    """Prefer AI over untracked manual boxes, within a bounded overlap gate."""
    eligible = []
    threshold = max(.2, .5 - .15 * (distance - 1))
    for index, candidate in enumerate(candidates):
        if (candidate.get('review_status') == 'rejected' or candidate.get('manual_track_id')
                or not _same_species(candidate, source)):
            continue
        overlap = box_iou(box, review_box(candidate))
        if overlap < threshold:
            continue
        model = candidate.get('annotation_source') != 'manual'
        same_id = bool(model and source.get('track_id') not in ('', None)
                       and str(candidate.get('track_id')) == str(source['track_id']))
        eligible.append(((model, same_id, overlap, -index), candidate))
    if not eligible:
        occupied = any(candidate.get('manual_track_id')
                       and candidate.get('review_status') != 'rejected'
                       and _same_species(candidate, source)
                       and box_iou(box, review_box(candidate)) >= threshold for candidate in candidates)
        return None, occupied
    eligible.sort(key=lambda item: item[0], reverse=True)
    # Close competing AI matches are unsafe identity assignments; don't fill over them.
    if (len(eligible) > 1 and eligible[0][0][0] and
            eligible[0][0][:2] == eligible[1][0][:2] and
            eligible[0][0][2] - eligible[1][0][2] < .05):
        return None, True
    return eligible[0][1], False


def end_track(rows, track_id, frame_number):
    """Finish a fish: keep its box on this frame and drop its later suggestions.

    Unreviewed generated boxes after this frame are removed outright, so frames they
    reopened go back to exactly how they were. Later boxes a person reviewed are
    rejected instead, keeping a record of the decision.
    """
    def later(r):
        return r.get('manual_track_id') == track_id and int(r['frame_number']) > frame_number

    def unreviewed_suggestion(r):
        return (r.get('track_keyframe') in GENERATED and r.get('review_status') == 'pending'
                and r.get('reviewed') != 'yes')
    rows = [r for r in rows if not (later(r) and unreviewed_suggestion(r))]
    # An unreviewed AI detection linked by the lookahead is just unlinked, not rejected.
    rows = [dict(r, manual_track_id='') if later(r) and r.get('annotation_source') != 'manual'
            and r.get('reviewed') != 'yes' else r for r in rows]
    return [dict(r, review_status='rejected', reviewed='yes') if later(r)
            else dict(r, track_keyframe='end')
            if r.get('manual_track_id') == track_id and int(r['frame_number']) == frame_number
            and r.get('review_status') != 'rejected' else r for r in rows]


def advance_tracks(rows, frame_number, total_frames, next_detections=(), *,
                   detections_by_frame=None, lookahead=1, completed_frames=(), track_ids=None):
    """Prefer AI anchors; bridge up to three future frames with pending SAM boxes.

    Only untouched generated copies can be replaced. Human-reviewed annotations,
    model coordinates/confidence and explicit track stops are preserved. Completed
    frames do not stop a track: a new pending box reopens that frame until it is
    reviewed again. ``track_ids`` limits the lookahead to those tracks.
    """
    current = [r for r in rows if int(r['frame_number']) == frame_number and r.get('manual_track_id')
               and (track_ids is None or r['manual_track_id'] in track_ids)]
    rows = list(rows)
    for row in current:
        if row.get('review_status') == 'rejected':
            rows = stop_track(rows, row['manual_track_id'], frame_number)
    end = min(total_frames, frame_number + max(1, min(3, lookahead)))
    detections = dict(detections_by_frame or {})
    detections.setdefault(frame_number + 1, next_detections)
    completed_frames = set(completed_frames)
    for source in current:
        identity = source['manual_track_id']
        if source.get('review_status') != 'confirmed':
            continue
        stops = [int(r['frame_number']) for r in rows if r.get('manual_track_id') == identity
                 and (r.get('review_status') == 'rejected' or r.get('track_keyframe') == 'end')]
        if any(number <= frame_number for number in stops):
            continue
        if any(r.get('manual_track_id') == identity and r.get('track_keyframe') == 'end' for r in rows):
            continue  # a finished fish was reviewed up to its end; don't refill boxes deleted from it
        later_stops = [number for number in stops if number > frame_number]
        track_end = min(end, min(later_stops)-1) if later_stops else end
        generated = {int(r['frame_number']): r for r in rows
                     if r.get('manual_track_id') == identity and frame_number < int(r['frame_number']) <= end
                     and int(r['frame_number']) not in completed_frames
                     and r.get('annotation_source') == 'manual' and r.get('track_keyframe') in GENERATED
                     and r.get('review_status') == 'pending' and r.get('reviewed') != 'yes'}
        generated_ids = {id(old) for old in generated.values()}
        rows = [r for r in rows if id(r) not in generated_ids]
        anchor_number, anchor_box, anchor_source = frame_number, review_box(source), source

        def fill_gaps(until, target_box=None):
            for number in range(anchor_number + 1, until):
                # Never overwrite another saved annotation on this track.
                if any(r.get('manual_track_id') == identity and int(r['frame_number']) == number for r in rows):
                    continue
                fraction = (number-anchor_number)/(until-anchor_number) if target_box is not None else 0
                box = [round(a+(b-a)*fraction, 2) for a,b in zip(anchor_box, target_box or anchor_box)]
                copied = dict(source, frame_number=number,
                              annotation_id=generated[number]['annotation_id'] if number in generated else str(uuid4()),
                              annotation_source='manual', original_bbox='', original_species='',
                              confidence='', track_id='', rejection_source='', review_status='pending',
                              reviewed='', timestamp='', frame_image='',
                              track_keyframe=INTERPOLATED if target_box is not None else COPIED)
                copied.update(zip(('x1','y1','x2','y2'), box))
                rows.append(copied)

        for number in range(frame_number + 1, track_end + 1):
            candidates = _frame_candidates(rows, number, detections.get(number, ()), source.keys())
            existing = next((r for r in candidates if r.get('manual_track_id') == identity), None)
            if existing:
                match, ambiguous = existing, False
            else:
                match, ambiguous = _match_anchor(candidates, anchor_source, anchor_box, number-anchor_number)
            if ambiguous:
                track_end = number - 1
                break
            if match is None:
                # A user's rejected detection is not a hole to refill automatically.
                if any(r.get('review_status') == 'rejected'
                       and _same_species(r, source)
                       and box_iou(anchor_box, review_box(r)) >= .5 for r in candidates):
                    track_end = number - 1
                    break
                continue
            box = review_box(match)
            fill_gaps(number, box)
            if existing is None:
                replacement = dict(match, manual_track_id=identity)
                if any(r is match for r in rows):
                    rows = [replacement if r is match else r for r in rows]
                else:
                    rows.append(replacement)
                match = replacement
            anchor_number, anchor_box, anchor_source = number, box, match
        fill_gaps(track_end + 1)
    return rows
