"""Combine several YOLO datasets (for example one per annotated video) into one.

Each dataset keeps its own train / validation / test split, so frames from one video
never end up on both sides of a split. Class names are merged by name, ignoring
capitals ("fish" and "Fish" are one class, spelled as first seen), and every label is
renumbered to the combined class list. Files are prefixed with their dataset's name so
they cannot collide. The new dataset is built in a temporary folder and only moved
into place once it is complete, then checked like any dataset the Train page opens.
"""
import json
import re
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from datasets.validation import inspect_dataset, label_path
from datasets.export import dataset_label

SPLITS = ('train', 'val', 'test')


def _prefix(name, used):
    """A short, filename-safe tag for a dataset, unique within this combination."""
    tag = re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')[:60] or 'dataset'
    unique, number = tag, 2
    while unique in used:
        unique, number = f'{tag}_{number}', number + 1
    used.add(unique)
    return unique


def _dataset_name(yaml_path):
    """The dataset's readable name (its folder, or its video for an older "yolo_dataset"),
    without the "yolo_dataset" part, so file prefixes are just the video's name."""
    label = dataset_label(Path(yaml_path).resolve())
    return re.sub(r'[ _]*\(?yolo_dataset\)?$', '', label) or label


def combine_datasets(yaml_paths, destination, progress=None, cancelled=None):
    """Combine the datasets at ``yaml_paths`` into a new folder ``destination``.

    Returns (path to the new data.yaml, {'train': n, 'val': n, 'test': n}, class names).
    ``progress(done, total)`` is called after each image; ``cancelled()`` returning True
    stops the job (nothing is left behind). Raises ValueError, FileExistsError, OSError,
    or InterruptedError when cancelled.
    """
    paths = [Path(p).expanduser().resolve() for p in yaml_paths]
    if len(paths) < 2:
        raise ValueError('Choose at least two datasets to combine.')
    if len(set(paths)) != len(paths):
        raise ValueError('The same dataset was chosen twice.')
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f'Dataset folder already exists: {destination}')
    if not destination.parent.is_dir():
        raise FileNotFoundError(f'Choose an existing parent folder: {destination.parent}')

    datasets = []
    for path in paths:
        try:
            datasets.append(inspect_dataset(path))
        except ValueError as exc:
            raise ValueError(f'{_dataset_name(path)}: {exc}') from exc

    names, index_of = [], {}  # combined class names; casefolded name -> new class id
    for info in datasets:
        for name in info['names']:
            key = name.strip().casefold()
            if key not in index_of:
                index_of[key] = len(names)
                names.append(name.strip())
    total = sum(len(images) for info in datasets for images in info['images'].values())
    counts = dict.fromkeys(SPLITS, 0)
    done, used = 0, set()

    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.yolo-combine-') as temporary:
        stage = Path(temporary) / 'dataset'
        for split in SPLITS:
            (stage / 'images' / split).mkdir(parents=True)
            (stage / 'labels' / split).mkdir(parents=True)
        for path, info in zip(paths, datasets):
            prefix = _prefix(_dataset_name(path), used)
            remap = [index_of[name.strip().casefold()] for name in info['names']]
            for split, images in info['images'].items():
                taken = set()
                for image in images:
                    if cancelled and cancelled():
                        raise InterruptedError('Combining stopped; nothing was saved.')
                    stem = f'{prefix}_{image.stem}'
                    unique, number = stem, 2
                    while unique in taken:  # the same file name in two sub-folders of one split
                        unique, number = f'{stem}_{number}', number + 1
                    taken.add(unique)
                    shutil.copy2(image, stage / 'images' / split / f'{unique}{image.suffix.lower()}')
                    lines = []
                    label = label_path(image)
                    if label.is_file():
                        for line in label.read_text(encoding='utf-8').splitlines():
                            if line.strip():
                                class_id, *box = line.split()
                                lines.append(' '.join([str(remap[int(float(class_id))]), *box]))
                    (stage / 'labels' / split / f'{unique}.txt').write_text(
                        '\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
                    counts[split] += 1
                    done += 1
                    if progress:
                        progress(done, total)
        if not counts['train'] or not counts['val']:
            raise ValueError('The combined dataset needs training and validation images.')
        yaml = ['train: images/train', 'val: images/val']
        if counts['test']:
            yaml.append('test: images/test')
        yaml.append('names:')
        yaml.extend(f'  {index}: {json.dumps(name, ensure_ascii=False)}' for index, name in enumerate(names))
        (stage / 'data.yaml').write_text('\n'.join(yaml) + '\n', encoding='utf-8')
        inspect_dataset(stage / 'data.yaml')  # the same checks the Train page makes
        stage.rename(destination)
    return destination / 'data.yaml', counts, names


class CombineWorker(QThread):
    progress = pyqtSignal(int, int)          # images copied, images in total
    succeeded = pyqtSignal(str, dict, list)  # data.yaml, counts per split, class names
    failed = pyqtSignal(str)

    def __init__(self, yaml_paths, destination, parent=None):
        super().__init__(parent)
        self.yaml_paths, self.destination = list(yaml_paths), destination

    def run(self):
        try:
            yaml, counts, names = combine_datasets(self.yaml_paths, self.destination, self.progress.emit,
                                                   self.isInterruptionRequested)
            self.succeeded.emit(str(yaml), counts, names)
        except Exception as exc:  # including InterruptedError when Stop was pressed
            self.failed.emit(str(exc))
