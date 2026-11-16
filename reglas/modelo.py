"""Objetos de partida. La definición es compartida; el id y la generación no."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Definicion:
    nombre: str
    oracle_text: str
    mana_cost: str | None
    supertypes: tuple[str, ...]
    card_types: tuple[str, ...]
    subtypes: tuple[str, ...]
    keywords: tuple[str, ...]
    power: int | str | None
    toughness: int | str | None
    abilities: tuple[dict[str, Any], ...]

    def habilidad(self, orden: int) -> dict[str, Any]:
        for habilidad in self.abilities:
            if habilidad["ability_order"] == orden:
                return habilidad
        raise KeyError(f"{self.nombre} no tiene la habilidad {orden}")

    def hechizo(self) -> dict[str, Any]:
        for habilidad in self.abilities:
            if habilidad["ability_type"] == "SpellAbility":
                return habilidad
        raise KeyError(f"{self.nombre} no es un hechizo")


@dataclass(frozen=True)
class Ref:
    object_id: str
    generacion: int


@dataclass(frozen=True)
class Coste:
    generico: int
    colores: dict[str, int]
    tiene_x: bool
    x: int = 0

    @property
    def total(self) -> int:
        return self.generico + sum(self.colores.values())


@dataclass
class Jugador:
    id: str
    nombre: str
    vida: int = 20
    mana: dict[str, int] = field(default_factory=dict)
    zonas: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for zona in ("Library", "Hand", "Battlefield", "Graveyard", "Exile", "Stack", "CommandZone"):
            self.zonas.setdefault(zona, [])


@dataclass
class Objeto:
    object_id: str
    generacion: int
    pieza_id: str | None
    definicion: Definicion
    zona: str
    dueno: str
    controlador: str
    es_ficha: bool = False
    danio: int = 0
    girada: bool = False
    entro_este_turno: bool = False
    revelada: bool = False
    atacando: bool = False
    bloqueando: bool = False
    coste_opcional_pagado: bool = False
    x: int | None = None
    mana_gastado: int | None = None
    anexada_a: Ref | None = None
    contadores: dict[str, int] = field(default_factory=dict)

    def ref(self) -> Ref:
        return Ref(self.object_id, self.generacion)


@dataclass
class ObjetoPila:
    """Habilidad activada o disparada. El hechizo es la propia carta en la pila."""

    object_id: str
    fuente: Ref
    habilidad: int
    controlador: str
    evento: Ref | None = None
    objetivos: dict[str, Ref] = field(default_factory=dict)
    efecto_actual: int = 0
    x: int | None = None
    resultado_anterior: Ref | None = None
    aceptados: set[int] = field(default_factory=set)
    objetivos_cerrados: bool = False
    listas: dict[str, list[Ref]] = field(default_factory=dict)
    slot_actual: int = 0


@dataclass
class EfectoContinuo:
    effect_id: str
    fuente: Ref
    objetivo: Ref
    tipo: str
    amount: int
    string_value: str | None
    duracion: str
