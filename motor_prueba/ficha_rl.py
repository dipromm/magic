"""Ficha de ontología para el starter RL (obs_layout=ficha_v1).

Cada carta visible es un vector fijo de FICHA_DIM números (definición JSON-LD +
estado de la copia). El entorno emite un Box plano que la red parte.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reglas.evaluar import coste_mana_convertido, parse_mana
from reglas.modelo import Definicion, Objeto

_ONTO = json.loads(
    (_ROOT / "IA_JSON" / "ontologia_motor.json").read_text(encoding="utf-8")
)

EFFECTS = tuple(_ONTO["Effect"])
KEYWORDS = tuple(_ONTO["KeywordAbility"])
EVENTS = tuple(_ONTO["TriggerEvent"])
ZONES = tuple(_ONTO["Zone"])
CARD_TYPES = tuple(_ONTO["CardType"])
SUPERTYPES = tuple(_ONTO["CardSupertype"])
COUNTERS = tuple(_ONTO["Counter"])
ATTRIBUTES = tuple(_ONTO["CardAttribute"])
PHASES = tuple(_ONTO["PhaseAndStep"])
ABILITIES = tuple(_ONTO["Ability"])
COSTS = tuple(_ONTO["Cost"])

_COLORS = ("W", "U", "B", "R", "G")
_MANA_KEYS = ("WHITE", "BLUE", "BLACK", "RED", "GREEN")
_AMOUNT_CLASSES = ("literal", "X", "COUNT", "attribute", "other")
_DURATIONS = (
    "null",
    "UNTIL_END_OF_TURN",
    "WHILE_STATIC_ACTIVE",
    "PERMANENT",
    "UNTIL_END_OF_YOUR_NEXT_TURN",
    "other",
)
_OPERATORS = (
    "EQUALS",
    "NOT_EQUAL",
    "GREATER_THAN",
    "LESS_THAN",
    "GREATER_OR_EQUAL",
    "LESS_OR_EQUAL",
)
_OWNERS = ("CONTROLLER", "OPPONENT", "SOURCE", "OTHER")
_SCOPES = ("CONTROLLER", "OPPONENT", "SOURCE", "EVENT", "OTHER")
_DECISION_MODES = (
    "menu", "objetivo", "x", "elegir_bloqueador", "elegir_atacante",
    "opcional", "scry", "offspring", "otro",
    "mulligan", "fondo",
)
_ACTION_TYPES = (
    "pasar_fase",
    "jugar_carta",
    "activar",
    "declarar_atacante",
    "finalizar_ataques",
    "elegir_bloqueador",
    "elegir_atacante",
    "cancelar_bloqueador",
    "resolver_combate",
    "elegir_objetivo",
    "elegir_x",
    "declarar_bloqueo",
    "descartar",
    "confirmar_objetivos",
    "aceptar",
    "rechazar",
    "pagar_opcional",
    "omitir_opcional",
    "mantener_objetivos",
    "elegir_scry",
    "quedarme",
    "mulligan",
    "poner_fondo",
)
_FASE_A_ONTO = {
    "Principal": "MainPhase",
    "Declarar Atacantes": "DeclareAttackers",
    "Declarar Bloqueadores": "DeclareBlockers",
    "Descartar": "Cleanup",
    "Mantenimiento": "Upkeep",
    "Robar": "DrawStep",
    "Inicio del combate": "BeginningOfCombat",
    "Daño de combate": "CombatDamage",
    "Fin del combate": "EndOfCombat",
    "Paso final": "EndStep",
    "Limpieza": "Cleanup",
}

MAX_HABILIDADES = 4
MAX_EFECTOS = 5
SUBTYPE_HASH_DIM = 32
TOKEN_SUBTYPE_HASH_DIM = 16

HEADER_DIM = 8 + len(CARD_TYPES) + len(SUPERTYPES) + 3 + len(KEYWORDS) + SUBTYPE_HASH_DIM + 5
# 8+7+3+3+19+32+5 = 77
TOKEN_BODY_DIM = 2 + len(CARD_TYPES) + len(KEYWORDS) + TOKEN_SUBTYPE_HASH_DIM + 5 + 1  # 50
CONDITION_DIM = 47
TARGET_DIM = 1 + 4 + len(ZONES) + len(CARD_TYPES) + 3  # 22
EFFECT_DIM = (
    len(EFFECTS) + 1 + len(_AMOUNT_CLASSES) + len(_DURATIONS) + 1 + len(ZONES)
    + TARGET_DIM + CONDITION_DIM + TOKEN_BODY_DIM
)  # 173
ABILITY_DIM = (
    len(ABILITIES) + len(EVENTS) + len(ZONES) + len(COSTS) + MAX_EFECTOS * EFFECT_DIM
)
DEFINICION_DIM = HEADER_DIM + MAX_HABILIDADES * ABILITY_DIM
INSTANCIA_DIM = 1 + 2 + 4 + 1 + len(COUNTERS) + len(ZONES) + 1 + 1 + 1
FICHA_DIM = DEFINICION_DIM + INSTANCIA_DIM

MAX_HAND = 10
MAX_FIELD = 10
MAX_GY_EACH = 8
MAX_EXILE_EACH = 4
N_SLOTS = (
    MAX_HAND + MAX_FIELD + MAX_FIELD + MAX_GY_EACH + MAX_GY_EACH + MAX_EXILE_EACH + MAX_EXILE_EACH
)  # 54
GLOBAL_DIM = 2 + 2 * len(_MANA_KEYS) + len(PHASES) + 8 + len(_DECISION_MODES)
N_ACTIONS = 16
ACTION_TYPE_DIM = len(_ACTION_TYPES)
ACTION_BLOCK_DIM = ACTION_TYPE_DIM + 2  # tipo + 2 índices
OBS_DIM = N_SLOTS * FICHA_DIM + GLOBAL_DIM + N_ACTIONS * ACTION_BLOCK_DIM

# Offsets útiles para probes
OFFSET_KEYWORDS = 8 + len(CARD_TYPES) + len(SUPERTYPES) + 3  # dentro de la ficha
IDX_FLYING = KEYWORDS.index("Flying")
IDX_REACH = KEYWORDS.index("Reach")
IDX_CREATE_TOKEN = EFFECTS.index("CreateToken")


def _one_hot(names: tuple[str, ...], value: str | None) -> list[float]:
    out = [0.0] * len(names)
    if value is None or value == "NONE":
        return out
    if value in names:
        out[names.index(value)] = 1.0
    return out


def _multi_hot(names: tuple[str, ...], values: list[str] | tuple[str, ...] | None) -> list[float]:
    out = [0.0] * len(names)
    for v in values or ():
        if v and v != "NONE" and v in names:
            out[names.index(v)] = 1.0
    return out


def _subtype_hash(subtypes: list[str] | tuple[str, ...], dim: int) -> list[float]:
    vec = [0.0] * dim
    for s in subtypes or ():
        if not s or s == "NONE":
            continue
        digest = hashlib.md5(s.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        vec[idx] = 1.0
    return vec


def _norm(x: float, scale: float = 20.0) -> float:
    return float(x) / scale


def _owner_bucket(owner: str | None) -> str:
    if owner in ("CONTROLLER", "OPPONENT", "SOURCE"):
        return owner
    return "OTHER"


def _scope_bucket(scope: str | None) -> str:
    if scope in ("CONTROLLER", "OPPONENT", "SOURCE"):
        return scope
    if scope in ("EVENT", "EVENT_OBJECT"):
        return "EVENT"
    return "OTHER"


def _amount_class_and_value(amount: Any) -> tuple[str, float]:
    if amount is None:
        return "other", 0.0
    if isinstance(amount, (int, float)):
        return "literal", _norm(float(amount), 10.0)
    if amount == "X":
        return "X", 0.0
    if isinstance(amount, str) and amount in ATTRIBUTES:
        return "attribute", 0.0
    if isinstance(amount, dict) or (isinstance(amount, str) and "COUNT" in amount):
        return "COUNT", 0.0
    if isinstance(amount, str) and amount.isdigit():
        return "literal", _norm(float(amount), 10.0)
    return "other", 0.0


def _duration_key(duration: Any) -> str:
    if duration is None:
        return "null"
    if duration in _DURATIONS:
        return duration
    return "other"


def _encode_condition(conds: list[dict] | None) -> list[float]:
    out = [0.0] * CONDITION_DIM
    if not conds:
        return out
    c = conds[0]
    out[0] = 1.0
    op = c.get("operator")
    if op in _OPERATORS:
        out[1 + _OPERATORS.index(op)] = 1.0
    left = c.get("left_operand")
    if isinstance(left, str) and left in ATTRIBUTES:
        out[1 + 6 + ATTRIBUTES.index(left)] = 1.0
    right = c.get("right_operand")
    try:
        out[1 + 6 + 10] = _norm(float(right), 20.0)
    except (TypeError, ValueError):
        out[1 + 6 + 10] = 0.0
    scope = _scope_bucket(c.get("left_scope"))
    out[1 + 6 + 10 + 1 + _SCOPES.index(scope)] = 1.0
    query = c.get("query") or {}
    base = 1 + 6 + 10 + 1 + 5
    z = query.get("target_zone")
    if z in ZONES:
        out[base + ZONES.index(z)] = 1.0
    rest = (query.get("restrictions") or {})
    types = rest.get("types") or []
    for t in types:
        if t in CARD_TYPES:
            out[base + 7 + CARD_TYPES.index(t)] = 1.0
    owner = _scope_bucket(query.get("target_owner"))
    out[base + 7 + 7 + _SCOPES.index(owner)] = 1.0
    # padding hasta 47 (5 huecos finales ya incluidos en el layout si hace falta)
    return out[:CONDITION_DIM]


def _encode_token_body(token_def: dict | None) -> list[float]:
    out = [0.0] * TOKEN_BODY_DIM
    if not token_def:
        return out
    out[0] = _norm(float(token_def.get("power") or 0), 10.0)
    out[1] = _norm(float(token_def.get("toughness") or 0), 10.0)
    types = token_def.get("card_types") or token_def.get("types") or []
    out[2:2 + len(CARD_TYPES)] = _multi_hot(CARD_TYPES, types)
    kws = token_def.get("keywords") or []
    base = 2 + len(CARD_TYPES)
    out[base:base + len(KEYWORDS)] = _multi_hot(KEYWORDS, kws)
    base += len(KEYWORDS)
    subs = token_def.get("subtypes") or []
    out[base:base + TOKEN_SUBTYPE_HASH_DIM] = _subtype_hash(subs, TOKEN_SUBTYPE_HASH_DIM)
    base += TOKEN_SUBTYPE_HASH_DIM
    colors = token_def.get("colors") or token_def.get("color_indicator") or []
    for i, c in enumerate(_COLORS):
        if c in colors or c.lower() in [str(x).lower() for x in colors]:
            out[base + i] = 1.0
    out[-1] = 1.0
    return out


def _encode_target(info: dict | None) -> list[float]:
    info = info or {}
    out: list[float] = []
    out.append(1.0 if info.get("requires_target") else 0.0)
    out.extend(_one_hot(_OWNERS, _owner_bucket(info.get("target_owner"))))
    out.extend(_one_hot(ZONES, info.get("target_zone")))
    rest = info.get("restrictions") or {}
    out.extend(_multi_hot(CARD_TYPES, rest.get("types") or []))
    count = info.get("target_count") or {}
    mn = count.get("min")
    mx = count.get("max")
    try:
        out.append(_norm(float(mn if mn is not None else 0), 5.0))
    except (TypeError, ValueError):
        out.append(0.0)
    unbounded = mx == "*" or mx is None
    try:
        out.append(0.0 if unbounded else _norm(float(mx), 5.0))
    except (TypeError, ValueError):
        out.append(0.0)
    out.append(1.0 if unbounded else 0.0)
    assert len(out) == TARGET_DIM
    return out


def encode_efecto(efecto: dict | None) -> list[float]:
    if not efecto:
        return [0.0] * EFFECT_DIM
    out: list[float] = []
    out.extend(_one_hot(EFFECTS, efecto.get("effect_type")))
    cls, val = _amount_class_and_value((efecto.get("parameters") or {}).get("amount"))
    out.append(val)
    out.extend(_one_hot(_AMOUNT_CLASSES, cls))
    out.extend(_one_hot(_DURATIONS, _duration_key((efecto.get("parameters") or {}).get("duration"))))
    out.append(1.0 if efecto.get("is_optional") else 0.0)
    dest = (efecto.get("parameters") or {}).get("destination_zone")
    out.extend(_one_hot(ZONES, dest))
    out.extend(_encode_target(efecto.get("target_info")))
    out.extend(_encode_condition(efecto.get("effect_conditions")))
    out.extend(_encode_token_body((efecto.get("parameters") or {}).get("token_definition")))
    assert len(out) == EFFECT_DIM, len(out)
    return out


def encode_habilidad(hab: dict | None) -> list[float]:
    if not hab:
        return [0.0] * ABILITY_DIM
    out: list[float] = []
    out.extend(_one_hot(ABILITIES, hab.get("ability_type")))
    trigger = hab.get("trigger") or {}
    ev = trigger.get("event_type")
    if ev == "NONE":
        ev = None
    out.extend(_one_hot(EVENTS, ev))
    out.extend(_multi_hot(ZONES, hab.get("zones_active")))
    cost_types = [c.get("cost_type") for c in (hab.get("costs") or []) if isinstance(c, dict)]
    out.extend(_multi_hot(COSTS, cost_types))
    efectos = list(hab.get("effects") or [])
    for i in range(MAX_EFECTOS):
        out.extend(encode_efecto(efectos[i] if i < len(efectos) else None))
    assert len(out) == ABILITY_DIM, len(out)
    return out


def encode_definicion(definicion: Definicion) -> list[float]:
    coste = parse_mana(definicion.mana_cost)
    out: list[float] = [
        _norm(coste.generico, 10.0),
        float(coste.colores.get("W", 0)),
        float(coste.colores.get("U", 0)),
        float(coste.colores.get("B", 0)),
        float(coste.colores.get("R", 0)),
        float(coste.colores.get("G", 0)),
        1.0 if coste.tiene_x else 0.0,
        _norm(coste_mana_convertido(definicion), 10.0),
    ]
    out.extend(_multi_hot(CARD_TYPES, definicion.card_types))
    supers = [s for s in definicion.supertypes if s and s != "NONE"]
    out.extend(_multi_hot(SUPERTYPES, supers))
    has_pt = definicion.power is not None or definicion.toughness is not None
    try:
        out.append(_norm(float(definicion.power), 10.0) if definicion.power is not None else 0.0)
    except (TypeError, ValueError):
        out.append(0.0)
    try:
        out.append(
            _norm(float(definicion.toughness), 10.0) if definicion.toughness is not None else 0.0
        )
    except (TypeError, ValueError):
        out.append(0.0)
    out.append(1.0 if has_pt else 0.0)
    out.extend(_multi_hot(KEYWORDS, definicion.keywords))
    out.extend(_subtype_hash(definicion.subtypes, SUBTYPE_HASH_DIM))
    # colores derivados del coste
    for c in _COLORS:
        out.append(1.0 if coste.colores.get(c, 0) > 0 else 0.0)
    assert len(out) == HEADER_DIM, len(out)
    abs_ = list(definicion.abilities)
    for i in range(MAX_HABILIDADES):
        out.extend(encode_habilidad(abs_[i] if i < len(abs_) else None))
    assert len(out) == DEFINICION_DIM, len(out)
    return out


def encode_instancia(
    carta: Objeto | None,
    partida,
    yo_id: str,
    mapa: dict[str, int] | None = None,
) -> list[float]:
    if carta is None:
        return [0.0] * INSTANCIA_DIM
    out: list[float] = [
        1.0,
        _norm(float(partida.poder(carta)), 10.0) if "Creature" in carta.definicion.card_types else 0.0,
        _norm(float(partida.resistencia(carta)), 10.0)
        if "Creature" in carta.definicion.card_types
        else 0.0,
        1.0 if carta.girada else 0.0,
        1.0 if carta.entro_este_turno else 0.0,
        1.0 if carta.atacando else 0.0,
        1.0 if carta.bloqueando else 0.0,
        _norm(float(carta.danio), 10.0),
    ]
    for name in COUNTERS:
        out.append(_norm(float(carta.contadores.get(name, 0)), 5.0))
    out.extend(_one_hot(ZONES, carta.zona))
    out.append(1.0 if carta.es_ficha else 0.0)
    out.append(1.0 if carta.controlador == yo_id else 0.0)
    enlace = -1.0
    if carta.anexada_a is not None:
        vivo = partida.objeto_vivo(carta.anexada_a)
        if vivo is not None and vivo.zona == "Battlefield":
            if mapa is not None:
                enlace = float(mapa.get(vivo.object_id, -1))
            else:
                enlace = 1.0
    out.append(enlace)
    assert len(out) == INSTANCIA_DIM, len(out)
    return out


def encode_ficha(
    carta: Objeto | None,
    partida,
    yo_id: str,
    mapa: dict[str, int] | None = None,
) -> list[float]:
    if carta is None:
        return [0.0] * FICHA_DIM
    return encode_definicion(carta.definicion) + encode_instancia(carta, partida, yo_id, mapa)


def _ultimos(ids: list[str], n: int) -> list[str | None]:
    tomados = list(ids[-n:]) if ids else []
    while len(tomados) < n:
        tomados.insert(0, None)
    return tomados[-n:]


def construir_slots(adaptador, jugador_idx: int) -> tuple[list[str | None], dict[str, int]]:
    """Devuelve la lista de object_id por hueco y el mapa object_id → índice."""
    yo = adaptador.jugadores[jugador_idx]
    rival = adaptador.jugadores[1 - jugador_idx]
    slots: list[str | None] = []
    for i in range(MAX_HAND):
        slots.append(yo.zonas["Hand"][i] if i < len(yo.zonas["Hand"]) else None)
    for i in range(MAX_FIELD):
        slots.append(yo.zonas["Battlefield"][i] if i < len(yo.zonas["Battlefield"]) else None)
    for i in range(MAX_FIELD):
        slots.append(
            rival.zonas["Battlefield"][i] if i < len(rival.zonas["Battlefield"]) else None
        )
    for oid in _ultimos(yo.zonas["Graveyard"], MAX_GY_EACH):
        slots.append(oid)
    for oid in _ultimos(rival.zonas["Graveyard"], MAX_GY_EACH):
        slots.append(oid)
    for oid in _ultimos(yo.zonas["Exile"], MAX_EXILE_EACH):
        slots.append(oid)
    for oid in _ultimos(rival.zonas["Exile"], MAX_EXILE_EACH):
        slots.append(oid)
    assert len(slots) == N_SLOTS, len(slots)
    mapa = {oid: i for i, oid in enumerate(slots) if oid is not None}
    return slots, mapa


def encode_global(adaptador, jugador_idx: int) -> list[float]:
    yo = adaptador.jugadores[jugador_idx]
    rival = adaptador.jugadores[1 - jugador_idx]
    out: list[float] = [
        _norm(yo.vida, 20.0),
        _norm(rival.vida, 20.0),
    ]
    for j in (yo, rival):
        for k in _MANA_KEYS:
            out.append(_norm(float(j.mana.get(k, 0)), 10.0))
    fase = _FASE_A_ONTO.get(adaptador.fase)
    out.extend(_one_hot(PHASES, fase))
    out.append(_norm(float(len(adaptador.partida.pila)), 5.0))
    out.append(float(adaptador.tierras_jugadas))
    out.append(_norm(float(len(yo.zonas["Hand"])), 10.0))
    out.append(_norm(float(len(yo.zonas["Library"])), 60.0))
    out.append(_norm(float(len(rival.zonas["Hand"])), 10.0))
    out.append(_norm(float(len(rival.zonas["Library"])), 60.0))
    out.append(_norm(float(len(adaptador.atacantes)), 10.0))
    out.append(1.0 if adaptador.indice_activo == jugador_idx else 0.0)
    modo = adaptador.modo_decision or "menu"
    if modo not in _DECISION_MODES:
        modo = "otro"
    out.extend(_one_hot(_DECISION_MODES, modo))
    assert len(out) == GLOBAL_DIM, len(out)
    return out


def _slot_de(mapa: dict[str, int], oid: str | None) -> float:
    if oid is None:
        return -1.0
    return float(mapa.get(oid, -1))


def encode_accion(accion: dict | None, mapa: dict[str, int]) -> list[float]:
    if not accion:
        return [0.0] * ACTION_TYPE_DIM + [-1.0, -1.0]
    tipo = accion.get("tipo")
    out = _one_hot(_ACTION_TYPES, tipo if tipo in _ACTION_TYPES else None)
    a = _slot_de(mapa, accion.get("carta_id") or accion.get("blocker_id"))
    b = _slot_de(mapa, accion.get("attacker_id") or accion.get("objetivo_id"))
    if tipo in ("elegir_objetivo", "elegir_scry"):
        b = _slot_de(mapa, accion.get("carta_id"))
        a = -1.0
    out.extend([a, b])
    assert len(out) == ACTION_BLOCK_DIM
    return out


class ObservadorOntologia:
    """Observación plana ficha_v1 para MagicStarterEnv."""

    def obtener_estado(self, adaptador, jugador_idx: int, acciones_legales: list[dict] | None = None) -> np.ndarray:
        slots, mapa = construir_slots(adaptador, jugador_idx)
        yo = adaptador.jugadores[jugador_idx]
        partida = adaptador.partida
        out: list[float] = []
        for oid in slots:
            carta = partida.objetos[oid] if oid is not None else None
            out.extend(encode_ficha(carta, partida, yo.id, mapa))
        out.extend(encode_global(adaptador, jugador_idx))
        legales = list(acciones_legales or [])
        for i in range(N_ACTIONS):
            out.extend(encode_accion(legales[i] if i < len(legales) else None, mapa))
        arr = np.asarray(out, dtype=np.float32)
        assert arr.shape == (OBS_DIM,), (arr.shape, OBS_DIM)
        return arr


def offsets_ficha() -> dict[str, int]:
    """Offsets dentro de una ficha (para probes)."""
    return {
        "header": 0,
        "keywords": OFFSET_KEYWORDS,
        "flying": OFFSET_KEYWORDS + IDX_FLYING,
        "reach": OFFSET_KEYWORDS + IDX_REACH,
        "definicion": 0,
        "instancia": DEFINICION_DIM,
        "ficha": FICHA_DIM,
    }
