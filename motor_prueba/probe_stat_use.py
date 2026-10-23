"""
Comprueba si una política usa fuerza y resistencia.

Juega partidas con criaturas aleatorias y, en cada decisión con al menos dos
acciones legales, vuelve a preguntar a la red con la misma máscara y la
observación alterada:

  const2   toda criatura visible pasa a 2/2
  zero     fuerza y resistencia a 0
  shuffle  se permutan esas stats entre criaturas

El motor no ve el cambio. Un porcentaje alto significa que la jugada greedy
depende de esos números. Con --baseline se mide otra política sobre los
mismos estados.

Ejemplo:
  python probe_stat_use.py --ckpt modelos\\sparse_random_01_policy.pth --baseline modelo_magic_sparse_1.pth
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import numpy as np

from match_checkpoints import act_from_obs, build_policy, make_magic_env
from train_modes import load_policy_weights_for_inference


def creature_bases(obs: np.ndarray) -> list[int]:
    return [b for b in range(4, 124, 4) if float(obs[b]) == 2.0]


def perturb(obs: np.ndarray, mode: str, rng: np.random.RandomState) -> np.ndarray:
    x = np.array(obs, copy=True)
    bases = creature_bases(x)
    if mode == "const2":
        for b in bases:
            x[b + 1] = 2.0
            x[b + 2] = 2.0
    elif mode == "zero":
        for b in bases:
            x[b + 1] = 0.0
            x[b + 2] = 0.0
    elif mode == "shuffle" and len(bases) > 1:
        order = rng.permutation(len(bases))
        powers = [x[b + 1] for b in bases]
        toughs = [x[b + 2] for b in bases]
        for i, b in enumerate(bases):
            x[b + 1] = powers[order[i]]
            x[b + 2] = toughs[order[i]]
    return x


def load_policy(path: str):
    policy = build_policy(True)
    load_policy_weights_for_inference(path, policy)
    policy.eval()
    policy.deterministic_eval = True
    return policy


def collect(policy, seeds: list[int], max_steps: int) -> list[tuple]:
    records = []
    for seed in seeds:
        env = make_magic_env(
            log_path="logs/probe_stat_use.jsonl",
            env_id=seed,
            masking_enabled=True,
            max_episode_steps=500,
            dataset_path=None,
            reward_mode="sparse",
            verbose=False,
            deck_mode="random_stats",
        )
        env.reset(seed=seed)
        steps = 0
        while env.agents and steps < max_steps:
            agent = env.agent_selection
            if env.terminations.get(agent) or env.truncations.get(agent):
                env.step(None)
                steps += 1
                continue
            raw = env.observe(agent)
            obs = np.asarray(raw["observation"], dtype=np.float32)
            mask = np.asarray(raw["action_mask"], dtype=np.float32)
            action = act_from_obs(policy, {"obs": obs, "mask": mask})
            records.append((obs, mask, env.motor.current_phase, int(mask.sum()), action))
            env.step(int(action))
            steps += 1
        lives = f"{env.motor.players[0].life}/{env.motor.players[1].life}"
        print(f"seed {seed}: steps {steps} vidas {lives}")
    return records


def report(name: str, policy, records: list[tuple], rng: np.random.RandomState) -> None:
    print(f"=== {name}")
    for mode in ("const2", "zero", "shuffle"):
        changed = defaultdict(int)
        total = defaultdict(int)
        for obs, mask, phase, n_legal, action in records:
            if n_legal < 2:
                continue
            if mode == "shuffle" and len(creature_bases(obs)) < 2:
                continue
            alt = perturb(obs, mode, rng)
            alt_action = act_from_obs(policy, {"obs": alt, "mask": mask})
            total[phase] += 1
            total["ALL"] += 1
            if alt_action != action:
                changed[phase] += 1
                changed["ALL"] += 1
        n = total["ALL"]
        c = changed["ALL"]
        frac = (c / n) if n else float("nan")
        detail = " ".join(
            f"{phase}:{changed[phase]}/{total[phase]}"
            for phase in ("Principal", "Declarar Atacantes", "Declarar Bloqueadores")
            if total[phase]
        )
        print(f"{mode}: {c}/{n} ({frac:.3f}) [{detail}]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mide si la jugada greedy depende de fuerza y resistencia.")
    ap.add_argument("--ckpt", required=True, help="Política a probar (.pth)")
    ap.add_argument("--baseline", default="", help="Otra política, medida en los mismos estados")
    ap.add_argument("--seeds", default="7,11,19,23")
    ap.add_argument("--max-steps", type=int, default=500)
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    policy = load_policy(args.ckpt)
    print(f"Partidas con {args.ckpt}")
    records = collect(policy, seeds, args.max_steps)
    choices = sum(1 for *_, n_legal, _action in records if n_legal >= 2)
    print(f"decisiones {len(records)}; con alternativa real {choices}")
    report(args.ckpt, policy, records, np.random.RandomState(1))
    if args.baseline:
        report(args.baseline, load_policy(args.baseline), records, np.random.RandomState(1))


if __name__ == "__main__":
    main()
