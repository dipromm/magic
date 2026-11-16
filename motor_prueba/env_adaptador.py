"""PettingZoo 124/50 sobre reglas.Adaptador (puente de la fase 8)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
from gymnasium.spaces import Box, Dict, Discrete
from pettingzoo import AECEnv

try:
    from pettingzoo.utils.agent_selector import AgentSelector as agent_selector
except ImportError:  # pragma: no cover
    from pettingzoo.utils import agent_selector  # type: ignore

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reglas.adaptador import Adaptador, DemasiadasAcciones, mazo_prueba, mazo_starter  # noqa: E402

from logging_utils import make_logger
from observador_adaptador import ObservadorAdaptador


class MagicAdaptadorEnv(AECEnv):
    metadata = {"name": "magic_adaptador_v1", "is_parallelizable": False}

    def __init__(
        self,
        *,
        verbose: bool = False,
        log_path: Optional[str] = None,
        env_id: int = 0,
        masking_enabled: bool = True,
        reward_mode: str = "sparse",
        max_episode_steps: Optional[int] = None,
        dense_reward_scale: float = 1.0,
        modo_menu: str = "puente",
        deck_mode: str = "puente",
        max_acciones: int = 50,
        bloqueo_partido: bool = False,
    ):
        super().__init__()
        self.env_id = env_id
        self.verbose = verbose
        self.masking_enabled = masking_enabled
        self.reward_mode = reward_mode
        self.dense_reward_scale = float(dense_reward_scale)
        self.max_episode_steps = int(max_episode_steps) if max_episode_steps is not None else None
        self.modo_menu = modo_menu
        self.deck_mode = deck_mode
        self.max_acciones = max_acciones
        self.bloqueo_partido = bloqueo_partido

        self.agents = ["player_0", "player_1"]
        self.possible_agents = self.agents[:]
        self.logger = make_logger(log_path)
        self.observador = ObservadorAdaptador()
        self.adaptador = Adaptador(
            modo_menu=modo_menu,
            max_acciones=max_acciones,
            bloqueo_partido=bloqueo_partido,
        )

        self.rewards = {a: 0 for a in self.agents}
        self.terminations = {a: False for a in self.agents}
        self.truncations = {a: False for a in self.agents}
        self.infos = {a: {} for a in self.agents}
        self._agent_selector = agent_selector(self.agents)
        self.agent_selection = None
        self.current_legal_actions: list[dict[str, Any]] = []
        self.invalid_action_counts = {a: 0 for a in self.agents}
        self.episode_id = 0
        self.episode_step_count = 0
        self._episode_end_logged = False
        self._truncated_por_acciones = False

    def observation_space(self, agent):
        return Dict({
            "observation": Box(low=-np.inf, high=np.inf, shape=(124,), dtype=np.float32),
            "action_mask": Box(low=0, high=1, shape=(self.max_acciones,), dtype=np.int8),
        })

    def action_space(self, agent):
        return Discrete(self.max_acciones)

    def observe(self, agent):
        idx = 0 if agent == "player_0" else 1
        obs = np.nan_to_num(self.observador.obtener_estado(self.adaptador, idx), nan=0.0)
        mask = np.zeros((self.max_acciones,), dtype=np.int8)
        if agent != self.agent_selection:
            return {"observation": obs, "action_mask": mask}
        if not self.masking_enabled:
            mask[:] = 1
        else:
            n = min(len(self.current_legal_actions), self.max_acciones)
            mask[:n] = 1
            if not self.current_legal_actions:
                mask[0] = 1
        return {"observation": obs, "action_mask": mask}

    def _mazos(self):
        if self.deck_mode == "starter":
            return mazo_starter("hare_raising"), mazo_starter("otter_limits")
        return mazo_prueba(20, 20), mazo_prueba(20, 20)

    def reset(self, seed=None, options=None):
        if seed is not None:
            import random
            random.seed(seed)
            np.random.seed(seed)
        self.agents = self.possible_agents[:]
        self.rewards = {a: 0 for a in self.agents}
        self._cumulative_rewards = {a: 0 for a in self.agents}
        self.terminations = {a: False for a in self.agents}
        self.truncations = {a: False for a in self.agents}
        self.infos = {a: {} for a in self.agents}
        self.invalid_action_counts = {a: 0 for a in self.agents}
        self.episode_id += 1
        self.episode_step_count = 0
        self._episode_end_logged = False
        self._truncated_por_acciones = False

        self.adaptador = Adaptador(
            modo_menu=self.modo_menu,
            max_acciones=self.max_acciones,
            bloqueo_partido=self.bloqueo_partido,
        )
        d0, d1 = self._mazos()
        self.adaptador.montar(d0, d1)

        self._agent_selector.reinit(self.agents)
        self.agent_selection = self._agent_selector.reset()
        self._update_legal_actions()
        return None

    def _jugador_de_agent(self, agent: str):
        return self.adaptador.jugadores[0 if agent == "player_0" else 1]

    def _update_legal_actions(self):
        try:
            self.current_legal_actions = self.adaptador.acciones_legales()
        except DemasiadasAcciones:
            self.current_legal_actions = []
            self._truncate_por_acciones()
            return
        self.agent_selection = self.agents[self.adaptador.quien_actua()]

    def _truncate_por_acciones(self):
        self._truncated_por_acciones = True
        self.truncations = {a: True for a in self.agents}
        if not self._episode_end_logged:
            self._episode_end_logged = True
            self.logger.log({
                "type": "episode_end",
                "env_id": int(self.env_id),
                "episode_id": int(self.episode_id),
                "episode_len_steps": int(self.episode_step_count),
                "winner": None,
                "truncated": True,
                "reason": "too_many_actions",
            })

    def _dense(self, amount: float) -> float:
        # sparse: solo ±10 terminal. dense/curriculum: shaping × dense_reward_scale
        # (en curriculum la escala baja a 0 desde train_adaptador).
        if self.reward_mode == "sparse":
            return 0.0
        return self.dense_reward_scale * amount

    def step(self, action_idx):
        if self.terminations[self.agent_selection] or self.truncations[self.agent_selection]:
            self._was_dead_step(action_idx)
            return

        agent = self.agent_selection
        self.episode_step_count += 1
        invalid = int(action_idx is None or action_idx >= len(self.current_legal_actions))
        if invalid:
            self.invalid_action_counts[agent] += 1
            action_idx = 0
        if not self.current_legal_actions:
            self._truncate_por_acciones()
            return

        decision = self.current_legal_actions[action_idx]
        jugador = self._jugador_de_agent(agent)
        vida_rival_antes = self.adaptador.jugadores[1 - (0 if agent == "player_0" else 1)].vida

        self.adaptador.ejecutar_accion(jugador, decision)

        recompensa = 0.0
        tipo = decision["tipo"]
        # quedarme, mulligan y poner_fondo no suman, igual que pasar y descartar.
        if tipo in ("jugar_carta", "elegir_x", "elegir_objetivo"):
            recompensa += self._dense(0.1)
        elif tipo == "declarar_atacante":
            recompensa += self._dense(0.2)
        elif tipo in ("declarar_bloqueo", "elegir_atacante"):
            recompensa += self._dense(0.2)
        rival = self.adaptador.jugadores[1 - (0 if agent == "player_0" else 1)]
        dano = vida_rival_antes - rival.vida
        if dano > 0:
            recompensa += self._dense(dano * 0.5)

        self.rewards = {a: 0.0 for a in self.agents}
        self.rewards[agent] = float(recompensa)
        self.infos[agent]["modo"] = self.adaptador.modo_decision
        self.infos[agent]["invalid_action"] = bool(invalid)

        p0 = self.adaptador.jugadores[0].vida
        p1 = self.adaptador.jugadores[1].vida
        game_over = p0 <= 0 or p1 <= 0
        truncated = False
        if (not game_over) and self.max_episode_steps and self.episode_step_count >= self.max_episode_steps:
            truncated = True
            self.truncations = {a: True for a in self.agents}

        if game_over:
            self.terminations = {a: True for a in self.agents}
            if p0 > 0:
                self.rewards["player_0"] += 10.0
                self.rewards["player_1"] -= 10.0
                winner = "player_0"
            else:
                self.rewards["player_1"] += 10.0
                self.rewards["player_0"] -= 10.0
                winner = "player_1"
            if not self._episode_end_logged:
                self._episode_end_logged = True
                self.logger.log({
                    "type": "episode_end",
                    "env_id": int(self.env_id),
                    "episode_id": int(self.episode_id),
                    "episode_len_steps": int(self.episode_step_count),
                    "winner": winner,
                    "truncated": False,
                })
        elif truncated and not self._episode_end_logged:
            self._episode_end_logged = True
            self.logger.log({
                "type": "episode_end",
                "env_id": int(self.env_id),
                "episode_id": int(self.episode_id),
                "episode_len_steps": int(self.episode_step_count),
                "winner": None,
                "truncated": True,
            })

        self._cumulative_rewards[agent] = 0
        if not any(self.terminations.values()) and not any(self.truncations.values()):
            self._update_legal_actions()
            if self.adaptador.terminada() and not any(self.terminations.values()):
                p0 = self.adaptador.jugadores[0].vida
                p1 = self.adaptador.jugadores[1].vida
                self.terminations = {a: True for a in self.agents}
                if p0 > 0 and p1 <= 0:
                    self.rewards["player_0"] += 10.0
                    self.rewards["player_1"] -= 10.0
                elif p1 > 0 and p0 <= 0:
                    self.rewards["player_1"] += 10.0
                    self.rewards["player_0"] -= 10.0
