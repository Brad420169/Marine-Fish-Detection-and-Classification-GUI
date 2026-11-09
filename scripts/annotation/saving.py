"""How a frame's edits turn into saved review rows. Plain functions, no Qt."""
from uuid import uuid4


def manual_row(session, number, species, box, image, track_id=''):
    """A new human annotation (drawn by hand or with SAM)."""
    row = session.blank_row(
        frame_number=number, timestamp=session.timestamp(number), species=species,
        confidence='', track_id='', original_species='', annotation_source='manual',
        annotation_id=str(uuid4()), review_status='confirmed', reviewed='yes',
        notes='Manually annotated', frame_image=image)
    row.update(zip(('x1', 'y1', 'x2', 'y2'), box))
    if track_id:
        row['manual_track_id'] = track_id
    return row


def model_row(session, number, detection, image):
    """Promote a confident detector box, never flagged before, to an editable review row."""
    row = session.blank_row(
        frame_number=number, timestamp=session.timestamp(number),
        species=detection['species'], original_species=detection['species'],
        confidence=detection['confidence'],
        track_id=detection.get('track_id') if detection.get('track_id') is not None else '',
        annotation_source='model', review_status='pending', reviewed='', frame_image=image)
    row.update(zip(('x1', 'y1', 'x2', 'y2'), [round(float(v), 2) for v in detection['bbox']]))
    return row


def keep_untouched_pending(current, drafts, edited, track=None):
    """For a save that does not complete the frame, leave untouched pending boxes pending.

    A box counts as touched when its species, position or decision changed, or it
    belongs to the fish being tracked (``track``), whose box is accepted.
    """
    return [draft if (draft['species'] != old['species'] or index in edited
                      or draft['review_status'] == 'rejected'
                      or old.get('review_status') != 'pending'
                      or (track and old.get('manual_track_id') == track)) else dict(old)
            for index, (old, draft) in enumerate(zip(current, drafts))]


def mark_reviewed(drafts, complete):
    for row in drafts:
        if complete or row['review_status'] != 'pending':
            row['reviewed'] = 'yes'


def unknown_species(drafts, pending, names):
    return sorted({r['species'] for r in drafts if r['review_status'] == 'confirmed' and r['species'] not in names}
                  | {r['species'] for r in pending if r['species'] not in names})


def merge_frame(rows, current, drafts, additions):
    """All rows with this frame's drafts applied and new boxes appended.

    Crossing out a manual annotation deletes it outright; a model detection keeps
    its row so the refresh knows to drop that detection rather than re-count it.
    """
    updates = {id(old): new for old, new in zip(current, drafts)}
    deleted = {id(old) for old, new in zip(current, drafts)
               if new['review_status'] == 'rejected' and new.get('annotation_source') == 'manual'
               and not new.get('manual_track_id')}
    return [updates.get(id(r), r) for r in rows if id(r) not in deleted] + additions
