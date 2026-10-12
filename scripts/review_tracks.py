"""Manual tracks advance one frame only when the source frame is confirmed."""
from uuid import uuid4

from review_io import review_box, original_box, box_iou, model_review_row, box_identity


def create_track(rows, annotation_id):
    source = next((r for r in rows if r.get('annotation_id') == annotation_id), None)
    if (source is None or source.get('annotation_source') != 'manual'
            or source.get('manual_track_id') or source.get('review_status') == 'rejected'):
        raise ValueError('Select a saved, untracked manual annotation.')
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


def advance_tracks(rows, frame_number, total_frames, next_detections=()):
    """Copy accepted boxes once; never overwrite an existing next-frame annotation."""
    current = [r for r in rows if int(r['frame_number']) == frame_number and r.get('manual_track_id')]
    for row in current:
        if row.get('review_status') == 'rejected':
            rows = stop_track(rows, row['manual_track_id'], frame_number)
    if frame_number >= total_frames:
        return rows
    rows = list(rows)
    additions = []
    for row in current:
        identity = row['manual_track_id']
        if row.get('review_status') != 'confirmed':
            continue
        if any(r.get('manual_track_id') == identity
               and (int(r['frame_number']) == frame_number+1
                    or (int(r['frame_number']) <= frame_number and r.get('review_status') == 'rejected'))
               for r in rows):
            continue
        box = review_box(row)
        # Include confident detections that have never appeared in the review CSV.
        candidates = [r for r in rows if int(r['frame_number']) == frame_number+1]
        represented = {box_identity(original_box(r)) for r in candidates
                       if r.get('annotation_source') != 'manual'}
        for detection in next_detections:
            if box_identity(detection['bbox']) not in represented:
                candidate = model_review_row(detection, frame_number+1, row.keys())
                candidates.append(candidate)
        matches = [(box_iou(box, review_box(candidate)), i, candidate)
                   for i, candidate in enumerate(candidates)
                   if candidate.get('review_status') != 'rejected'
                   and not candidate.get('manual_track_id')
                   and candidate['species'].strip().casefold() == row['species'].strip().casefold()
                   and box_iou(box, review_box(candidate)) >= 0.5]
        if matches:
            _, _, match = max(matches, key=lambda item: (item[0], -item[1]))
            replacement = dict(match, manual_track_id=identity)
            if any(candidate is match for candidate in rows):
                rows = [replacement if candidate is match else candidate for candidate in rows]
            else:
                rows.append(replacement)
            continue
        # After a model handoff, a missing detection becomes a manual box again.
        additions.append(dict(row, frame_number=frame_number+1, annotation_id=str(uuid4()),
                              annotation_source='manual', original_bbox='', original_species='',
                              confidence='', track_id='', rejection_source='',
                              review_status='pending', reviewed='', timestamp='',
                              frame_image='', track_keyframe=''))
    return rows + additions
