"""The app's working review data: one SQLite database per run (review.sqlite).

This is where the annotation page reads and writes boxes and which frames are
complete. It replaces low_confidence_review.csv and review_completion.json, which
older runs are migrated from on first open (the old files are kept, renamed
*.migrated.*). Scientists still get CSV files: Refresh Results writes
track_summary.csv and reviewed_detections.csv from this database.

Rows are plain dicts, as before. The columns the app looks things up by are typed
and checked (frame number, box, species, status, source, ids); the other columns'
values are kept together as a JSON list. Saving writes only the rows that changed, and a frame's completion
is recorded in the same transaction as its boxes.
"""
import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from operator import itemgetter
from pathlib import Path

from review_io import load_review_rows, normalize_review_rows

DB_NAME = 'review.sqlite'
LEGACY_CSV = 'low_confidence_review.csv'
LEGACY_COMPLETION = 'review_completion.json'
BOX = ('x1', 'y1', 'x2', 'y2')
CORE = ('frame_number', *BOX, 'species', 'review_status', 'annotation_source',
        'annotation_id', 'manual_track_id', 'track_keyframe')
GAP = 1 << 16  # spacing between row positions, so inserting rows rarely renumbers
SCHEMA_VERSION = '1'

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boxes (
    position INTEGER PRIMARY KEY,          -- row order
    frame_number INTEGER NOT NULL CHECK (frame_number >= 1),
    x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
    species TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'confirmed', 'rejected')),
    annotation_source TEXT NOT NULL CHECK (annotation_source IN ('model', 'manual')),
    annotation_id TEXT NOT NULL DEFAULT '',
    manual_track_id TEXT NOT NULL DEFAULT '',
    track_keyframe TEXT NOT NULL DEFAULT '',
    extra TEXT NOT NULL DEFAULT '[]'       -- the other columns' values, in field order (JSON)
);
CREATE INDEX IF NOT EXISTS boxes_by_frame ON boxes (frame_number);
CREATE INDEX IF NOT EXISTS boxes_by_track ON boxes (manual_track_id) WHERE manual_track_id != '';
CREATE TABLE IF NOT EXISTS completed_frames (
    frame_number INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL              -- the frame's boxes when it was completed
);
"""


class StoreError(OSError):
    """The review database could not be read or written."""


def exists(root):
    """The run has saved reviews (a database, or an older CSV to migrate)."""
    root = Path(root)
    return (root / DB_NAME).is_file() or (root / LEGACY_CSV).is_file()


def frame_fingerprint(frame_rows):
    """Identifies a frame's boxes, so a later edit can reopen a completed frame.

    Only what defines the frame's annotations counts; numbers are normalised so
    1, 1.0 and '1.00' agree.
    """
    return _hash([[int(r['frame_number']), *(f'{float(r[k]):.2f}' for k in BOX),
                   *(str(r.get(k, '')) for k in ('species', 'review_status', 'annotation_source', 'annotation_id'))]
                  for r in frame_rows])


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _reviewed(frame_rows):
    return all(r['review_status'] in ('confirmed', 'rejected') for r in frame_rows)


class ReviewStore:
    """An open review database. Use as a context manager, or call close()."""

    def __init__(self, root):
        self.root = Path(root)
        path = self.root / DB_NAME
        migrate = not path.is_file()
        if migrate and not (self.root / LEGACY_CSV).is_file():
            raise FileNotFoundError(f'No saved reviews in {self.root}.')
        self._connect()
        if migrate:
            try:
                self._migrate()
            except BaseException:
                # Leave the run as it was, so the next open tries again.
                self.db.close()
                path.unlink(missing_ok=True)
                raise
        if self._meta('schema') != SCHEMA_VERSION:
            self.db.close()
            raise StoreError(f'{path} was made by a different version of this app.')
        self._set_fields(json.loads(self._meta('fields')))

    def _set_fields(self, fields):
        self.fields = list(dict.fromkeys(list(fields) + list(CORE)))
        self._extra = [f for f in self.fields if f not in CORE]

    def _connect(self):
        path = self.root / DB_NAME
        try:
            # Autocommit; every write is an explicit transaction (_Transaction).
            self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
            self.db.executescript(SCHEMA)
        except sqlite3.Error as exc:
            raise StoreError(f'Cannot open {path}: {exc}') from exc
        self._saved = None       # rows as last loaded or saved, to find what changed
        self._positions = None   # their positions in the boxes table
        self._version = None     # data_version at our last read/write; other writers change it
        self._writes = 0         # our own commits (data_version only counts other connections')
        self._completed = None   # ((data_version, writes), frames) cache

    @classmethod
    def create(cls, root, rows, fields):
        """A new database for a run (replacing any existing one), holding ``rows``."""
        root = Path(root)
        (root / DB_NAME).unlink(missing_ok=True)
        rows, fields = normalize_review_rows([dict(r) for r in rows], fields)
        store = cls.__new__(cls)
        store.root = root
        store._connect()
        store._set_fields(fields)
        with store._write():
            store._set_meta('schema', SCHEMA_VERSION)
            store._set_meta('fields', json.dumps(store.fields))
            store._rewrite(rows)
        return store

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # Rows

    def load(self):
        """Every row, in order, as dicts with one key per field."""
        rows, positions = [], []
        # Each row's values arrive as the core columns then the extras; pick them in field order.
        columns = list(CORE) + self._extra
        pick = itemgetter(*(columns.index(field) for field in self.fields))
        fields = self.fields
        try:
            for position, *values, extra in self.db.execute(
                    f'SELECT position, {", ".join(CORE)}, extra FROM boxes ORDER BY position'):
                values.extend(json.loads(extra))
                rows.append(dict(zip(fields, pick(values))))
                positions.append(position)
        except sqlite3.Error as exc:
            raise StoreError(f'Cannot read reviews: {exc}') from exc
        self._saved, self._positions = [dict(r) for r in rows], positions
        self._version = self._data_version()
        return rows

    def save(self, rows, complete=None):
        """Replace the saved rows with ``rows``, writing only what changed.

        With ``complete`` (a frame number), that frame is also recorded as fully
        reviewed, in the same transaction: both are saved or neither is.
        """
        rows = list(rows)
        with self._write():
            if self._saved is None or self._version != self._data_version():
                self._rewrite(rows)  # never loaded, or another writer changed the file
            else:
                self._apply_changes(rows)
            if complete is not None:
                self._record(complete, [r for r in rows if int(r['frame_number']) == complete])

    def _apply_changes(self, rows):
        old, positions = self._saved, self._positions
        start, end_old, end_new = 0, len(old), len(rows)
        while start < min(end_old, end_new) and old[start] == rows[start]:
            start += 1
        while end_old > start and end_new > start and old[end_old - 1] == rows[end_new - 1]:
            end_old, end_new = end_old - 1, end_new - 1
        if end_old - start == end_new - start:
            # Same number of rows: update the ones that differ, where they are.
            for index in range(start, end_old):
                if old[index] != rows[index]:
                    self.db.execute(f'UPDATE boxes SET {", ".join(f"{c} = ?" for c in CORE)}, extra = ? '
                                    'WHERE position = ?', (*self._record_values(rows[index]), positions[index]))
                    old[index] = dict(rows[index])
            return
        # Rows were added or removed: replace the changed stretch, keeping positions around it.
        if end_old > start:
            self.db.execute('DELETE FROM boxes WHERE position BETWEEN ? AND ?',
                            (positions[start], positions[end_old - 1]))
        count = end_new - start
        low = positions[start - 1] if start else 0
        high = positions[end_old] if end_old < len(old) else low + (count + 1) * GAP
        if high - low <= count:
            self._rewrite(rows)  # no room between the neighbours: renumber everything
            return
        step = (high - low) // (count + 1)
        inserted = [low + step * (k + 1) for k in range(count)]
        self._insert(rows[start:end_new], inserted)
        # Only the changed stretch is copied; the rest of the snapshot already matches.
        self._saved = old[:start] + [dict(r) for r in rows[start:end_new]] + old[end_old:]
        self._positions = positions[:start] + inserted + positions[end_old:]

    def _rewrite(self, rows):
        self.db.execute('DELETE FROM boxes')
        positions = [GAP * (i + 1) for i in range(len(rows))]
        self._insert(rows, positions)
        self._saved, self._positions = [dict(r) for r in rows], positions

    def _insert(self, rows, positions):
        self.db.executemany(f'INSERT INTO boxes (position, {", ".join(CORE)}, extra) '
                            f'VALUES ({", ".join("?" * (len(CORE) + 2))})',
                            [(position, *self._record_values(row)) for row, position in zip(rows, positions)])

    def _record_values(self, row):
        unknown = row.keys() - set(self.fields)
        if unknown:
            raise ValueError(f'Unknown review columns: {", ".join(sorted(unknown))}')
        return (int(row['frame_number']), *(float(row[k]) for k in BOX), str(row.get('species') or ''),
                row.get('review_status'), row.get('annotation_source'),
                *(str(row.get(k) or '') for k in ('annotation_id', 'manual_track_id', 'track_keyframe')),
                json.dumps([row.get(f, '') for f in self._extra], default=_plain))

    # Completed frames

    def record_completion(self, number, frame_rows):
        """Record that a frame, with these saved rows, is fully reviewed."""
        with self._write():
            self._record(number, frame_rows)

    def _record(self, number, frame_rows):
        self.db.execute('INSERT OR REPLACE INTO completed_frames VALUES (?, ?)',
                        (number, frame_fingerprint(frame_rows)))

    def completed_frames(self):
        """Frames recorded complete whose boxes are unchanged since and all reviewed (cached)."""
        version = self._data_version(), self._writes
        if self._completed and self._completed[0] == version:
            return self._completed[1]
        by_frame, recorded = defaultdict(list), {}
        try:
            for number, digest, *values in self.db.execute(
                    f'SELECT c.frame_number, c.fingerprint, {", ".join("b." + c for c in CORE)} '
                    'FROM completed_frames c LEFT JOIN boxes b USING (frame_number) '
                    'ORDER BY c.frame_number, b.position'):
                recorded[number] = digest
                if values[0] is not None:  # a completed empty frame (background) has no boxes
                    by_frame[number].append(dict(zip(CORE, values)))
        except sqlite3.Error as exc:
            raise StoreError(f'Cannot read completed frames: {exc}') from exc
        frames = [n for n, digest in recorded.items()
                  if digest == frame_fingerprint(by_frame[n]) and _reviewed(by_frame[n])]
        self._completed = (version, frames)
        return frames

    def modified_ns(self):
        """When the reviews last changed (nanoseconds since the epoch)."""
        return int(self._meta('modified_ns') or 0)

    # Internals

    def _write(self):
        return _Transaction(self)

    def _data_version(self):
        return self.db.execute('PRAGMA data_version').fetchone()[0]

    def _meta(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, str(value)))

    def _migrate(self):
        """Copy an older run's CSV and completion file in, then rename them out of the way."""
        csv_path, completion_path = self.root / LEGACY_CSV, self.root / LEGACY_COMPLETION
        rows, fields = load_review_rows(csv_path)
        done = _legacy_completed(self.root, rows)
        self._set_fields(fields)
        by_frame = defaultdict(list)
        for row in rows:
            by_frame[row['frame_number']].append(row)
        changed = max(path.stat().st_mtime_ns for path in (csv_path, completion_path) if path.is_file())
        with self._write():
            self._set_meta('schema', SCHEMA_VERSION)
            self._set_meta('fields', json.dumps(self.fields))
            self._rewrite(rows)
            for number in done:
                self._record(number, by_frame[number])
        # Keep the results-are-current check as it was before migrating.
        self._set_meta('modified_ns', changed)
        csv_path.rename(csv_path.with_name('low_confidence_review.migrated.csv'))
        if completion_path.is_file():
            completion_path.rename(completion_path.with_name('review_completion.migrated.json'))


class _Transaction:
    """One atomic write; database failures become StoreError (an OSError)."""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.store.db.execute('BEGIN IMMEDIATE')

    def __exit__(self, kind, error, _):
        store = self.store
        if kind is None:
            try:
                store._set_meta('modified_ns', time.time_ns())
                store.db.commit()
                store._version = store._data_version()
                store._writes += 1
                return False
            except sqlite3.Error as exc:
                error = exc
        store.db.rollback()
        store._saved = None  # memory may not match the file now; the next save rewrites
        if isinstance(error, sqlite3.Error):
            raise StoreError(f'Reviews not saved: {error}') from error
        return False


def _plain(value):
    """JSON for NumPy scalars from the detector (e.g. float32 confidences)."""
    return value.item() if hasattr(value, 'item') else str(value)


def _legacy_completed(root, rows):
    """Frames an older run's review_completion.json verifies, with its own fingerprints."""
    completion_path, source_path = root / LEGACY_COMPLETION, root / 'original_detections.json'
    if not (completion_path.is_file() and source_path.is_file()):
        return []
    completion = json.loads(completion_path.read_text(encoding='utf-8'))
    if completion.get('source') != _hash(json.loads(source_path.read_text(encoding='utf-8'))):
        return []
    keys = ('frame_number', *BOX, 'species', 'review_status', 'annotation_source', 'annotation_id')
    by_frame = defaultdict(list)
    for row in rows:
        by_frame[row['frame_number']].append(row)
    return [int(n) for n, digest in completion.get('frames', {}).items()
            if n.isdigit()
            and digest == _hash([{k: str(r.get(k, '')) for k in keys} for r in by_frame[int(n)]])
            and _reviewed(by_frame[int(n)])]


def load_rows(root):
    """(rows, fields) for a run, opening and closing the database."""
    with ReviewStore(root) as store:
        return store.load(), store.fields


def save_rows(root, rows):
    """Replace a run's saved rows in one go (scripts and tests; the app keeps a store open)."""
    with ReviewStore(root) as store:
        store.save(rows)


def record_completion(root, number):
    with ReviewStore(root) as store:
        rows = [r for r in store.load() if r['frame_number'] == number]
        store.record_completion(number, rows)


def modified_ns(root):
    """When a run's reviews last changed, or None if it has none."""
    if not exists(root):
        return None
    with ReviewStore(root) as store:
        return store.modified_ns()
