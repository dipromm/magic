"""
Busca mazos públicos en Archidekt que lleven **ambos** tags Budget y Combo.

Usa el endpoint ``GET /api/decks/v3/`` con el parámetro repetido
``deckTagName`` (descubierto vía scraping del front).  El backend trata ese
filtro como OR (o ranking), así que después de cada página se aplica un
filtro AND estricto en cliente: solo se conservan mazos cuyo array ``tags``
contiene a la vez las dos etiquetas.

Salida: archivo ``salida_budget_combo.json`` en la raíz del repo con una
lista de dicts (id, nombre, owner, ``url`` al mazo en archidekt.com, tags,
viewCount, size, updatedAt).  No descarga la decklist (cartas) vía
``GET /api/decks/{id}/``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

# ── Constantes de red ──────────────────────────────────────────────────
API_DECKS_V3 = "https://archidekt.com/api/decks/v3/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "application/json",
}
MAX_REINTENTOS = 5
BACKOFF_INICIAL_SEG = 2.0

# ── Parámetros de búsqueda (fáciles de tocar) ─────────────────────────
TAGS_REQUERIDOS: set[str] = {"budget", "combo"}
DECK_FORMAT = 3          # Commander / EDH
ORDER_BY = "-viewCount"
PAGE_SIZE = 50
MAX_PAGINAS = 40
MAX_COINCIDENCIAS = 200
PAUSA_ENTRE_PAGINAS_SEG = 0.6

SALIDA_PATH = Path(__file__).resolve().parent / "salida_budget_combo.json"
URL_MAZO_FMT = "https://archidekt.com/decks/{deck_id}"


def _get_con_reintentos(
    session: requests.Session, url: str, *, params: Any = None
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


def _etiquetas_mazo(deck: dict[str, Any]) -> set[str]:
    return {t["name"].casefold() for t in deck.get("tags") or [] if t.get("name")}


def _cumple_tags(deck: dict[str, Any]) -> bool:
    return TAGS_REQUERIDOS <= _etiquetas_mazo(deck)


def _resumen_mazo(deck: dict[str, Any]) -> dict[str, Any]:
    deck_id = deck["id"]
    return {
        "id": deck_id,
        "name": deck.get("name"),
        "owner": (deck.get("owner") or {}).get("username"),
        "url": URL_MAZO_FMT.format(deck_id=deck_id),
        "tags": [t["name"] for t in deck.get("tags") or [] if t.get("name")],
        "viewCount": deck.get("viewCount"),
        "size": deck.get("size"),
        "updatedAt": deck.get("updatedAt"),
    }


def buscar_mazos_budget_combo(session: requests.Session) -> list[dict[str, Any]]:
    coincidencias: list[dict[str, Any]] = []
    pagina = 0
    next_url: str | None = None

    params_iniciales: list[tuple[str, str]] = [
        ("deckFormat", str(DECK_FORMAT)),
        ("orderBy", ORDER_BY),
        ("pageSize", str(PAGE_SIZE)),
        ("deckTagName", "Budget"),
        ("deckTagName", "Combo"),
    ]

    while pagina < MAX_PAGINAS and len(coincidencias) < MAX_COINCIDENCIAS:
        pagina += 1

        if next_url:
            r = _get_con_reintentos(session, next_url)
        else:
            r = _get_con_reintentos(session, API_DECKS_V3, params=params_iniciales)

        data = r.json()
        resultados = data.get("results") or []
        if not resultados:
            break

        nuevos = 0
        for deck in resultados:
            if _cumple_tags(deck):
                coincidencias.append(_resumen_mazo(deck))
                nuevos += 1
                if len(coincidencias) >= MAX_COINCIDENCIAS:
                    break

        print(
            f"  Página {pagina}: {len(resultados)} mazos revisados, "
            f"{nuevos} con Budget+Combo (total acumulado: {len(coincidencias)})"
        )

        next_url = data.get("next")
        if not next_url:
            break
        time.sleep(PAUSA_ENTRE_PAGINAS_SEG)

    return coincidencias


def main() -> None:
    print("Buscando mazos con tags Budget + Combo en Archidekt...")
    with requests.Session() as session:
        mazos = buscar_mazos_budget_combo(session)

    with SALIDA_PATH.open("w", encoding="utf-8") as f:
        json.dump(mazos, f, indent=2, ensure_ascii=False)

    print(f"\nResultado: {len(mazos)} mazos con ambos tags.")
    print(f"JSON guardado en: {SALIDA_PATH}")


if __name__ == "__main__":
    main()
