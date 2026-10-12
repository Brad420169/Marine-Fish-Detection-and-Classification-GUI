"""Epoch telemetry shared by the training subprocess and its dashboard."""
import json
import math

PREFIX = 'TRAIN_METRICS='
METRICS = (
    ('metrics/precision(B)', 'Precision', '#42c9e8'),
    ('metrics/recall(B)', 'Recall', '#50dfb1'),
    ('metrics/mAP50(B)', 'mAP@50', '#b69cff'),
    ('metrics/mAP50-95(B)', 'mAP@50–95', '#ffc477'),
)


def attach_metrics(model):
    # final_eval also fires on_fit_epoch_end; only report actual training epochs.
    pending = False

    def started(trainer):
        nonlocal pending
        pending = True

    def finished(trainer):
        nonlocal pending
        if not pending:
            return
        pending = False
        values = {}
        for key, _, _ in METRICS:
            value = (trainer.metrics or {}).get(key)
            if value is not None and math.isfinite(float(value)):
                values[key] = float(value)
        print('\n' + PREFIX + json.dumps({'epoch': trainer.epoch + 1, 'metrics': values}), flush=True)

    model.add_callback('on_train_epoch_start', started)
    model.add_callback('on_fit_epoch_end', finished)


def parse_metrics(line):
    if not line.startswith(PREFIX):
        return None
    try:
        data = json.loads(line[len(PREFIX):])
        epoch = data['epoch']
        if type(epoch) is not int or epoch < 1:
            return None
        values = data['metrics']
        if not isinstance(values, dict):
            return None
        return epoch, {key: float(values[key]) for key, _, _ in METRICS
                       if type(values.get(key)) in (int, float)
                       and math.isfinite(values[key]) and 0 <= values[key] <= 1}
    except (ValueError, KeyError, TypeError):
        return None
