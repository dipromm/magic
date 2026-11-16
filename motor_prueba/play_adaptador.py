"""Humano vs aleatorio o checkpoint sobre el puente o el starter."""
from __future__ import annotations

import argparse

import numpy as np
import torch
from gymnasium.spaces import Discrete
from tianshou.utils.net.common import Net
from tianshou.utils.net.discrete import DiscreteActor

from env_adaptador import MagicAdaptadorEnv
from env_starter import MagicStarterEnv
from match_checkpoints import MaskedProbabilisticActorPolicy, ObsExtractorNet, act_from_obs
from red_fichas import build_actor_critic
from reglas.adaptador import MAX_ACCIONES_STARTER
from train_modes import load_policy_weights_for_inference


def _linea_permanente(ad, object_id: str) -> str:
    o = ad.partida.objetos[object_id]
    if "Creature" in o.definicion.card_types:
        base = f"{o.definicion.nombre} {ad.partida.poder(o)}/{ad.partida.resistencia(o)}"
    else:
        base = o.definicion.nombre
    marcas = []
    if o.girada:
        marcas.append("girada")
    if o.atacando:
        marcas.append("ataca")
    if o.bloqueando:
        marcas.append("bloquea")
    if o.es_ficha:
        marcas.append("ficha")
    if o.anexada_a is not None:
        ancla = ad.partida.objetos.get(o.anexada_a.object_id)
        nombre = ancla.definicion.nombre if ancla is not None else o.anexada_a.object_id
        marcas.append(f"anexa a {nombre}")
    if not marcas:
        return base
    return f"{base} ({', '.join(marcas)})"


def _linea_pila(ad, item) -> str:
    if hasattr(item, "definicion"):
        return item.definicion.nombre
    fuente = ad.partida.objetos.get(item.fuente.object_id)
    if fuente is None:
        return f"habilidad {item.habilidad}"
    return f"{fuente.definicion.nombre} (hab {item.habilidad})"


def _print(env, *, actor: str | None = None, accion: str | None = None) -> None:
    ad = env.adaptador
    quien = actor or env.agent_selection
    print(
        f"\n--- Fase: {ad.fase} | modo={ad.modo_decision} | "
        f"activo=P{ad.indice_activo} | le toca {quien} ---"
    )
    for i, j in enumerate(ad.jugadores):
        print(
            f"  P{i}: vida={j.vida} mano={len(j.zonas['Hand'])} "
            f"campo={len(j.zonas['Battlefield'])} mana={ad.maná_disponible(j)}"
        )
        campo = j.zonas["Battlefield"]
        if not campo:
            print("      (campo vacío)")
            continue
        for object_id in campo:
            print(f"      {_linea_permanente(ad, object_id)}")
    if ad.partida.pila:
        print("  pila: " + " > ".join(_linea_pila(ad, item) for item in ad.partida.pila))
    if accion is not None and actor is not None:
        print(f"  {actor} juega: {accion}")


def _build_policy_puente(act_n: int = 50):
    net_base = Net(state_shape=124, hidden_sizes=[128, 128])
    net = ObsExtractorNet(net_base)
    actor = DiscreteActor(preprocess_net=net, action_shape=act_n)
    return MaskedProbabilisticActorPolicy(
        actor=actor,
        dist_fn=torch.distributions.Categorical,
        action_space=Discrete(act_n),
        action_scaling=False,
    )


def _build_policy_starter(act_n: int = MAX_ACCIONES_STARTER):
    actor, _critic, _torso = build_actor_critic()
    return MaskedProbabilisticActorPolicy(
        actor=actor,
        dist_fn=torch.distributions.Categorical,
        action_space=Discrete(act_n),
        action_scaling=False,
    )


def load_starter_policy(ckpt: str, *, stochastic: bool = False, device: str = "cpu"):
    """Carga un checkpoint starter sin abrir el bucle humano (para humo)."""
    policy = _build_policy_starter()
    load_policy_weights_for_inference(ckpt, policy)
    policy = policy.to(device)
    policy.eval()
    policy.deterministic_eval = not stochastic
    return policy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=("puente", "starter"), default="puente")
    ap.add_argument("--human-seat", choices=("p0", "p1"), default="p0")
    ap.add_argument("--max-steps", type=int, default=500)
    ap.add_argument("--ckpt", type=str, default="", help="Checkpoint del oponente; vacío = aleatorio")
    ap.add_argument("--stochastic", action="store_true")
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Fija el barajado. Sin esto, cada partida baraja al azar.",
    )
    args = ap.parse_args()

    if args.env == "starter":
        env = MagicStarterEnv(verbose=False, max_episode_steps=args.max_steps)
        act_n = MAX_ACCIONES_STARTER
    else:
        env = MagicAdaptadorEnv(verbose=False, max_episode_steps=args.max_steps)
        act_n = 50

    policy = None
    if args.ckpt:
        if args.env == "starter":
            policy = load_starter_policy(args.ckpt, stochastic=args.stochastic)
        else:
            policy = _build_policy_puente(act_n)
            load_policy_weights_for_inference(args.ckpt, policy)
            policy.eval()
            policy.deterministic_eval = not args.stochastic

    human = "player_0" if args.human_seat == "p0" else "player_1"
    if args.seed is None:
        print("baraja aleatoria")
        env.reset()
    else:
        print(f"baraja semilla={args.seed}")
        np.random.seed(args.seed)
        torch.manual_seed(args.seed)
        env.reset(seed=args.seed)
    steps = 0
    while env.agents and steps < args.max_steps:
        agent = env.agent_selection
        if env.terminations.get(agent) or env.truncations.get(agent):
            env.step(None)
            steps += 1
            continue
        legal = env.current_legal_actions
        if agent == human:
            _print(env)
            for i, a in enumerate(legal):
                print(f"  [{i}] {a.get('descripcion')}")
            while True:
                try:
                    idx = int(input("Acción: ").strip())
                    if 0 <= idx < len(legal):
                        break
                except ValueError:
                    pass
                print("índice inválido")
        else:
            raw = env.observe(agent)
            mask = np.asarray(raw["action_mask"], dtype=np.float32)
            if policy is not None:
                idx = act_from_obs(
                    policy,
                    {"obs": raw["observation"], "mask": mask},
                )
            else:
                idx = int(np.random.choice(np.flatnonzero(mask)))
            desc = legal[idx].get("descripcion") if idx < len(legal) else str(idx)
            _print(env, actor=agent, accion=str(desc))
        env.step(idx)
        steps += 1
    vidas = [j.vida for j in env.adaptador.jugadores]
    print(f"fin vidas={vidas} terminations={env.terminations} truncations={env.truncations}")


if __name__ == "__main__":
    main()
