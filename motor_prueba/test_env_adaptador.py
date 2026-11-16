"""Humo: puente 124/50 y starter con fichas de ontología."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from env_adaptador import MagicAdaptadorEnv
from env_starter import MagicStarterEnv
from ficha_rl import (
    ACTION_BLOCK_DIM,
    EFFECTS,
    FICHA_DIM,
    GLOBAL_DIM,
    HEADER_DIM,
    IDX_CREATE_TOKEN,
    N_ACTIONS,
    N_SLOTS,
    OBS_DIM,
    OFFSET_KEYWORDS,
    IDX_REACH,
    construir_slots,
    encode_definicion,
)
from red_fichas import build_actor_critic
from reglas.adaptador import MAX_ACCIONES_STARTER, Adaptador
from reglas.compilador import catalogo, criatura


def _jugar(env, steps: int = 40) -> None:
    env.reset(seed=0)
    for _ in range(steps):
        if not env.agents:
            break
        agent = env.agent_selection
        if env.terminations.get(agent) or env.truncations.get(agent):
            env.step(None)
            continue
        raw = env.observe(agent)
        mask = np.asarray(raw["action_mask"])
        legales = np.flatnonzero(mask)
        assert legales.size > 0
        env.step(int(legales[0]))


def test_puente_obs_124() -> None:
    env = MagicAdaptadorEnv(verbose=False, reward_mode="sparse", max_episode_steps=100)
    env.reset(seed=1)
    raw = env.observe(env.agent_selection)
    assert raw["observation"].shape == (124,)
    assert raw["action_mask"].shape == (50,)
    _jugar(env, 30)
    print("ok test_puente_obs_124")


def test_starter_fichas() -> None:
    env = MagicStarterEnv(verbose=False, max_episode_steps=80)
    env.reset(seed=2)
    raw = env.observe(env.agent_selection)
    assert raw["observation"].shape == (OBS_DIM,), raw["observation"].shape
    assert raw["action_mask"].shape == (MAX_ACCIONES_STARTER,)
    _jugar(env, 40)
    print("ok test_starter_fichas")


def test_hop_create_token() -> None:
    cat = catalogo(_ROOT / "mazos" / "bloomburrow_jsonld")
    hop = cat["Hop to It"]
    vec = encode_definicion(hop)
    # primera habilidad, primer efecto
    hab_header = 4 + 8 + 7 + 7
    eff0 = HEADER_DIM + hab_header
    assert vec[eff0 + IDX_CREATE_TOKEN] == 1.0
    amount = vec[eff0 + len(EFFECTS)]
    assert abs(amount - 0.3) < 1e-5  # 3 / 10
    print("ok test_hop_create_token")


def test_greenshell_reach_y_condicion() -> None:
    cat = catalogo(_ROOT / "mazos" / "bloomburrow_jsonld")
    fg = cat["Fecund Greenshell"]
    vec = encode_definicion(fg)
    assert vec[OFFSET_KEYWORDS + IDX_REACH] == 1.0
    # primera habilidad estática, primer efecto ModifyPower: condición activa
    from ficha_rl import EFFECT_DIM, CONDITION_DIM, TARGET_DIM

    hab_header = 4 + 8 + 7 + 7
    eff0 = HEADER_DIM + hab_header
    # layout efecto: effects + amount + classes + durations + optional + dest_zone + target + condition
    cond_off = (
        eff0
        + len(EFFECTS)
        + 1
        + 5
        + 6
        + 1
        + 7
        + TARGET_DIM
    )
    assert vec[cond_off] == 1.0  # condición activa
    print("ok test_greenshell_reach_y_condicion")


def test_accion_apunta_hueco() -> None:
    env = MagicStarterEnv(verbose=False, max_episode_steps=40)
    env.reset(seed=3)
    agent = env.agent_selection
    raw = env.observe(agent)
    obs = raw["observation"]
    slots, mapa = construir_slots(env.adaptador, 0 if agent == "player_0" else 1)
    legales = env.current_legal_actions
    base = N_SLOTS * FICHA_DIM + GLOBAL_DIM
    for i, a in enumerate(legales[:N_ACTIONS]):
        if a.get("tipo") != "jugar_carta":
            continue
        oid = a["carta_id"]
        block = obs[base + i * ACTION_BLOCK_DIM: base + (i + 1) * ACTION_BLOCK_DIM]
        idx = int(block[-2])
        assert oid in mapa
        assert idx == mapa[oid]
        print("ok test_accion_apunta_hueco")
        return
    # si en seed 3 no hay jugar_carta, forzar con mano
    print("ok test_accion_apunta_hueco (sin jugar_carta en el paso; slots ok)")
    assert len(slots) == N_SLOTS


def test_forward_red() -> None:
    actor, critic, _ = build_actor_critic()
    obs = torch.zeros(1, OBS_DIM)
    probs, _ = actor(obs)
    v = critic(obs)
    assert probs.shape == (1, 16)
    assert torch.isfinite(probs).all()
    assert torch.isfinite(v).all()
    print("ok test_forward_red")


def test_hueco_vacio_cero() -> None:
    env = MagicStarterEnv(verbose=False, max_episode_steps=10)
    env.reset(seed=0)
    raw = env.observe(env.agent_selection)
    empty = np.zeros(FICHA_DIM, dtype=np.float32)
    exile_start = 10 + 10 + 10 + 8 + 8
    for i in range(exile_start, N_SLOTS):
        chunk = raw["observation"][i * FICHA_DIM:(i + 1) * FICHA_DIM]
        assert chunk.shape == (FICHA_DIM,)
        if np.allclose(chunk, empty):
            print("ok test_hueco_vacio_cero")
            return
    raise AssertionError("se esperaba al menos un hueco de exilio vacío al inicio")


def test_vigilance_no_gira() -> None:
    ad = Adaptador(modo_menu="puente")
    ad.montar([], [])
    j0 = ad.jugadores[0]
    vigilante = ad.partida.instanciar(
        criatura("Vigia", 2, 2, keywords=("Vigilance",), mana_cost="{2}"),
        j0.id,
        "Battlefield",
    )
    vigilante.entro_este_turno = False
    ad.fase = "Declarar Atacantes"
    ad.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": vigilante.object_id})
    assert vigilante.atacando
    assert not vigilante.girada
    print("ok test_vigilance_no_gira")


def test_trample_pasa_dano() -> None:
    ad = Adaptador(modo_menu="puente")
    ad.montar([], [])
    j0, j1 = ad.jugadores
    atq = ad.partida.instanciar(
        criatura("Trompa", 5, 5, keywords=("Trample",)), j0.id, "Battlefield",
    )
    blk = ad.partida.instanciar(criatura("Muro", 1, 2), j1.id, "Battlefield")
    ad.atacantes = [atq.object_id]
    ad.bloqueos = {blk.object_id: atq.object_id}
    vida = j1.vida
    ad._resolver_combate()
    assert j1.vida == vida - 3
    print("ok test_trample_pasa_dano")


def main() -> None:
    test_puente_obs_124()
    test_starter_fichas()
    test_hop_create_token()
    test_greenshell_reach_y_condicion()
    test_accion_apunta_hueco()
    test_forward_red()
    test_hueco_vacio_cero()
    test_vigilance_no_gira()
    test_trample_pasa_dano()
    print("ok")


if __name__ == "__main__":
    main()
