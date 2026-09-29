"""Run YOLO training in a separate process so the GUI remains responsive."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('data', type=Path)
    parser.add_argument('--model', default='yolo11s.pt')
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--imgsz', type=int, default=576)
    parser.add_argument('--batch', type=int, default=0)
    parser.add_argument('--patience', type=int, default=20)
    parser.add_argument('--name', default='fish_yolo11s')
    parser.add_argument('--evaluate', type=Path, help='Evaluate saved weights on the held-out test split')
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f'Dataset YAML not found: {args.data}')
    if args.epochs < 1 or args.imgsz < 32 or args.batch < 0 or args.patience < 0:
        parser.error('Training settings must be nonnegative; epochs must be at least 1.')
    if not args.evaluate and args.model not in ('yolo11n.pt', 'yolo11s.pt') and (
            Path(args.model).suffix.lower() != '.pt' or not Path(args.model).is_file()):
        parser.error('Choose an existing YOLO .pt weights file.')

    import torch
    from ultralytics import YOLO

    device = 0 if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    if args.evaluate:
        if not args.evaluate.is_file():
            parser.error(f'Model weights not found: {args.evaluate}')
        print(f'Evaluating test frames with {args.evaluate}', flush=True)
        YOLO(str(args.evaluate)).val(data=str(args.data.resolve()), split='test', device=device,
                                     project=str(args.data.resolve().parent / 'runs'), name='test_evaluation')
        return
    batch = args.batch or (-1 if device == 0 else 4)
    print(f'Dataset: {args.data.resolve()}\nDevice: {device}\nBatch: {batch}', flush=True)
    model = YOLO(args.model)
    model.train(
        data=str(args.data.resolve()), epochs=args.epochs, imgsz=args.imgsz,
        batch=batch, device=device, workers=0, project=str(args.data.resolve().parent / 'runs'),
        name=args.name, pretrained=True, patience=args.patience, save=True,
        plots=True, verbose=True, seed=42,
    )
    print(f'BEST_WEIGHTS={model.trainer.best}', flush=True)


if __name__ == '__main__':
    main()
