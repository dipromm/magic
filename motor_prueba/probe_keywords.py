"""Fase 11 + ficha_v1: máscara Flying; bit de keyword en la cabecera de la ficha."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reglas.adaptador import Adaptador
from reglas.compilador import criatura
from ficha_rl import (
    FICHA_DIM,
    OFFSET_KEYWORDS,
    IDX_FLYING,
    ObservadorOntologia,
    construir_slots,
)


def test_mascara_flying() -> None:
    ad = Adaptador(modo_menu="puente")
    ad.montar([], [])
    j0, j1 = ad.jugadores
    flyer = ad.partida.instanciar(
        criatura("Ave", 2, 2, keywords=("Flying",)), j0.id, "Battlefield",
    )
    flyer.entro_este_turno = False
    ground = ad.partida.instanciar(criatura("Oso", 2, 2), j1.id, "Battlefield")
    ground.entro_este_turno = False
    reach = ad.partida.instanciar(
        criatura("Arquero", 1, 3, keywords=("Reach",)), j1.id, "Battlefield",
    )
    reach.entro_este_turno = False
    ad.atacantes = [flyer.object_id]
    ad.fase = "Declarar Bloqueadores"
    acciones = ad.acciones_legales(j1)
    bloqueos = [a for a in acciones if a["tipo"] == "declarar_bloqueo"]
    ids = {a["blocker_id"] for a in bloqueos}
    assert ground.object_id not in ids
    assert reach.object_id in ids
    print("ok test_mascara_flying")


def test_bit_flying_en_ficha() -> None:
    ad = Adaptador(modo_menu="completo")
    ad.montar([], [])
    j0 = ad.jugadores[0]
    flyer = ad.partida.instanciar(
        criatura("Ave", 2, 2, keywords=("Flying",)), j0.id, "Battlefield",
    )
    obs = ObservadorOntologia().obtener_estado(ad, 0, [])
    slots, mapa = construir_slots(ad, 0)
    assert flyer.object_id in mapa
    base = mapa[flyer.object_id] * FICHA_DIM
    assert obs[base + OFFSET_KEYWORDS + IDX_FLYING] == 1.0
    obs2 = np.array(obs, copy=True)
    obs2[base + OFFSET_KEYWORDS + IDX_FLYING] = 0.0
    assert obs2[base + OFFSET_KEYWORDS + IDX_FLYING] != obs[base + OFFSET_KEYWORDS + IDX_FLYING]
    print("ok test_bit_flying_en_ficha")


def main() -> None:
    test_mascara_flying()
    test_bit_flying_en_ficha()
    print("ok")


if __name__ == "__main__":
    main()
