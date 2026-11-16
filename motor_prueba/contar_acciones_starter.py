"""Fase 9: histograma de acciones legales del starter (sin tope)."""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reglas.adaptador import Adaptador, mazo_starter


def _tipo_menu(acciones: list[dict]) -> str:
    tipos = {a["tipo"] for a in acciones}
    if "elegir_x" in tipos:
        return "x"
    if "elegir_objetivo" in tipos:
        return "objetivo"
    if "elegir_bloqueador" in tipos or "elegir_atacante" in tipos:
        return "bloquear_partido"
    if any(t.startswith("declarar_bloqueo") for t in tipos) or "resolver_combate" in tipos:
        return "bloquear"
    if "declarar_atacante" in tipos or "finalizar_ataques" in tipos:
        return "atacar"
    if "activar" in tipos:
        return "activar"
    return "jugar"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--max-steps", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--bloqueo-partido", action="store_true", default=True)
    ap.add_argument("--no-bloqueo-partido", action="store_true")
    args = ap.parse_args()
    bloqueo_partido = not args.no_bloqueo_partido

    random.seed(args.seed)
    por_tipo: dict[str, list[int]] = defaultdict(list)
    max_global = 0
    for g in range(args.games):
        random.seed(args.seed + g)
        ad = Adaptador(modo_menu="completo", medir=True, bloqueo_partido=bloqueo_partido)
        ad.montar(mazo_starter("hare_raising"), mazo_starter("otter_limits"))
        for _ in range(args.max_steps):
            if ad.terminada():
                break
            if ad.fase == "Declarar Bloqueadores":
                jugador = ad.defensor()
            else:
                jugador = ad.jugador_activo()
            acciones = ad.acciones_legales(jugador)
            n = len(acciones)
            por_tipo[_tipo_menu(acciones)].append(n)
            max_global = max(max_global, n)
            if not acciones:
                break
            ad.ejecutar_accion(jugador, random.choice(acciones))

    resumen = {}
    for tipo, vals in sorted(por_tipo.items()):
        arr = np.asarray(vals, dtype=np.float64)
        resumen[tipo] = {
            "n": int(arr.size),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "max": float(arr.max()),
        }
    p95_all = max(v["p95"] for v in resumen.values()) if resumen else 50
    max_all = max(v["max"] for v in resumen.values()) if resumen else 50
    # Criterio del plan: p95 del run (o el máximo si p95 < 50).
    tope = int(np.ceil(max_all if p95_all < 50 else p95_all))
    # Margen hasta múltiplo de 8 (Discrete estable) sin bajar de lo observado.
    tope = max(tope, int(max_global))
    tope = int(np.ceil(tope / 8) * 8)

    out = {
        "bloqueo_partido": bloqueo_partido,
        "games": args.games,
        "max_global": max_global,
        "p95_global": p95_all,
        "MAX_ACCIONES_STARTER_sugerido": tope,
        "por_tipo": resumen,
    }
    print(json.dumps(out, indent=2))
    out_path = Path(__file__).resolve().parent / "logs" / "acciones_starter.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"escrito {out_path}")


if __name__ == "__main__":
    main()
