"""Partida aleatoria Hare Raising vs Otter Limits sobre el adaptador."""
from __future__ import annotations

import random

from reglas.adaptador import Adaptador, mazo_starter


def jugar_starter(*, semilla: int = 0, max_pasos: int = 4000) -> Adaptador:
    random.seed(semilla)
    adaptador = Adaptador()
    adaptador.montar(mazo_starter("hare_raising"), mazo_starter("otter_limits"))
    for _ in range(max_pasos):
        if adaptador.terminada():
            return adaptador
        adaptador.paso_aleatorio()
    return adaptador


def main() -> None:
    adaptador = jugar_starter(semilla=1, max_pasos=4000)
    vidas = [j.vida for j in adaptador.jugadores]
    print(f"vidas={vidas} terminada={adaptador.terminada()} pila={len(adaptador.partida.pila)}")
    assert adaptador.terminada(), f"la partida no terminó: vidas={vidas}"
    print("ok")


if __name__ == "__main__":
    main()
