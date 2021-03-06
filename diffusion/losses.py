import torch
import torch.nn
import torch.nn.functional as F
import warnings

from .types import DataScoreModel, ScoreMatchingLoss, ConditionalDataScoreModel
from . import jacobians


def _ism(tr_jac: torch.Tensor, scores: torch.Tensor) -> torch.Tensor:
    return (tr_jac + 0.5 * (scores ** 2).sum(-1)).mean()


def ism_loss(score_model: DataScoreModel, x: torch.Tensor) -> torch.Tensor:
    """Computes the implicit score matching loss."""
    B, _ = x.shape
    j = jacobians.full_jacobian(score_model, x)
    tr = j[range(B), 0, range(B), 0] + j[range(B), 1, range(B), 1]
    return _ism(tr, score_model(x))


def ism_loss_fast(score_model: DataScoreModel, x: torch.Tensor) -> torch.Tensor:
    """Computes the implicit score matching loss.

    The output of this method is equivalent to `ism_loss` but will be faster in computation. It trades computational speed for increased memory requirements and may not work with all score model architectures, since it will increase the number of input dimensions. But, at least for FC architectures this works fine and is x10 faster.
    """
    B, _ = x.shape
    j = jacobians.batched_jacobian(score_model, x, 2)
    tr = j[range(B), 0, 0] + j[range(B), 1, 1]
    return _ism(tr, score_model(x))


def dsm_loss(
    score_model: DataScoreModel, x: torch.Tensor, sigma: float
) -> torch.Tensor:
    """Denoising score matching loss.
    q(y|x) is assumed to be N(y;x,sigma^2) and hence \nabla_y log q = 1/sigma^2 * (x-y)

    The smaller sigma, the larger the gradients since q peaks more rapidly.
    """

    xn = x + torch.randn_like(x) * sigma
    targets = (1 / sigma ** 2) * (x - xn)  # \nabla_y log q(y|x)
    scores = score_model(xn)
    return 0.5 * ((scores - targets) ** 2).sum(dim=-1).mean(dim=0)


def ncsm_loss(
    score_model: ConditionalDataScoreModel, x: torch.Tensor, sigmae: torch.Tensor
) -> torch.Tensor:
    ids = torch.randint(0, sigmae.shape[0], size=(x.shape[0],)).to(x.device)  # (B,)
    chosen_sigma = sigmae[ids].unsqueeze(-1)  # (B,1)
    xn = x + torch.randn_like(x) * chosen_sigma  # (B,D)
    targets = (1 / (chosen_sigma ** 2)) * (x - xn)  # (B,D)
    scores = score_model(xn, ids)  # (B,D)
    losses = 0.5 * ((scores - targets) ** 2).sum(dim=-1)  # (B,)
    lambd = (chosen_sigma ** 2).squeeze(-1)  # (B,)
    return (losses * lambd).mean()


class ISMLoss(ScoreMatchingLoss):
    def __init__(self, enable_fast: bool = True) -> None:
        super().__init__()
        if enable_fast:
            self.fn = ism_loss_fast
            warnings.warn(
                (
                    "Fast Jacobian computation mode takes more memory"
                    " and is not compatible with every torch.nn.Module except for"
                    " the simplest ones."
                )
            )
        else:
            self.fn = ism_loss

    def forward(self, score_model: DataScoreModel, x: torch.Tensor) -> torch.Tensor:
        return self.fn(score_model, x)


class DSMLoss(ScoreMatchingLoss):
    def __init__(self, sigma: float = 1e-2) -> None:
        super().__init__()
        self.sigma = sigma

    def forward(self, score_model: DataScoreModel, x: torch.Tensor) -> torch.Tensor:
        return dsm_loss(score_model, x, sigma=self.sigma)


class NCSMLoss(ScoreMatchingLoss):
    def __init__(self, sigmae: torch.Tensor):
        super().__init__()
        self.register_buffer("sigmae", sigmae)

    def forward(self, score_model: DataScoreModel, x: torch.Tensor) -> torch.Tensor:
        return ncsm_loss(score_model, x, self.sigmae)
