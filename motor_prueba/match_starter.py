"""Duelos del starter (ficha_v1), sin entrenar.

Política contra política, o política contra aleatorio legal.
Con --swap (activo por defecto) cada partida impar cambia quién sale como player_0.
El winrate es victorias / partidas con un solo jugador a 0 vidas.
Truncado, tope de pasos o doble muerte no suman victoria.
Sin --seed cada partida baraja al azar. --seed N repite la tanda.

Ejemplos, desde motor_prueba:
  python match_starter.py --a modelos/starter_c0_policy.pth --b random --games 20
  python match_starter.py --a modelos/starter_c0_policy.pth --b modelos/starter_s0_policy.pth --games 20
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from env_starter import MagicStarterEnv
from match_checkpoints import act_from_obs
from play_adaptador import load_starter_policy


def episode_outcome(env: MagicStarterEnv) -> str | None:
    p0 = env.adaptador.jugadores[0].vida
    p1 = env.adaptador.jugadores[1].vida
    if p0 > 0 and p1 > 0:
        return None
    if p0 <= 0 and p1 <= 0:
        return None
    return "player_0" if p0 > 0 else "player_1"


def _accion_aleatoria(mask: np.ndarray, rng: np.random.RandomState) -> int:
    legales = np.flatnonzero(mask > 0)
    if legales.size == 0:
        return 0
    return int(rng.choice(legales))


def play_episode(
    env: MagicStarterEnv,
    policy_p0,
    policy_p1,
    max_steps: int,
    rng: np.random.RandomState,
    seed: int | None = None,
) -> tuple[str | None, int]:
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
        mask = np.asarray(raw["action_mask"], dtype=np.float32)
        policy = policy_p0 if agent == "player_0" else policy_p1
        if policy is None:
            action = _accion_aleatoria(mask, rng)
        else:
            action = act_from_obs(policy, {"obs": raw["observation"], "mask": mask})
        env.step(int(action))
        steps += 1
    return episode_outcome(env), steps


def _etiqueta(spec: str) -> str:
    if spec == "random":
        return "aleatorio"
    return Path(spec).stem


def _cargar(spec: str, device: str, stochastic: bool):
    if spec == "random":
        return None
    path = Path(spec)
    if not path.is_file():
        raise FileNotFoundError(f"No existe el checkpoint: {spec}")
    return load_starter_policy(str(path), stochastic=stochastic, device=device)


def main() -> None:
    ap = argparse.ArgumentParser(description="Duelos del starter: política vs política o vs aleatorio.")
    ap.add_argument("--a", required=True, help="Checkpoint A (.pth)")
    ap.add_argument("--b", required=True, help="Checkpoint B (.pth) o random")
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Fija el barajado de la tanda. Sin esto, cada partida baraja al azar.",
    )
    ap.add_argument(
        "--swap",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Alternar asiento en las partidas impares (por defecto sí)",
    )
    ap.add_argument("--stochastic", action="store_true", help="Muestrear; por defecto greedy")
    ap.add_argument("--max-steps", type=int, default=50_000, help="Tope del bucle por partida")
    ap.add_argument("--max-episode-steps", type=int, default=500)
    ap.add_argument(
        "--device",
        default="cuda" if torch.cuda.is_available() else "cpu",
    )
    ap.add_argument("--log", type=str, default="", help="JSONL de partidas; vacío = logs/match_starter_<a>_vs_<b>.jsonl")
    args = ap.parse_args()

    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise SystemExit("Pediste --device cuda pero torch.cuda.is_available() es False")
    if args.games < 1:
        raise SystemExit("--games debe ser >= 1")

    if args.seed is None:
        rng = np.random.RandomState()
    else:
        rng = np.random.RandomState(args.seed)
        torch.manual_seed(args.seed)
        if args.device.startswith("cuda"):
            torch.cuda.manual_seed_all(args.seed)

    label_a = _etiqueta(args.a)
    label_b = _etiqueta(args.b)
    policy_a = _cargar(args.a, args.device, args.stochastic)
    policy_b = _cargar(args.b, args.device, args.stochastic)

    log_path = Path(args.log) if args.log else Path("logs") / f"match_starter_{label_a}_vs_{label_b}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    wins_a_as_p0 = wins_a_as_p1 = 0
    wins_b_as_p0 = wins_b_as_p1 = 0
    draws = 0
    steps_sum = 0

    with log_path.open("w", encoding="utf-8") as log:
        for g in range(args.games):
            swap = bool(args.swap) and (g % 2 == 1)
            p0_pol, p1_pol = (policy_b, policy_a) if swap else (policy_a, policy_b)
            env = MagicStarterEnv(
                verbose=False,
                log_path=None,
                env_id=100 + g,
                reward_mode="sparse",
                max_episode_steps=args.max_episode_steps,
                masking_enabled=True,
            )
            ep_seed = None if args.seed is None else int(rng.randint(0, 2**31 - 1))
            winner, steps = play_episode(
                env, p0_pol, p1_pol, max_steps=args.max_steps, rng=rng, seed=ep_seed
            )
            steps_sum += steps
            vidas = [j.vida for j in env.adaptador.jugadores]
            a_is_p0 = not swap
            record = {
                "game": g + 1,
                "swap": swap,
                "seed": ep_seed,
                "steps": steps,
                "vidas": vidas,
                "winner_seat": winner,
                "a": label_a,
                "b": label_b,
            }

            if winner is None:
                draws += 1
                record["winner"] = None
                print(
                    f"game {g + 1}/{args.games}: empate/trunc (swap={swap}) "
                    f"steps={steps} vidas={vidas}",
                    flush=True,
                )
            elif winner == "player_0":
                if a_is_p0:
                    wins_a_as_p0 += 1
                    record["winner"] = label_a
                    print(f"game {g + 1}/{args.games}: win {label_a} (P0) steps={steps} vidas={vidas}", flush=True)
                else:
                    wins_b_as_p0 += 1
                    record["winner"] = label_b
                    print(f"game {g + 1}/{args.games}: win {label_b} (P0) steps={steps} vidas={vidas}", flush=True)
            else:
                if a_is_p0:
                    wins_b_as_p1 += 1
                    record["winner"] = label_b
                    print(f"game {g + 1}/{args.games}: win {label_b} (P1) steps={steps} vidas={vidas}", flush=True)
                else:
                    wins_a_as_p1 += 1
                    record["winner"] = label_a
                    print(f"game {g + 1}/{args.games}: win {label_a} (P1) steps={steps} vidas={vidas}", flush=True)
            log.write(json.dumps(record, ensure_ascii=False) + "\n")

        wins_a = wins_a_as_p0 + wins_a_as_p1
        wins_b = wins_b_as_p0 + wins_b_as_p1
        decisive = args.games - draws
        summary = {
            "type": "summary",
            "a": label_a,
            "b": label_b,
            "games": args.games,
            "seed": args.seed,
            "swap": bool(args.swap),
            "stochastic": bool(args.stochastic),
            "max_episode_steps": args.max_episode_steps,
            "device": args.device,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "draws": draws,
            "wins_a_as_p0": wins_a_as_p0,
            "wins_a_as_p1": wins_a_as_p1,
            "wins_b_as_p0": wins_b_as_p0,
            "wins_b_as_p1": wins_b_as_p1,
            "winrate_a_decisive": (wins_a / decisive) if decisive else None,
            "mean_steps": steps_sum / args.games,
        }
        log.write(json.dumps(summary, ensure_ascii=False) + "\n")

    print("\n--- Resumen ---", flush=True)
    print(f"{label_a} vs {label_b}", flush=True)
    print(
        f"partidas={args.games} semilla={'aleatoria' if args.seed is None else args.seed} "
        f"swap={bool(args.swap)} "
        f"max_episode_steps={args.max_episode_steps} device={args.device}",
        flush=True,
    )
    print(f"{label_a} victorias: {wins_a} (P0 {wins_a_as_p0}, P1 {wins_a_as_p1})", flush=True)
    print(f"{label_b} victorias: {wins_b} (P0 {wins_b_as_p0}, P1 {wins_b_as_p1})", flush=True)
    print(f"Empates/truncados sin ganador: {draws} / {args.games}", flush=True)
    print(f"Pasos medios: {steps_sum / args.games:.1f}", flush=True)
    if decisive:
        print(
            f"Win rate {label_a} (sobre partidas con ganador): {wins_a / decisive:.3f}",
            flush=True,
        )
        print(
            f"Win rate {label_b} (sobre partidas con ganador): {wins_b / decisive:.3f}",
            flush=True,
        )
    print(f"log: {log_path}", flush=True)


if __name__ == "__main__":
    main()
