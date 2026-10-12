"""Apply explicit spatial exclusions to saved model detections."""
from review_io import original_box, review_box, model_review_row, box_identity


def exclude_detection_area(rows, area, frame_number, detections_by_frame, fields):
    """Reject model boxes whose centres lie in the area, from this frame onward.

    Manual annotations and active tracks remain under explicit user control.
    Materialized rejection rows keep display, exports and results consistent.
    """
    x1, y1, x2, y2 = area
    def inside(box):
        return x1 <= (box[0]+box[2])/2 <= x2 and y1 <= (box[1]+box[3])/2 <= y2
    lookup = {(int(row['frame_number']), box_identity(original_box(row))): row
              for row in rows if row.get('annotation_source') != 'manual'}
    replacements, additions = {}, []
    for frame, detections in detections_by_frame.items():
        if frame < frame_number:
            continue
        for detection in detections:
            existing = lookup.get((frame, box_identity(detection['bbox'])))
            if existing and (existing.get('manual_track_id') or existing.get('review_status') == 'rejected'):
                continue
            if not inside(review_box(existing) if existing else detection['bbox']):
                continue
            target = existing if existing is not None else model_review_row(detection, frame, fields)
            rejected = dict(target, review_status='rejected', reviewed='yes', rejection_source='area')
            if existing is not None:
                replacements[id(existing)] = rejected
            else:
                additions.append(rejected)
    return [replacements.get(id(row), row) for row in rows] + additions, len(replacements) + len(additions)
