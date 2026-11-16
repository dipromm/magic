"""Descarga la imagen normal de Scryfall para cada nombre del starter.

Lee mazos/bloomburrow_jsonld/_indice.json y guarda mesa/cartas/<nombre>.jpg.
Si el archivo ya está, no vuelve a pedirlo. Scryfall pide un User-Agent
identificable y una pausa entre peticiones: este script espera entre cada una.

    python mesa/descargar_cartas.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

_RAIZ = Path(__file__).resolve().parent.parent
_INDICE = _RAIZ / "mazos" / "bloomburrow_jsonld" / "_indice.json"
_SALIDA = Path(__file__).resolve().parent / "cartas"
_PAUSA = 0.15
_AGENTE = "MagicMesa/1.0 (educational localhost Godot client for a Bloomburrow starter)"


def nombres() -> list[str]:
    indice = json.loads(_INDICE.read_text(encoding="utf-8"))
    vistos: list[str] = []
    for mazo in indice["mazos"]:
        for carta in mazo["cartas"]:
            nombre = carta["nombre"]
            if nombre not in vistos:
                vistos.append(nombre)
    return vistos


def _pedir(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _AGENTE, "Accept": "application/json,image/*"},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read()


def url_imagen(nombre: str) -> str:
    datos = json.loads(_pedir(
        "https://api.scryfall.com/cards/named?exact=" + quote(nombre)
    ))
    time.sleep(_PAUSA)
    uris = datos.get("image_uris") or {}
    if "normal" not in uris:
        caras = datos.get("card_faces") or []
        if caras:
            uris = caras[0].get("image_uris") or {}
    normal = uris.get("normal")
    if not normal:
        raise RuntimeError(f"Scryfall no tiene imagen normal para {nombre}")
    return normal


def descargar(forzar: bool = False) -> tuple[int, list[str]]:
    _SALIDA.mkdir(parents=True, exist_ok=True)
    nuevas = 0
    fallos: list[str] = []
    lista = nombres()
    for i, nombre in enumerate(lista, start=1):
        destino = _SALIDA / f"{nombre}.jpg"
        if destino.is_file() and destino.stat().st_size > 0 and not forzar:
            print(f"[{i}/{len(lista)}] ya está {destino.name}")
            continue
        try:
            url = url_imagen(nombre)
            destino.write_bytes(_pedir(url))
            nuevas += 1
            print(f"[{i}/{len(lista)}] {destino.name} ({destino.stat().st_size} bytes)")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                time.sleep(1.5)
                try:
                    url = url_imagen(nombre)
                    destino.write_bytes(_pedir(url))
                    nuevas += 1
                    print(f"[{i}/{len(lista)}] {destino.name} (reintento)")
                    time.sleep(_PAUSA)
                    continue
                except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as otra:
                    exc = otra
            fallos.append(f"{nombre}: {exc}")
            print(f"[{i}/{len(lista)}] FALLO {nombre}: {exc}", file=sys.stderr)
        time.sleep(_PAUSA)
    return nuevas, fallos


def main() -> None:
    forzar = "--forzar" in sys.argv
    nuevas, fallos = descargar(forzar=forzar)
    print(f"listas en {_SALIDA}  nuevas={nuevas}  fallos={len(fallos)}")
    if fallos:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
