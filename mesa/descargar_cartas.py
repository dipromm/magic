"""Descarga la imagen normal de Scryfall para cada nombre del starter.

Lee mazos/bloomburrow_jsonld/_indice.json y guarda mesa/cartas/<nombre>.jpg.
También parsea las fichas que el starter puede crear (CreateToken y
CreateCopyToken) y baja su imagen al mismo directorio. Si el archivo ya
está, no vuelve a pedirlo. Scryfall pide un User-Agent identificable y una
pausa entre peticiones: este script espera entre cada una.

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
_MANIFIESTO = _SALIDA / "_fichas.json"
_PAUSA = 0.15
_AGENTE = "MagicMesa/1.0 (educational localhost Godot client for a Bloomburrow starter)"
_COLOR_SCRYFALL = {
    "WHITE": "W",
    "BLUE": "U",
    "BLACK": "B",
    "RED": "R",
    "GREEN": "G",
}
_SETS_PREFERIDOS = ("tblb", "blb")


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


def _definiciones_fichas() -> list[dict]:
    """Fichas distintas que los mazos del índice pueden crear."""
    indice = json.loads(_INDICE.read_text(encoding="utf-8"))
    grupos: dict[str, dict] = {}
    for mazo in indice["mazos"]:
        for entrada in mazo["cartas"]:
            ruta = _INDICE.parent / entrada["archivo"]
            carta = json.loads(ruta.read_text(encoding="utf-8"))
            for habilidad in carta.get("abilities") or []:
                for efecto in habilidad.get("effects") or []:
                    if efecto.get("effect_type") not in ("CreateToken", "CreateCopyToken"):
                        continue
                    definicion = (efecto.get("parameters") or {}).get("token_definition")
                    if not definicion:
                        continue
                    clave = json.dumps(definicion, sort_keys=True, ensure_ascii=False)
                    grupo = grupos.setdefault(clave, {
                        "definicion": definicion,
                        "efecto": efecto["effect_type"],
                        "origenes": [],
                    })
                    if entrada["nombre"] not in grupo["origenes"]:
                        grupo["origenes"].append(entrada["nombre"])
    return list(grupos.values())


def _es_copia(definicion: dict) -> bool:
    return not (definicion.get("card_types") or definicion.get("subtypes"))


def _consulta_ficha(definicion: dict) -> str:
    partes = ["type:token", "game:paper"]
    for tipo in definicion.get("card_types") or []:
        partes.append("type:" + tipo)
    for subtipo in definicion.get("subtypes") or []:
        partes.append("type:" + subtipo)
    if definicion.get("power") is not None:
        partes.append(f"pow={definicion['power']}")
    if definicion.get("toughness") is not None:
        partes.append(f"tou={definicion['toughness']}")
    letras = "".join(
        _COLOR_SCRYFALL[color]
        for color in definicion.get("colors") or []
        if color in _COLOR_SCRYFALL
    )
    if letras:
        partes.append("c:" + letras)
    return " ".join(partes)


def _tipos_de_linea(type_line: str) -> tuple[set[str], set[str]]:
    if "—" in type_line:
        izquierda, derecha = type_line.split("—", 1)
    else:
        izquierda, derecha = type_line, ""
    tipos = {trozo for trozo in izquierda.replace("Token", " ").split() if trozo and trozo != "Token"}
    subtipos = {trozo for trozo in derecha.split() if trozo}
    return tipos, subtipos


def _colores_esperados(definicion: dict) -> set[str]:
    return {
        _COLOR_SCRYFALL[color]
        for color in definicion.get("colors") or []
        if color in _COLOR_SCRYFALL
    }


def _sirve(carta: dict, definicion: dict) -> bool:
    tipos, subtipos = _tipos_de_linea(str(carta.get("type_line") or ""))
    if set(definicion.get("card_types") or []) != tipos:
        return False
    if set(definicion.get("subtypes") or []) != subtipos:
        return False
    if definicion.get("power") is not None and str(carta.get("power")) != str(definicion["power"]):
        return False
    if definicion.get("toughness") is not None and str(carta.get("toughness")) != str(definicion["toughness"]):
        return False
    if set(carta.get("colors") or []) != _colores_esperados(definicion):
        return False
    uris = carta.get("image_uris") or {}
    if "normal" not in uris and carta.get("card_faces"):
        uris = (carta["card_faces"][0] or {}).get("image_uris") or {}
    return "normal" in uris


def _url_normal(carta: dict) -> str:
    uris = carta.get("image_uris") or {}
    if "normal" not in uris and carta.get("card_faces"):
        uris = (carta["card_faces"][0] or {}).get("image_uris") or {}
    return str(uris.get("normal") or "")


def _buscar_fichas(consulta: str) -> list[dict]:
    url = "https://api.scryfall.com/cards/search?unique=prints&q=" + quote(consulta)
    cartas: list[dict] = []
    while url and len(cartas) < 80:
        datos = json.loads(_pedir(url))
        time.sleep(_PAUSA)
        cartas.extend(datos.get("data") or [])
        url = datos.get("next_page") or ""
    return cartas


def _elegir_impresion(cartas: list[dict], definicion: dict) -> dict | None:
    validas = [carta for carta in cartas if _sirve(carta, definicion)]
    if not validas:
        return None

    def orden(carta: dict) -> tuple[int, str]:
        codigo = str(carta.get("set") or "")
        if codigo in _SETS_PREFERIDOS:
            return (_SETS_PREFERIDOS.index(codigo), codigo)
        return (len(_SETS_PREFERIDOS), codigo)

    validas.sort(key=orden)
    return validas[0]


def _leer_manifiesto() -> list[dict]:
    if not _MANIFIESTO.is_file():
        return []
    try:
        datos = json.loads(_MANIFIESTO.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    fichas = datos.get("fichas")
    return fichas if isinstance(fichas, list) else []


def _firma_ficha(definicion: dict) -> str:
    return json.dumps({
        "power": definicion.get("power"),
        "toughness": definicion.get("toughness"),
        "colors": list(definicion.get("colors") or []),
        "card_types": list(definicion.get("card_types") or []),
        "subtypes": list(definicion.get("subtypes") or []),
    }, sort_keys=True, ensure_ascii=False)


def _manifiesto_al_dia(grupos: list[dict], previo: list[dict]) -> bool:
    if len(previo) != len(grupos):
        return False
    por_firma = {_firma_ficha(entrada): entrada for entrada in previo}
    for grupo in grupos:
        entrada = por_firma.get(_firma_ficha(grupo["definicion"]))
        if entrada is None:
            return False
        if entrada.get("copia_de_origen"):
            continue
        archivo = str(entrada.get("archivo") or "")
        if not archivo:
            return False
        destino = _SALIDA / archivo
        if not destino.is_file() or destino.stat().st_size <= 0:
            return False
    return True


def descargar_fichas(forzar: bool = False) -> tuple[int, list[str]]:
    """Baja las fichas que faltan. Un fallo de Scryfall no borra el nombre."""
    _SALIDA.mkdir(parents=True, exist_ok=True)
    grupos = _definiciones_fichas()
    if not forzar and _manifiesto_al_dia(grupos, _leer_manifiesto()):
        print(f"fichas ya están en {_SALIDA}")
        return 0, []
    nuevas = 0
    fallos: list[str] = []
    manifiesto: list[dict] = []
    for grupo in grupos:
        definicion = grupo["definicion"]
        entrada = {
            "power": definicion.get("power"),
            "toughness": definicion.get("toughness"),
            "colors": list(definicion.get("colors") or []),
            "card_types": list(definicion.get("card_types") or []),
            "subtypes": list(definicion.get("subtypes") or []),
            "efecto": grupo["efecto"],
            "origenes": grupo["origenes"],
            "copia_de_origen": _es_copia(definicion),
            "nombre": "",
            "archivo": "",
            "set": "",
        }
        if entrada["copia_de_origen"]:
            print(f"ficha copia de {', '.join(grupo['origenes'])}: usa la carta de origen")
            manifiesto.append(entrada)
            continue
        etiqueta = " ".join(entrada["subtypes"]) or grupo["efecto"]
        try:
            impresion = _elegir_impresion(_buscar_fichas(_consulta_ficha(definicion)), definicion)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                impresion = None
            else:
                fallos.append(f"{etiqueta}: {exc}")
                print(f"FALLO ficha {etiqueta}: {exc}", file=sys.stderr)
                manifiesto.append(entrada)
                continue
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            fallos.append(f"{etiqueta}: {exc}")
            print(f"FALLO ficha {etiqueta}: {exc}", file=sys.stderr)
            manifiesto.append(entrada)
            continue
        if impresion is None:
            fallos.append(f"{etiqueta}: Scryfall no tiene una ficha que coincida")
            print(f"sin foto para {etiqueta}; en la mesa se ve el nombre", file=sys.stderr)
            manifiesto.append(entrada)
            continue
        nombre = str(impresion.get("name") or etiqueta)
        destino = _SALIDA / f"{nombre}.jpg"
        entrada["nombre"] = nombre
        entrada["archivo"] = destino.name
        entrada["set"] = str(impresion.get("set") or "")
        if destino.is_file() and destino.stat().st_size > 0 and not forzar:
            print(f"ya está {destino.name}")
            manifiesto.append(entrada)
            continue
        try:
            destino.write_bytes(_pedir(_url_normal(impresion)))
            nuevas += 1
            print(f"{destino.name} ({destino.stat().st_size} bytes)")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            fallos.append(f"{nombre}: {exc}")
            entrada["archivo"] = ""
            print(f"FALLO {nombre}: {exc}", file=sys.stderr)
        time.sleep(_PAUSA)
        manifiesto.append(entrada)
    _MANIFIESTO.write_text(
        json.dumps({"fichas": manifiesto}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return nuevas, fallos


def asegurar_fichas() -> list[dict]:
    """Antes de la partida: baja las fichas que falten y deja el manifiesto."""
    try:
        descargar_fichas(forzar=False)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"fichas sin actualizar: {exc}", file=sys.stderr)
    return _leer_manifiesto()


def main() -> None:
    forzar = "--forzar" in sys.argv
    nuevas, fallos = descargar(forzar=forzar)
    fnuevas, ffallos = descargar_fichas(forzar=forzar)
    print(
        f"listas en {_SALIDA}  nuevas={nuevas}  fallos={len(fallos)}"
        f"  fichas_nuevas={fnuevas}  fichas_sin_foto={len(ffallos)}"
    )
    if fallos:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
