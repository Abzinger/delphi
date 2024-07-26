import torch
from typing import Callable

class AutoencoderLatents(torch.nn.Module):
    """
    Wrapper module to simplify capturing of autoencoder latents.
    """

    def __init__(
        self,
        _forward: Callable,
        width: int = 32768,
    ) -> None:
        super().__init__()
        self._forward = _forward
        self.width = width

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self._forward(x)