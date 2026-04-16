"""
Partida interactiva en consola: tú contra un checkpoint (.pth) entrenado.
Misma arquitectura y máscaras que match_checkpoints / train.

Ejemplos:
  python play_human_vs_ai.py --ckpt modelo_sparse.pth --human-seat p0
  python play_human_vs_ai.py --ckpt modelo_dense.pth --human-seat p1 --verbose-env
  python play_human_vs_ai.py --ckpt policy.pth --human-seat p0 --stochastic
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from env_magic import MagicEnv
from match_checkpoints import act_from_obs, build_policy, episode_outcome, make_magic_env
from train_modes import load_policy_weights_for_inference


def _print_situation(env) -> None:
    m = env.motor
    p0, p1 = m.players[0], m.players[1]
    active = p0.name if m.current_turn == 0 else p1.name
    print()
    print(f"--- Fase: {m.current_phase} | Turno del motor: {active} ---")
    print(f"  {p0.name}: {p0.life} vida | mano {len(p0.hand)} | mesa {len(p0.battlefield)}")
    print(f"  {p1.name}: {p1.life} vida | mano {len(p1.hand)} | mesa {len(p1.battlefield)}")


def _prompt_human_action(env) -> int:
    legal = env.current_legal_actions
    agent = env.agent_selection
    print(f"\nTe toca ({agent}). Acciones legales (mismo índice que usa la IA):")
    for i, action in enumerate(legal):
        desc = action.get("descripcion", str(action))
        print(f"  [{i}] {desc}")
    while True:
        raw = input("Número de acción: ").strip()
        try:
            idx = int(raw)
        except ValueError:
            print("Introduce un entero.")
            continue
        if 0 <= idx < len(legal):
            return idx
        print(f"Fuera de rango (0..{len(legal) - 1}).")


def _describe_ai_action(env, action_idx: int) -> str:
    legal = env.current_legal_actions
    if 0 <= action_idx < len(legal):
        return str(legal[action_idx].get("descripcion", action_idx))
    return str(action_idx)


def play_one_game(
    env,
    *,
    policy_ai,
    human_agent: str,
    max_steps: int,
    show_ai_action: bool,
) -> str | None:
    env.reset()
    ai_agent = "player_1" if human_agent == "player_0" else "player_0"
    steps = 0
    while env.agents and steps < max_steps:
        agent = env.agent_selection
        if env.terminations.get(agent) or env.truncations.get(agent):
            env.step(None)
            steps += 1
            continue

        if agent == human_agent:
            _print_situation(env)
            action_idx = _prompt_human_action(env)
        else:
            raw = env.observe(agent)
            obs = {
                "obs": raw["observation"],
                "mask": np.asarray(raw["action_mask"], dtype=np.float32),
            }
            action_idx = act_from_obs(policy_ai, obs)
            if show_ai_action:
                print(f"\n[{ai_agent} / IA] acción {action_idx}: {_describe_ai_action(env, action_idx)}")

        env.step(int(action_idx))
        steps += 1

    return episode_outcome(env)


def main() -> None:
    ap = argparse.ArgumentParser(description="Humano vs checkpoint en MagicEnv (consola).")
    ap.add_argument("--ckpt", type=str, required=True, help="Ruta al .pth del oponente IA")
    ap.add_argument(
        "--human-seat",
        type=str,
        choices=("p0", "p1"),
        default="p0",
        help="p0 = tú eres player_0; p1 = tú eres player_1",
    )
    ap.add_argument("--seed", type=int, default=None, help="Semilla del reset del entorno (opcional)")
    ap.add_argument("--max-steps", type=int, default=50_000, help="Tope de pasos por partida")
    ap.add_argument("--max-episode-steps", type=int, default=500)
    ap.add_argument("--disable-mask", action="store_true")
    ap.add_argument("--dataset-path", type=str, default="")
    ap.add_argument("--verbose-env", action="store_true", help="Mensajes del entorno (recompensas RL, etc.)")
    ap.add_argument("--stochastic", action="store_true", help="IA muestrea acciones; por defecto greedy (modo)")
    ap.add_argument(
        "--quiet-ai",
        action="store_true",
        help="No imprimir la acción elegida por la IA",
    )
    ap.add_argument(
        "--log-path",
        type=str,
        default="",
        help="Si se indica, JSONL de pasos (como en entrenamiento). Vacío = sin log en disco.",
    )
    args = ap.parse_args()

    ckpt = Path(args.ckpt)
    if not ckpt.is_file():
        raise FileNotFoundError(f"No existe el checkpoint: {ckpt}")

    masking = not args.disable_mask
    dataset = args.dataset_path or None
    log_path = args.log_path.strip() or None

    if args.seed is not None:
        torch.manual_seed(int(args.seed))
        np.random.seed(int(args.seed))

    policy = build_policy(masking)
    load_policy_weights_for_inference(ckpt, policy)
    policy.eval()
    policy.deterministic_eval = not args.stochastic

    human_agent = "player_0" if args.human_seat == "p0" else "player_1"

    if log_path is not None:
        env = make_magic_env(
            log_path=log_path,
            env_id=2000,
            masking_enabled=masking,
            max_episode_steps=args.max_episode_steps,
            dataset_path=dataset,
            reward_mode="dense",
            verbose=args.verbose_env,
        )
    else:
        env = MagicEnv(
            verbose=args.verbose_env,
            log_path=None,
            env_id=2000,
            dataset_path=dataset,
            masking_enabled=masking,
            reward_mode="dense",
            max_episode_steps=args.max_episode_steps,
        )

    winner = play_one_game(
        env,
        policy_ai=policy,
        human_agent=human_agent,
        max_steps=args.max_steps,
        show_ai_action=not args.quiet_ai,
    )

    _print_situation(env)
    print()
    if winner is None:
        print("Fin: empate técnico, truncado o sin ganador claro.")
    elif winner == human_agent:
        print("Has ganado.")
    else:
        print("Gana la IA.")


if __name__ == "__main__":
    main()
