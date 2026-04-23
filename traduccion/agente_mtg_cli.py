"""
agente_mtg_cli.py
=================
Script CLI para traducir el Oracle Text de cartas de Magic: The Gathering
a formato JSON-LD polimórfico, usando LiteLlm contra la API de PoliGPT (UPV).

Las condiciones de estado se modelan como comparaciones descompuestas
(left_operand + operator + right_operand) para evitar constantes monolíticas
y garantizar que el motor de Python pueda evaluar cualquier condición
sin código ad-hoc por carta.

Flujo:
  1. Carga (o genera) el diccionario canónico desde OWL -> JSON.
  2. Inyecta el diccionario en el prompt del LLM.
  3. Traduce Oracle Text -> JSON-LD con estructura polimórfica.
  4. Si hay cuarentena: HITL por bloque [Y/N/C].
     - [Y] persiste la propuesta en JSON + OWL y re-traduce la carta.
     - [C] pide corrección humana y vuelve a llamar al LLM.

Ejecución (CMD):
  set POLIGPT_API_KEY=tu_clave
  python agente_mtg_cli.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import litellm

from ontology_store import (
    apply_accepted_proposals,
    load_or_build_dictionary,
    save_json_atomic,
    save_rdf_atomic,
)

# ---------------------------------------------------------------------------
# 1. CONFIGURACIÓN DEL ENTORNO Y LLM
# ---------------------------------------------------------------------------

_API_KEY: str = (os.environ.get("POLIGPT_API_KEY") or "").strip()
if not _API_KEY:
    sys.exit(
        "[ERROR] Variable de entorno POLIGPT_API_KEY no definida.\n"
        "Configúrala antes de ejecutar:\n"
        '  PowerShell:  $env:POLIGPT_API_KEY = "sk-XXXX"\n'
        '  CMD:         set POLIGPT_API_KEY=sk-XXXX\n'
        '  Linux/Mac:   export POLIGPT_API_KEY="sk-XXXX"'
    )

_MODEL: str = os.environ.get("LITELLM_MODEL", "openai/gpt-oss-120b")
_API_BASE: str = os.environ.get("POLIGPT_API_BASE", "https://api.poligpt.upv.es/")
_MAX_TOKENS: int = int(os.environ.get("LITELLM_MAX_TOKENS", "8192"))
_TEMPERATURE: float = float(os.environ.get("LITELLM_TEMPERATURE", "0.15"))

os.environ.setdefault("OPENAI_API_KEY", _API_KEY)

# ---------------------------------------------------------------------------
# 2. RUTAS POR DEFECTO (relativas a la raíz del repositorio)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OWL_PATH = str(_REPO_ROOT / "IA_OWL" / "OWL_tfg.rdf")
_JSON_PATH = str(_REPO_ROOT / "IA_JSON" / "ontologia_motor.json")

# ---------------------------------------------------------------------------
# 3. PROMPT DEL SISTEMA (generado dinámicamente con el diccionario vigente)
# ---------------------------------------------------------------------------


def build_system_prompt(ontologia_json: str) -> str:
    """Genera el system prompt V1.2 con ontología, esquema, reglas y ejemplos."""

    # ------------------------------------------------------------------
    # Sección 1: Rol y objetivo
    # ------------------------------------------------------------------
    sec_rol = (
        "Eres un traductor experto de Oracle Text de Magic: The Gathering a "
        "JSON-LD estructurado para un motor de simulación.\n"
        "Tu salida alimenta un sistema de BLOQUES DE CONSTRUCCIÓN reutilizables. "
        "El motor ejecuta combinando bloques atómicos (efectos, costes, triggers, "
        "restricciones), NO programando carta a carta. Si inventas constantes "
        "compuestas o monolíticas (ej. DestroyAndGainLife, Target_Creature_Hand, "
        "COUNT_LANDS_CONTROLLED), el motor NO las reconocerá y la traducción será "
        "INÚTIL.\n"
        "Devuelve SOLO un JSON válido. Sin bloques de código markdown (```), "
        "sin texto antes ni después."
    )

    # ------------------------------------------------------------------
    # Sección 2: Ontología inyectada
    # ------------------------------------------------------------------
    sec_ontologia = (
        "=== ONTOLOGÍA PERMITIDA ===\n"
        f"{ontologia_json}\n"
        "=== FIN ONTOLOGÍA ===\n"
        "ANTES de usar cualquier constante en tu JSON, BÚSCALA literalmente en "
        "esta ontología. Si no aparece, activa cuarentena. No asumas que existe "
        "porque suene razonable."
    )

    # ------------------------------------------------------------------
    # Sección 3: Esquema V1.2 obligatorio
    # ------------------------------------------------------------------
    sec_esquema = (
        "=== ESQUEMA JSON-LD V1.2 (OBLIGATORIO) ===\n"
        "{\n"
        '  "@context": "https://mtg-engine.example.org/schema/v1",\n'
        '  "@type": "Card",\n'
        '  "name": "<nombre en inglés>",\n'
        '  "mana_cost": "<string exacto o null>",\n'
        '  "supertypes": ["<LEGENDARY | BASIC | SNOW>"],\n'
        '  "card_types": ["<CREATURE | INSTANT | SORCERY | ARTIFACT | ENCHANTMENT | LAND | PLANESWALKER>"],\n'
        '  "subtypes": ["<Human | Wizard | Aura | etc>"],\n'
        '  "base_attributes": {\n'
        '    "power": "<entero | * | null>",\n'
        '    "toughness": "<entero | * | null>",\n'
        '    "loyalty": "<entero | null>",\n'
        '    "color_indicator": ["<WHITE | BLUE | BLACK | RED | GREEN>"]\n'
        "  },\n"
        '  "requires_human_review": false,\n'
        '  "ontology_proposal": [],\n'
        '  "abilities": [\n'
        "    {\n"
        '      "ability_order": 1,\n'
        '      "ability_type": "<SPELL | ACTIVATED | TRIGGERED | STATIC>",\n'
        '      "zones_active": ["<STACK | BATTLEFIELD | HAND | GRAVEYARD | EXILE | COMMAND>"],\n'
        '      "modal_choices": {"min": "<entero>", "max": "<entero>"} | null,\n'
        '      "costs": [\n'
        "        {\n"
        '          "cost_type": "<MANA | TAP | SACRIFICE | DISCARD | PAY_LIFE | REMOVE_COUNTER>",\n'
        '          "amount": "<entero | string | null>",\n'
        '          "restrictions": {\n'
        '            "logical_operator": "<AND | OR | NONE>",\n'
        '            "types": ["<CREATURE | ARTIFACT | ...>"],\n'
        '            "modifiers": ["<OTHER | NONLAND | UNTAPPED | NONTOKEN>"]\n'
        "          }\n"
        "        }\n"
        "      ],\n"
        '      "trigger": {\n'
        '        "event_type": "<ENTERS_BATTLEFIELD | DIES | UPKEEP | DRAW_STEP | CAST_SPELL | ATTACKS | BLOCKS | NONE>",\n'
        '        "conditions": [\n'
        "          {\n"
        '            "condition_type": "COMPARISON",\n'
        '            "left_operand": "<constante de la ontología>",\n'
        '            "left_scope": "<SOURCE | TARGET_1 | TARGET_2 | CONTROLLER | OPPONENT>",\n'
        '            "operator": "<EQUALS | NOT_EQUAL | GREATER_THAN | LESS_THAN | GREATER_OR_EQUAL | LESS_OR_EQUAL>",\n'
        '            "right_operand": "<constante de la ontología | entero>",\n'
        '            "right_scope": "<SOURCE | TARGET_1 | TARGET_2 | CONTROLLER | OPPONENT | null>"\n'
        "          }\n"
        "        ]\n"
        "      },\n"
        '      "effects": [\n'
        "        {\n"
        '          "effect_order": 1,\n'
        '          "mode_id": "<entero | null>",\n'
        '          "is_optional": false,\n'
        '          "effect_type": "<constante de la ontología: DESTROY | DEAL_DAMAGE | DRAW_CARDS | ADD_MANA | EXILE | TAP | UNTAP | GAIN_LIFE | LOSE_LIFE | COUNTERSPELL | MOVE_ZONE | DEFINE_STATS | CREATE_TOKEN | ADD_COUNTER | REMOVE_COUNTER | SEARCH_LIBRARY | SHUFFLE | REVEAL | DISCARD | etc>",\n'
        '          "target_info": {\n'
        '            "requires_target": true | false,\n'
        '            "target_owner": "<CONTROLLER | OPPONENT | SOURCE | TARGET_1 | TARGET_2 | null>",\n'
        '            "target_zone": "<BATTLEFIELD | HAND | GRAVEYARD | LIBRARY | STACK | EXILE | COMMAND | null>  (zona ORIGEN del objetivo)",\n'
        '            "target_count": {"min": "<entero>", "max": "<entero | *>"},\n'
        '            "restrictions": {\n'
        '              "logical_operator": "<AND | OR | NONE>",\n'
        '              "types": ["<CREATURE | ARTIFACT | ENCHANTMENT | LAND | PLANESWALKER | PLAYER | SPELL | CARD | PERMANENT>"],\n'
        '              "modifiers": ["<OTHER | TAPPED | NONBLACK | NONTOKEN | ATTACKING | etc>"]\n'
        "            }\n"
        "          },\n"
        '          "effect_conditions": [\n'
        "            {\n"
        '              "condition_type": "COMPARISON",\n'
        '              "left_operand": "<constante de la ontología>",\n'
        '              "left_scope": "<SOURCE | TARGET_1 | CONTROLLER | OPPONENT>",\n'
        '              "operator": "<EQUALS | NOT_EQUAL | GREATER_THAN | LESS_THAN | GREATER_OR_EQUAL | LESS_OR_EQUAL>",\n'
        '              "right_operand": "<constante de la ontología | entero>",\n'
        '              "right_scope": "<SOURCE | TARGET_1 | CONTROLLER | OPPONENT | null>"\n'
        "            }\n"
        "          ],\n"
        '          "parameters": {\n'
        '            "amount": "<entero | X | null | dynamic_amount (ver abajo)>",\n'
        '            "duration": "<UNTIL_END_OF_TURN | PERMANENT | WHILE_CONDITION | null>",\n'
        '            "chooser": "<CONTROLLER | TARGET_1 | OPPONENT | null>",\n'
        '            "string_value": "<nombre de keyword | tipo de contador | color | null>",\n'
        '            "destination_zone": "<HAND | GRAVEYARD | EXILE | BATTLEFIELD | LIBRARY | COMMAND | null>  (SOLO para MOVE_ZONE)",\n'
        '            "token_definition": {\n'
        '              "power": "<entero | null>",\n'
        '              "toughness": "<entero | null>",\n'
        '              "colors": ["<WHITE | BLUE | BLACK | RED | GREEN>"],\n'
        '              "card_types": ["<CREATURE | ARTIFACT | ENCHANTMENT>"],\n'
        '              "subtypes": ["<Soldier | Spirit | Zombie | Treasure>"],\n'
        '              "keywords": ["<FLYING | HASTE | LIFELINK | VIGILANCE>"]\n'
        '            } | null  (SOLO para CREATE_TOKEN)\n'
        "          }\n"
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "\n"
        "--- dynamic_amount (cuando amount requiere cálculo en tiempo de juego) ---\n"
        "Si amount debe contar objetos o leer un atributo de otra entidad, usa este "
        "objeto EN LUGAR de un entero:\n"
        "{\n"
        '  "dynamic_calculation": "<COUNT | ATTRIBUTE_REFERENCE>",\n'
        '  "distinct_property": "<CARD_TYPES | NAMES | MANA_VALUES | COLORS | null>",\n'
        '  "multiplier": "<entero | null>",\n'
        '  "offset": "<entero | null>",\n'
        '  "source_ref": "<SOURCE | TARGET_1 | CONTROLLER | OPPONENT | null>",\n'
        '  "attribute": "<Power | Toughness | ConvertedManaCost | null>  (solo ATTRIBUTE_REFERENCE)",\n'
        '  "query": {  (solo COUNT)\n'
        '    "target_zone": "<BATTLEFIELD | GRAVEYARD | EXILE | LIBRARY | ALL_ZONES | null>",\n'
        '    "target_owner": "<CONTROLLER | OPPONENT | ANY | null>",\n'
        '    "restrictions": {"logical_operator": "...", "types": [...], "modifiers": [...]}\n'
        "  }\n"
        "}\n"
        "- COUNT: usa query para definir qué contar, ignora attribute.\n"
        "- ATTRIBUTE_REFERENCE: usa source_ref + attribute, ignora query.\n"
        "- multiplier para casos como 'twice the number of...' (multiplier: 2).\n"
        "- offset para ajustes fijos: '+1' -> offset: 1, '-2' -> offset: -2.\n"
        "- distinct_property para contar valores ÚNICOS de una propiedad entre "
        "las cartas filtradas (ej. Tarmogoyf cuenta tipos de carta distintos en "
        "cementerios -> distinct_property: CARD_TYPES). Si no se cuenta por "
        "propiedad única, dejar null.\n"
        "=== FIN ESQUEMA ==="
    )

    # ------------------------------------------------------------------
    # Sección 4: Reglas semánticas duras
    # ------------------------------------------------------------------
    sec_reglas = (
        "=== REGLAS SEMÁNTICAS (OBLIGATORIAS) ===\n"
        "R1. POLIMORFISMO ESTRICTO: Tienes PROHIBIDO inventar constantes "
        "combinadas o monolíticas. Ejemplos PROHIBIDOS: DestroyAndGainLife, "
        "Target_Creature_Hand, COUNT_LANDS_CONTROLLED, ManaValueEqualsChargeCounters. "
        "CORRECTO: descomponer en múltiples bloques atómicos dentro de effects[].\n"
        "\n"
        "R2. SPELL SOLO PARA INSTANT/SORCERY: su única ability es de tipo SPELL. "
        "Permanentes (Creature, Artifact, Enchantment, Land, Planeswalker) NUNCA "
        "tienen ability_type SPELL; su casteo se infiere del mana_cost en la raíz. "
        "El array abilities[] de un permanente solo contiene habilidades impresas "
        "(ACTIVATED, TRIGGERED, STATIC).\n"
        "\n"
        "R3. PERTENENCIA SOLO VIA target_owner: PROHIBIDO poner CONTROLLED_BY_YOU, "
        "OPPONENT_CONTROLS o similares en restrictions.modifiers. La pertenencia "
        "se indica ÚNICAMENTE con target_owner (CONTROLLER, OPPONENT, SOURCE, "
        "TARGET_1, TARGET_2, null). Para 'creatures you control': "
        "target_owner=CONTROLLER + restrictions.types=[CREATURE].\n"
        "\n"
        "R4. MOVE_ZONE: target_info.target_zone = zona de ORIGEN (donde está ahora). "
        "parameters.destination_zone = zona de DESTINO (a donde se mueve). "
        "Si effect_type es MOVE_ZONE, destination_zone es OBLIGATORIO (no null).\n"
        "\n"
        "R5. CREATE_TOKEN: parameters.token_definition es OBLIGATORIO como objeto "
        "estructurado. PROHIBIDO meter datos de token en string_value. "
        "parameters.amount indica cuántas fichas se crean.\n"
        "\n"
        "R6. CÁLCULOS DINÁMICOS: si amount requiere contar objetos en juego o "
        "leer un atributo (ej. 'damage equal to its power', 'number of creatures "
        "you control'), usa el objeto dynamic_amount. PROHIBIDO inventar constantes "
        "como COUNT_LANDS_CONTROLLED o POWER_OF_SOURCE.\n"
        "\n"
        "R7. MODALES: si la carta dice 'Choose one', 'Choose two', etc., pon "
        "modal_choices: {min: N, max: N} en la ability. Cada efecto lleva mode_id "
        "(entero) indicando a qué viñeta modal pertenece. Para 'Choose one or more': "
        "min=1, max=<total modos>. Si no es modal: modal_choices=null, mode_id=null.\n"
        "\n"
        "R8. target_count COMO RANGO: siempre objeto {min, max}. 'Up to two' = "
        "{min:0, max:2}. 'Target creature' = {min:1, max:1}. 'Any number' = "
        '{min:0, max:"*"}.\n'
        "\n"
        "R9. NULLABILIDAD: NUNCA omitas claves del esquema. Si un campo no aplica, "
        "pon null (escalares) o [] (arrays). parameters SIEMPRE tiene las 6 claves: "
        "amount, duration, chooser, string_value, destination_zone, token_definition.\n"
        "\n"
        "R10. NO BOOLEANS REDUNDANTES EN RAÍZ: PROHIBIDO añadir is_permanent, "
        "is_nontoken, is_spell, etc. El motor infiere eso de card_types. "
        "Permanent, Nonland, Nontoken solo se usan dentro de restrictions.\n"
        "\n"
        "R11. MULTIPLICIDAD: si la carta tiene varias habilidades independientes, "
        "crea múltiples objetos en abilities[]. Si una habilidad tiene varios "
        "efectos secuenciales, crea múltiples objetos en effects[]. Mantén "
        "ability_order y effect_order correlativos (1, 2, 3...).\n"
        "\n"
        "R12. MATEMÁTICAS COMPLEJAS: NUNCA inventes constantes para operaciones "
        "matemáticas (ej. PROHIBIDO: PLUS_ONE, MINUS_TWO, TARMOGOYF_COUNT). "
        "Usa los campos del objeto dynamic_amount:\n"
        "  - Si una carta dice 'plus 1' o 'plus one': usa offset: 1.\n"
        "  - Si una carta dice 'minus X': usa offset: -X (entero negativo).\n"
        "  - Si una carta requiere contar un atributo ÚNICO entre un grupo de "
        "cartas (ej. Tarmogoyf cuenta tipos de carta distintos en cementerios, "
        "NO cuenta cartas): usa distinct_property: CARD_TYPES.\n"
        "  - Si la cuenta aplica a TODOS los jugadores (no solo controller u "
        "opponent): usa target_owner: ANY.\n"
        "  - Si la cuenta abarca TODAS las zonas: usa target_zone: ALL_ZONES.\n"
        "\n"
        "R13. SUPERTIPOS: si el tipo de la carta incluye palabras como "
        "'Basic Land', 'Legendary Creature' o 'Snow Artifact', DEBES separar "
        "los conceptos. 'Basic', 'Legendary' y 'Snow' van EXCLUSIVAMENTE en "
        "supertypes. 'Land', 'Creature' o 'Artifact' van en card_types. "
        "Búscalos en la ontología en sus categorías respectivas.\n"
        "=== FIN REGLAS ==="
    )

    # ------------------------------------------------------------------
    # Sección 5: Regla de cuarentena
    # ------------------------------------------------------------------
    sec_cuarentena = (
        "=== REGLA DE CUARENTENA (HITL) ===\n"
        "Si falta CUALQUIER constante necesaria para modelar la carta que no "
        "exista en la ontología proporcionada:\n"
        '  - Pon "requires_human_review": true.\n'
        '  - Deja "abilities": [].\n'
        '  - Devuelve "ontology_proposal" como LISTA con una entrada por cada '
        "constante faltante.\n"
        "  - Estructura de cada propuesta:\n"
        "    {\n"
        '      "keyword": "<término detectado en el Oracle Text>",\n'
        '      "category": "<categoría OWL objetivo: Effect, Trigger_event, '
        "KeywordAbility, Counters, Zones, PhaseandStep, Ability, Cost, CardType, "
        'Status>",\n'
        '      "block_kind": "<effect_type | trigger_event | cost_type | '
        "keyword_ability | counter_type | zone | phase | status | "
        'card_type | other>",\n'
        '      "proposed_constant": "<NOMBRE_CONSTANTE_SUGERIDO>",\n'
        '      "description": "<descripción técnica GENÉRICA en español, '
        "independiente de la carta; define el concepto y su semántica de reglas "
        'para implementar en Python>"\n'
        "    }\n"
        "  - La descripción debe ser reusable para cualquier carta y NO mencionar "
        "cartas concretas.\n"
        "  - No inventes constantes fuera de la ontología para abilities/effects.\n"
        "=== FIN CUARENTENA ==="
    )

    # ------------------------------------------------------------------
    # Sección 6: Valores reservados
    # ------------------------------------------------------------------
    sec_reservados = (
        "=== VALORES RESERVADOS (Lista Blanca) ===\n"
        "Las siguientes palabras son parte de la sintaxis del motor y NO necesitan "
        "estar en la ontología:\n"
        "NONE, COMPARISON, "
        "EQUALS, GREATER_THAN, LESS_THAN, GREATER_OR_EQUAL, LESS_OR_EQUAL, NOT_EQUAL, "
        "TARGET, TARGET_1, TARGET_2, SOURCE, CONTROLLER, OPPONENT, "
        "ANY, ALL_ZONES, COUNT, ATTRIBUTE_REFERENCE, "
        "PERMANENT, UNTIL_END_OF_TURN, WHILE_STATIC_ACTIVE, WHILE_CONDITION, "
        "AND, OR, "
        "X, *, TRUE, FALSE"
        "y cualquier número entero.\n"
        "Usa NONE cuando un campo opcional no aplica "
        "(ej. event_type=NONE para instantes sin trigger).\n"
        "IMPORTANTE: Si un campo del JSON contiene uno de estos valores reservados, "
        "NO actives cuarentena por ese campo. Los valores reservados son punteros "
        "dinámicos o instrucciones del motor, no constantes de la ontología.\n"
        "TODAS las demás constantes (effect_type, event_type si no es NONE, "
        "cost_type, counter types, keywords, zones, attributes en COMPARISON, "
        "etc.) DEBEN existir en la ontología. Si NO aparecen, activa cuarentena.\n"
        "=== FIN RESERVADOS ==="
    )

    # ------------------------------------------------------------------
    # Sección 7: Checklist de autovalidación
    # ------------------------------------------------------------------
    sec_checklist = (
        "=== CHECKLIST DE AUTOVALIDACIÓN ===\n"
        "Antes de devolver tu respuesta, verifica internamente estos 8 puntos:\n"
        "1. ¿Todas las constantes que usé existen en la ontología o son reservadas?\n"
        "2. ¿Cada ability tiene ability_order correlativo (1, 2, 3...)?\n"
        "3. ¿Cada effect tiene effect_order correlativo?\n"
        "4. ¿parameters tiene las 6 claves (amount, duration, chooser, "
        "string_value, destination_zone, token_definition) aunque sean null?\n"
        "5. ¿target_count es objeto {min, max}, NO un entero?\n"
        "6. ¿No hay constantes combinadas/monolíticas (ej. DestroyAndGainLife)?\n"
        "7. ¿Si effect_type es MOVE_ZONE, destination_zone NO es null?\n"
        "8. ¿Si effect_type es CREATE_TOKEN, token_definition NO es null?\n"
        "Si algún punto falla, CORRIGE antes de responder.\n"
        "=== FIN CHECKLIST ==="
    )

    # ------------------------------------------------------------------
    # Sección 8: Few-shot examples
    # ------------------------------------------------------------------
    sec_ejemplos = (
        "=== EJEMPLOS DE TRADUCCIÓN ===\n"
        "\n"
        "--- Ejemplo 1: Lightning Bolt (Instant simple, SPELL, un efecto) ---\n"
        "Oracle Text: Lightning Bolt deals 3 damage to any target.\n"
        "JSON-LD:\n"
        "{\n"
        '  "@context": "https://mtg-engine.example.org/schema/v1",\n'
        '  "@type": "Card",\n'
        '  "name": "Lightning Bolt",\n'
        '  "mana_cost": "{R}",\n'
        '  "supertypes": [],\n'
        '  "card_types": ["INSTANT"],\n'
        '  "subtypes": [],\n'
        '  "base_attributes": {"power": null, "toughness": null, "loyalty": null, "color_indicator": []},\n'
        '  "requires_human_review": false,\n'
        '  "ontology_proposal": [],\n'
        '  "abilities": [{\n'
        '    "ability_order": 1,\n'
        '    "ability_type": "SPELL",\n'
        '    "zones_active": ["STACK"],\n'
        '    "modal_choices": null,\n'
        '    "costs": [{"cost_type": "MANA", "amount": "{R}", "restrictions": {"logical_operator": "NONE", "types": [], "modifiers": []}}],\n'
        '    "trigger": {"event_type": "NONE", "conditions": []},\n'
        '    "effects": [{\n'
        '      "effect_order": 1,\n'
        '      "mode_id": null,\n'
        '      "is_optional": false,\n'
        '      "effect_type": "DEAL_DAMAGE",\n'
        '      "target_info": {\n'
        '        "requires_target": true,\n'
        '        "target_owner": null,\n'
        '        "target_zone": "BATTLEFIELD",\n'
        '        "target_count": {"min": 1, "max": 1},\n'
        '        "restrictions": {"logical_operator": "OR", "types": ["CREATURE", "PLAYER", "PLANESWALKER"], "modifiers": []}\n'
        "      },\n"
        '      "effect_conditions": [],\n'
        '      "parameters": {"amount": 3, "duration": null, "chooser": null, "string_value": null, "destination_zone": null, "token_definition": null}\n'
        "    }]\n"
        "  }]\n"
        "}\n"
        "\n"
        "--- Ejemplo 2: Aether Vial (Artifact, TRIGGERED + ACTIVATED, COMPARISON, MOVE_ZONE) ---\n"
        "Oracle Text:\n"
        "At the beginning of your upkeep, you may put a charge counter on Aether Vial.\n"
        "{T}: You may put a creature card with mana value equal to the number of "
        "charge counters on Aether Vial from your hand onto the battlefield.\n"
        "JSON-LD:\n"
        "{\n"
        '  "@context": "https://mtg-engine.example.org/schema/v1",\n'
        '  "@type": "Card",\n'
        '  "name": "Aether Vial",\n'
        '  "mana_cost": "{1}",\n'
        '  "supertypes": [],\n'
        '  "card_types": ["ARTIFACT"],\n'
        '  "subtypes": [],\n'
        '  "base_attributes": {"power": null, "toughness": null, "loyalty": null, "color_indicator": []},\n'
        '  "requires_human_review": false,\n'
        '  "ontology_proposal": [],\n'
        '  "abilities": [\n'
        "    {\n"
        '      "ability_order": 1,\n'
        '      "ability_type": "TRIGGERED",\n'
        '      "zones_active": ["BATTLEFIELD"],\n'
        '      "modal_choices": null,\n'
        '      "costs": [],\n'
        '      "trigger": {"event_type": "UPKEEP", "conditions": []},\n'
        '      "effects": [{\n'
        '        "effect_order": 1,\n'
        '        "mode_id": null,\n'
        '        "is_optional": true,\n'
        '        "effect_type": "ADD_COUNTER",\n'
        '        "target_info": {\n'
        '          "requires_target": false,\n'
        '          "target_owner": "SOURCE",\n'
        '          "target_zone": "BATTLEFIELD",\n'
        '          "target_count": {"min": 1, "max": 1},\n'
        '          "restrictions": {"logical_operator": "NONE", "types": [], "modifiers": []}\n'
        "        },\n"
        '        "effect_conditions": [],\n'
        '        "parameters": {"amount": 1, "duration": null, "chooser": null, "string_value": "Charge", "destination_zone": null, "token_definition": null}\n'
        "      }]\n"
        "    },\n"
        "    {\n"
        '      "ability_order": 2,\n'
        '      "ability_type": "ACTIVATED",\n'
        '      "zones_active": ["BATTLEFIELD"],\n'
        '      "modal_choices": null,\n'
        '      "costs": [{"cost_type": "TAP", "amount": null, "restrictions": {"logical_operator": "NONE", "types": [], "modifiers": []}}],\n'
        '      "trigger": {"event_type": "NONE", "conditions": []},\n'
        '      "effects": [{\n'
        '        "effect_order": 1,\n'
        '        "mode_id": null,\n'
        '        "is_optional": true,\n'
        '        "effect_type": "MOVE_ZONE",\n'
        '        "target_info": {\n'
        '          "requires_target": false,\n'
        '          "target_owner": "CONTROLLER",\n'
        '          "target_zone": "HAND",\n'
        '          "target_count": {"min": 1, "max": 1},\n'
        '          "restrictions": {"logical_operator": "AND", "types": ["CREATURE"], "modifiers": []}\n'
        "        },\n"
        '        "effect_conditions": [\n'
        "          {\n"
        '            "condition_type": "COMPARISON",\n'
        '            "left_operand": "ConvertedManaCost",\n'
        '            "left_scope": "TARGET_1",\n'
        '            "operator": "EQUALS",\n'
        '            "right_operand": "Charge",\n'
        '            "right_scope": "SOURCE"\n'
        "          }\n"
        "        ],\n"
        '        "parameters": {"amount": 1, "duration": null, "chooser": null, "string_value": null, "destination_zone": "BATTLEFIELD", "token_definition": null}\n'
        "      }]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "\n"
        "--- Ejemplo 3: Raise the Alarm (Instant, CREATE_TOKEN con token_definition) ---\n"
        "Oracle Text: Create two 1/1 white Soldier creature tokens.\n"
        "JSON-LD:\n"
        "{\n"
        '  "@context": "https://mtg-engine.example.org/schema/v1",\n'
        '  "@type": "Card",\n'
        '  "name": "Raise the Alarm",\n'
        '  "mana_cost": "{1}{W}",\n'
        '  "supertypes": [],\n'
        '  "card_types": ["INSTANT"],\n'
        '  "subtypes": [],\n'
        '  "base_attributes": {"power": null, "toughness": null, "loyalty": null, "color_indicator": []},\n'
        '  "requires_human_review": false,\n'
        '  "ontology_proposal": [],\n'
        '  "abilities": [{\n'
        '    "ability_order": 1,\n'
        '    "ability_type": "SPELL",\n'
        '    "zones_active": ["STACK"],\n'
        '    "modal_choices": null,\n'
        '    "costs": [{"cost_type": "MANA", "amount": "{1}{W}", "restrictions": {"logical_operator": "NONE", "types": [], "modifiers": []}}],\n'
        '    "trigger": {"event_type": "NONE", "conditions": []},\n'
        '    "effects": [{\n'
        '      "effect_order": 1,\n'
        '      "mode_id": null,\n'
        '      "is_optional": false,\n'
        '      "effect_type": "CREATE_TOKEN",\n'
        '      "target_info": {\n'
        '        "requires_target": false,\n'
        '        "target_owner": "CONTROLLER",\n'
        '        "target_zone": "BATTLEFIELD",\n'
        '        "target_count": {"min": 1, "max": 1},\n'
        '        "restrictions": {"logical_operator": "NONE", "types": [], "modifiers": []}\n'
        "      },\n"
        '      "effect_conditions": [],\n'
        '      "parameters": {\n'
        '        "amount": 2,\n'
        '        "duration": null,\n'
        '        "chooser": null,\n'
        '        "string_value": null,\n'
        '        "destination_zone": null,\n'
        '        "token_definition": {\n'
        '          "power": 1,\n'
        '          "toughness": 1,\n'
        '          "colors": ["WHITE"],\n'
        '          "card_types": ["CREATURE"],\n'
        '          "subtypes": ["Soldier"],\n'
        '          "keywords": []\n'
        "        }\n"
        "      }\n"
        "    }]\n"
        "  }]\n"
        "}\n"
        "=== FIN EJEMPLOS ==="
    )

    # ------------------------------------------------------------------
    # Sección 9: Anti-ejemplos (prevenir alucinaciones por bias)
    # ------------------------------------------------------------------
    sec_anti_ejemplos = (
        "=== ANTI-EJEMPLOS (INCORRECTO vs CORRECTO) ===\n"
        "Estudia estos errores frecuentes. Si tu salida se parece a la columna "
        "INCORRECTO, PARA y corrígelo.\n"
        "\n"
        "1. Constante monolítica en amount:\n"
        '   INCORRECTO: "amount": "COUNT_LANDS_CONTROLLED"\n'
        '   CORRECTO:   "amount": {\n'
        '     "dynamic_calculation": "COUNT",\n'
        '     "distinct_property": null,\n'
        '     "multiplier": null,\n'
        '     "offset": null,\n'
        '     "source_ref": null,\n'
        '     "attribute": null,\n'
        '     "query": {\n'
        '       "target_zone": "BATTLEFIELD",\n'
        '       "target_owner": "CONTROLLER",\n'
        '       "restrictions": {"logical_operator": "AND", "types": ["LAND"], "modifiers": []}\n'
        "     }\n"
        "   }\n"
        "\n"
        "2. Referencia a atributo como constante:\n"
        '   INCORRECTO: "amount": "POWER_OF_SOURCE"\n'
        '   CORRECTO:   "amount": {\n'
        '     "dynamic_calculation": "ATTRIBUTE_REFERENCE",\n'
        '     "distinct_property": null,\n'
        '     "multiplier": null,\n'
        '     "offset": null,\n'
        '     "source_ref": "SOURCE",\n'
        '     "attribute": "Power",\n'
        '     "query": null\n'
        "   }\n"
        "\n"
        "3. Constante para operación matemática:\n"
        '   INCORRECTO: "amount": "PLUS_ONE" o "amount": "TARMOGOYF_COUNT"\n'
        "   CORRECTO (Tarmogoyf: 'power equal to the number of card types "
        "among cards in all graveyards, toughness is that number plus 1'):\n"
        '   Power -> "amount": {\n'
        '     "dynamic_calculation": "COUNT",\n'
        '     "distinct_property": "CARD_TYPES",\n'
        '     "multiplier": null,\n'
        '     "offset": null,\n'
        '     "source_ref": null,\n'
        '     "attribute": null,\n'
        '     "query": {"target_zone": "GRAVEYARD", "target_owner": "ANY", '
        '"restrictions": {"logical_operator": "AND", "types": ["CARD"], "modifiers": []}}\n'
        "   }\n"
        '   Toughness -> misma query pero con "offset": 1\n'
        "\n"
        "4. Token como string en vez de objeto:\n"
        '   INCORRECTO: "token_definition": null, "string_value": "1/1 white soldier"\n'
        '   CORRECTO:   "string_value": null, "token_definition": {\n'
        '     "power": 1, "toughness": 1,\n'
        '     "colors": ["WHITE"],\n'
        '     "card_types": ["CREATURE"],\n'
        '     "subtypes": ["Soldier"],\n'
        '     "keywords": []\n'
        "   }\n"
        "\n"
        "5. Pertenencia en modifiers en vez de target_owner:\n"
        '   INCORRECTO: "modifiers": ["CONTROLLED_BY_YOU"]\n'
        '   CORRECTO:   "target_owner": "CONTROLLER", "modifiers": []\n'
        "\n"
        "6. target_count como entero:\n"
        '   INCORRECTO: "target_count": 2\n'
        '   CORRECTO:   "target_count": {"min": 2, "max": 2}\n'
        "=== FIN ANTI-EJEMPLOS ==="
    )

    return "\n\n".join([
        sec_rol,
        sec_ontologia,
        sec_esquema,
        sec_reglas,
        sec_cuarentena,
        sec_reservados,
        sec_checklist,
        sec_ejemplos,
        sec_anti_ejemplos,
    ])

# ---------------------------------------------------------------------------
# 4. LLAMADA AL LLM
# ---------------------------------------------------------------------------


def _llamar_llm(mensajes: List[Dict[str, str]]) -> str:
    """Invoca litellm.completion y devuelve el texto de respuesta."""
    respuesta = litellm.completion(
        model=_MODEL,
        api_base=_API_BASE,
        api_key=_API_KEY,
        messages=mensajes,
        max_tokens=_MAX_TOKENS,
        temperature=_TEMPERATURE,
    )
    choice = respuesta.choices[0] if respuesta and respuesta.choices else None

    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        print(
            "[WARN] Respuesta truncada por límite de tokens "
            f"(_MAX_TOKENS={_MAX_TOKENS}). "
            "Considera aumentar LITELLM_MAX_TOKENS."
        )

    msg = getattr(choice, "message", None)
    contenido = getattr(msg, "content", None) if msg else None
    if isinstance(contenido, str):
        return contenido.strip()
    return ""


def _limpiar_trailing_commas(texto: str) -> str:
    """Elimina comas finales antes de } o ] (error frecuente de LLMs)."""
    return re.sub(r",\s*([}\]])", r"\1", texto)


def _extraer_json(texto: str) -> Dict[str, Any]:
    """Extrae el primer objeto JSON {...} balanceado del texto del LLM."""
    limpio = re.sub(r"^```(?:json)?\s*", "", texto.strip())
    limpio = re.sub(r"\s*```\s*$", "", limpio)

    inicio = limpio.find("{")
    if inicio < 0:
        raise ValueError("No se encontró '{' en la respuesta del LLM.")

    profundidad = 0
    en_string = False
    escape = False
    for i in range(inicio, len(limpio)):
        ch = limpio[i]
        if en_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                en_string = False
            continue
        if ch == '"':
            en_string = True
            continue
        if ch == "{":
            profundidad += 1
        elif ch == "}":
            profundidad -= 1
            if profundidad == 0:
                fragmento = limpio[inicio : i + 1]
                try:
                    return json.loads(fragmento)
                except json.JSONDecodeError:
                    fragmento_limpio = _limpiar_trailing_commas(fragmento)
                    try:
                        return json.loads(fragmento_limpio)
                    except json.JSONDecodeError as e:
                        raise ValueError(
                            f"JSON extraído pero con errores de sintaxis "
                            f"incluso tras limpiar trailing commas: {e}"
                        ) from e

    raise ValueError("JSON incompleto: no se cerraron todas las llaves.")


# ---------------------------------------------------------------------------
# Valores reservados que no necesitan estar en la ontología
# ---------------------------------------------------------------------------


_RESERVED_VALUES: frozenset[str] = frozenset({
    # Lógica y comparación
    "NONE", "COMPARISON",
    "EQUALS", "NOT_EQUAL", "GREATER_THAN", "LESS_THAN",
    "GREATER_OR_EQUAL", "LESS_OR_EQUAL",
    "AND", "OR",
    # Cálculos dinámicos
    "COUNT", "ATTRIBUTE_REFERENCE",
    # Propiedades distintas (para distinct_property)
    "CARD_TYPES", "NAMES", "MANA_VALUES", "COLORS",
    # Punteros de entidad (motor)
    "TARGET", "TARGET_1", "TARGET_2", "SOURCE", "CONTROLLER", "OPPONENT",
    "ANY", "ALL_ZONES",
    # Duraciones
    "PERMANENT", "UNTIL_END_OF_TURN", "WHILE_CONDITION", "WHILE_STATIC_ACTIVE",
    # Tipos de habilidad
    "SPELL", "ACTIVATED", "TRIGGERED", "STATIC",
    # Tipos de coste base
    "MANA", "TAP", "SACRIFICE", "DISCARD", "PAY_LIFE", "REMOVE_COUNTER",
    # Comodines
    "X", "*",
    # Zonas (estructurales del motor)
    "BATTLEFIELD", "HAND", "GRAVEYARD", "LIBRARY", "STACK", "EXILE", "COMMAND",
    # Tipos de carta base (usados en restrictions.types)
    "INSTANT", "SORCERY", "CREATURE", "ARTIFACT", "ENCHANTMENT", "LAND",
    "PLANESWALKER", "PLAYER", "CARD", "PERMANENT", "SPELL",
    # Supertipos
    "LEGENDARY", "BASIC", "SNOW",
    # Colores
    "WHITE", "BLUE", "BLACK", "RED", "GREEN",
})

_PARAM_REQUIRED_KEYS: frozenset[str] = frozenset({
    "amount", "duration", "chooser", "string_value",
    "destination_zone", "token_definition",
})


def _validar_esquema(
    resultado: Dict[str, Any], diccionario: Dict[str, Any],
) -> List[str]:
    """Valida estructura y constantes del JSON-LD contra el esquema V1.2."""
    errores: List[str] = []

    todas_constantes: set[str] = set()
    for valores in diccionario.values():
        if isinstance(valores, list):
            todas_constantes.update(valores)

    def _es_valida(const: Any) -> bool:
        if const is None or isinstance(const, (int, float, bool)):
            return True
        s = str(const)
        if s.lstrip("-").isdigit():
            return True
        return s in _RESERVED_VALUES or s in todas_constantes

    for campo in ("name", "card_types", "abilities"):
        if campo not in resultado:
            errores.append(f"Falta campo obligatorio en raíz: '{campo}'")

    base = resultado.get("base_attributes")
    if not isinstance(base, dict):
        errores.append("Falta 'base_attributes' como objeto en raíz")

    abilities = resultado.get("abilities")
    if not isinstance(abilities, list):
        return errores

    for ai, ab in enumerate(abilities, 1):
        prefix = f"abilities[{ai}]"
        if not isinstance(ab, dict):
            errores.append(f"{prefix}: no es un objeto")
            continue

        for key in ("ability_order", "ability_type", "effects"):
            if key not in ab:
                errores.append(f"{prefix}: falta '{key}'")

        ab_type = ab.get("ability_type")
        if ab_type and not _es_valida(ab_type):
            errores.append(
                f"{prefix}: ability_type '{ab_type}' no está en ontología ni reservados"
            )

        effects = ab.get("effects")
        if not isinstance(effects, list):
            continue

        for ei, ef in enumerate(effects, 1):
            ep = f"{prefix}.effects[{ei}]"
            if not isinstance(ef, dict):
                errores.append(f"{ep}: no es un objeto")
                continue

            for key in ("effect_order", "effect_type", "target_info", "parameters"):
                if key not in ef:
                    errores.append(f"{ep}: falta '{key}'")

            et = ef.get("effect_type")
            if et and not _es_valida(et):
                errores.append(
                    f"{ep}: effect_type '{et}' no está en ontología ni reservados"
                )

            ti = ef.get("target_info")
            if isinstance(ti, dict):
                tc = ti.get("target_count")
                if tc is not None and not isinstance(tc, dict):
                    errores.append(
                        f"{ep}.target_info: target_count debe ser "
                        f"objeto {{min, max}}, no {type(tc).__name__}"
                    )
                elif isinstance(tc, dict):
                    for k in ("min", "max"):
                        if k not in tc:
                            errores.append(
                                f"{ep}.target_info.target_count: falta '{k}'"
                            )

            params = ef.get("parameters")
            if isinstance(params, dict):
                faltantes = _PARAM_REQUIRED_KEYS - params.keys()
                if faltantes:
                    errores.append(
                        f"{ep}.parameters: faltan claves {sorted(faltantes)}"
                    )

                if et == "MOVE_ZONE" and not params.get("destination_zone"):
                    errores.append(
                        f"{ep}: MOVE_ZONE requiere destination_zone no nulo"
                    )
                if et == "CREATE_TOKEN" and not params.get("token_definition"):
                    errores.append(
                        f"{ep}: CREATE_TOKEN requiere token_definition no nulo"
                    )

    return errores


def traducir_oracle_text(
    oracle_text: str,
    system_prompt: str,
    nombre_carta: str = "",
    coste_mana: str = "",
    tipos: str = "",
    correccion_usuario: Optional[str] = None,
    diccionario: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Traduce el Oracle Text de una carta a JSON-LD usando el LLM."""
    partes_usuario: List[str] = []
    if nombre_carta:
        partes_usuario.append(f"Nombre: {nombre_carta}")
    if coste_mana:
        partes_usuario.append(f"Coste de maná: {coste_mana}")
    if tipos:
        partes_usuario.append(f"Tipos: {tipos}")
    partes_usuario.append(f"Oracle Text:\n{oracle_text}")

    if correccion_usuario:
        partes_usuario.append(
            f"\n--- CORRECCIÓN DEL REVISOR HUMANO ---\n{correccion_usuario}\n"
            "Genera de nuevo el JSON-LD teniendo en cuenta esta corrección."
        )

    mensajes: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(partes_usuario)},
    ]

    texto_crudo = _llamar_llm(mensajes)
    resultado = _extraer_json(texto_crudo)

    if diccionario and not resultado.get("requires_human_review"):
        errores = _validar_esquema(resultado, diccionario)
        if errores:
            print(f"[VALIDACIÓN] {len(errores)} problema(s) en el JSON generado:")
            for err in errores:
                print(f"  - {err}")

    return resultado


# ---------------------------------------------------------------------------
# 5. BUCLE HUMAN-IN-THE-LOOP
# ---------------------------------------------------------------------------


def _mostrar_propuesta(propuesta: Any) -> None:
    """Imprime la propuesta de ontología de forma legible."""
    propuestas = propuesta if isinstance(propuesta, list) else [propuesta]
    for idx, p in enumerate(propuestas, 1):
        if not isinstance(p, dict):
            continue
        print(f"\n  Propuesta #{idx}:")
        print(f"    Keyword:   {p.get('keyword', '?')}")
        print(f"    Categoría: {p.get('category', '?')}")
        print(f"    Bloque:    {p.get('block_kind', '?')}")
        print(f"    Constante: {p.get('proposed_constant', '?')}")
        desc = p.get("description", "")
        for linea in desc.split("\n"):
            print(f"    {linea}")


def _collect_proposals(resultado: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normaliza y unifica propuestas de diferentes claves de salida del LLM."""
    proposals: List[Dict[str, Any]] = []
    raw = resultado.get("ontology_proposal")
    if isinstance(raw, dict):
        proposals.append(raw)
    elif isinstance(raw, list):
        proposals.extend(x for x in raw if isinstance(x, dict))

    extra = resultado.get("missing_building_blocks")
    if isinstance(extra, dict):
        proposals.append(extra)
    elif isinstance(extra, list):
        proposals.extend(x for x in extra if isinstance(x, dict))

    return proposals


def _review_proposals_by_block(propuestas: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[str]]:
    """
    Revisión propuesta por propuesta (bloque por bloque).
    Devuelve (aceptadas, comentarios_para_regenerar).
    """
    aceptadas: List[Dict[str, Any]] = []
    comentarios: List[str] = []

    for idx, prop in enumerate(propuestas, 1):
        print(f"\n--- Revisión de propuesta #{idx} ---")
        _mostrar_propuesta([prop])
        print("\n  [Y] Aceptar este bloque")
        print("  [N] Rechazar este bloque")
        print("  [C] Comentar/corregir este bloque y regenerar")
        decision = input("\n  Decisión [Y/N/C]: ").strip().upper()

        if decision == "Y":
            aceptadas.append(prop)
            continue
        if decision == "N":
            continue
        if decision == "C":
            comentario = input(
                "\n  Escribe corrección para este bloque (genérica y reusable):\n  > "
            ).strip()
            if comentario:
                comentarios.append(
                    f"Bloque #{idx} ({prop.get('block_kind', 'other')} / {prop.get('category', 'unknown')}): {comentario}"
                )
            continue

        print("  (opción no válida -> se toma como rechazo de este bloque)")

    return aceptadas, comentarios


class SessionState:
    """Estado mutable de la sesión: grafo, diccionario y prompt vigente."""

    def __init__(self, owl_path: str, json_path: str) -> None:
        self.owl_path = owl_path
        self.json_path = json_path
        self.graph, self.diccionario = load_or_build_dictionary(owl_path, json_path)
        self.system_prompt = self._rebuild_prompt()

    def _rebuild_prompt(self) -> str:
        return build_system_prompt(
            json.dumps(self.diccionario, indent=2, ensure_ascii=False)
        )

    def accept_proposals(self, proposals: Any) -> None:
        """Integra propuestas, persiste JSON+OWL y reconstruye el prompt."""
        apply_accepted_proposals(self.graph, self.diccionario, proposals)
        save_json_atomic(self.json_path, self.diccionario)
        save_rdf_atomic(self.owl_path, self.graph)
        self.system_prompt = self._rebuild_prompt()


def procesar_carta(
    oracle_text: str,
    session: SessionState,
    nombre_carta: str = "",
    coste_mana: str = "",
    tipos: str = "",
) -> Dict[str, Any]:
    """Traduce una carta y gestiona cuarentena con intervención humana."""
    print(f"\n{'=' * 60}")
    print(f"  Traduciendo: {nombre_carta or '(sin nombre)'}")
    print(f"{'=' * 60}")

    resultado = traducir_oracle_text(
        oracle_text, session.system_prompt, nombre_carta, coste_mana, tipos,
        diccionario=session.diccionario,
    )

    while resultado.get("requires_human_review"):
        print("\n[CUARENTENA] La carta requiere revisión humana.")
        propuestas = _collect_proposals(resultado)
        if propuestas:
            aceptadas, comentarios = _review_proposals_by_block(propuestas)

            if aceptadas:
                print("\n  Persistiendo bloques aceptados en JSON y OWL...")
                session.accept_proposals(aceptadas)
                print(f"  JSON: {session.json_path}")
                print(f"  OWL:  {session.owl_path}")

            if aceptadas or comentarios:
                correccion = "\n".join(comentarios) if comentarios else None
                print("\n  Re-traduciendo con revisión por bloque aplicada...")
                resultado = traducir_oracle_text(
                    oracle_text,
                    session.system_prompt,
                    nombre_carta,
                    coste_mana,
                    tipos,
                    correccion_usuario=correccion,
                    diccionario=session.diccionario,
                )
                continue

            print("\n  -> No se aceptó ni comentó ningún bloque. Carta queda en cuarentena.")
            break

        print("\n  [WARN] El modelo no devolvió propuestas estructuradas. Carta en cuarentena.")
        break

    return resultado


# ---------------------------------------------------------------------------
# 6. PUNTO DE ENTRADA
# ---------------------------------------------------------------------------


_CARTAS_EJEMPLO: List[Dict[str, str]] = [
        {
        "nombre": "Raise the Alarm",
        "coste": "{1}{W}",
        "tipos": "Instant",
        "oracle": "Create two 1/1 white Soldier creature tokens.",
    },
    {
        "nombre": "Cryptic Command",
        "coste": "{1}{U}{U}{U}",
        "tipos": "Instant",
        "oracle": (
            "Choose two —\n"
            "• Counter target spell.\n"
            "• Return target permanent to its owner's hand.\n"
            "• Tap all creatures your opponents control.\n"
            "• Draw a card."
        ),
    },
    {
        "nombre": "Tarmogoyf",
        "coste": "{1}{G}",
        "tipos": "Creature",
        "oracle": (
            "Tarmogoyf's power is equal to the number of card types among cards "
            "in all graveyards and its toughness is equal to that number plus 1."
        ),
    },
    {
        "nombre": "Thoughtseize",
        "coste": "{B}",
        "tipos": "Sorcery",
        "oracle": (
            "Target player reveals their hand. You choose a nonland card "
            "from it. That player discards that card. You lose 2 life."
        ),
    },
    {
        "nombre": "Path to Exile",
        "coste": "{W}",
        "tipos": "Instant",
        "oracle": (
            "Exile target creature. Its controller may search their library "
            "for a basic land card, put that card onto the battlefield "
            "tapped, then shuffle."
        ),
    },
    {
        "nombre": "Ashaya, soul of the wilds",
        "coste": "{3}{G}{G}",
        "tipos": "Legendary Creature",
        "oracle": (
            "Ashaya, Soul of the Wild's power and toughness are each equal to the number of lands you control."
            "Nontoken creatures you control are Forest lands in addition to their other types. (They're still affected by summoning sickness.)"
        ),
    },
    
]


def main() -> None:
    print("=" * 60)
    print("  MTG Oracle Text -> JSON-LD  (CLI)")
    print(f"  Modelo: {_MODEL}")
    print(f"  Endpoint: {_API_BASE}")
    print("=" * 60)

    session = SessionState(_OWL_PATH, _JSON_PATH)
    print("  Diccionario cargado desde OWL:")
    for cat, items in sorted(session.diccionario.items()):
        if isinstance(items, list):
            print(f"    {cat}: {len(items)} entradas")
    print(f"  JSON: {_JSON_PATH}")
    print(f"  OWL:  {_OWL_PATH}")
    print("=" * 60)

    for carta in _CARTAS_EJEMPLO:
        try:
            resultado = procesar_carta(
                oracle_text=carta["oracle"],
                session=session,
                nombre_carta=carta["nombre"],
                coste_mana=carta["coste"],
                tipos=carta["tipos"],
            )
            print("\n--- JSON-LD resultante ---")
            print(json.dumps(resultado, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"\n[ERROR] Fallo al procesar '{carta['nombre']}': {e}")

    print("\n" + "=" * 60)
    print("  Fin de la ejecución.")
    print("=" * 60)


if __name__ == "__main__":
    main()
