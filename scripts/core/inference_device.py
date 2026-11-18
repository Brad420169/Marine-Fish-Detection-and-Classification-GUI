"""Select and verify the device used by inference."""
from __future__ import annotations

import torch


def select_device(requested: str | None = None) -> tuple[str, str]:
    """Return an explicit device and a user-facing explanation.

    Test actual GPU execution before loading a large model. Explicit GPU
    requests fail visibly instead of silently falling back to the CPU.
    """
    automatic = requested is None or requested == 'auto'
    device = ('cuda:0' if torch.cuda.is_available() else 'cpu') if automatic else requested
    if device == 'cpu':
        if not automatic:
            return device, 'CPU (explicitly selected)'
        reason = ('installed PyTorch has no CUDA support' if torch.version.cuda is None
                  else 'CUDA is unavailable; check the NVIDIA driver and GPU access')
        return device, f'CPU — {reason}'
    try:
        target = torch.device(device)
        if target.type == 'cuda' and target.index is None:
            target = torch.device('cuda:0')
        probe = torch.ones((2, 2), device=target)
        (probe @ probe).sum().item()
        if target.type == 'cuda':
            name = torch.cuda.get_device_name(target)
            return str(target), f'CUDA — {name} ({target})'
        return str(target), str(target)
    except Exception as exc:
        if not automatic:
            raise RuntimeError(f'Cannot use requested inference device {device}: {exc}') from exc
        return 'cpu', f'CPU — GPU execution check failed: {exc}'
