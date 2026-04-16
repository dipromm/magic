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
_MAX_TOKENS: int = int(os.environ.get("LITELLM_MAX_TOKENS", "4096"))
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
    return (
        "Eres un Juez de Nivel 3 de Magic: The Gathering y un traductor de reglas\n"
        "a lógica de programación. Tu tarea es recibir el \"Oracle Text\" de una carta\n"
        "y devolver **exclusivamente** un JSON-LD válido que represente su semántica\n"
        "mecánica, mapeando TODO a las constantes de la ontología cerrada que se\n"
        "proporciona a continuación.\n\n"
        "=== ONTOLOGÍA PERMITIDA ===\n"
        f"{ontologia_json}\n"
        "=== FIN ONTOLOGÍA ===\n\n"
        "REGLAS DE SALIDA:\n"
        "1. Devuelve UN ÚNICO JSON válido, sin bloques de código markdown (```),\n"
        "   sin texto adicional antes ni después.\n"
        "2. Estructura obligatoria:\n"
        "   {\n"
        '     "@context": "https://mtg-engine.example.org/schema/v1",\n'
        '     "@type": "Card",\n'
        '     "name": "<nombre de la carta en inglés>",\n'
        '     "mana_cost": "<coste de maná tal como aparece>",\n'
        '     "types": ["<supertipo>", "<tipo>", "<subtipo si aplica>"],\n'
        '     "requires_human_review": false,\n'
        '     "resolution_blocks": [\n'
        "       {\n"
        '         "block_order": 1,\n'
        '         "trigger": "<TRIGGER de la ontología o NONE>",\n'
        '         "effect_type": "<EFECTO de la ontología>",\n'
        '         "target_info": {\n'
        '           "requires_target": true/false,\n'
        '           "target_type": "<TARGET de la ontología>",\n'
        '           "target_count": <entero>\n'
        "         },\n"
        '         "trigger_condition": {"event_condition_type": "<condición opcional>", "value": "<valor>"},\n'
        '         "activation_cost": [{"cost_type": "<tipo_coste>", "amount": "<valor>"}],\n'
        '         "state_conditions": [\n'
        "           {\n"
        '             "condition_type": "COMPARISON",\n'
        '             "left_operand": "<constante_de_la_ontología (ej. ConvertedManaCost, Power)>",\n'
        '             "left_scope": "<TARGET | SOURCE | CONTROLLER | OPPONENT>",\n'
        '             "operator": "<EQUALS | GREATER_THAN | LESS_THAN | GREATER_OR_EQUAL | LESS_OR_EQUAL | NOT_EQUAL>",\n'
        '             "right_operand": "<constante_de_la_ontología o valor numérico>",\n'
        '             "right_scope": "<TARGET | SOURCE | CONTROLLER | OPPONENT | null si es número>"\n'
        "           }\n"
        "         ],\n"
        '         "parameters": { <valores numéricos o descriptivos relevantes> }\n'
        "       }\n"
        "     ]\n"
        "   }\n"
        "3. Si la carta tiene varias habilidades o efectos, crea un\n"
        "   `resolution_block` por cada uno, numerados por `block_order`.\n"
        "4. REGLA DE CUARENTENA: si falta CUALQUIER bloque semántico necesario para modelar la carta\n"
        "   (efecto, target, trigger, condición de trigger, coste de activación, condición de estado,\n"
        "   restricción de fase/zona, tipo de contador, etc.) que no exista en la ontología:\n"
        '   - Pon "requires_human_review": true.\n'
        '   - Deja "resolution_blocks": [].\n'
        '   - Devuelve "ontology_proposal" como LISTA con una entrada por cada bloque faltante.\n'
        "   - Cada entrada debe usar esta estructura:\n"
        "     {\n"
        '       "keyword": "<término detectado>",\n'
        '       "category": "<categoría OWL objetivo: Effect, Trigger_event, TargetType, KeywordAbility, Counters, Zones, PhaseandStep, Ability, etc.>",\n'
        '       "block_kind": "<effect_type|target_type|trigger_event|trigger_condition|activation_cost|state_condition|zone_restriction|phase_restriction|counter_type|other>",\n'
        '       "proposed_constant": "<NOMBRE_CONSTANTE_SUGERIDO>",\n'
        '       "description": "<descripción técnica GENÉRICA en español, independiente de la carta concreta; define el concepto y su semántica de reglas para implementar en Python>"\n'
        "     }\n"
        "5. La descripción debe ser reusable para cualquier carta y NO mencionar cartas concretas.\n"
        "6. No inventes constantes que no estén en la ontología para resolution_blocks.\n"
        "7. REGLA DE POLIMORFISMO: Tienes estrictamente PROHIBIDO inventar constantes combinadas\n"
        "   o monolíticas para condiciones complejas (ej. NO uses 'ManaValueEqualsChargeCounters').\n"
        "   Si una carta requiere una comparación o condición matemática dinámica, usa\n"
        "   obligatoriamente el tipo 'COMPARISON' con los campos:\n"
        "   - left_operand / right_operand: deben ser constantes QUE EXISTAN en la ontología\n"
        "     (de categorías como CardAtribute, Counters, etc.) o valores numéricos.\n"
        "   - left_scope / right_scope: indica a QUÉ objeto se aplica (TARGET, SOURCE,\n"
        "     CONTROLLER, OPPONENT). Usa null si el operando es un número.\n"
        "   - operator: EQUALS, GREATER_THAN, LESS_THAN, GREATER_OR_EQUAL, LESS_OR_EQUAL, NOT_EQUAL.\n"
        "   Ejemplo: 'mana value equal to the number of charge counters' se modela como:\n"
        "     left_operand=ConvertedManaCost, left_scope=TARGET,\n"
        "     operator=EQUALS,\n"
        "     right_operand=Charge, right_scope=SOURCE.\n"
        "8. VALORES RESERVADOS (NO necesitan estar en la ontología): NONE, true, false,\n"
        "   COMPARISON, EQUALS, GREATER_THAN, LESS_THAN, GREATER_OR_EQUAL, LESS_OR_EQUAL,\n"
        "   NOT_EQUAL, TARGET, SOURCE, CONTROLLER, OPPONENT, y cualquier número entero.\n"
        "   Usa NONE cuando un campo opcional no aplica (ej. trigger=NONE para instantes).\n"
        "9. REGLA DE VALIDACIÓN TOTAL: TODAS las demás constantes que uses en CUALQUIER campo\n"
        "   del JSON-LD deben existir en la ontología. Esto incluye:\n"
        "   - effect_type, trigger (si no es NONE), target_type\n"
        "   - trigger_condition.event_condition_type\n"
        "   - activation_cost[].cost_type (ej. Tap está en Ability; Mana_cost requiere estar)\n"
        "   - left_operand y right_operand de state_conditions (si no son numéricos)\n"
        "   - cualquier zone, phase, counter_type, keyword, ability_type referenciados\n"
        "   Si CUALQUIERA de estos valores no existe en la ontología, DEBES activar cuarentena\n"
        "   (requires_human_review: true) y proponer CADA valor faltante en ontology_proposal.\n"
        "   No asumas que una constante existe solo porque suena razonable: búscala en la\n"
        "   ontología y si no aparece, activa cuarentena y propón su adición."
    )

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
    msg = getattr(choice, "message", None)
    contenido = getattr(msg, "content", None) if msg else None
    if isinstance(contenido, str):
        return contenido.strip()
    return ""


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
                return json.loads(limpio[inicio : i + 1])

    raise ValueError("JSON incompleto: no se cerraron todas las llaves.")


def traducir_oracle_text(
    oracle_text: str,
    system_prompt: str,
    nombre_carta: str = "",
    coste_mana: str = "",
    tipos: str = "",
    correccion_usuario: Optional[str] = None,
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
    return _extraer_json(texto_crudo)


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
        "nombre": "Lightning Bolt",
        "coste": "{R}",
        "tipos": "Instant",
        "oracle": "Lightning Bolt deals 3 damage to any target.",
    },
    {
        "nombre": "Aether Vial",
        "coste": "{1}",
        "tipos": "Artifact",
        "oracle": (
            "At the beginning of your upkeep, you may put a charge counter "
            "on Aether Vial.\n"
            "{T}: You may put a creature card with mana value equal to the "
            "number of charge counters on Aether Vial from your hand onto "
            "the battlefield."
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
