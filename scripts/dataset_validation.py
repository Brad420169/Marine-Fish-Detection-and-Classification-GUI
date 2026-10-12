"""Read local YOLO detection datasets without running YAML download hooks."""
from pathlib import Path
import math
import yaml

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp'}


def label_path(image):
    parts = list(Path(image).parts)
    if 'images' in parts:
        index = len(parts) - 1 - parts[::-1].index('images')
        parts[index] = 'labels'
    return Path(*parts).with_suffix('.txt')


def inspect_dataset(filename):
    path = Path(filename).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Choose an existing data.yaml file.')
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise ValueError(f'Invalid dataset YAML: {exc}') from exc
    if not isinstance(data, dict) or data.get('download'):
        raise ValueError('Use a local dataset YAML without download commands.')
    names = data.get('names')
    if isinstance(names, dict):
        if set(names) != set(range(len(names))):
            raise ValueError('Class IDs must be consecutive integers starting at zero.')
        names = [names[i] for i in range(len(names))]
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError('Dataset names must contain nonempty class names.')
    if len(set(names)) != len(names):
        raise ValueError('Class names must be unique.')
    root = Path(data.get('path') or path.parent).expanduser()
    if not root.is_absolute():
        root = path.parent / root
    counts, seen, split_images = {}, set(), {}
    for split in ('train', 'val', 'test'):
        entries = data.get(split)
        if not entries:
            if split == 'test':
                counts[split] = 0
                continue
            raise ValueError(f'Missing {split} split.')
        entries = entries if isinstance(entries, list) else [entries]
        images = []
        for entry in entries:
            if not isinstance(entry, str):
                raise ValueError(f'Invalid {split} path.')
            target = Path(entry).expanduser()
            if not target.is_absolute(): target = root / target
            if target.is_dir():
                images.extend(p.resolve() for p in target.rglob('*') if p.suffix.lower() in IMAGE_SUFFIXES)
            elif target.is_file() and target.suffix.lower() == '.txt':
                for line in target.read_text(encoding='utf-8').splitlines():
                    if line.strip():
                        image = Path(line.strip()).expanduser()
                        images.append((target.parent / image).resolve() if not image.is_absolute() else image.resolve())
            else:
                raise ValueError(f'{split} path not found: {target}')
        if not images: raise ValueError(f'{split} has no images.')
        labelled = 0
        for image in images:
            if not image.is_file(): raise ValueError(f'Image not found: {image}')
            if image in seen: raise ValueError(f'Duplicate image across dataset entries or splits: {image.name}')
            seen.add(image)
            label = label_path(image)
            if not label.exists(): continue  # YOLO treats missing labels as background.
            for line in label.read_text(encoding='utf-8').splitlines():
                if not line.strip(): continue
                try:
                    values = [float(v) for v in line.split()]
                except ValueError as exc:
                    raise ValueError(f'Invalid detection label: {label}') from exc
                if (len(values) != 5 or not all(math.isfinite(v) for v in values)
                    or not values[0].is_integer() or not 0 <= values[0] < len(names)
                    or not all(0 <= v <= 1 for v in values[1:]) or min(values[3:]) <= 0):
                    raise ValueError(f'Expected class ID and normalized x/y/width/height: {label}')
                labelled += 1
        if split == 'train' and not labelled:
            raise ValueError('Training images have no labelled objects. Check the labels folder.')
        counts[split] = len(images)
        split_images[split] = images
    return dict(names=names, counts=counts, root=root, images=split_images)
