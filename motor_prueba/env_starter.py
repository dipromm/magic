"""Entorno starter: observación por fichas de ontología + Discrete(MAX_ACCIONES_STARTER)."""
from __future__ import annotations

import sys
from pathlib import Path

from gymnasium.spaces import Box, Dict, Discrete
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reglas.adaptador import MAX_ACCIONES_STARTER

from env_adaptador import MagicAdaptadorEnv
from ficha_rl import OBS_DIM, ObservadorOntologia


class MagicStarterEnv(MagicAdaptadorEnv):
    """Hare Raising vs Otter Limits con ficha de ontología (obs_layout=ficha_v1)."""

    metadata = {"name": "magic_starter_v1", "is_parallelizable": False}

    def __init__(self, **kwargs):
        kwargs.setdefault("modo_menu", "completo")
        kwargs.setdefault("deck_mode", "starter")
        kwargs.setdefault("max_acciones", MAX_ACCIONES_STARTER)
        kwargs.setdefault("bloqueo_partido", True)
        kwargs.setdefault("reward_mode", "sparse")
        super().__init__(**kwargs)
        self.observador_ontologia = ObservadorOntologia()

    def observation_space(self, agent):
        return Dict({
            "observation": Box(low=-np.inf, high=np.inf, shape=(OBS_DIM,), dtype=np.float32),
            "action_mask": Box(low=0, high=1, shape=(self.max_acciones,), dtype=np.int8),
        })

    def action_space(self, agent):
        return Discrete(self.max_acciones)

    def observe(self, agent):
        idx = 0 if agent == "player_0" else 1
        legales = self.current_legal_actions if agent == self.agent_selection else []
        obs = np.nan_to_num(
            self.observador_ontologia.obtener_estado(self.adaptador, idx, legales),
            nan=0.0,
        )
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
