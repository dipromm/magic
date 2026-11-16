"""Traduce el Starter Kit de Bloomburrow a JSON-LD V1.2.

Lee el Oracle Text oficial guardado en bloomburrow_oracle.json, escribe una
carta por fichero y añade a la ontología solo las clases que no se pueden
componer con las que ya existían. Las acepta por ontology_store, igual que
una propuesta marcada con Y en el CLI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "traduccion"))

from ontology_store import (  # noqa: E402
    apply_accepted_proposals,
    load_or_build_dictionary,
    save_json_atomic,
    save_rdf_atomic,
)

ORACLE_PATH = Path(__file__).with_name("bloomburrow_oracle.json")
STARTER_PATH = Path(__file__).with_name("bloomburrow_starter.json")
OUT_DIR = Path(__file__).with_name("bloomburrow_jsonld")
OWL_PATH = str(ROOT / "IA_OWL" / "OWL_tfg.rdf")
JSON_PATH = str(ROOT / "IA_JSON" / "ontologia_motor.json")

CONTEXT = "https://mtg-engine.example.org/schema/v1"
SUPERTYPES = {"Basic", "Legendary", "Snow"}
CARD_TYPES = {
    "Artifact", "Creature", "Enchantment", "Instant",
    "Land", "Planeswalker", "Sorcery",
}

PROPOSALS = [
    {
        "keyword": "gain life",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "GainLife",
        "description": (
            "El jugador indicado gana una cantidad de vidas. amount es un entero "
            "o un cálculo dinámico. No destruye, no hace daño y no se niega con pérdida de vidas."
        ),
    },
    {
        "keyword": "add mana",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "AddMana",
        "description": (
            "Añade amount maná del color string_value (WHITE, BLUE, BLACK, RED o GREEN) "
            "a la reserva del jugador target. Una elección entre colores son modos "
            "distintos de este mismo efecto, no una clase nueva."
        ),
    },
    {
        "keyword": "gets +N power",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "ModifyPower",
        "description": (
            "Suma amount al poder del objetivo durante duration. amount puede ser "
            "negativo. No fija el poder: para asignar poder y resistencia al mismo "
            "valor sigue existiendo SetPowerToughness."
        ),
    },
    {
        "keyword": "gets +N toughness",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "ModifyToughness",
        "description": (
            "Suma amount a la resistencia del objetivo durante duration. amount puede "
            "ser negativo. Un bonus +X/+Y son un ModifyPower y un ModifyToughness."
        ),
    },
    {
        "keyword": "scry",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "Scry",
        "description": (
            "El controlador mira las primeras amount cartas de su biblioteca y puede "
            "poner cualquiera de ellas en el fondo, en cualquier orden. El resto vuelve "
            "al tope en el orden que elija."
        ),
    },
    {
        "keyword": "look at",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "Look",
        "description": (
            "El controlador mira en privado las primeras amount cartas de la zona "
            "indicada, sin revelarlas. Si amount es 1, esa carta queda como resultado "
            "del efecto y el efecto siguiente puede nombrarla con PREVIOUS_TARGET."
        ),
    },
    {
        "keyword": "reveal until",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "RevealUntil",
        "description": (
            "Revela cartas del tope de la biblioteca hasta revelar una que cumpla "
            "restrictions, incluida esa. La carta que cumple es el resultado del efecto. "
            "Las demás quedan con el estado Revealed hasta que termine la habilidad, "
            "para que un MoveZone posterior las mueva sin volverse a buscar."
        ),
    },
    {
        "keyword": "doesn't untap",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "PreventUntap",
        "description": (
            "Mientras duration esté activo, el permanente objetivo no se endereza "
            "en el paso de enderezar de su controlador. No lo gira por sí mismo."
        ),
    },
    {
        "keyword": "enters tapped",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "EnterTapped",
        "description": (
            "Efecto de reemplazo: este permanente entra al campo de batalla girado. "
            "No es un disparo de Tap después de entrar."
        ),
    },
    {
        "keyword": "copy spell",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "CopySpell",
        "description": (
            "Copia el hechizo objetivo, que está en la pila. El controlador de la "
            "habilidad puede elegir nuevos objetivos para la copia. Esa elección forma "
            "parte de copiar un hechizo y no es otro efecto."
        ),
    },
    {
        "keyword": "enter as a copy",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "EnterAsCopy",
        "description": (
            "Este permanente entra como copia del objeto objetivo. token_definition "
            "solo lista excepciones: poder o resistencia no nulos sustituyen los de la "
            "copia; subtypes, keywords y abilities se añaden; una lista vacía o un "
            "poder nulo no cambia ese campo. Si el efecto es opcional y se rechaza, "
            "el permanente conserva sus características impresas."
        ),
    },
    {
        "keyword": "attach",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "Attach",
        "description": (
            "Anexa la fuente al permanente objetivo. Si la ability lleva "
            "implements_keyword Enchant, el objetivo se elige al lanzar el aura y "
            "la anexión ocurre una vez al resolverse. Si lleva Equip, es la resolución "
            "de la habilidad activada y puede repetirse pagando su coste."
        ),
    },
    {
        "keyword": "can't be blocked",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "CantBeBlocked",
        "description": (
            "La criatura objetivo no puede ser bloqueada durante duration. "
            "No le otorga una keyword y no impide que ella misma bloquee."
        ),
    },
    {
        "keyword": "costs less",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "ReduceManaCost",
        "description": (
            "Los hechizos que cumplen el objetivo cuestan amount maná genérico menos "
            "para ser lanzados. No modifica el texto ni el coste de habilidades activadas. "
            "Se aplica mientras se lanza el hechizo, no al resolverse."
        ),
    },
    {
        "keyword": "token copy",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "CreateCopyToken",
        "description": (
            "Crea amount fichas que son copia de la fuente. token_definition solo "
            "lista excepciones, con la misma regla que EnterAsCopy: un valor nulo o "
            "una lista vacía copia ese campo; un número sustituye poder o resistencia; "
            "una lista con elementos añade subtipos, keywords o habilidades."
        ),
    },
    {
        "keyword": "may play",
        "category": "Effect",
        "block_kind": "effect_type",
        "proposed_constant": "GrantPlay",
        "description": (
            "El controlador puede jugar la carta objetivo durante duration, sea tierra "
            "o hechizo, respetando el momento en que las reglas permiten jugarla. "
            "No la mueve de zona hasta que se juega."
        ),
    },
    {
        "keyword": "sacrifice",
        "category": "TriggerEvent",
        "block_kind": "trigger_event",
        "proposed_constant": "Sacrifice",
        "description": (
            "Un permanente es sacrificado. Con subject null, el evento es que se "
            "sacrifica la fuente. No equivale a Dies: destruir la fuente no dispara "
            "este evento si no ha sido sacrificada."
        ),
    },
    {
        "keyword": "deals damage",
        "category": "TriggerEvent",
        "block_kind": "trigger_event",
        "proposed_constant": "DealsDamage",
        "description": (
            "La fuente inflige daño. subject describe al receptor. Se dispara una vez "
            "por cada objeto que recibe daño de la fuente y cumple el filtro."
        ),
    },
    {
        "keyword": "prowess",
        "category": "KeywordAbility",
        "block_kind": "keyword_ability",
        "proposed_constant": "Prowess",
        "description": (
            "Siempre que el controlador lanza un hechizo que no es criatura, esta "
            "criatura gana +1/+1 hasta el final del turno. No tiene coste. Se lista "
            "en keywords y no se vuelve a escribir como habilidad de la carta. "
            "Otorgarla a otras criaturas es AddKeyword con string_value Prowess."
        ),
    },
    {
        "keyword": "offspring",
        "category": "KeywordAbility",
        "block_kind": "keyword_ability",
        "proposed_constant": "Offspring",
        "description": (
            "Coste adicional opcional de maná al lanzar la criatura. El importe va en "
            "OptionalPayMana, no en el nombre de la keyword. Si se paga, al entrar "
            "crea una ficha copia 1/1 mediante CreateCopyToken. Las abilities que lo "
            "implementan llevan implements_keyword Offspring; el motor no añade el "
            "recordatorio otra vez."
        ),
    },
    {
        "keyword": "you may pay",
        "category": "Cost",
        "block_kind": "cost_type",
        "proposed_constant": "OptionalPayMana",
        "description": (
            "Coste adicional de maná que se puede pagar al lanzar el hechizo. amount "
            "es el coste entre llaves. Si se paga, OptionalCostPaid de ese objeto pasa "
            "a 1. Si no se paga, el hechizo se lanza igual y el atributo queda en 0."
        ),
    },
    {
        "keyword": "mana spent",
        "category": "CardAttribute",
        "block_kind": "other",
        "proposed_constant": "ManaSpent",
        "description": (
            "Cantidad total de maná gastada para lanzar este hechizo, incluyendo el "
            "valor elegido de X y los costes adicionales. No es el coste de maná impreso."
        ),
    },
    {
        "keyword": "total power",
        "category": "CardAttribute",
        "block_kind": "other",
        "proposed_constant": "TotalPower",
        "description": (
            "Suma del poder actual de los objetos que describe query en la misma "
            "comparación. Se evalúa en el momento de resolver el efecto."
        ),
    },
    {
        "keyword": "spells cast this turn",
        "category": "CardAttribute",
        "block_kind": "other",
        "proposed_constant": "MatchingCastCountThisTurn",
        "description": (
            "Número de hechizos que el jugador del scope ha lanzado este turno, "
            "incluido el hechizo del evento, que cumplen el subject del grupo y las "
            "comparaciones de ese grupo cuyo objeto comparado es el hechizo. Dentro de "
            "condition_groups cada grupo cuenta con su propio filtro. Vale 1 cuando "
            "el hechizo actual es el primero que cumple ese filtro."
        ),
    },
    {
        "keyword": "optional cost paid",
        "category": "CardAttribute",
        "block_kind": "other",
        "proposed_constant": "OptionalCostPaid",
        "description": (
            "1 si al lanzar este objeto se pagó su OptionalPayMana; 0 en caso contrario."
        ),
    },
    {
        "keyword": "name",
        "category": "CardAttribute",
        "block_kind": "other",
        "proposed_constant": "Name",
        "description": (
            "Nombre en inglés del objeto. Compararlo entre dos scopes distingue "
            "identidades de carta sin convertir el nombre en una constante."
        ),
    },
    {
        "keyword": "other",
        "category": "Status",
        "block_kind": "status",
        "proposed_constant": "Other",
        "description": "El objeto no es la fuente de la habilidad.",
    },
    {
        "keyword": "token",
        "category": "Status",
        "block_kind": "status",
        "proposed_constant": "Token",
        "description": "El objeto es una ficha. Nontoken sigue siendo la negación.",
    },
    {
        "keyword": "noncreature",
        "category": "Status",
        "block_kind": "status",
        "proposed_constant": "Noncreature",
        "description": "El objeto no tiene el tipo de carta Creature.",
    },
    {
        "keyword": "entered this turn",
        "category": "Status",
        "block_kind": "status",
        "proposed_constant": "EnteredThisTurn",
        "description": "El permanente entró al campo de batalla en este turno.",
    },
    {
        "keyword": "self",
        "category": "Status",
        "block_kind": "status",
        "proposed_constant": "Self",
        "description": (
            "El objeto del coste o del efecto es la fuente. Sirve cuando un coste "
            "de sacrificar podría, en otras cartas, apuntar a otro permanente."
        ),
    },
    {
        "keyword": "revealed",
        "category": "Status",
        "block_kind": "status",
        "proposed_constant": "Revealed",
        "description": (
            "Marca temporal de las cartas reveladas por RevealUntil que no son la "
            "carta que cumplió la condición. Dura solo hasta el final de la habilidad."
        ),
    },
]


def rst(types=None, subtypes=None, modifiers=None, supertypes=None, op=None):
    types = list(types or [])
    subtypes = list(subtypes or [])
    modifiers = list(modifiers or [])
    supertypes = list(supertypes or [])
    if op is None:
        op = "NONE" if not (types or subtypes or modifiers or supertypes) else "AND"
    return {
        "logical_operator": op,
        "types": types,
        "subtypes": subtypes,
        "supertypes": supertypes,
        "modifiers": modifiers,
    }


def tgt(requires, owner, zone, lo, hi, restrictions, extra=None):
    info = {
        "requires_target": requires,
        "target_owner": owner,
        "target_zone": zone,
        "target_count": {"min": lo, "max": hi},
        "restrictions": restrictions,
    }
    if extra:
        info["additional_targets"] = extra
    return info


def prm(amount=None, duration=None, chooser=None, string_value=None,
        destination_zone=None, token_definition=None, **extra):
    data = {
        "amount": amount,
        "duration": duration,
        "chooser": chooser,
        "string_value": string_value,
        "destination_zone": destination_zone,
        "token_definition": token_definition,
    }
    data.update(extra)
    return data


def eff(order, kind, target, parameters, conditions=None, optional=False,
        mode_id=None, condition_operator=None):
    data = {
        "effect_order": order,
        "mode_id": mode_id,
        "is_optional": optional,
        "effect_type": kind,
        "target_info": target,
        "effect_conditions": conditions or [],
        "parameters": parameters,
    }
    if condition_operator:
        data["condition_operator"] = condition_operator
    return data


def cmp(left, left_scope, op, right, right_scope=None, query=None):
    data = {
        "condition_type": "COMPARISON",
        "left_operand": left,
        "left_scope": left_scope,
        "operator": op,
        "right_operand": right,
        "right_scope": right_scope,
    }
    if query is not None:
        data["query"] = query
    return data


def qcount(zone, owner, types=None, subtypes=None, modifiers=None, op=None):
    return {
        "target_zone": zone,
        "target_owner": owner,
        "restrictions": rst(types, subtypes, modifiers, op=op),
    }


def dcount(query):
    return {
        "dynamic_calculation": "COUNT",
        "distinct_property": None,
        "multiplier": None,
        "offset": None,
        "source_ref": None,
        "attribute": None,
        "query": query,
    }


def dattr(source, attribute):
    return {
        "dynamic_calculation": "ATTRIBUTE_REFERENCE",
        "distinct_property": None,
        "multiplier": None,
        "offset": None,
        "source_ref": source,
        "attribute": attribute,
        "query": None,
    }


def cost(kind, amount=None, restrictions=None):
    return {
        "cost_type": kind,
        "amount": amount,
        "restrictions": restrictions if restrictions is not None else rst(),
    }


def abil(order, kind, zones, effects, costs=None, trigger=None, modal=None,
         implements=None):
    data = {
        "ability_order": order,
        "ability_type": kind,
        "zones_active": zones,
        "modal_choices": modal,
        "costs": costs or [],
        "trigger": trigger or {"event_type": "NONE", "conditions": []},
        "effects": effects,
    }
    if implements:
        data["implements_keyword"] = implements
    return data


def trig(event, conditions=None, subject=None, groups=None):
    data = {"event_type": event, "conditions": conditions or []}
    if subject is not None:
        data["subject"] = subject
    if groups is not None:
        data["condition_operator"] = "OR"
        data["condition_groups"] = groups
    return data


def subj(owner, types=None, subtypes=None, modifiers=None, zone=None, op=None):
    return {
        "target_owner": owner,
        "target_zone": zone,
        "restrictions": rst(types, subtypes, modifiers, op=op),
    }


def token(power, toughness, colors, subtypes, keywords=None, abilities=None,
          card_types=None):
    return {
        "power": power,
        "toughness": toughness,
        "colors": colors,
        "card_types": ["Creature"] if card_types is None else card_types,
        "subtypes": subtypes,
        "keywords": keywords or [],
        "abilities": abilities or [],
    }


def copy_exceptions(power=None, toughness=None, subtypes=None, keywords=None):
    return {
        "power": power,
        "toughness": toughness,
        "colors": [],
        "card_types": [],
        "subtypes": list(subtypes or []),
        "keywords": list(keywords or []),
        "abilities": [],
    }


def self_perm():
    return tgt(False, "SOURCE", "Battlefield", 1, 1, rst())


def attached():
    return tgt(False, "ATTACHED", "Battlefield", 0, 1, rst(["Creature"]))


def your_creatures(subtypes=None, modifiers=None, op=None):
    return tgt(
        False, "CONTROLLER", "Battlefield", 0, "*",
        rst(["Creature"], subtypes, modifiers, op=op),
    )


def who(owner, requires=False, lo=1, hi=1):
    return tgt(requires, owner, None, lo, hi, rst(["Player"]))


def creature(owner, requires=True, lo=1, hi=1, subtypes=None, modifiers=None, extra=None):
    return tgt(
        requires, owner, "Battlefield", lo, hi,
        rst(["Creature"], subtypes, modifiers), extra,
    )


def library_top():
    return tgt(False, "CONTROLLER", "Library", 1, 1, rst())


def previous(zone, types=None, modifiers=None, lo=1, hi=1):
    return tgt(False, "PREVIOUS_TARGET", zone, lo, hi, rst(types, modifiers=modifiers))


def event_obj(zone="Battlefield", types=None):
    return tgt(False, "EVENT_OBJECT", zone, 1, 1, rst(types))


def spell(order, mana, effects):
    return abil(order, "SpellAbility", ["Stack"], effects, costs=[cost("PayMana", mana)])


def mana_ability(order, colors):
    if len(colors) == 1:
        effects = [eff(
            1, "AddMana", who("CONTROLLER"),
            prm(1, chooser="CONTROLLER", string_value=colors[0]),
        )]
        modal = None
    else:
        effects = [
            eff(
                index, "AddMana", who("CONTROLLER"),
                prm(1, chooser="CONTROLLER", string_value=color),
                mode_id=index,
            )
            for index, color in enumerate(colors, 1)
        ]
        modal = {"min": 1, "max": 1}
    return abil(
        order, "ActivatedAbility", ["Battlefield"], effects,
        costs=[cost("TapCost")], modal=modal,
    )


def enter_tapped(order):
    return abil(order, "StaticAbility", ["Battlefield"], [
        eff(1, "EnterTapped", self_perm(), prm()),
    ])


def gain_on_enter(order, amount):
    return abil(order, "TriggeredAbility", ["Battlefield"], [
        eff(1, "GainLife", who("CONTROLLER"), prm(amount)),
    ], trigger=trig("EnterTheBattlefield"))


def enchant_creature(order):
    return abil(order, "StaticAbility", ["Stack"], [
        eff(1, "Attach", creature(None), prm(duration="PERMANENT")),
    ], implements="Enchant")


def pump(order, power, toughness, target, duration, conditions=None, start=1):
    effects = []
    cursor = start
    if power is not None:
        effects.append(eff(cursor, "ModifyPower", target, prm(power, duration), conditions))
        cursor += 1
    if toughness is not None:
        effects.append(eff(
            cursor, "ModifyToughness", target, prm(toughness, duration), conditions,
        ))
    return effects


def keywords_until(start, names, target, duration, conditions=None):
    return [
        eff(start + offset, "AddKeyword", target, prm(duration=duration, string_value=name), conditions)
        for offset, name in enumerate(names)
    ]


def rabbit_token():
    return token(1, 1, ["WHITE"], ["Rabbit"])


def rabbit_and_scry():
    home = tgt(False, "CONTROLLER", "Battlefield", 1, 1, rst())
    return [
        eff(1, "CreateToken", home, prm(1, token_definition=rabbit_token())),
        eff(2, "Scry", library_top(), prm(1)),
    ]


def count_lands():
    return qcount("Battlefield", "CONTROLLER", ["Land"])


def count_creatures():
    return qcount("Battlefield", "CONTROLLER", ["Creature"])


def count_tokens():
    return qcount("Battlefield", "CONTROLLER", ["Creature"], modifiers=["Token"])


def count_rabbits():
    return qcount("Battlefield", "CONTROLLER", ["Creature"], subtypes=["Rabbit"])


def count_otters():
    return qcount("Battlefield", "CONTROLLER", ["Creature"], subtypes=["Otter"])


def cast_group(types=None, subtypes=None, extra=None):
    conditions = [cmp("MatchingCastCountThisTurn", "CONTROLLER", "EQUALS", 1)]
    if extra:
        conditions.extend(extra)
    return {
        "subject": subj("CONTROLLER", types, subtypes, zone="Stack"),
        "conditions": conditions,
    }


def alania_trigger():
    return trig("Cast", groups=[
        cast_group(["Instant"]),
        cast_group(["Sorcery"]),
        cast_group(
            subtypes=["Otter"],
            extra=[cmp("Name", "EVENT_OBJECT", "NOT_EQUAL", "Name", "SOURCE")],
        ),
    ])


KEYWORDS = {
    "Byrke, Long Ear of the Law": ["Vigilance"],
    "Serra Redeemer": ["Flying"],
    "Colossification": ["Enchant"],
    "Fecund Greenshell": ["Reach"],
    "Finneas, Ace Archer": ["Reach", "Vigilance"],
    "Warren Elder": ["NONE"],
    "Pileated Provisioner": ["Flying"],
    "Carrot Cake": ["NONE"],
    "Rabbit Response": ["NONE"],
    "Repel Calamity": ["NONE"],
    "Hop to It": ["NONE"],
    "Druid of the Spade": ["NONE"],
    "Treeguard Duo": ["NONE"],
    "Rabid Bite": ["NONE"],
    "Giant Growth": ["NONE"],
    "Clifftop Lookout": ["Reach"],
    "Burrowguard Mentor": ["Trample"],
    "Blossoming Sands": ["NONE"],
    "Plains": ["NONE"],
    "Forest": ["NONE"],
    "Bria, Riptide Rogue": ["Prowess"],
    "Mockingbird": ["Flying"],
    "Mind Spring": ["NONE"],
    "Alania, Divergent Storm": ["NONE"],
    "Sword of Vengeance": ["Equip"],
    "Bellowing Crier": ["NONE"],
    "Thieving Otter": ["NONE"],
    "Waterspout Warden": ["NONE"],
    "Pearl of Wisdom": ["NONE"],
    "Charmed Sleep": ["Enchant"],
    "Alania's Pathmaker": ["NONE"],
    "Flame Lash": ["NONE"],
    "Coruscation Mage": ["Offspring"],
    "Quaketusk Boar": ["Reach", "Trample", "Haste"],
    "Rabid Gnaw": ["NONE"],
    "Stormcatch Mentor": ["Haste", "Prowess"],
    "Swiftwater Cliffs": ["NONE"],
    "Island": ["NONE"],
    "Mountain": ["NONE"],
}


def abilities_for(name: str, mana: str | None) -> list:
    end = "UNTIL_END_OF_TURN"
    static = "WHILE_STATIC_ACTIVE"
    you = "CONTROLLER"
    home = tgt(False, you, "Battlefield", 1, 1, rst())

    if name == "Byrke, Long Ear of the Law":
        return [
            abil(1, "TriggeredAbility", ["Battlefield"], [
                eff(1, "AddCounter", creature(None, lo=0, hi=2), prm(1, string_value="PlusOnePlusOne")),
            ], trigger=trig("EnterTheBattlefield")),
            abil(2, "TriggeredAbility", ["Battlefield"], [
                eff(
                    1, "AddCounter", event_obj(types=["Creature"]),
                    prm(dattr("EVENT_OBJECT", "PlusOnePlusOne"), string_value="PlusOnePlusOne"),
                ),
            ], trigger=trig(
                "Attacks",
                [cmp("PlusOnePlusOne", "EVENT_OBJECT", "GREATER_OR_EQUAL", 1)],
                subject=subj(you, ["Creature"], zone="Battlefield"),
            )),
        ]
    if name == "Serra Redeemer":
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(1, "AddCounter", event_obj(types=["Creature"]), prm(2, string_value="PlusOnePlusOne")),
        ], trigger=trig(
            "EnterTheBattlefield",
            [cmp("Power", "EVENT_OBJECT", "LESS_OR_EQUAL", 2)],
            subject=subj(you, ["Creature"], modifiers=["Other"], zone="Battlefield"),
        ))]
    if name == "Colossification":
        return [
            enchant_creature(1),
            abil(2, "TriggeredAbility", ["Battlefield"], [
                eff(1, "Tap", attached(), prm()),
            ], trigger=trig("EnterTheBattlefield")),
            abil(3, "StaticAbility", ["Battlefield"], pump(0, 20, 20, attached(), static)),
        ]
    if name == "Fecund Greenshell":
        return [
            abil(1, "StaticAbility", ["Battlefield"], pump(
                0, 2, 2, your_creatures(), static,
                [cmp("COUNT", you, "GREATER_OR_EQUAL", 10, query=count_lands())],
            )),
            abil(2, "TriggeredAbility", ["Battlefield"], [
                eff(1, "Look", library_top(), prm(1)),
                eff(
                    2, "MoveZone", previous("Library", ["Land"]),
                    prm(1, destination_zone="Battlefield"), optional=True,
                ),
                eff(3, "Tap", previous("Battlefield"), prm(if_effect_accepted=2)),
                eff(
                    4, "MoveZone", previous("Library", modifiers=["Nonland"]),
                    prm(1, destination_zone="Hand"),
                ),
            ], trigger=trig(
                "EnterTheBattlefield",
                [cmp("Toughness", "EVENT_OBJECT", "GREATER_THAN", "Power", "EVENT_OBJECT")],
                subject=subj(you, ["Creature"], zone="Battlefield"),
            )),
        ]
    if name == "Finneas, Ace Archer":
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(
                1, "AddCounter", your_creatures(modifiers=["Other", "Token"]),
                prm(1, string_value="PlusOnePlusOne"),
            ),
            eff(
                2, "AddCounter",
                your_creatures(subtypes=["Rabbit"], modifiers=["Other", "Nontoken"]),
                prm(1, string_value="PlusOnePlusOne"),
            ),
            eff(
                3, "DrawCard", who(you), prm(1),
                [cmp("TotalPower", you, "GREATER_OR_EQUAL", 10, query=count_creatures())],
            ),
        ], trigger=trig("Attacks"))]
    if name == "Warren Elder":
        return [abil(1, "ActivatedAbility", ["Battlefield"], pump(
            0, 1, 1, your_creatures(), end,
        ), costs=[cost("PayMana", "{3}{W}")])]
    if name == "Pileated Provisioner":
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(
                1, "AddCounter", creature(you),
                prm(1, string_value="PlusOnePlusOne"),
                [cmp("Flying", "TARGET_1", "EQUALS", 0)],
            ),
        ], trigger=trig("EnterTheBattlefield"))]
    if name == "Carrot Cake":
        shared = rabbit_and_scry()
        return [
            abil(1, "TriggeredAbility", ["Battlefield"], shared, trigger=trig("EnterTheBattlefield")),
            abil(2, "TriggeredAbility", ["Battlefield"], shared, trigger=trig("Sacrifice")),
            abil(3, "ActivatedAbility", ["Battlefield"], [
                eff(1, "GainLife", who(you), prm(3)),
            ], costs=[
                cost("PayMana", "{2}"),
                cost("TapCost"),
                cost("SacrificeCost", 1, rst(["Artifact"], modifiers=["Self"])),
            ]),
        ]
    if name == "Rabbit Response":
        return [spell(1, mana, [
            *pump(0, 2, 1, your_creatures(), end),
            eff(
                3, "Scry", library_top(), prm(2),
                [cmp("COUNT", you, "GREATER_OR_EQUAL", 1, query=count_rabbits())],
            ),
        ])]
    if name == "Repel Calamity":
        return [spell(1, mana, [eff(
            1, "Destroy", creature(None), prm(),
            [
                cmp("Power", "TARGET_1", "GREATER_OR_EQUAL", 4),
                cmp("Toughness", "TARGET_1", "GREATER_OR_EQUAL", 4),
            ],
            condition_operator="OR",
        )])]
    if name == "Hop to It":
        return [spell(1, mana, [eff(
            1, "CreateToken", home, prm(3, token_definition=rabbit_token()),
        )])]
    if name == "Druid of the Spade":
        gate = [cmp("COUNT", you, "GREATER_OR_EQUAL", 1, query=count_tokens())]
        return [abil(1, "StaticAbility", ["Battlefield"], [
            *pump(0, 2, None, self_perm(), static, gate),
            *keywords_until(2, ["Trample"], self_perm(), static, gate),
        ])]
    if name == "Treeguard Duo":
        amount = dcount(count_creatures())
        chosen = creature(you)
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            *keywords_until(1, ["Vigilance"], chosen, end),
            *pump(1, amount, amount, chosen, end, start=2),
        ], trigger=trig("EnterTheBattlefield"))]
    if name == "Rabid Bite":
        yours = creature(you)
        return [spell(1, mana, [eff(
            1, "DealDamage", creature("NOT_CONTROLLER", extra=[yours]),
            prm(dattr("TARGET_2", "Power"), damage_source="TARGET_2"),
        )])]
    if name == "Giant Growth":
        return [spell(1, mana, pump(0, 3, 3, creature(None), end))]
    if name == "Clifftop Lookout":
        revealed = tgt(False, you, "Library", 0, "*", rst(modifiers=["Revealed"]))
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(1, "RevealUntil", tgt(False, you, "Library", 1, 1, rst(["Land"])), prm()),
            eff(2, "MoveZone", previous("Library"), prm(1, destination_zone="Battlefield")),
            eff(3, "Tap", previous("Battlefield"), prm()),
            eff(
                4, "MoveZone", revealed,
                prm(destination_zone="Library", placement="BOTTOM_RANDOM"),
            ),
        ], trigger=trig("EnterTheBattlefield"))]
    if name == "Burrowguard Mentor":
        return [abil(1, "StaticAbility", ["Battlefield"], [eff(
            1, "SetPowerToughness", self_perm(),
            prm(dcount(count_creatures()), static),
        )])]
    if name == "Blossoming Sands":
        return [enter_tapped(1), gain_on_enter(2, 1), mana_ability(3, ["GREEN", "WHITE"])]
    if name == "Plains":
        return [mana_ability(1, ["WHITE"])]
    if name == "Forest":
        return [mana_ability(1, ["GREEN"])]
    if name == "Bria, Riptide Rogue":
        return [
            abil(1, "StaticAbility", ["Battlefield"], keywords_until(
                1, ["Prowess"], your_creatures(modifiers=["Other"]), static,
            )),
            abil(2, "TriggeredAbility", ["Battlefield"], [
                eff(1, "CantBeBlocked", creature(you), prm(duration=end)),
            ], trigger=trig(
                "Cast",
                subject=subj(you, modifiers=["Noncreature"], zone="Stack"),
            )),
        ]
    if name == "Mockingbird":
        return [abil(1, "StaticAbility", ["Stack", "Battlefield"], [eff(
            1, "EnterAsCopy", creature(None, lo=0, hi=1),
            prm(token_definition=copy_exceptions(subtypes=["Bird"], keywords=["Flying"])),
            [cmp("ConvertedManaCost", "TARGET_1", "LESS_OR_EQUAL", "ManaSpent", "SOURCE")],
            optional=True,
        )])]
    if name == "Mind Spring":
        return [spell(1, mana, [eff(1, "DrawCard", who(you), prm("X"))])]
    if name == "Alania, Divergent Storm":
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(1, "DrawCard", who("OPPONENT", requires=True), prm(1), optional=True),
            eff(
                2, "CopySpell", event_obj("Stack"),
                prm(if_effect_accepted=1),
            ),
        ], trigger=alania_trigger())]
    if name == "Sword of Vengeance":
        return [
            abil(1, "StaticAbility", ["Battlefield"], [
                *pump(0, 2, None, attached(), static),
                *keywords_until(2, ["FirstStrike", "Vigilance", "Trample", "Haste"], attached(), static),
            ]),
            abil(2, "ActivatedAbility", ["Battlefield"], [
                eff(1, "Attach", creature(you), prm(duration="PERMANENT")),
            ], costs=[cost("PayMana", "{3}")], implements="Equip"),
        ]
    if name == "Bellowing Crier":
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(1, "DrawCard", who(you), prm(1)),
            eff(2, "Discard", tgt(False, you, "Hand", 1, 1, rst()), prm(1, chooser=you)),
        ], trigger=trig("EnterTheBattlefield"))]
    if name == "Thieving Otter":
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(1, "DrawCard", who(you), prm(1)),
        ], trigger=trig(
            "DealsDamage",
            subject=subj("OPPONENT", ["Player"]),
        ))]
    if name == "Waterspout Warden":
        entered = qcount(
            "Battlefield", you, ["Creature"], modifiers=["EnteredThisTurn", "Other"],
        )
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(
                1, "AddKeyword", self_perm(), prm(duration=end, string_value="Flying"),
                [cmp("COUNT", you, "GREATER_OR_EQUAL", 1, query=entered)],
            ),
        ], trigger=trig("Attacks"))]
    if name == "Pearl of Wisdom":
        return [
            abil(1, "StaticAbility", ["Hand", "Stack"], [eff(
                1, "ReduceManaCost", self_perm(), prm(1, static),
                [cmp("COUNT", you, "GREATER_OR_EQUAL", 1, query=count_otters())],
            )]),
            spell(2, mana, [eff(1, "DrawCard", who(you), prm(2))]),
        ]
    if name == "Charmed Sleep":
        return [
            enchant_creature(1),
            abil(2, "TriggeredAbility", ["Battlefield"], [
                eff(1, "Tap", attached(), prm()),
            ], trigger=trig("EnterTheBattlefield")),
            abil(3, "StaticAbility", ["Battlefield"], [
                eff(1, "PreventUntap", attached(), prm(duration=static)),
            ]),
        ]
    if name == "Alania's Pathmaker":
        return [abil(1, "TriggeredAbility", ["Battlefield"], [
            eff(1, "MoveZone", library_top(), prm(1, destination_zone="Exile")),
            eff(
                2, "GrantPlay", previous("Exile"),
                prm(duration="UNTIL_END_OF_YOUR_NEXT_TURN"),
            ),
        ], trigger=trig("EnterTheBattlefield"))]
    if name == "Flame Lash":
        victim = tgt(
            True, None, "Battlefield", 1, 1,
            rst(["Creature", "Player", "Planeswalker"], op="OR"),
        )
        return [spell(1, mana, [eff(1, "DealDamage", victim, prm(4))])]
    if name == "Coruscation Mage":
        return [
            abil(
                1, "StaticAbility", ["Hand"], [],
                costs=[cost("OptionalPayMana", "{2}")], implements="Offspring",
            ),
            abil(2, "TriggeredAbility", ["Battlefield"], [eff(
                1, "CreateCopyToken", home,
                prm(1, token_definition=copy_exceptions(1, 1)),
            )], trigger=trig(
                "EnterTheBattlefield",
                [cmp("OptionalCostPaid", "SOURCE", "EQUALS", 1)],
            ), implements="Offspring"),
            abil(3, "TriggeredAbility", ["Battlefield"], [eff(
                1, "DealDamage", who("OPPONENT", lo=0, hi="*"), prm(1),
            )], trigger=trig(
                "Cast",
                subject=subj(you, modifiers=["Noncreature"], zone="Stack"),
            )),
        ]
    if name == "Quaketusk Boar":
        return []
    if name == "Rabid Gnaw":
        return [spell(1, mana, [
            *pump(0, 1, None, creature(you), end),
            eff(
                2, "DealDamage", creature("NOT_CONTROLLER"),
                prm(dattr("PREVIOUS_TARGET", "Power"), damage_source="PREVIOUS_TARGET"),
            ),
        ])]
    if name == "Stormcatch Mentor":
        spells = tgt(
            False, you, "Stack", 0, "*",
            rst(["Instant", "Sorcery"], op="OR"),
        )
        return [abil(1, "StaticAbility", ["Battlefield"], [eff(
            1, "ReduceManaCost", spells, prm(1, static),
        )])]
    if name == "Swiftwater Cliffs":
        return [enter_tapped(1), gain_on_enter(2, 1), mana_ability(3, ["BLUE", "RED"])]
    if name == "Island":
        return [mana_ability(1, ["BLUE"])]
    if name == "Mountain":
        return [mana_ability(1, ["RED"])]
    raise KeyError(name)


def parse_type_line(type_line: str):
    left, _, right = type_line.partition("—")
    words = left.split()
    supertypes = [word for word in words if word in SUPERTYPES]
    card_types = [word for word in words if word in CARD_TYPES]
    subtypes = right.split() if right.strip() else []
    return supertypes or ["NONE"], card_types, subtypes


def stat(value):
    if value is None or value == "":
        return None
    if value == "*":
        return "*"
    return int(value)


def assemble(raw: dict) -> dict:
    name = raw["name"]
    supertypes, card_types, subtypes = parse_type_line(raw["type_line"])
    mana = raw.get("mana_cost") or None
    return {
        "@context": CONTEXT,
        "@type": "Card",
        "name": name,
        "oracle_text": raw["oracle_text"],
        "mana_cost": mana,
        "supertypes": supertypes,
        "card_types": card_types,
        "subtypes": subtypes,
        "keywords": KEYWORDS[name],
        "base_attributes": {
            "power": stat(raw.get("power")),
            "toughness": stat(raw.get("toughness")),
            "loyalty": None,
            "color_indicator": [],
        },
        "requires_human_review": False,
        "ontology_proposal": [],
        "abilities": abilities_for(name, mana),
    }


def slug(name: str) -> str:
    cleaned = name.lower().replace("'", "")
    out = []
    prev = False
    for char in cleaned:
        if char.isascii() and char.isalnum():
            out.append(char)
            prev = False
        elif not prev:
            out.append("_")
            prev = True
    return "".join(out).strip("_")


def accept_proposals():
    graph, dictionary = load_or_build_dictionary(OWL_PATH, JSON_PATH)
    apply_accepted_proposals(graph, dictionary, PROPOSALS)
    for key, values in list(dictionary.items()):
        if isinstance(values, list):
            dictionary[key] = sorted(set(values))
    save_json_atomic(JSON_PATH, dictionary)
    save_rdf_atomic(OWL_PATH, graph)
    for bak in Path(OWL_PATH).parent.glob("OWL_tfg.rdf.bak.*"):
        bak.unlink()
    return dictionary


def validate(card: dict, dictionary: dict) -> list[str]:
    errors = []
    constants = {item for values in dictionary.values() if isinstance(values, list) for item in values}
    reserved = {
        "NONE", "WHITE", "BLUE", "BLACK", "RED", "GREEN", "X", "*",
        "SOURCE", "TARGET_1", "TARGET_2", "CONTROLLER", "OPPONENT", "NOT_CONTROLLER",
        "ATTACHED", "EVENT_OBJECT", "PREVIOUS_TARGET", "ANY",
        "UNTIL_END_OF_TURN", "UNTIL_END_OF_YOUR_NEXT_TURN", "WHILE_STATIC_ACTIVE",
        "PERMANENT", "BOTTOM", "BOTTOM_RANDOM",
        "EQUALS", "NOT_EQUAL", "GREATER_THAN", "LESS_THAN", "GREATER_OR_EQUAL", "LESS_OR_EQUAL",
        "AND", "OR", "COUNT", "ATTRIBUTE_REFERENCE",
    }
    scopes = {
        None, "SOURCE", "TARGET_1", "TARGET_2", "CONTROLLER", "OPPONENT",
        "NOT_CONTROLLER", "ATTACHED", "EVENT_OBJECT", "PREVIOUS_TARGET",
    }

    def known(value) -> bool:
        if value is None or isinstance(value, (int, float, bool)):
            return True
        if isinstance(value, str) and (value in reserved or value in constants):
            return True
        return False

    if not card["keywords"]:
        errors.append("keywords vacío")

    def check_conditions(conditions, label):
        for condition in conditions:
            if not known(condition["left_operand"]) or not known(condition["right_operand"]):
                errors.append(
                    f"{label} {condition['left_operand']} {condition['right_operand']}"
                )
            if condition["left_scope"] not in scopes or condition["right_scope"] not in scopes:
                errors.append(f"{label} scope")

    for index, ability in enumerate(card["abilities"], 1):
        if ability["ability_order"] != index:
            errors.append(f"ability_order {ability['ability_order']}")
    for keyword in card["keywords"]:
        if keyword != "NONE" and keyword not in dictionary["KeywordAbility"]:
            errors.append(f"keyword {keyword}")
    for ability in card["abilities"]:
        if ability["ability_type"] not in dictionary["Ability"]:
            errors.append(f"ability {ability['ability_type']}")
        if ability["ability_type"] == "SpellAbility" and not (
            "Instant" in card["card_types"] or "Sorcery" in card["card_types"]
        ):
            errors.append("SpellAbility en permanente")
        trigger = ability["trigger"]
        if trigger["event_type"] != "NONE" and trigger["event_type"] not in dictionary["TriggerEvent"]:
            errors.append(f"trigger {trigger['event_type']}")
        check_conditions(trigger.get("conditions") or [], "trigger")
        for group in trigger.get("condition_groups") or []:
            check_conditions(group.get("conditions") or [], "grupo")
        implemented = ability.get("implements_keyword")
        if implemented and implemented not in dictionary["KeywordAbility"]:
            errors.append(f"implements {implemented}")
        effect_orders = [effect["effect_order"] for effect in ability["effects"]]
        if effect_orders != list(range(1, len(effect_orders) + 1)):
            errors.append(f"effect_order {effect_orders}")
        for effect in ability["effects"]:
            if effect["effect_type"] not in dictionary["Effect"]:
                errors.append(f"effect {effect['effect_type']}")
            params = effect["parameters"]
            missing = {"amount", "duration", "chooser", "string_value", "destination_zone", "token_definition"} - params.keys()
            if missing:
                errors.append(f"params {missing}")
            if effect["effect_type"] == "MoveZone" and not params.get("destination_zone"):
                errors.append("MoveZone sin destino")
            if effect["effect_type"] in {"CreateToken", "CreateCopyToken", "EnterAsCopy"}:
                token_def = params.get("token_definition")
                if not isinstance(token_def, dict):
                    errors.append(f"{effect['effect_type']} sin token_definition")
                elif effect["effect_type"] == "CreateToken":
                    if not isinstance(token_def.get("keywords"), list) or not isinstance(token_def.get("abilities"), list):
                        errors.append("token incompleto")
            count = effect["target_info"]["target_count"]
            if not isinstance(count, dict) or "min" not in count or "max" not in count:
                errors.append("target_count")
            owner = effect["target_info"]["target_owner"]
            if owner not in scopes:
                errors.append(f"owner {owner}")
            for condition in effect["effect_conditions"]:
                if not known(condition["left_operand"]) or not known(condition["right_operand"]):
                    errors.append(f"comparación {condition['left_operand']} {condition['right_operand']}")
                if condition["left_scope"] not in scopes or condition["right_scope"] not in scopes:
                    errors.append("scope de condición")
    return errors


def main() -> None:
    dictionary = accept_proposals()
    oracle_cards = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    by_name = {card["name"]: assemble(card) for card in oracle_cards}
    OUT_DIR.mkdir(exist_ok=True)
    failed = False
    for name, card in by_name.items():
        errors = validate(card, dictionary)
        if errors:
            failed = True
            print(name)
            for error in errors:
                print(" -", error)
        path = OUT_DIR / f"{slug(name)}.json"
        path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        card["_file"] = path.name

    starter = json.loads(STARTER_PATH.read_text(encoding="utf-8"))
    index = {
        "fuente": starter["fuente"],
        "oracle": "Scryfall, texto oficial guardado en mazos/bloomburrow_oracle.json",
        "formato": CONTEXT,
        "mazos": [],
    }
    for deck in starter["mazos"]:
        translated = []
        for entry in deck["cartas"]:
            card = by_name[entry["nombre"]]
            translated.append({
                "cantidad": entry["cantidad"],
                "nombre": entry["nombre"],
                "archivo": card["_file"],
            })
        index["mazos"].append({
            "id": deck["id"],
            "nombre": deck["nombre"],
            "colores": deck["colores"],
            "cartas": translated,
        })
    (OUT_DIR / "_indice.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    starter["estado"] = (
        "Cada carta está traducida a JSON-LD en mazos/bloomburrow_jsonld. "
        "El índice conserva las cantidades."
    )
    starter["traduccion"] = "mazos/bloomburrow_jsonld/_indice.json"
    STARTER_PATH.write_text(
        json.dumps(starter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if failed:
        raise SystemExit(1)
    print(f"{len(by_name)} cartas en {OUT_DIR}")


if __name__ == "__main__":
    main()
