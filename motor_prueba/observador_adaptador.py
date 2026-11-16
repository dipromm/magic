"""Observación 124 alineada con ObservadorRL, leyendo poder()/resistencia() del adaptador."""
from __future__ import annotations

import numpy as np


class ObservadorAdaptador:
    def __init__(self, max_cartas_mano: int = 10, max_cartas_campo: int = 10):
        self.max_cartas_mano = max_cartas_mano
        self.max_cartas_campo = max_cartas_campo

    def _codificar(self, adaptador, object_id: str | None) -> list[float]:
        if object_id is None:
            return [0.0, 0.0, 0.0, 0.0]
        carta = adaptador.partida.objetos[object_id]
        tipos = carta.definicion.card_types
        if "Land" in tipos:
            return [1.0, 1.0, 1.0 if carta.girada else 0.0, 0.0]
        if "Creature" in tipos:
            mareo = 1.0 if (carta.girada or carta.entro_este_turno) else 0.0
            return [
                2.0,
                float(adaptador.partida.poder(carta)),
                float(adaptador.partida.resistencia(carta)),
                mareo,
            ]
        # Otros permanentes: hueco tipo 3 para no romper el layout.
        return [3.0, 0.0, 1.0 if carta.girada else 0.0, 0.0]

    def obtener_estado(self, adaptador, jugador_idx: int) -> np.ndarray:
        yo = adaptador.jugadores[jugador_idx]
        rival = adaptador.jugadores[1 - jugador_idx]
        global_ = [
            float(yo.vida),
            float(adaptador.maná_disponible(yo)),
            float(rival.vida),
            float(adaptador.maná_disponible(rival)),
        ]
        mano = []
        for i in range(self.max_cartas_mano):
            oid = yo.zonas["Hand"][i] if i < len(yo.zonas["Hand"]) else None
            mano.extend(self._codificar(adaptador, oid))
        mi_campo = []
        for i in range(self.max_cartas_campo):
            oid = yo.zonas["Battlefield"][i] if i < len(yo.zonas["Battlefield"]) else None
            mi_campo.extend(self._codificar(adaptador, oid))
        su_campo = []
        for i in range(self.max_cartas_campo):
            oid = rival.zonas["Battlefield"][i] if i < len(rival.zonas["Battlefield"]) else None
            su_campo.extend(self._codificar(adaptador, oid))
        return np.array(global_ + mano + mi_campo + su_campo, dtype=np.float32)
