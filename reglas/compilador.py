"""JSON-LD a Definicion. No genera funciones: el intérprete lee estas habilidades."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reglas.modelo import Definicion


def _entero_o_asterisco(valor: Any) -> int | str | None:
    if valor is None or valor == "":
        return None
    if valor == "*":
        return "*"
    return int(valor)


def compilar(datos: dict[str, Any]) -> Definicion:
    atributos = datos.get("base_attributes") or {}
    keywords = tuple(k for k in datos.get("keywords") or [] if k != "NONE")
    return Definicion(
        nombre=datos["name"],
        oracle_text=datos.get("oracle_text") or "",
        mana_cost=datos.get("mana_cost") or None,
        supertypes=tuple(datos.get("supertypes") or ["NONE"]),
        card_types=tuple(datos["card_types"]),
        subtypes=tuple(datos.get("subtypes") or []),
        keywords=keywords,
        power=_entero_o_asterisco(atributos.get("power")),
        toughness=_entero_o_asterisco(atributos.get("toughness")),
        abilities=tuple(datos.get("abilities") or []),
    )


def compilar_archivo(ruta: Path | str) -> Definicion:
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    return compilar(datos)


def catalogo(directorio: Path | str) -> dict[str, Definicion]:
    raiz = Path(directorio)
    cartas = {}
    for ruta in sorted(raiz.glob("*.json")):
        if ruta.name.startswith("_"):
            continue
        definicion = compilar_archivo(ruta)
        cartas[definicion.nombre] = definicion
    return cartas


def criatura(
    nombre: str,
    poder: int | str | None,
    resistencia: int | str | None,
    subtypes: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
    abilities: tuple[dict[str, Any], ...] = (),
    mana_cost: str | None = None,
) -> Definicion:
    """Criatura auxiliar, para fichas y para pruebas de una habilidad aislada."""
    return Definicion(
        nombre=nombre,
        oracle_text="",
        mana_cost=mana_cost,
        supertypes=("NONE",),
        card_types=("Creature",),
        subtypes=subtypes,
        keywords=keywords,
        power=poder,
        toughness=resistencia,
        abilities=abilities,
    )
