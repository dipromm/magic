"""
Duelo rápido entre dos checkpoints con mazos de criaturas aleatorias.

Tras las tierras, el resto del mazo son criaturas con:
  - potencia y resistencia uniformes en 0..10
  - coste de maná 0..10, aproximadamente (P+T)/2 con un poco de ruido

No entrena: solo sirve para ver cómo se comportan políticas ya entrenadas
fuera del mazo fijo de osos 2/2.

Ejemplo:
  python match_random_creatures.py --p0-ckpt modelo_magic_sparse_1.pth --p1-ckpt modelo_magic_dense_1.pth --games 20 --swap
  python match_random_creatures.py --p0-ckpt modelos\\anchor_curriculum_sparse3_policy_2.pth --p1-ckpt modelo_magic_sparse_1.pth --games 30 --seed 7
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    # Reutiliza el CLI de match_checkpoints forzando deck-mode=random_stats.
    # Así no duplicamos la lógica de carga de políticas / marcador.
    argv = list(sys.argv[1:])
    if "--deck-mode" not in argv:
        argv.extend(["--deck-mode", "random_stats"])

    # Sustituimos argv para que match_checkpoints.main() lea nuestros flags.
    sys.argv = [sys.argv[0], *argv]

    from match_checkpoints import main as match_main

    print("Modo de mazo: random_stats (tierras + criaturas P/T/maná aleatorios 0-10)")
    match_main()


if __name__ == "__main__":
    # argparse solo para --help legible; el parseo real lo hace match_checkpoints.
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help"):
        ap = argparse.ArgumentParser(
            description="Igual que match_checkpoints.py pero con criaturas aleatorias."
        )
        ap.add_argument("--p0-ckpt", required=True)
        ap.add_argument("--p1-ckpt", required=True)
        ap.add_argument("--games", type=int, default=20)
        ap.add_argument("--seed", type=int, default=0)
        ap.add_argument("--swap", action="store_true")
        ap.add_argument("--stochastic", action="store_true")
        ap.print_help()
        raise SystemExit(0)
    main()
