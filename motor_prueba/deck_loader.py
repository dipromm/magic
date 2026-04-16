import json
import re
from pathlib import Path
from typing import Any

from cartas import Creature, Land


_MANA_RE = re.compile(r"\{([^}]+)\}")


def _parse_mana_cost_to_int(mana_cost: str) -> int:
    """
    Convierte strings tipo \"{2}{B}{R}\" a un coste aproximado entero.
    - símbolos numéricos suman
    - símbolos no numéricos cuentan como 1
    """
    if not mana_cost:
        return 0
    total = 0
    for sym in _MANA_RE.findall(mana_cost):
        try:
            total += int(sym)
        except ValueError:
            total += 1
    return total


def load_archidekt_dataset(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Dataset debe ser una lista de mazos")
    return data


def build_poc_deck_from_archidekt(deck_obj: dict[str, Any]) -> list[Any]:
    """
    Construye un mazo compatible con el motor PoC actual:
    - Lands -> Land
    - Creatures -> Creature (si se puede inferir power/toughness; si no, defaults 2/2)
    - El resto se ignora por ahora
    """
    result: list[Any] = []
    for entry in deck_obj.get("mazo_principal", []):
        name = entry.get("name", "Desconocida")
        types = entry.get("types") or []
        qty = int(entry.get("cantidad", 1) or 1)

        is_land = "Land" in types
        is_creature = "Creature" in types

        if is_land:
            for _ in range(qty):
                result.append(Land(name=name))
            continue

        if is_creature:
            power_raw = entry.get("power", "")
            tough_raw = entry.get("toughness", "")
            try:
                power = int(power_raw)
                tough = int(tough_raw)
            except Exception:
                power, tough = 2, 2
            mana_cost = _parse_mana_cost_to_int(entry.get("manaCost", "") or "")
            for _ in range(qty):
                result.append(Creature(name=name, mana_cost=mana_cost, power=power, toughness=tough))
            continue

    return result

