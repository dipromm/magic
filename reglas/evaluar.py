"""Comparaciones, cantidades y filtros. Los números se leen de la partida, no se guardan en la carta."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from reglas.modelo import Coste, Definicion, Objeto, Ref

_MANA = re.compile(r"\{([^}]+)\}")
_ATRIBUTOS = {
    "Power", "Toughness", "ConvertedManaCost", "ManaCost", "Color",
    "ManaSpent", "OptionalCostPaid", "Name", "TotalPower", "COUNT",
    "MatchingCastCountThisTurn",
}
_KEYWORDS = {
    "Defender", "DoubleStrike", "Enchant", "Equip", "FirstStrike", "Flash",
    "Flying", "Haste", "Hexproof", "Indestructible", "Lifelink", "Menace",
    "Protection", "Reach", "Trample", "Vigilance", "Ward", "Prowess", "Offspring",
}
_OPERADORES = {
    "EQUALS": lambda a, b: a == b,
    "NOT_EQUAL": lambda a, b: a != b,
    "GREATER_THAN": lambda a, b: a > b,
    "LESS_THAN": lambda a, b: a < b,
    "GREATER_OR_EQUAL": lambda a, b: a >= b,
    "LESS_OR_EQUAL": lambda a, b: a <= b,
}


@dataclass
class Contexto:
    partida: Any
    fuente: Objeto
    controlador: str
    evento: Objeto | None = None
    objetivos: dict[str, Ref] = field(default_factory=dict)
    resultado_anterior: Objeto | None = None
    x: int | None = None
    en_lanzamiento: bool = False
    listas: dict[str, list[Ref]] = field(default_factory=dict)
    clave_objetivo: str = "TARGET_1"
    filtro_lanzamiento: dict[str, Any] | None = None


def parse_mana(coste: str | None) -> Coste:
    if not coste:
        return Coste(0, {}, False)
    generico = 0
    colores: dict[str, int] = {}
    tiene_x = False
    for simbolo in _MANA.findall(coste):
        if simbolo == "X":
            tiene_x = True
        elif simbolo.isdigit():
            generico += int(simbolo)
        else:
            colores[simbolo] = colores.get(simbolo, 0) + 1
    return Coste(generico, colores, tiene_x)


def coste_mana_convertido(definicion: Definicion) -> int:
    coste = parse_mana(definicion.mana_cost)
    return coste.generico + sum(coste.colores.values())


def tiene_modificador(nombre: str, objeto: Objeto, fuente: Objeto) -> bool:
    if nombre == "Token":
        return objeto.es_ficha
    if nombre == "Nontoken":
        return not objeto.es_ficha
    if nombre == "Other":
        return objeto.object_id != fuente.object_id
    if nombre == "Self":
        return objeto.object_id == fuente.object_id
    if nombre == "Noncreature":
        return "Creature" not in objeto.definicion.card_types
    if nombre == "Nonland":
        return "Land" not in objeto.definicion.card_types
    if nombre == "Tapped":
        return objeto.girada
    if nombre == "Untapped":
        return not objeto.girada
    if nombre == "EnteredThisTurn":
        return objeto.entro_este_turno
    if nombre == "Revealed":
        return objeto.revelada
    if nombre == "Attacking":
        return objeto.atacando
    if nombre == "Blocking":
        return objeto.bloqueando
    return False


def cumple_restricciones(restricciones: dict[str, Any] | None, objeto: Objeto, fuente: Objeto) -> bool:
    if not restricciones:
        return True
    operador = restricciones.get("logical_operator") or "NONE"
    tipos = list(restricciones.get("types") or [])
    subtipos = list(restricciones.get("subtypes") or [])
    modificadores = list(restricciones.get("modifiers") or [])
    if operador == "NONE" or not (tipos or subtipos or modificadores):
        return True
    tipos_ok = [tipo in objeto.definicion.card_types for tipo in tipos]
    subtipos_ok = [subtipo in objeto.definicion.subtypes for subtipo in subtipos]
    modificadores_ok = [tiene_modificador(mod, objeto, fuente) for mod in modificadores]
    if operador == "OR":
        return any(tipos_ok + subtipos_ok + modificadores_ok)
    if tipos and not all(tipos_ok):
        return False
    if subtipos and not all(subtipos_ok):
        return False
    if modificadores and not all(modificadores_ok):
        return False
    return True


def zona_de(objeto: Objeto, en_lanzamiento: bool) -> str:
    if en_lanzamiento and objeto.zona == "Hand":
        return "Stack"
    return objeto.zona


def afecta(efecto: dict[str, Any], fuente: Objeto, objeto: Objeto, ctx: Contexto) -> bool:
    info = efecto["target_info"]
    dueno = info.get("target_owner")
    if dueno == "SOURCE" and objeto.object_id != fuente.object_id:
        return False
    if dueno == "CONTROLLER" and objeto.controlador != fuente.controlador:
        return False
    if dueno == "OPPONENT" and objeto.controlador == fuente.controlador:
        return False
    if dueno == "NOT_CONTROLLER" and objeto.controlador == fuente.controlador:
        return False
    if dueno == "EVENT_OBJECT" and (ctx.evento is None or objeto.object_id != ctx.evento.object_id):
        return False
    if dueno == "ATTACHED":
        enlace = fuente.anexada_a
        if enlace is None or enlace.object_id != objeto.object_id:
            return False
    if dueno == "PREVIOUS_TARGET":
        anterior = ctx.resultado_anterior
        if anterior is None or anterior.object_id != objeto.object_id:
            return False
    zona = info.get("target_zone")
    if dueno not in (None, "SOURCE") and zona and zona_de(objeto, ctx.en_lanzamiento) != zona:
        return False
    return cumple_restricciones(info.get("restrictions"), objeto, fuente)


def objeto_de_scope(scope: str | None, ctx: Contexto) -> Objeto | None:
    if scope in (None, "SOURCE"):
        return ctx.fuente
    if scope == "EVENT_OBJECT":
        return ctx.evento
    if scope == "PREVIOUS_TARGET":
        return ctx.resultado_anterior
    if scope == "ATTACHED" and ctx.fuente.anexada_a is not None:
        return ctx.partida.objeto_vivo(ctx.fuente.anexada_a)
    if scope in ("TARGET_1", "TARGET_2"):
        ref = ctx.objetivos.get(scope)
        return ctx.partida.objeto_vivo(ref) if ref else None
    return None


def consulta(ctx: Contexto, query: dict[str, Any] | None) -> list[Objeto]:
    if not query:
        return []
    dueno = query.get("target_owner")
    if dueno == "CONTROLLER":
        controladores = {ctx.controlador}
    elif dueno == "OPPONENT":
        controladores = {j.id for j in ctx.partida.jugadores.values() if j.id != ctx.controlador}
    else:
        controladores = set(ctx.partida.jugadores)
    zona = query.get("target_zone")
    objetos = [
        objeto for objeto in ctx.partida.objetos.values()
        if objeto.controlador in controladores and (zona in (None, "ALL_ZONES") or objeto.zona == zona)
    ]
    restricciones = query.get("restrictions") or {}
    return [objeto for objeto in objetos if cumple_restricciones(restricciones, objeto, ctx.fuente)]


def valor_operando(operando: Any, scope: str | None, ctx: Contexto, query: dict[str, Any] | None = None) -> Any:
    if isinstance(operando, bool) or operando is None:
        return operando
    if isinstance(operando, (int, float)):
        return operando
    if operando == "COUNT":
        return len(consulta(ctx, query))
    if operando == "TotalPower":
        return sum(ctx.partida.poder(objeto) for objeto in consulta(ctx, query))
    if operando == "MatchingCastCountThisTurn":
        return ctx.partida.hechizos_lanzados(ctx.controlador, ctx)
    objeto = objeto_de_scope(scope, ctx)
    if objeto is None:
        return 0
    if operando == "Power":
        return ctx.partida.poder(objeto)
    if operando == "Toughness":
        return ctx.partida.resistencia(objeto)
    if operando == "ConvertedManaCost":
        return coste_mana_convertido(objeto.definicion)
    if operando == "ManaSpent":
        return objeto.mana_gastado or 0
    if operando == "OptionalCostPaid":
        return 1 if objeto.coste_opcional_pagado else 0
    if operando == "Name":
        return objeto.definicion.nombre
    if operando in _KEYWORDS:
        return 1 if ctx.partida.tiene_keyword(objeto, operando) else 0
    if operando in _ATRIBUTOS:
        return 0
    return objeto.contadores.get(str(operando), 0)


def comparar(condicion: dict[str, Any], ctx: Contexto) -> bool:
    izquierda = valor_operando(
        condicion["left_operand"], condicion.get("left_scope"), ctx, condicion.get("query"),
    )
    derecha = valor_operando(
        condicion["right_operand"], condicion.get("right_scope"), ctx, None,
    )
    return _OPERADORES[condicion["operator"]](izquierda, derecha)


def condiciones_ok(condiciones: list[dict[str, Any]] | None, operador: str | None, ctx: Contexto) -> bool:
    if not condiciones:
        return True
    resultados = [comparar(condicion, ctx) for condicion in condiciones]
    if operador == "OR":
        return any(resultados)
    return all(resultados)


_CONTADORES = {
    "Charge", "Defense", "Finality", "Lore", "Loyalty",
    "MinusOneMinusOne", "Oil", "PlusOnePlusOne", "PlusXPlusY",
    "Poison", "Stun", "Time",
}
EVENTOS_CON_RESOLUTOR = {
    "EnterTheBattlefield", "Cast", "Attacks", "DealsDamage", "Sacrifice",
}
SCOPES_CON_RESOLUTOR = {
    "SOURCE", "EVENT_OBJECT", "PREVIOUS_TARGET", "TARGET_1", "TARGET_2",
    "CONTROLLER", "OPPONENT", "NOT_CONTROLLER", "ATTACHED",
}
CALCULOS_CON_RESOLUTOR = {"COUNT", "ATTRIBUTE_REFERENCE"}


class OperandoSinResolver(Exception):
    """El mazo usa un operando, evento o ámbito que el intérprete no resuelve."""


def resuelve_operando(nombre: str) -> bool:
    return (
        nombre in _ATRIBUTOS
        or nombre in _KEYWORDS
        or nombre in _CONTADORES
        or nombre in ("X", "COUNT", "TotalPower")
    )


def recopilar_operandos(definicion: Definicion) -> dict[str, set[str]]:
    """Nombres que el mazo pide resolver. No guarda el valor de la partida."""
    acc: dict[str, set[str]] = {
        "operandos": set(),
        "atributos": set(),
        "calculos": set(),
        "eventos": set(),
        "scopes": set(),
    }

    def walk(nodo: Any) -> None:
        if isinstance(nodo, dict):
            if isinstance(nodo.get("left_operand"), str):
                acc["operandos"].add(nodo["left_operand"])
            if isinstance(nodo.get("right_operand"), str):
                acc["operandos"].add(nodo["right_operand"])
            if isinstance(nodo.get("attribute"), str):
                acc["atributos"].add(nodo["attribute"])
            if isinstance(nodo.get("dynamic_calculation"), str):
                acc["calculos"].add(nodo["dynamic_calculation"])
            evento = nodo.get("event_type")
            if isinstance(evento, str) and evento != "NONE":
                acc["eventos"].add(evento)
            for clave in ("left_scope", "right_scope", "source_ref", "target_owner"):
                valor = nodo.get(clave)
                if isinstance(valor, str):
                    acc["scopes"].add(valor)
            parametros = nodo.get("parameters")
            if isinstance(parametros, dict) and isinstance(parametros.get("amount"), str):
                acc["operandos"].add(parametros["amount"])
            for valor in nodo.values():
                walk(valor)
        elif isinstance(nodo, list):
            for valor in nodo:
                walk(valor)

    for habilidad in definicion.abilities:
        walk(habilidad)
    return acc


def exigir_resolutores(definiciones: list[Definicion]) -> None:
    faltan: list[str] = []
    for definicion in definiciones:
        acc = recopilar_operandos(definicion)
        for nombre in acc["operandos"] | acc["atributos"]:
            if not resuelve_operando(nombre):
                faltan.append(f"{definicion.nombre}: operando {nombre}")
        for calculo in acc["calculos"]:
            if calculo not in CALCULOS_CON_RESOLUTOR:
                faltan.append(f"{definicion.nombre}: cálculo {calculo}")
        for evento in acc["eventos"]:
            if evento not in EVENTOS_CON_RESOLUTOR:
                faltan.append(f"{definicion.nombre}: evento {evento}")
        for scope in acc["scopes"]:
            if scope not in SCOPES_CON_RESOLUTOR:
                faltan.append(f"{definicion.nombre}: ámbito {scope}")
    if faltan:
        raise OperandoSinResolver("; ".join(faltan))


def cantidad(amount: Any, ctx: Contexto) -> int:
    if amount is None:
        return 0
    if isinstance(amount, bool):
        return int(amount)
    if isinstance(amount, int):
        return amount
    if isinstance(amount, float):
        return int(amount)
    if amount == "X":
        return 0 if ctx.x is None else ctx.x
    if isinstance(amount, dict):
        calculo = amount.get("dynamic_calculation")
        if calculo == "COUNT":
            valor = len(consulta(ctx, amount.get("query")))
        elif calculo == "ATTRIBUTE_REFERENCE":
            valor = int(valor_operando(amount.get("attribute"), amount.get("source_ref"), ctx) or 0)
        else:
            valor = 0
        if amount.get("multiplier"):
            valor *= int(amount["multiplier"])
        if amount.get("offset"):
            valor += int(amount["offset"])
        return valor
    raise TypeError(f"amount no numérico: {amount!r}")
