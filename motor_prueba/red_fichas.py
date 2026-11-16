"""Actor/crítico con torso compartido sobre fichas de ontología."""
from __future__ import annotations

import torch
import torch.nn as nn

from ficha_rl import (
    ACTION_BLOCK_DIM,
    ACTION_TYPE_DIM,
    FICHA_DIM,
    GLOBAL_DIM,
    N_ACTIONS,
    N_SLOTS,
    OBS_DIM,
)


class TorsoFicha(nn.Module):
    """FICHA_DIM → 256 → 128. Compartido entre actor y crítico."""

    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(FICHA_DIM, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
        )
        self.output_dim = 128

    def forward(self, fichas: torch.Tensor) -> torch.Tensor:
        # fichas: (B, N_SLOTS, FICHA_DIM) o (B*N, FICHA_DIM)
        flat = fichas.reshape(-1, FICHA_DIM)
        out = self.net(flat)
        if fichas.dim() == 3:
            return out.reshape(fichas.shape[0], fichas.shape[1], 128)
        return out


class ActorFichas(nn.Module):
    """Puntúa N_ACTIONS logits a partir del vector plano de observación."""

    def __init__(self, torso: TorsoFicha | None = None):
        super().__init__()
        self.torso = torso or TorsoFicha()
        self.global_net = nn.Sequential(
            nn.Linear(GLOBAL_DIM, 64),
            nn.ReLU(),
        )
        self.action_net = nn.Sequential(
            nn.Linear(ACTION_TYPE_DIM + 128 + 128, 128),
            nn.ReLU(),
        )
        self.score = nn.Sequential(
            nn.Linear(128 + 64, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )
        self.output_dim = N_ACTIONS

    def get_output_dim(self) -> int:
        return N_ACTIONS

    def _partir(self, obs: torch.Tensor):
        if hasattr(obs, "obs") and not isinstance(getattr(obs, "obs"), str):
            obs = obs.obs
        device = next(self.parameters()).device
        if not torch.is_tensor(obs):
            obs = torch.as_tensor(obs, dtype=torch.float32, device=device)
        else:
            obs = obs.to(device=device, dtype=torch.float32)
        if obs.dim() == 1:
            obs = obs.unsqueeze(0)
        b = obs.shape[0]
        cursor = 0
        fichas = obs[:, cursor:cursor + N_SLOTS * FICHA_DIM].reshape(b, N_SLOTS, FICHA_DIM)
        cursor += N_SLOTS * FICHA_DIM
        global_ = obs[:, cursor:cursor + GLOBAL_DIM]
        cursor += GLOBAL_DIM
        acciones = obs[:, cursor:cursor + N_ACTIONS * ACTION_BLOCK_DIM].reshape(
            b, N_ACTIONS, ACTION_BLOCK_DIM
        )
        return fichas, global_, acciones

    def forward(self, obs, state=None, info=None):
        fichas, global_, acciones = self._partir(obs)
        latentes = self.torso(fichas)  # (B, N, 128)
        g = self.global_net(global_)  # (B, 64)
        tipos = acciones[:, :, :ACTION_TYPE_DIM]
        idx_a = acciones[:, :, ACTION_TYPE_DIM].long()
        idx_b = acciones[:, :, ACTION_TYPE_DIM + 1].long()
        def gather(idx: torch.Tensor) -> torch.Tensor:
            # idx: (B, N_ACTIONS) en [-1, N_SLOTS)
            safe = idx.clamp(min=0)
            gathered = torch.gather(
                latentes,
                1,
                safe.unsqueeze(-1).expand(-1, -1, 128),
            )
            mask = (idx >= 0).unsqueeze(-1).float()
            return gathered * mask

        la = gather(idx_a)
        lb = gather(idx_b)
        act_in = torch.cat([tipos, la, lb], dim=-1)  # (B, A, 268)
        act_h = self.action_net(act_in)  # (B, A, 128)
        g_exp = g.unsqueeze(1).expand(-1, N_ACTIONS, -1)
        logits = self.score(torch.cat([act_h, g_exp], dim=-1)).squeeze(-1)  # (B, A)
        # DiscreteActor espera (probs_or_logits, hidden); MaskedPolicy aplica softmax si hace falta.
        # Devolvemos logits; la política enmascara y normaliza como probs.
        probs = torch.softmax(logits, dim=-1)
        return probs, state


class CriticFichas(nn.Module):
    """Valor del estado: media de fichas presentes + global."""

    def __init__(self, torso: TorsoFicha):
        super().__init__()
        # No registrar el torso otra vez (ActorCritic une parameters()).
        self._torso = torso
        self.global_net = nn.Sequential(
            nn.Linear(GLOBAL_DIM, 64),
            nn.ReLU(),
        )
        self.value = nn.Sequential(
            nn.Linear(128 + 64, 128),
            nn.ReLU(),
            nn.Linear(128, 1),
        )

    def forward(self, obs, **kwargs):
        if hasattr(obs, "obs") and not isinstance(getattr(obs, "obs"), str):
            obs = obs.obs
        device = next(self._torso.parameters()).device
        if not torch.is_tensor(obs):
            obs = torch.as_tensor(obs, dtype=torch.float32, device=device)
        else:
            obs = obs.to(device=device, dtype=torch.float32)
        if obs.dim() == 1:
            obs = obs.unsqueeze(0)
        b = obs.shape[0]
        fichas = obs[:, : N_SLOTS * FICHA_DIM].reshape(b, N_SLOTS, FICHA_DIM)
        global_ = obs[:, N_SLOTS * FICHA_DIM: N_SLOTS * FICHA_DIM + GLOBAL_DIM]
        latentes = self._torso(fichas)
        presente = (fichas.abs().sum(dim=-1, keepdim=True) > 0).float()
        denom = presente.sum(dim=1).clamp(min=1.0)
        media = (latentes * presente).sum(dim=1) / denom
        g = self.global_net(global_)
        return self.value(torch.cat([media, g], dim=-1))


def build_actor_critic():
    torso = TorsoFicha()
    actor = ActorFichas(torso)
    critic = CriticFichas(torso)
    return actor, critic, torso


def contar_parametros(actor: nn.Module, critic: nn.Module) -> int:
    ids = set()
    n = 0
    for m in (actor, critic):
        for p in m.parameters():
            if id(p) in ids:
                continue
            ids.add(id(p))
            n += p.numel()
    return n
