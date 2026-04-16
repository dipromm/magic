"""
Enfrentar dos checkpoints (misma arquitectura) en MagicEnv sin entrenar.
player_0 usa --p0-ckpt, player_1 usa --p1-ckpt.

No importa train.py (evita side-effects del filtro de stdout).

Ejemplo:
  python match_checkpoints.py --p0-ckpt modelo_dense.pth --p1-ckpt modelo_sparse.pth --games 50 --swap
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
from tianshou.data import Batch
from tianshou.utils.net.common import Net
from tianshou.utils.net.discrete import DiscreteActor
from tianshou.algorithm.modelfree.reinforce import ProbabilisticActorPolicy

from env_magic import MagicEnv
from train_modes import load_policy_weights_for_inference


class ObsExtractorNet(torch.nn.Module):
    def __init__(self, net: Net) -> None:
        super().__init__()
        self.net = net
        self.output_dim = net.get_output_dim()

    def get_output_dim(self) -> int:
        return self.output_dim

    def forward(self, obs, state=None, info=None):
        if hasattr(obs, "obs"):
            inner_obs = getattr(obs, "obs")
            if not isinstance(inner_obs, str):
                obs = inner_obs
        return self.net(obs, state=state, info=info)


class MaskedProbabilisticActorPolicy(ProbabilisticActorPolicy):
    def forward(self, batch, state=None):
        action_dist_input, hidden = self.actor(batch.obs, state=state, info=batch.info)
        mask = getattr(batch.obs, "mask", None)
        if mask is not None:
            mask_t = torch.as_tensor(mask, device=action_dist_input.device, dtype=action_dist_input.dtype)
            probs = action_dist_input
            probs = probs * mask_t
            probs_sum = probs.sum(dim=-1, keepdim=True)
            safe = probs_sum > 0
            if not bool(safe.all()):
                uniform = mask_t / (mask_t.sum(dim=-1, keepdim=True) + 1e-8)
                probs = torch.where(safe, probs / (probs_sum + 1e-8), uniform)
            else:
                probs = probs / (probs_sum + 1e-8)
            action_dist_input = probs
        dist = self.dist_fn(action_dist_input)
        act = dist.mode if self.deterministic_eval and not self.is_within_training_step else dist.sample()
        return Batch(logits=action_dist_input, act=act, state=hidden, dist=dist)


def make_magic_env(
    *,
    log_path: str,
    env_id: int,
    masking_enabled: bool,
    max_episode_steps: int | None,
    dataset_path: str | None,
    reward_mode: str,
    verbose: bool,
) -> MagicEnv:
    return MagicEnv(
        verbose=verbose,
        log_path=log_path,
        env_id=env_id,
        dataset_path=dataset_path,
        masking_enabled=masking_enabled,
        reward_mode=reward_mode,
        max_episode_steps=max_episode_steps,
    )


def build_policy(masking_enabled: bool) -> ProbabilisticActorPolicy:
    env0 = make_magic_env(
        log_path="logs/match_build_dummy.jsonl",
        env_id=0,
        masking_enabled=masking_enabled,
        max_episode_steps=500,
        dataset_path=None,
        reward_mode="dense",
        verbose=False,
    )
    obs_space = env0.observation_space("player_0")["observation"]
    act_sp = env0.action_space("player_0")
    net_base = Net(state_shape=obs_space.shape or obs_space.n, hidden_sizes=[128, 128]).to("cpu")
    net = ObsExtractorNet(net_base).to("cpu")
    action_shape = act_sp.shape or act_sp.n
    actor = DiscreteActor(preprocess_net=net, action_shape=action_shape).to("cpu")
    policy_cls = MaskedProbabilisticActorPolicy if masking_enabled else ProbabilisticActorPolicy
    policy = policy_cls(
        actor=actor,
        dist_fn=torch.distributions.Categorical,
        action_space=act_sp,
        observation_space=env0.observation_space("player_0"),
        action_scaling=False,
    )
    return policy


def act_from_obs(policy: ProbabilisticActorPolicy, obs: dict) -> int:
    o = torch.as_tensor(np.asarray(obs["obs"], dtype=np.float32)).unsqueeze(0)
    m = torch.as_tensor(np.asarray(obs["mask"], dtype=np.float32)).unsqueeze(0)
    batch = Batch(obs=Batch(obs=o, mask=m), info=Batch())
    with torch.no_grad():
        out = policy(batch)
    return int(out.act[0].item())


def episode_outcome(env: MagicEnv) -> str | None:
    p0 = env.motor.players[0].life
    p1 = env.motor.players[1].life
    if p0 > 0 and p1 > 0:
        return None
    if p0 <= 0 and p1 <= 0:
        return None
    return "player_0" if p0 > p1 else "player_1"


def play_episode(
    env: MagicEnv,
    policy_p0: ProbabilisticActorPolicy,
    policy_p1: ProbabilisticActorPolicy,
    max_steps: int,
    seed: int | None = None,
) -> str | None:
    if seed is not None:
        env.reset(seed=seed)
    else:
        env.reset()
    steps = 0
    while env.agents and steps < max_steps:
        agent = env.agent_selection
        if env.terminations.get(agent) or env.truncations.get(agent):
            env.step(None)
            steps += 1
            continue
        raw = env.observe(agent)
        obs = {
            "obs": raw["observation"],
            "mask": np.asarray(raw["action_mask"], dtype=np.float32),
        }
        pol = policy_p0 if agent == "player_0" else policy_p1
        a = act_from_obs(pol, obs)
        env.step(int(a))
        steps += 1
    return episode_outcome(env)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--p0-ckpt", type=str, required=True, help="Checkpoint para player_0 (.pth)")
    ap.add_argument("--p1-ckpt", type=str, required=True, help="Checkpoint para player_1 (.pth)")
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--swap", action="store_true", help="Alternar qué checkpoint juega player_0 cada partida")
    ap.add_argument("--stochastic", action="store_true", help="Muestrear acciones (exploración); por defecto greedy (modo)")
    ap.add_argument("--max-steps", type=int, default=50_000, help="Tope de pasos por partida")
    ap.add_argument("--max-episode-steps", type=int, default=500)
    ap.add_argument("--disable-mask", action="store_true")
    ap.add_argument("--dataset-path", type=str, default="")
    ap.add_argument("--verbose-env", action="store_true")
    args = ap.parse_args()

    masking = not args.disable_mask
    dataset = args.dataset_path or None
    rng = np.random.RandomState(args.seed)
    torch.manual_seed(args.seed)

    policy_a = build_policy(masking)
    policy_b = build_policy(masking)
    for p in (args.p0_ckpt, args.p1_ckpt):
        if not Path(p).is_file():
            raise FileNotFoundError(f"No existe el checkpoint: {p}")
    load_policy_weights_for_inference(args.p0_ckpt, policy_a)
    load_policy_weights_for_inference(args.p1_ckpt, policy_b)
    for p in (policy_a, policy_b):
        p.eval()
        p.deterministic_eval = not args.stochastic

    Path("logs").mkdir(parents=True, exist_ok=True)
    log = Path("logs") / "match_checkpoints.jsonl"
    label_a = Path(args.p0_ckpt).stem
    label_b = Path(args.p1_ckpt).stem

    wins_a_as_p0 = 0
    wins_b_as_p0 = 0
    wins_a_as_p1 = 0
    wins_b_as_p1 = 0
    draws = 0

    for g in range(args.games):
        swap = args.swap and (g % 2 == 1)
        p0_pol, p1_pol = (policy_b, policy_a) if swap else (policy_a, policy_b)

        env = make_magic_env(
            log_path=str(log),
            env_id=100 + g,
            masking_enabled=masking,
            max_episode_steps=args.max_episode_steps,
            dataset_path=dataset,
            reward_mode="dense",
            verbose=args.verbose_env,
        )
        ep_seed = int(rng.randint(0, 2**31 - 1))
        winner = play_episode(
            env, p0_pol, p1_pol, max_steps=args.max_steps, seed=ep_seed
        )

        a_is_p0 = not swap

        if winner is None:
            draws += 1
            print(f"game {g+1}/{args.games}: draw/trunc (swap={swap})")
            continue

        if winner == "player_0":
            if a_is_p0:
                wins_a_as_p0 += 1
                print(f"game {g+1}/{args.games}: win {label_a} (P0)")
            else:
                wins_b_as_p0 += 1
                print(f"game {g+1}/{args.games}: win {label_b} (P0)")
        else:
            if a_is_p0:
                wins_b_as_p1 += 1
                print(f"game {g+1}/{args.games}: win {label_b} (P1)")
            else:
                wins_a_as_p1 += 1
                print(f"game {g+1}/{args.games}: win {label_a} (P1)")

    total_decisive = args.games - draws
    wins_a = wins_a_as_p0 + wins_a_as_p1
    wins_b = wins_b_as_p0 + wins_b_as_p1

    print("\n--- Resumen ---")
    print(f"{label_a} victorias: {wins_a}")
    print(f"{label_b} victorias: {wins_b}")
    print(f"Empates/truncados sin ganador: {draws} / {args.games}")
    if total_decisive > 0:
        print(f"Win rate {label_a} (sobre partidas con ganador): {wins_a / total_decisive:.3f}")
    if args.swap:
        print(f"  como P0: {label_a} {wins_a_as_p0}W, {label_b} {wins_b_as_p0}W | como P1: {label_a} {wins_a_as_p1}W, {label_b} {wins_b_as_p1}W")


if __name__ == "__main__":
    main()
