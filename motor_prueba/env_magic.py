import functools
import numpy as np
from pettingzoo import AECEnv
from pettingzoo.utils import agent_selector, wrappers
from gymnasium.spaces import Box, Discrete, Dict
import random
from typing import Optional, Any

from motor import Game
from jugador import Player # Usamos Player normal porque la IA tomará el control
from entorno_rl import ObservadorRL
from logging_utils import make_logger
from deck_loader import load_archidekt_dataset, build_poc_deck_from_archidekt

class MagicEnv(AECEnv):
    """
    El entorno de Magic: The Gathering adaptado a la API AEC de PettingZoo.
    """
    metadata = {
        "name": "magic_micro_v1",
        "is_parallelizable": False # AEC es secuencial, como los turnos de Magic
    }

    def __init__(
        self,
        *,
        verbose: bool = True,
        log_path: Optional[str] = None,
        env_id: int = 0,
        dataset_path: Optional[str] = None,
        masking_enabled: bool = True,
        reward_mode: str = "dense",
        max_episode_steps: Optional[int] = None,
        dense_reward_scale: float = 1.0,
        log_anchor_roles: bool = False,
        learner_plays_p0: bool = True,
    ):
        super().__init__()
        self.env_id = env_id
        self.dataset_path = dataset_path
        self.masking_enabled = masking_enabled
        self.reward_mode = reward_mode
        self.dense_reward_scale = float(dense_reward_scale)
        self.log_anchor_roles = bool(log_anchor_roles)
        self.learner_plays_p0 = bool(learner_plays_p0)
        self.max_episode_steps = int(max_episode_steps) if max_episode_steps is not None else None
        
        # 1. Definimos los agentes (Las IAs que jugarán)
        self.agents = ["player_0", "player_1"]
        self.possible_agents = self.agents[:]
        
        # 2. Creamos nuestro motor interno
        self.logger = make_logger(log_path)
        self.motor = Game(
            Player("player_0", verbose=verbose, logger=self.logger),
            Player("player_1", verbose=verbose, logger=self.logger),
            verbose=verbose,
            logger=self.logger,
        )
        self.motor.env_id = self.env_id
        self._sync_motor_reward()
        self.observador = ObservadorRL()
        self.verbose = verbose
        
        # Espacio de acciones (Fijamos un máximo teórico de opciones por simplicidad ahora mismo)
        # 0: Pasar/Terminar. 1-20: Jugar carta mano. 21-40: Atacar/Bloquear.
        # En el futuro lo cambiaremos por un espacio dinámico, pero esto es lo estándar para empezar.
        self.max_acciones = 50 
        
        # 3. Variables obligatorias de PettingZoo
        self.rewards = {agent: 0 for agent in self.agents}
        self.terminations = {agent: False for agent in self.agents}
        self.truncations = {agent: False for agent in self.agents}
        self.infos = {agent: {} for agent in self.agents}
        
        # Esta lista de PettingZoo nos ayuda a saber a quién le toca
        self._agent_selector = agent_selector(self.agents)
        self.agent_selection = None
        
        # Guardaremos las acciones legales calculadas en cada paso
        self.current_legal_actions = []
        # Métrica simple para TFG: cuántas acciones inválidas intentó cada agente
        self.invalid_action_counts = {agent: 0 for agent in self.agents}
        # Métricas de episodio (para visualización)
        self.episode_id = 0
        self.episode_step_count = 0
        self._episode_end_logged = False

    def observation_space(self, agent):
        # PettingZoo + Tianshou soportan action masking si devolvemos un dict con:
        # {"observation": <vector>, "action_mask": <0/1 por acción>}
        return Dict(
            {
                "observation": Box(low=-np.inf, high=np.inf, shape=(124,), dtype=np.float32),
                "action_mask": Box(low=0, high=1, shape=(self.max_acciones,), dtype=np.int8),
            },
        )

    def action_space(self, agent):
        # Le damos el "plano": Un conjunto discreto de 50 opciones enteras
        return Discrete(self.max_acciones)

    def observe(self, agent):
        """Devuelve lo que 've' el agente que se le pasa por parámetro."""
        idx = 0 if agent == "player_0" else 1
        obs = self.observador.obtener_estado(self.motor, idx)

        # Solo el agente en turno debería necesitar máscara precisa.
        # Para evitar inconsistencias, si nos piden observar a otro agente,
        # devolvemos una máscara conservadora (solo permitir acción 0).
        if agent != self.agent_selection:
            mask = np.zeros((self.max_acciones,), dtype=np.int8)
            mask[0] = 1
            return {"observation": obs, "action_mask": mask}

        # Acción i es legal si i < len(current_legal_actions)
        if not self.masking_enabled:
            mask = np.ones((self.max_acciones,), dtype=np.int8)
        else:
            mask = np.zeros((self.max_acciones,), dtype=np.int8)
            legal_n = min(len(self.current_legal_actions), self.max_acciones)
            if legal_n > 0:
                mask[:legal_n] = 1
            else:
                # seguridad: al menos dejar pasar
                mask[0] = 1
        return {"observation": obs, "action_mask": mask}

    def _episode_end_identity_fields(self, winner: str | None) -> dict[str, Any]:
        """Campos extra para visualización con --anchor-checkpoint (aprendiz vs ancla por asiento)."""
        if not self.log_anchor_roles:
            return {}
        rp0 = float(self.rewards.get("player_0", 0.0) or 0.0)
        rp1 = float(self.rewards.get("player_1", 0.0) or 0.0)
        lp0 = self.learner_plays_p0
        win_l = win_a = False
        if winner == "player_0":
            win_l, win_a = (True, False) if lp0 else (False, True)
        elif winner == "player_1":
            win_l, win_a = (False, True) if lp0 else (True, False)
        return {
            "learner_plays_p0": lp0,
            "reward_learner": rp0 if lp0 else rp1,
            "reward_anchor": rp1 if lp0 else rp0,
            "win_learner": bool(win_l),
            "win_anchor": bool(win_a),
        }

    def _sync_motor_reward(self) -> None:
        rm = self.reward_mode
        if rm == "curriculum":
            self.motor.reward_mode = "curriculum"
            self.motor.dense_reward_scale = float(self.dense_reward_scale)
        elif rm == "sparse":
            self.motor.reward_mode = "sparse"
            self.motor.dense_reward_scale = 0.0
        else:
            self.motor.reward_mode = "dense"
            self.motor.dense_reward_scale = float(self.dense_reward_scale)

    def reset(self, seed=None, options=None):
        """Reinicia la partida desde cero."""
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)
        self.agents = self.possible_agents[:]
        self.rewards = {agent: 0 for agent in self.agents}
        self._cumulative_rewards = {agent: 0 for agent in self.agents}
        self.terminations = {agent: False for agent in self.agents}
        self.truncations = {agent: False for agent in self.agents}
        self.infos = {agent: {} for agent in self.agents}

        # Reiniciamos tu motor[cite: 23]
        self.motor = Game(
            Player("player_0", verbose=self.verbose, logger=self.logger),
            Player("player_1", verbose=self.verbose, logger=self.logger),
            verbose=self.verbose,
            logger=self.logger,
        )
        self.motor.env_id = self.env_id
        self._sync_motor_reward()

        # Integración mínima con `ingesta_datos/`: si hay dataset, construir decks PoC desde Archidekt
        dataset_path = None
        deck_idx = 0
        if isinstance(options, dict):
            dataset_path = options.get("dataset_path")
            deck_idx = int(options.get("deck_idx", 0) or 0)
        if dataset_path is None:
            dataset_path = self.dataset_path

        if dataset_path:
            ds = load_archidekt_dataset(dataset_path)
            deck_obj = ds[deck_idx % len(ds)]
            deck0 = build_poc_deck_from_archidekt(deck_obj)
            deck1 = build_poc_deck_from_archidekt(deck_obj)
            # Si el deck sale vacío (p.ej. no hay Lands/Creatures), caer a default
            if deck0 and deck1:
                self.motor.setup_game(deck_p0=deck0, deck_p1=deck1)
            else:
                self.motor.setup_game()
        else:
            self.motor.setup_game()
        
        # Robamos la carta del primer turno
        self.motor.players[0].draw_card()

        # Le decimos a PettingZoo a quién le toca empezar
        self._agent_selector.reinit(self.agents)
        self.agent_selection = self._agent_selector.reset()
        
        self._update_legal_actions()
        # reset métrica
        self.invalid_action_counts = {agent: 0 for agent in self.agents}
        self.episode_id += 1
        self.episode_step_count = 0
        self._episode_end_logged = False
        for a in self.agents:
            self.infos[a]["invalid_action_count"] = 0

    def _update_legal_actions(self):
        """Actualiza la lista interna de acciones legales para el jugador activo."""
        idx = 0 if self.agent_selection == "player_0" else 1
        jugador_activo = self.motor.players[idx]
        # Usamos tu función get_legal_actions[cite: 23]
        self.current_legal_actions = self.motor.get_legal_actions(jugador_activo)

    def step(self, action_idx):
        """
        La IA ejecutará este paso pasándole un número (ej: acción 0, acción 3).
        """
        # 1. Comprobaciones de seguridad obligatorias de PettingZoo
        if (
            self.terminations[self.agent_selection]
            or self.truncations[self.agent_selection]
        ):
            self._was_dead_step(action_idx)
            return

        agent = self.agent_selection
        idx = 0 if agent == "player_0" else 1
        jugador_actual = self.motor.players[idx]

        # Contamos pasos del episodio (cada llamada a step de PettingZoo cuenta).
        self.episode_step_count += 1

        # 2. Traducir el número de la IA a una acción real de nuestro juego
        # Si la IA escoge un número inválido o mayor a las opciones, forzamos que sea "0" (Pasar)
        invalid = int(action_idx >= len(self.current_legal_actions))
        if invalid:
            self.invalid_action_counts[agent] += 1
            self.infos[agent]["invalid_action"] = True
            self.infos[agent]["invalid_action_count"] = self.invalid_action_counts[agent]
            action_idx = 0
        else:
            self.infos[agent]["invalid_action"] = False
            self.infos[agent]["invalid_action_count"] = self.invalid_action_counts[agent]
            
        decision = self.current_legal_actions[action_idx]

        # 3. Ejecutar la acción en TU motor y recoger la recompensa[cite: 23]
        recompensa, turno_terminado = self.motor.execute_action(jugador_actual, decision)
        
        if recompensa > 0:
            # Evitamos emojis en consola (Windows cp1252).
            if self.verbose:
                print(f"   [SISTEMA RL] {agent} acaba de ganar +{recompensa} puntos!")

        self.logger.log(
            {
                "type": "step",
                "env_id": int(self.env_id),
                "agent": agent,
                "action_idx": int(action_idx),
                "invalid_action": bool(invalid),
                "invalid_action_count": int(self.invalid_action_counts[agent]),
                "reward": float(recompensa),
                "phase": self.motor.current_phase,
                "current_turn": int(self.motor.current_turn),
                "legal_actions_n": int(len(self.current_legal_actions)),
            },
        )

        # Asignamos la recompensa obtenida al agente actual
        self.rewards[agent] = recompensa

        # 4. Comprobar si alguien ha muerto (Fin de partida)
        p0_life = self.motor.players[0].life
        p1_life = self.motor.players[1].life
        
        game_over = (p0_life <= 0) or (p1_life <= 0)
        truncated = False
        if (not game_over) and (self.max_episode_steps is not None) and (self.episode_step_count >= self.max_episode_steps):
            truncated = True
            self.truncations = {a: True for a in self.agents}

        if game_over:
            self.terminations = {a: True for a in self.agents}
            # Recompensa final (Gana = +10, Pierde = -10)
            if p0_life > 0:
                self.rewards["player_0"] += 10.0
                self.rewards["player_1"] -= 10.0
            elif p1_life > 0:
                self.rewards["player_1"] += 10.0
                self.rewards["player_0"] -= 10.0

            # Log de final de episodio para visualización de winrate/longitud.
            if not self._episode_end_logged:
                self._episode_end_logged = True
                winner = "player_0" if p0_life > 0 else "player_1"
                win0 = bool(winner == "player_0")
                win1 = bool(winner == "player_1")
                row = {
                    "type": "episode_end",
                    "env_id": int(self.env_id),
                    "episode_id": int(self.episode_id),
                    "episode_len_steps": int(self.episode_step_count),
                    "winner": winner,
                    "win0": win0,
                    "win1": win1,
                    "win": win0,
                    "truncated": False,
                    "reward_player_0": float(self.rewards.get("player_0", 0.0) or 0.0),
                    "reward_player_1": float(self.rewards.get("player_1", 0.0) or 0.0),
                }
                row.update(self._episode_end_identity_fields(winner))
                self.logger.log(row)

        elif truncated:
            # Truncation: forzamos fin de episodio para tener métricas aunque la partida sea muy larga.
            if not self._episode_end_logged:
                self._episode_end_logged = True
                if p0_life == p1_life:
                    winner = None
                    win0 = False
                    win1 = False
                else:
                    winner = "player_0" if p0_life > p1_life else "player_1"
                    win0 = bool(winner == "player_0")
                    win1 = bool(winner == "player_1")
                row = {
                    "type": "episode_end",
                    "env_id": int(self.env_id),
                    "episode_id": int(self.episode_id),
                    "episode_len_steps": int(self.episode_step_count),
                    "winner": winner,
                    "win0": bool(win0),
                    "win1": bool(win1),
                    "win": bool(win0),
                    "truncated": True,
                    "p0_life": float(p0_life),
                    "p1_life": float(p1_life),
                    "reward_player_0": float(self.rewards.get("player_0", 0.0) or 0.0),
                    "reward_player_1": float(self.rewards.get("player_1", 0.0) or 0.0),
                }
                row.update(self._episode_end_identity_fields(winner))
                self.logger.log(row)
                
        # 5. Ceder el turno o actualizar opciones
        self._cumulative_rewards[agent] = 0 # Requisito interno de PZ
        
        # ¿A quién le toca ahora?
        # Si estamos en fase de bloqueadores, le toca al defensor
        if self.motor.current_phase == "Declarar Bloqueadores":
            siguiente_idx = 1 - self.motor.current_turn
        else:
            siguiente_idx = self.motor.current_turn
            
        self.agent_selection = self.agents[siguiente_idx]
        
        # Actualizamos las opciones legales para el que le toque ahora
        if not any(self.terminations.values()):
            self._update_legal_actions()