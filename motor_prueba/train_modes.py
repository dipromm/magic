"""
Modos de entrenamiento compartidos: checkpoints policy+critic, curriculum, ancla congelada.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
from tianshou.algorithm import PPO
from tianshou.algorithm.modelfree.a2c import A2CTrainingStats
from tianshou.data import SequenceSummaryStats
from tianshou.data.types import LogpOldProtocol
from torch.nn import ModuleList

MAGIC_CKPT_FORMAT = "magic_policy_critic_v1"


def swap_anchor_learner_seats(policy_manager: Any) -> None:
    """
    Intercambia la asignación player_0/player_1 entre el PPO que aprende y el FrozenPPO ancla.
    Debe llamarse al inicio de ciertos epochs (no a mitad de un batch del buffer).
    """
    disp = policy_manager._dispatcher
    alg = disp.algorithms
    k0, k1 = "player_0", "player_1"
    if k0 not in alg or k1 not in alg:
        return
    alg[k0], alg[k1] = alg[k1], alg[k0]
    pol = policy_manager.policy.policies
    pol[k0], pol[k1] = pol[k1], pol[k0]
    policy_manager.policy._submodules = ModuleList([pol[k0], pol[k1]])
    policy_manager._submodules = ModuleList([alg[k0], alg[k1]])


def save_magic_checkpoint(path: str | Path, policy: torch.nn.Module, critic: torch.nn.Module) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "format": MAGIC_CKPT_FORMAT,
            "policy": policy.state_dict(),
            "critic": critic.state_dict(),
        },
        path,
    )


def load_magic_checkpoint(
    path: str | Path,
    policy: torch.nn.Module,
    critic: torch.nn.Module,
    *,
    strict_policy: bool = False,
    strict_critic: bool = False,
) -> tuple[bool, bool]:
    """
    Carga checkpoint. Soporta .pth antiguos (solo state_dict de policy plano).
    Returns: (policy_loaded_ok, critic_loaded_ok)
    """
    path = Path(path)
    try:
        data = torch.load(str(path), map_location="cpu", weights_only=True)
    except TypeError:
        data = torch.load(str(path), map_location="cpu")

    policy_ok = critic_ok = False
    if isinstance(data, dict) and data.get("format") == MAGIC_CKPT_FORMAT:
        policy.load_state_dict(data["policy"], strict=strict_policy)
        critic.load_state_dict(data["critic"], strict=strict_critic)
        policy_ok = critic_ok = True
    elif isinstance(data, dict) and "policy" in data and "critic" in data:
        policy.load_state_dict(data["policy"], strict=strict_policy)
        critic.load_state_dict(data["critic"], strict=strict_critic)
        policy_ok = critic_ok = True
    else:
        policy.load_state_dict(data, strict=strict_policy)
        policy_ok = True
    return policy_ok, critic_ok


def load_policy_weights_for_inference(path: str | Path, policy: torch.nn.Module) -> None:
    """Carga solo el actor/policy desde bundle o .pth plano (inferencia / humano)."""
    try:
        data = torch.load(str(path), map_location="cpu", weights_only=True)
    except TypeError:
        data = torch.load(str(path), map_location="cpu")
    if isinstance(data, dict) and "policy" in data:
        policy.load_state_dict(data["policy"], strict=True)
    else:
        policy.load_state_dict(data, strict=True)


def _magic_from_worker(worker: Any) -> Any:
    pz = worker.env
    return pz.env


def apply_dense_reward_scale(vec_env: Any, scale: float) -> None:
    for w in vec_env.workers:
        me = _magic_from_worker(w)
        me.dense_reward_scale = float(scale)
        if getattr(me, "motor", None) is not None:
            me.motor.dense_reward_scale = float(scale)


def apply_dense_reward_scale_petting(env_pz: Any, scale: float) -> None:
    me = env_pz.env
    me.dense_reward_scale = float(scale)
    if getattr(me, "motor", None) is not None:
        me.motor.dense_reward_scale = float(scale)


def flip_learner_plays_p0_all(train_envs: Any, test_envs: Any, env_prueba: Any) -> None:
    """Tras swap_anchor_learner_seats: el aprendiz pasa al otro asiento físico."""
    for vec in (train_envs, test_envs):
        for w in vec.workers:
            me = _magic_from_worker(w)
            if getattr(me, "log_anchor_roles", False):
                me.learner_plays_p0 = not bool(getattr(me, "learner_plays_p0", True))
    me = env_prueba.env
    if getattr(me, "log_anchor_roles", False):
        me.learner_plays_p0 = not bool(getattr(me, "learner_plays_p0", True))


class FrozenPPO(PPO):
    """PPO que no actualiza pesos (oponente ancla)."""

    def _update_with_batch(
        self,
        batch: LogpOldProtocol,
        batch_size: int | None,
        repeat: int,
    ) -> A2CTrainingStats:
        z = SequenceSummaryStats.from_single_value(0.0)
        return A2CTrainingStats(
            loss=z,
            actor_loss=z,
            vf_loss=z,
            ent_loss=z,
            gradient_steps=0,
        )
