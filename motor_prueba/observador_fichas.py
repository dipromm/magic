"""Observación por fichas de carta (fase 10). Rompe los checkpoints de 124."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reglas.evaluar import coste_mana_convertido

GLOBAL_DIM = 16
FICHA_DIM = 18
MAX_HAND = 8
MAX_FIELD = 8
OBS_DIM = GLOBAL_DIM + FICHA_DIM * (MAX_HAND + MAX_FIELD + MAX_FIELD)

_FASES = {
    "Principal": 0.0,
    "Declarar Atacantes": 1.0,
    "Declarar Bloqueadores": 2.0,
}

_KEYWORDS = ("Flying", "Reach", "Trample", "Vigilance", "Haste", "Defender")


class ObservadorFichas:
    def obtener_estado(self, adaptador, jugador_idx: int) -> np.ndarray:
        yo = adaptador.jugadores[jugador_idx]
        rival = adaptador.jugadores[1 - jugador_idx]
        partida = adaptador.partida
        mana_cols = [float(yo.mana.get(c, 0)) for c in ("WHITE", "BLUE", "BLACK", "RED", "GREEN")]
        global_ = [
            float(yo.vida),
            float(rival.vida),
            float(adaptador.maná_disponible(yo)),
            float(adaptador.maná_disponible(rival)),
            _FASES.get(adaptador.fase, -1.0),
            float(len(partida.pila)),
            float(adaptador.tierras_jugadas),
            float(adaptador.indice_activo == jugador_idx),
            float(len(yo.zonas["Hand"])),
            float(len(yo.zonas["Library"])),
            *mana_cols,
        ]
        while len(global_) < GLOBAL_DIM:
            global_.append(0.0)
        global_ = global_[:GLOBAL_DIM]

        def ficha(oid: str | None, zona: float) -> list[float]:
            if oid is None:
                return [0.0] * FICHA_DIM
            carta = partida.objetos[oid]
            tipos = carta.definicion.card_types
            if "Land" in tipos:
                tipo = 1.0
                poder = res = 0.0
            elif "Creature" in tipos:
                tipo = 2.0
                poder = float(partida.poder(carta))
                res = float(partida.resistencia(carta))
            else:
                tipo = 3.0
                poder = res = 0.0
            abs_ = carta.definicion.abilities
            vec = [
                tipo,
                float(coste_mana_convertido(carta.definicion)),
                poder,
                res,
                1.0 if carta.girada else 0.0,
                1.0 if carta.entro_este_turno else 0.0,
                float(carta.contadores.get("PlusOnePlusOne", 0)),
            ]
            for kw in _KEYWORDS:
                vec.append(1.0 if partida.tiene_keyword(carta, kw) else 0.0)
            vec.append(1.0 if any(a["ability_type"] == "TriggeredAbility" for a in abs_) else 0.0)
            vec.append(1.0 if any(a["ability_type"] == "ActivatedAbility" for a in abs_) else 0.0)
            vec.append(1.0 if any(a["ability_type"] == "SpellAbility" for a in abs_) else 0.0)
            vec.append(zona)
            while len(vec) < FICHA_DIM:
                vec.append(0.0)
            return vec[:FICHA_DIM]

        out = list(global_)
        for i in range(MAX_HAND):
            oid = yo.zonas["Hand"][i] if i < len(yo.zonas["Hand"]) else None
            out.extend(ficha(oid, 1.0))
        for i in range(MAX_FIELD):
            oid = yo.zonas["Battlefield"][i] if i < len(yo.zonas["Battlefield"]) else None
            out.extend(ficha(oid, 2.0))
        for i in range(MAX_FIELD):
            oid = rival.zonas["Battlefield"][i] if i < len(rival.zonas["Battlefield"]) else None
            out.extend(ficha(oid, 3.0))
        return np.array(out, dtype=np.float32)
