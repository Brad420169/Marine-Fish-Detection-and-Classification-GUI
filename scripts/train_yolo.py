"""Run YOLO training in a separate process so the GUI remains responsive."""
import argparse
from pathlib import Path
import tempfile
import yaml

from dataset_validation import inspect_dataset

from training_metrics import attach_metrics

# Fixed training recipe for the small datasets this app exports (often tens of frames).
# - AMP off: mixed precision skips weight updates while it calibrates, which with only a
#   few updates per epoch wasted the first ~10 epochs and then crashed training. Older
#   GPUs (e.g. GTX 10-series) gain no speed from it anyway.
# - SGD at 0.01 instead of Ultralytics' "auto" (AdamW ~0.002 on small datasets), which
#   diverged on a 29-image dataset and never recovered; SGD trained steadily.
OPTIMIZER, LEARNING_RATE = 'SGD', 0.01
MIN_STABLE_BATCH = 4


def warn_small_batch(trainer):
    """Auto batch can pick 1 or 2 for a large model on a small GPU, which trains badly."""
    if trainer.batch_size < MIN_STABLE_BATCH:
        print(f'WARNING: Auto batch chose {trainer.batch_size} image(s) per batch, too few to train well. '
              'Stop, then choose a smaller starting model (e.g. yolo11s.pt), a lower image size, '
              f'or set Batch size to at least {MIN_STABLE_BATCH}.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('data', type=Path)
    parser.add_argument('--model', default='yolo11s.pt')
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--imgsz', type=int, default=576)
    parser.add_argument('--batch', type=int, default=0)
    parser.add_argument('--patience', type=int, default=20)
    parser.add_argument('--name', default='fish_yolo11s')
    parser.add_argument('--output-dir', type=Path, help='Dedicated output folder allocated by the GUI')
    parser.add_argument('--work-dir', type=Path, help='Temporary configuration folder owned by the GUI')
    parser.add_argument('--evaluate', type=Path, help='Evaluate saved weights on the held-out test split')
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f'Dataset YAML not found: {args.data}')
    if args.epochs < 1 or args.imgsz < 32 or args.batch < 0 or args.patience < 0:
        parser.error('Training settings must be nonnegative; epochs must be at least 1.')
    if not args.evaluate and args.model not in ('yolo11n.pt', 'yolo11s.pt') and (
            Path(args.model).suffix.lower() != '.pt' or not Path(args.model).is_file()):
        parser.error('Choose an existing YOLO .pt weights file.')

    try:
        info = inspect_dataset(args.data)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    if args.evaluate and not info['counts']['test']:
        parser.error('This dataset has no held-out test split.')
    # Resolve paths relative to the supplied YAML, not Ultralytics' global dataset directory.
    # Only a temporary, sanitized config is passed to the trainer; the user's YAML stays intact.
    temporary = None if args.work_dir else tempfile.TemporaryDirectory(prefix='marine-training-')
    work_dir = args.work_dir if args.work_dir else Path(temporary.name)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_options = (dict(project=str(args.output_dir.parent), name=args.output_dir.name, exist_ok=True)
                      if args.output_dir else dict(project=str(args.data.resolve().parent / 'runs'),
                                                   name='test_evaluation' if args.evaluate else args.name))
    dataset = dict(names=info['names'], path=str(info['root'].resolve()))
    for split, images in info['images'].items():
        listing = work_dir / f'{split}.txt'
        listing.write_text('\n'.join(map(str, images)) + '\n', encoding='utf-8')
        dataset[split] = str(listing)
    normalized = work_dir / 'data.yaml'
    normalized.write_text(yaml.safe_dump(dataset), encoding='utf-8')

    import torch
    from ultralytics import YOLO

    device = 0 if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    if args.evaluate:
        if not args.evaluate.is_file():
            parser.error(f'Model weights not found: {args.evaluate}')
        print(f'Evaluating test frames with {args.evaluate}', flush=True)
        YOLO(str(args.evaluate)).val(data=str(normalized), split='test', device=device,
                                     **output_options)
        return
    batch = args.batch or (-1 if device == 0 else 4)
    print(f'Dataset: {args.data.resolve()}\nDevice: {device}\nBatch: {"Auto" if batch == -1 else batch}', flush=True)
    print(f'Optimizer: {OPTIMIZER} (learning rate {LEARNING_RATE}); mixed precision (AMP) off', flush=True)
    model = YOLO(args.model)
    attach_metrics(model)
    model.add_callback('on_pretrain_routine_end', warn_small_batch)
    model.train(
        data=str(normalized), epochs=args.epochs, imgsz=args.imgsz,
        batch=batch, device=device, workers=0, **output_options,
        pretrained=True, patience=args.patience, save=True,
        plots=True, verbose=True, seed=42,
        optimizer=OPTIMIZER, lr0=LEARNING_RATE, amp=False,
    )
    print(f'BEST_WEIGHTS={model.trainer.best}', flush=True)


if __name__ == '__main__':
    main()
