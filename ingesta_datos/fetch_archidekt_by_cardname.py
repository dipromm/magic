"""
Obtiene la ficha de una carta tal como la modela Archidekt (objeto `card` con
`oracleCard`, edición, ids, etc.).

Archidekt no expone un endpoint público tipo “/cards/{nombre}”. El parámetro
`decks/v3/?cardName=` solo sirve para buscar mazos. Este script usa **un solo**
mazo público que contenga la carta (pageSize=1), descarga `GET /api/decks/{id}/`
y extrae la primera coincidencia en `cards` con el nombre oracle esperado.

Limitación: la carta debe aparecer en al menos un mazo público indexado; el JSON
incluye metadatos del mazo usado solo como referencia.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

API_DECKS_V3 = "https://archidekt.com/api/decks/v3/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "application/json",
}
MAX_REINTENTOS = 5
BACKOFF_INICIAL_SEG = 2.0

CARTAS = [
    "Panglacial Wurm",
    "Illusionary Mask",
    "Chains of Mephistopheles",
    "Shahrazad",
    "Grist, the Hunger Tide",
    "Word of Command",
    "Urza's Saga",
    "Invasion of Alara",
]

# `cardName` en v3 a veces falla con el nombre “corto” (p. ej. MDFC).
CONSULTAS_ALTERNATIVAS: dict[str, list[str]] = {
    "Invasion of Alara": [
        "Invasion of Alara // Awaken the Maelstrom",
    ],
}

SALIDA_DIR = Path(__file__).resolve().parent / "salida_archidekt_cartas"
PAUSA_ENTRE_CARTAS_SEG = 0.8


def _get_con_reintentos(
    session: requests.Session, url: str, *, params: dict[str, Any] | None = None
) -> requests.Response:
    espera = BACKOFF_INICIAL_SEG
    ultimo: Exception | None = None
    for _ in range(MAX_REINTENTOS):
        try:
            r = session.get(url, headers=HEADERS, params=params, timeout=90)
            r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError) as e:
            ultimo = e
            time.sleep(espera)
            espera = min(espera * 1.8, 60.0)
    assert ultimo is not None
    raise ultimo


def _normalizar_nombre(s: str) -> str:
    t = s.casefold().strip()
    for ap in ("'", "\u2019"):
        t = t.replace(ap, "")
    t = t.replace("//", " // ")
    return " ".join(t.split())


def _variantes_nombre_oracle(oracle_name: str) -> set[str]:
    base = _normalizar_nombre(oracle_name)
    out = {base}
    if " // " in oracle_name:
        a, b = oracle_name.split(" // ", 1)
        out.add(_normalizar_nombre(a.strip()))
        out.add(_normalizar_nombre(b.strip()))
    return out


def _puntuacion_coincidencia(consulta_norm: str, oracle_name: str) -> int:
    if not oracle_name:
        return 0
    variants = _variantes_nombre_oracle(oracle_name)
    if consulta_norm in variants:
        return 3
    on = _normalizar_nombre(oracle_name)
    if consulta_norm == on:
        return 3
    if consulta_norm in on or on in consulta_norm:
        return 1
    return 0


def _elegir_slot_carta(
    slots: list[dict[str, Any]], etiqueta: str, terminos_busqueda: list[str]
) -> dict[str, Any] | None:
    consultas_norm = {_normalizar_nombre(t) for t in terminos_busqueda}
    consultas_norm.add(_normalizar_nombre(etiqueta))

    mejor: tuple[int, int, dict[str, Any]] | None = None
    for i, slot in enumerate(slots):
        oc = (slot.get("card") or {}).get("oracleCard") or {}
        name = oc.get("name") or ""
        score = 0
        for q in consultas_norm:
            score = max(score, _puntuacion_coincidencia(q, name))
        if score == 0:
            continue
        cand = (score, -i, slot)
        if mejor is None or cand > mejor:
            mejor = cand
    return mejor[2] if mejor else None


def _consultas_para_carta(nombre_etiqueta: str) -> list[str]:
    seen: set[str] = set()
    orden: list[str] = []
    for q in [nombre_etiqueta, *CONSULTAS_ALTERNATIVAS.get(nombre_etiqueta, [])]:
        if q not in seen:
            seen.add(q)
            orden.append(q)
    return orden


def fetch_carta_via_archidekt(session: requests.Session, nombre_carta: str) -> dict[str, Any]:
    terminos = _consultas_para_carta(nombre_carta)
    ultimo_count: Any = None
    busqueda: dict[str, Any] = {}
    card_name_usado: str | None = None
    resultados: list[Any] = []

    for termino in terminos:
        r = _get_con_reintentos(
            session,
            API_DECKS_V3,
            params={"cardName": termino, "pageSize": 1},
        )
        busqueda = r.json()
        ultimo_count = busqueda.get("count")
        resultados = busqueda.get("results") or []
        if resultados:
            card_name_usado = termino
            break
        time.sleep(0.3)

    if not resultados:
        return {
            "consulta": nombre_carta,
            "error": "Sin mazos públicos tras probar las variantes de cardName.",
            "count": ultimo_count,
            "consultas_intentadas": terminos,
        }

    mazo_resumen = resultados[0]
    deck_id = mazo_resumen.get("id")
    if deck_id is None:
        return {"consulta": nombre_carta, "error": "Respuesta v3 sin id de mazo.", "mazo_resumen": mazo_resumen}

    r2 = _get_con_reintentos(session, f"https://archidekt.com/api/decks/{deck_id}/")
    mazo = r2.json()
    slots = mazo.get("cards") or []
    slot = _elegir_slot_carta(slots, nombre_carta, terminos)
    if slot is None:
        return {
            "consulta": nombre_carta,
            "error": "El mazo no contiene una carta cuyo oracle coincida con la consulta.",
            "deck_id": deck_id,
            "deck_name": mazo.get("name"),
            "slots_en_mazo": len(slots),
            "consultas_intentadas": terminos,
        }

    oracle = (slot.get("card") or {}).get("oracleCard") or {}

    return {
        "consulta": nombre_carta,
        "cardName_busqueda_usado": card_name_usado,
        "oracle_name_resuelto": oracle.get("name"),
        "mazo_referencia": {
            "id": deck_id,
            "name": mazo.get("name"),
            "url": f"https://archidekt.com/decks/{deck_id}",
        },
        "total_mazos_con_esta_busqueda": busqueda.get("count"),
        "carta_en_mazo": slot,
        "oracleCard": oracle,
    }


def nombre_archivo_seguro(nombre: str) -> str:
    return "".join(c if c.isalnum() or c in " -_," else "_" for c in nombre).strip().replace(" ", "_")


def main() -> None:
    SALIDA_DIR.mkdir(parents=True, exist_ok=True)
    indice: list[dict[str, Any]] = []

    with requests.Session() as session:
        for carta in CARTAS:
            print(f"Resolviendo carta vía Archidekt: {carta!r} ...")
            try:
                payload = fetch_carta_via_archidekt(session, carta)
            except requests.HTTPError as e:
                print(f"  HTTP error: {e}")
                indice.append({"consulta": carta, "error": str(e)})
                time.sleep(PAUSA_ENTRE_CARTAS_SEG)
                continue
            except Exception as e:
                print(f"  Error: {e}")
                indice.append({"consulta": carta, "error": str(e)})
                time.sleep(PAUSA_ENTRE_CARTAS_SEG)
                continue

            fname = nombre_archivo_seguro(carta) + ".json"
            path = SALIDA_DIR / fname
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

            if payload.get("error"):
                print(f"  -> {path.name}: {payload['error']}")
            else:
                print(
                    f"  -> {path.name}: oracle={payload.get('oracle_name_resuelto')!r}, "
                    f"mazo #{payload['mazo_referencia']['id']}"
                )
            indice.append(
                {
                    "consulta": carta,
                    "archivo": fname,
                    "oracle_name_resuelto": payload.get("oracle_name_resuelto"),
                    "error": payload.get("error"),
                    "mazo_id": (payload.get("mazo_referencia") or {}).get("id"),
                }
            )
            time.sleep(PAUSA_ENTRE_CARTAS_SEG)

    with (SALIDA_DIR / "_indice.json").open("w", encoding="utf-8") as f:
        json.dump(indice, f, indent=2, ensure_ascii=False)

    print(f"\nListo. JSON en: {SALIDA_DIR}")


if __name__ == "__main__":
    main()
