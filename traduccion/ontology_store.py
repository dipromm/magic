"""
ontology_store.py
=================
Módulo de sincronización bidireccional OWL (RDF/XML) <-> JSON canónico
para el diccionario del motor de traducción de cartas MTG.

Descubre TODAS las categorías del OWL automáticamente: busca las clases
raíz del namespace de la ontología (las que no son subclase de otra clase
del mismo namespace) y extrae recursivamente sus descendientes.

Al aceptar propuestas nuevas, las escribe como subclases OWL (coherente
con el modelado en Protégé) y actualiza el JSON canónico.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import Any, Dict, List, Set, Tuple

from rdflib import OWL, RDF, RDFS, Graph, Literal, Namespace, URIRef

# ---------------------------------------------------------------------------
# Namespace de la ontología (coincide con el base del RDF existente)
# ---------------------------------------------------------------------------

NS = Namespace("http://www.tfg.mtg.simulacion/ontology/")
_NS_STR = str(NS)

# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------


def _safe_iri_fragment(name: str) -> str:
    """Convierte un nombre de constante a un fragmento IRI seguro."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    return s or "UNKNOWN"


def _local_name(uri: URIRef) -> str:
    return str(uri).rsplit("/", 1)[-1]


def _load_graph(owl_path: str) -> Graph:
    g = Graph()
    g.parse(owl_path, format="xml")
    return g


def _is_ontology_class(uri: Any) -> bool:
    """True si el URI pertenece al namespace de la ontología."""
    return isinstance(uri, URIRef) and str(uri).startswith(_NS_STR)


def _direct_subclasses(g: Graph, parent: URIRef) -> List[URIRef]:
    """Subclases directas de parent que pertenecen al namespace."""
    return sorted(
        (s for s in g.subjects(RDFS.subClassOf, parent) if _is_ontology_class(s)),
        key=str,
    )


def _all_descendants(g: Graph, parent: URIRef) -> List[str]:
    """Nombres locales de TODOS los descendientes (recursivo) de parent."""
    visited: Set[URIRef] = set()
    result: List[str] = []

    def _walk(cls: URIRef) -> None:
        for child in _direct_subclasses(g, cls):
            if child in visited:
                continue
            visited.add(child)
            result.append(_local_name(child))
            _walk(child)

    _walk(parent)
    return sorted(set(result))


def _find_root_classes(g: Graph) -> List[URIRef]:
    """
    Encuentra las clases raíz del namespace: clases OWL que NO son
    subclase de otra clase del mismo namespace.
    """
    all_classes: Set[URIRef] = set()
    for s in g.subjects(RDF.type, OWL.Class):
        if _is_ontology_class(s):
            all_classes.add(s)
    for s in g.subjects(RDFS.subClassOf, None):
        if _is_ontology_class(s):
            all_classes.add(s)
    for _, _, o in g.triples((None, RDFS.subClassOf, None)):
        if _is_ontology_class(o):
            all_classes.add(o)

    has_parent_in_ns: Set[URIRef] = set()
    for cls in all_classes:
        for parent in g.objects(cls, RDFS.subClassOf):
            if _is_ontology_class(parent) and parent != cls:
                has_parent_in_ns.add(cls)
                break

    roots = sorted(all_classes - has_parent_in_ns, key=str)
    return roots


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def export_dictionary_from_owl(g: Graph) -> Dict[str, Any]:
    """
    Recorre TODAS las clases raíz del OWL y exporta un diccionario
    { nombre_clase_raiz: [descendientes...] } con todas las categorías.
    """
    roots = _find_root_classes(g)
    result: Dict[str, Any] = {}
    for root in roots:
        name = _local_name(root)
        descendants = _all_descendants(g, root)
        if descendants:
            result[name] = descendants
    return result


def _is_vocab_empty(d: Dict[str, Any]) -> bool:
    return all(len(v) == 0 for v in d.values() if isinstance(v, list))


def load_or_build_dictionary(
    owl_path: str, json_path: str
) -> Tuple[Graph, Dict[str, Any]]:
    """
    Carga el diccionario canónico. Estrategia:
      1) Exporta siempre desde OWL (fuente de verdad).
      2) Si el JSON existente tiene entradas extra, las fusiona.
      3) Persiste el JSON resultante.
    Devuelve (grafo rdflib, diccionario).
    """
    g = _load_graph(owl_path)
    owl_data = export_dictionary_from_owl(g)

    if os.path.isfile(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
        for key, json_list in json_data.items():
            if not isinstance(json_list, list):
                continue
            owl_list = set(owl_data.get(key, []))
            merged = sorted(owl_list | set(json_list))
            owl_data[key] = merged

    if _is_vocab_empty(owl_data):
        print("[INFO] OWL sin subclases en ninguna categoría.")
        print("       Añade subclases en Protégé o acepta propuestas del LLM.")

    save_json_atomic(json_path, owl_data)
    return g, owl_data


def resolve_parent_class(g: Graph, category: str) -> Tuple[str, URIRef] | None:
    """
    Dado un nombre de categoría (del LLM), intenta encontrar la clase OWL
    padre correspondiente. Busca por nombre exacto y variantes comunes.
    """
    candidates = [
        category,
        category.replace(" ", "_"),
        category.capitalize(),
        category.title().replace(" ", "_"),
        category.title().replace(" ", ""),
    ]
    owl_data = export_dictionary_from_owl(g)
    for candidate in candidates:
        if candidate in owl_data:
            return candidate, NS[candidate]

    _ALIASES: Dict[str, str] = {
        "efecto": "Effect",
        "effect": "Effect",
        "trigger": "Trigger_event",
        "trigger_event": "Trigger_event",
        "target": "TargetType",
        "targettype": "TargetType",
        "keyword_ability": "KeywordAbility",
        "keywordability": "KeywordAbility",
        "keyword": "KeywordAbility",
        "counter": "Counters",
        "counters": "Counters",
        "zone": "Zones",
        "zones": "Zones",
        "ability": "Ability",
        "phase": "PhaseandStep",
        "phaseandstep": "PhaseandStep",
        "cardtype": "CardType",
        "card_type": "CardType",
        "supertype": "CardSupertype",
        "cardsupertype": "CardSupertype",
        "subtype": "CardSubtype",
        "cardsubtype": "CardSubtype",
        "game_entity": "GameEntity",
        "gameentity": "GameEntity",
        "card_atribute": "CardAtribute",
        "cardatribute": "CardAtribute",
    }
    norm = category.strip().lower().replace(" ", "_")
    resolved = _ALIASES.get(norm)
    if resolved and resolved in owl_data:
        return resolved, NS[resolved]

    return None


def apply_accepted_proposals(
    g: Graph,
    diccionario: Dict[str, Any],
    proposals: Any,
) -> Dict[str, Any]:
    """
    Integra una o varias propuestas aceptadas en el grafo y el diccionario.
    Las escribe como owl:Class + rdfs:subClassOf (coherente con Protégé).
    Devuelve el diccionario actualizado (mutado in-place).
    """
    if not proposals:
        return diccionario
    items = proposals if isinstance(proposals, list) else [proposals]

    for prop in items:
        if not isinstance(prop, dict):
            continue
        category = (prop.get("category") or "").strip()
        constant = (prop.get("proposed_constant") or "").strip()
        description = (prop.get("description") or "").strip()
        keyword = (prop.get("keyword") or "").strip()

        if not constant:
            continue

        resolved = resolve_parent_class(g, category)
        if resolved is None:
            print(f"  [WARN] Categoría '{category}' no encontrada en OWL. Propuesta ignorada.")
            continue

        json_key, parent_uri = resolved
        lista = diccionario.setdefault(json_key, [])
        if constant in lista:
            print(f"  [SKIP] {constant} ya existe en {json_key}.")
            continue

        lista.append(constant)

        iri = NS[_safe_iri_fragment(constant)]
        g.add((iri, RDF.type, OWL.Class))
        g.add((iri, RDFS.subClassOf, parent_uri))
        if description:
            g.add((iri, RDFS.comment, Literal(description, lang="es")))

        parent_name = _local_name(parent_uri)
        print(f"  [OK] Añadido {constant} como subclase de {parent_name} -> {json_key}")

    return diccionario


# ---------------------------------------------------------------------------
# Persistencia atómica
# ---------------------------------------------------------------------------


def backup_file(path: str) -> str:
    """Crea una copia de seguridad con timestamp. Devuelve la ruta del backup."""
    if not os.path.isfile(path):
        return ""
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{path}.bak.{ts}"
    shutil.copy2(path, bak)
    return bak


def save_json_atomic(json_path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    tmp = json_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, json_path)


def save_rdf_atomic(owl_path: str, g: Graph) -> None:
    backup_file(owl_path)
    tmp = owl_path + ".tmp"
    g.serialize(destination=tmp, format="xml")
    os.replace(tmp, owl_path)
