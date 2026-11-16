"""Probe: ¿la política greedy del puente cambia si se alteran fuerza/resistencia?"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from env_adaptador import MagicAdaptadorEnv
from match_checkpoints import act_from_obs, build_policy
from train_modes import load_policy_weights_for_inference


def creature_bases(obs: np.ndarray) -> list[int]:
    return [b for b in range(4, 124, 4) if float(obs[b]) == 2.0]


def perturb(obs: np.ndarray, mode: str) -> np.ndarray:
    x = np.array(obs, copy=True)
    for b in creature_bases(x):
        if mode == "zero":
            x[b + 1] = 0.0
            x[b + 2] = 0.0
        elif mode == "const2":
            x[b + 1] = 2.0
            x[b + 2] = 2.0
    return x


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", type=str, default="")
    ap.add_argument("--games", type=int, default=5)
    ap.add_argument("--max-steps", type=int, default=80)
    args = ap.parse_args()

    policy = None
    if args.ckpt:
        policy = build_policy(True)
        load_policy_weights_for_inference(args.ckpt, policy)
        policy.eval()
        policy.deterministic_eval = True

    cambios = 0
    total = 0
    for g in range(args.games):
        env = MagicAdaptadorEnv(verbose=False, reward_mode="sparse", max_episode_steps=args.max_steps)
        env.reset(seed=g)
        steps = 0
        while env.agents and steps < args.max_steps:
            agent = env.agent_selection
            if env.terminations.get(agent) or env.truncations.get(agent):
                env.step(None)
                steps += 1
                continue
            raw = env.observe(agent)
            obs = np.asarray(raw["observation"], dtype=np.float32)
            mask = np.asarray(raw["action_mask"], dtype=np.float32)
            legales = int(mask.sum())
            if legales >= 2 and creature_bases(obs):
                if policy is not None:
                    a0 = act_from_obs(policy, {"obs": obs, "mask": mask})
                    a1 = act_from_obs(policy, {"obs": perturb(obs, "zero"), "mask": mask})
                else:
                    # Sin checkpoint: la máscara legal no cambia al perturbar obs;
                    # comprobamos que el probe al menos ve criaturas.
                    a0, a1 = 0, 0
                total += 1
                if a0 != a1:
                    cambios += 1
            idx = int(np.flatnonzero(mask)[0])
            if policy is not None and legales >= 1:
                idx = act_from_obs(policy, {"obs": obs, "mask": mask})
            env.step(idx)
            steps += 1
    rate = (cambios / total) if total else 0.0
    print(f"decisiones_con_criaturas={total} cambios_greedy={cambios} tasa={rate:.3f}")


if __name__ == "__main__":
    main()
