"""Entrena PPO sobre el puente (124) o el starter (fichas de ontología).

Bucle collect/update explícito con la misma máscara que train.py.
Soporta sparse, dense y curriculum (shaping denso → 0 lineal).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
from tianshou.algorithm import PPO
from tianshou.algorithm.multiagent.marl import MultiAgentOnPolicyAlgorithm
from tianshou.algorithm.optim import AdamOptimizerFactory
from tianshou.data import Collector, VectorReplayBuffer
from tianshou.env import DummyVectorEnv, PettingZooEnv
from tianshou.utils.net.common import Net
from tianshou.utils.net.discrete import DiscreteActor, DiscreteCritic
from tianshou.utils.torch_utils import policy_within_training_step, torch_train_mode

from env_adaptador import MagicAdaptadorEnv
from env_starter import MagicStarterEnv
from red_fichas import build_actor_critic, contar_parametros
from train import MaskedProbabilisticActorPolicy, ObsExtractorNet
from train_modes import apply_dense_reward_scale, apply_dense_reward_scale_petting, save_magic_checkpoint


def make_raw_env(kind: str, env_id: int, reward_mode: str, max_steps: int):
    dense_scale = 0.0 if reward_mode == "sparse" else 1.0
    kwargs = dict(
        verbose=False,
        env_id=env_id,
        reward_mode=reward_mode,
        max_episode_steps=max_steps,
        masking_enabled=True,
        dense_reward_scale=dense_scale,
    )
    if kind == "starter":
        return MagicStarterEnv(**kwargs)
    return MagicAdaptadorEnv(
        **kwargs,
        modo_menu="puente",
        deck_mode="puente",
        max_acciones=50,
    )


def build_policy_critic_puente(env_pz: PettingZooEnv, device: str):
    obs_space = env_pz.observation_space["observation"]
    action_shape = env_pz.action_space.shape or env_pz.action_space.n
    net_base = Net(state_shape=obs_space.shape or obs_space.n, hidden_sizes=[128, 128]).to(device)
    net = ObsExtractorNet(net_base).to(device)
    actor = DiscreteActor(preprocess_net=net, action_shape=action_shape).to(device)
    critic = DiscreteCritic(preprocess_net=net).to(device)
    policy = MaskedProbabilisticActorPolicy(
        actor=actor,
        dist_fn=torch.distributions.Categorical,
        action_space=env_pz.action_space,
        observation_space=env_pz.observation_space,
        action_scaling=False,
    ).to(device)
    return policy, critic


def build_policy_critic_starter(env_pz: PettingZooEnv, device: str):
    actor, critic, _torso = build_actor_critic()
    actor = actor.to(device)
    critic = critic.to(device)
    policy = MaskedProbabilisticActorPolicy(
        actor=actor,
        dist_fn=torch.distributions.Categorical,
        action_space=env_pz.action_space,
        observation_space=env_pz.observation_space,
        action_scaling=False,
    ).to(device)
    return policy, critic


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=("puente", "starter"), default="puente")
    ap.add_argument("--run-name", type=str, default="adaptador_demo")
    ap.add_argument(
        "--reward-mode",
        choices=("sparse", "dense", "curriculum"),
        default="sparse",
        help="curriculum: shaping denso -> 0 lineal en --curriculum-steps (terminal ±10 igual).",
    )
    ap.add_argument(
        "--curriculum-steps",
        type=int,
        default=150000,
        help="Pasos de entorno hasta shaping 0 (solo reward-mode=curriculum).",
    )
    ap.add_argument("--max-epochs", type=int, default=3)
    ap.add_argument("--epoch-steps", type=int, default=1000)
    ap.add_argument("--max-episode-steps", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--device",
        type=str,
        default="cuda" if torch.cuda.is_available() else "cpu",
        help="cuda o cpu (por defecto usa GPU si está disponible)",
    )
    args = ap.parse_args()
    if args.reward_mode == "curriculum" and args.curriculum_steps < 1:
        ap.error("--curriculum-steps debe ser >= 1")

    device = args.device
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise SystemExit("Pediste --device cuda pero torch.cuda.is_available() es False")

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    if device.startswith("cuda"):
        torch.cuda.manual_seed_all(args.seed)

    def thunk(i: int):
        def _():
            return PettingZooEnv(
                make_raw_env(args.env, i, args.reward_mode, args.max_episode_steps)
            )

        return _

    train_envs = DummyVectorEnv([thunk(0)])
    env_prueba = PettingZooEnv(
        make_raw_env(args.env, 99, args.reward_mode, args.max_episode_steps)
    )

    if args.env == "starter":
        policy, critic = build_policy_critic_starter(env_prueba, device)
        print(f"parametros_unicos={contar_parametros(policy.actor, critic)}")
    else:
        policy, critic = build_policy_critic_puente(env_prueba, device)
    print(f"device={device}" + (f" ({torch.cuda.get_device_name(0)})" if device.startswith("cuda") else ""))
    if args.reward_mode == "curriculum":
        print(
            f"reward curriculum: shaping denso -> 0 en {args.curriculum_steps} env steps"
        )

    ppo = PPO(
        policy=policy,
        critic=critic,
        optim=AdamOptimizerFactory(lr=3e-4),
        eps_clip=0.2,
        ent_coef=0.01,
        gamma=0.99,
    )
    manager = MultiAgentOnPolicyAlgorithm(algorithms=[ppo, ppo], env=env_prueba)
    # La obs ficha_v1 es ~124k floats: un buffer de 20k pediría ~9 GiB.
    buffer_size = 2048 if args.env == "starter" else 20000
    buffer = VectorReplayBuffer(buffer_size, len(train_envs))
    train_collector = Collector(manager, train_envs, buffer, exploration_noise=True)

    out = Path(__file__).resolve().parent / "modelos"
    out.mkdir(parents=True, exist_ok=True)
    ckpt = out / f"{args.run_name}_policy.pth"
    obs_dim = int(np.prod(env_prueba.observation_space["observation"].shape))
    act_n = int(env_prueba.action_space.n)
    print(f"entrenando env={args.env} obs_dim={obs_dim} act={act_n} reward={args.reward_mode}")

    env_step = 0
    for epoch in range(1, args.max_epochs + 1):
        if args.reward_mode == "curriculum":
            w = min(1.0, float(env_step) / float(args.curriculum_steps))
            scale = 1.0 - w
            apply_dense_reward_scale(train_envs, scale)
            apply_dense_reward_scale_petting(env_prueba, scale)
        else:
            scale = 0.0 if args.reward_mode == "sparse" else 1.0

        train_collector.reset()
        stats = train_collector.collect(n_step=args.epoch_steps)
        steps = int(getattr(stats, "n_collected_steps", args.epoch_steps) or args.epoch_steps)
        env_step += steps
        with policy_within_training_step(manager.policy):
            with torch_train_mode(manager.policy):
                result = manager.update(buffer, batch_size=64, repeat=2)
        print(
            f"epoch {epoch}/{args.max_epochs} "
            f"steps={steps} "
            f"env_step={env_step} "
            f"episodes={getattr(stats, 'n_collected_episodes', '?')} "
            f"dense_scale={scale:.3f} "
            f"update={type(result).__name__}"
        )

    save_magic_checkpoint(ckpt, policy, critic)
    try:
        data = torch.load(ckpt, map_location="cpu", weights_only=False)
    except TypeError:
        data = torch.load(ckpt, map_location="cpu")
    if isinstance(data, dict):
        data["env"] = args.env
        data["obs_dim"] = obs_dim
        data["act_n"] = act_n
        data["reward_mode"] = args.reward_mode
        if args.reward_mode == "curriculum":
            data["curriculum_steps"] = args.curriculum_steps
        if args.env == "starter":
            data["obs_layout"] = "ficha_v1"
        torch.save(data, ckpt)
    print(f"guardado {ckpt}")


if __name__ == "__main__":
    main()
