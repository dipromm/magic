"""Comprueba el compilador contra las cartas reales y los cuatro casos del intérprete."""
from __future__ import annotations

from pathlib import Path

from reglas.adaptador import Adaptador
from reglas.compilador import catalogo, criatura
from reglas.evaluar import OperandoSinResolver, exigir_resolutores, recopilar_operandos, resuelve_operando
from reglas.modelo import Definicion
from reglas.partida import Partida

RAIZ = Path(__file__).resolve().parent.parent / "mazos" / "bloomburrow_jsonld"


def _partida() -> tuple[Partida, str]:
    partida = Partida()
    jugador = partida.nuevo_jugador("A")
    partida.nuevo_jugador("B")
    return partida, jugador.id


def _campo(partida: Partida, dueno: str, definicion: Definicion, *, ficha: bool = False):
    return partida.instanciar(definicion, dueno, "Battlefield", es_ficha=ficha)


def test_compila_el_starter() -> None:
    cartas = catalogo(RAIZ)
    assert len(cartas) == 39
    assert "Mind Spring" in cartas
    assert cartas["Burrowguard Mentor"].power == "*"
    assert cartas["Mind Spring"].hechizo()["effects"][0]["parameters"]["amount"] == "X"


def test_estatica_condicional_y_keyword() -> None:
    cartas = catalogo(RAIZ)
    partida, dueno = _partida()
    druida = _campo(partida, dueno, cartas["Druid of the Spade"])
    assert partida.poder(druida) == 2
    assert partida.resistencia(druida) == 3
    assert not partida.tiene_keyword(druida, "Trample")

    _campo(partida, dueno, criatura("Ficha", 1, 1), ficha=True)
    assert partida.poder(druida) == 4
    assert partida.tiene_keyword(druida, "Trample")


def test_dependencia_entre_carta_estatica_y_definitoria() -> None:
    cartas = catalogo(RAIZ)
    partida, dueno = _partida()
    finneas = _campo(partida, dueno, cartas["Finneas, Ace Archer"])
    druida = _campo(partida, dueno, cartas["Druid of the Spade"])
    mentor = _campo(partida, dueno, cartas["Burrowguard Mentor"])
    efecto = cartas["Finneas, Ace Archer"].habilidad(1)["effects"][2]
    assert partida.poder(mentor) == 3
    assert partida.poder(druida) == 2
    assert not partida.condiciones_de(finneas, efecto)

    _campo(partida, dueno, criatura("Ficha", 1, 1), ficha=True)
    assert partida.poder(druida) == 4
    assert partida.poder(mentor) == 4
    assert partida.poder(finneas) == 2
    assert partida.condiciones_de(finneas, efecto)


def test_definicion_que_se_lee_a_si_misma_termina() -> None:
    habilidad = {
        "ability_order": 1,
        "ability_type": "StaticAbility",
        "zones_active": ["Battlefield"],
        "modal_choices": None,
        "costs": [],
        "trigger": {"event_type": "NONE", "conditions": []},
        "effects": [{
            "effect_order": 1,
            "mode_id": None,
            "is_optional": False,
            "effect_type": "SetPowerToughness",
            "target_info": {
                "requires_target": False,
                "target_owner": "SOURCE",
                "target_zone": "Battlefield",
                "target_count": {"min": 1, "max": 1},
                "restrictions": {"logical_operator": "NONE", "types": [], "subtypes": [], "modifiers": []},
            },
            "effect_conditions": [],
            "parameters": {
                "amount": {
                    "dynamic_calculation": "ATTRIBUTE_REFERENCE",
                    "distinct_property": None,
                    "multiplier": None,
                    "offset": None,
                    "source_ref": "SOURCE",
                    "attribute": "Power",
                    "query": None,
                },
                "duration": "WHILE_STATIC_ACTIVE",
                "chooser": None,
                "string_value": None,
                "destination_zone": None,
                "token_definition": None,
            },
        }],
    }
    definicion = criatura("Bucle", 2, 2, abilities=(habilidad,))
    partida, dueno = _partida()
    carta = _campo(partida, dueno, definicion)
    assert partida.poder(carta) == 2
    assert partida.resistencia(carta) == 2


def test_comparacion_con_or() -> None:
    cartas = catalogo(RAIZ)
    partida, dueno = _partida()
    repel = partida.instanciar(cartas["Repel Calamity"], dueno, "Hand")
    efecto = cartas["Repel Calamity"].hechizo()["effects"][0]
    grande = _campo(partida, dueno, criatura("Grande", 5, 2))
    alta = _campo(partida, dueno, criatura("Alta", 2, 5))
    pequena = _campo(partida, dueno, criatura("Pequeña", 2, 2))
    assert partida.condiciones_de(repel, efecto, grande)
    assert partida.condiciones_de(repel, efecto, alta)
    assert not partida.condiciones_de(repel, efecto, pequena)


def test_disparo_y_resolucion() -> None:
    cartas = catalogo(RAIZ)
    partida, dueno = _partida()
    serra = partida.instanciar(cartas["Serra Redeemer"], dueno, "Library")
    assert partida.entrar(serra) == []

    pequena = partida.instanciar(criatura("Soldado", 1, 1), dueno, "Library")
    disparos = partida.entrar(pequena)
    assert len(disparos) == 1
    assert disparos[0].fuente == serra.ref()
    partida.resolver_pila(disparos[0])
    assert pequena.contadores["PlusOnePlusOne"] == 2
    assert partida.poder(pequena) == 3

    grande = partida.instanciar(criatura("Coloso", 5, 5), dueno, "Library")
    assert partida.entrar(grande) == []


def test_x_y_reduccion_de_coste() -> None:
    cartas = catalogo(RAIZ)
    partida, dueno = _partida()
    for _ in range(4):
        partida.instanciar(criatura("Mazo", 1, 1), dueno, "Library")
    primavera = partida.instanciar(cartas["Mind Spring"], dueno, "Hand")
    solo = partida.coste_de_lanzar(primavera, x=3)
    assert solo.generico == 3
    assert solo.colores == {"U": 2}
    assert solo.total == 5

    _campo(partida, dueno, cartas["Stormcatch Mentor"])
    reducido = partida.coste_de_lanzar(primavera, x=3)
    assert reducido.generico == 2
    assert reducido.total == 4

    partida.lanzar(primavera, x=3)
    assert primavera.x == 3
    assert primavera.mana_gastado == 4
    assert primavera.zona == "Stack"
    partida.resolver_hechizo(primavera)
    assert len(partida.en_zona("Hand", dueno)) == 3
    assert primavera.zona == "Graveyard"

    perla = partida.instanciar(cartas["Pearl of Wisdom"], dueno, "Hand")
    assert partida.coste_de_lanzar(perla).generico == 0


def test_hechizo_deja_un_efecto_continuo() -> None:
    cartas = catalogo(RAIZ)
    partida, dueno = _partida()
    oso = _campo(partida, dueno, criatura("Oso", 2, 2))
    crecimiento = partida.instanciar(cartas["Giant Growth"], dueno, "Hand")
    partida.lanzar(crecimiento, objetivos={"TARGET_1": oso})
    partida.resolver_hechizo(crecimiento)
    assert partida.poder(oso) == 5
    assert partida.resistencia(oso) == 5
    assert crecimiento.zona == "Graveyard"
    partida.limpiar_fin_de_turno()
    assert partida.poder(oso) == 2


def test_druid_cuenta_ficha_que_no_es_criatura() -> None:
    cartas = catalogo(RAIZ)
    partida, dueno = _partida()
    druida = _campo(partida, dueno, cartas["Druid of the Spade"])
    tesoro = Definicion(
        nombre="Tesoro",
        oracle_text="",
        mana_cost=None,
        supertypes=("NONE",),
        card_types=("Artifact",),
        subtypes=(),
        keywords=(),
        power=None,
        toughness=None,
        abilities=(),
    )
    _campo(partida, dueno, tesoro, ficha=True)
    assert partida.poder(druida) == 4
    assert partida.tiene_keyword(druida, "Trample")


def test_mind_spring_pierde_si_la_biblioteca_se_vacia() -> None:
    cartas = catalogo(RAIZ)
    partida, dueno = _partida()
    jugador = partida.jugadores[dueno]
    partida.instanciar(criatura("Una", 1, 1), dueno, "Library")
    primavera = partida.instanciar(cartas["Mind Spring"], dueno, "Hand")
    assert jugador.vida == 20
    partida.lanzar(primavera, x=4)
    partida.resolver_hechizo(primavera)
    assert jugador.vida == 0
    assert len(partida.en_zona("Hand", dueno)) == 1


def test_catalogo_de_operandos() -> None:
    cartas = catalogo(RAIZ)
    exigir_resolutores(list(cartas.values()))
    exigir_resolutores([])
    operandos: set[str] = set()
    atributos: set[str] = set()
    for definicion in cartas.values():
        acc = recopilar_operandos(definicion)
        operandos |= acc["operandos"]
        atributos |= acc["atributos"]
        for nombre in acc["operandos"] | acc["atributos"]:
            assert resuelve_operando(nombre), (definicion.nombre, nombre)
    assert "Power" in operandos
    assert "Power" in atributos
    habilidad = {
        "ability_order": 1,
        "ability_type": "StaticAbility",
        "zones_active": ["Battlefield"],
        "costs": [],
        "trigger": {"event_type": "NONE", "conditions": []},
        "effects": [{
            "effect_order": 1,
            "effect_type": "ModifyPower",
            "is_optional": False,
            "target_info": {
                "requires_target": False,
                "target_owner": "SOURCE",
                "target_zone": "Battlefield",
                "target_count": {"min": 1, "max": 1},
                "restrictions": {},
            },
            "effect_conditions": [{
                "condition_type": "COMPARISON",
                "left_operand": "Banana",
                "left_scope": "SOURCE",
                "operator": "EQUALS",
                "right_operand": 1,
            }],
            "parameters": {"amount": 1, "duration": "WHILE_STATIC_ACTIVE"},
        }],
    }
    mala = criatura("Mala", 1, 1, abilities=(habilidad,))
    try:
        exigir_resolutores([mala])
    except OperandoSinResolver:
        pass
    else:
        raise AssertionError("un operando desconocido tiene que fallar al montar")
    adaptador = Adaptador()
    try:
        adaptador.montar([mala], [])
    except OperandoSinResolver:
        pass
    else:
        raise AssertionError("montar tiene que rechazar el operando")
    vacio = Adaptador()
    vacio.montar([], [])
    assert vacio.jugadores[0].vida == 20


def main() -> None:
    pruebas = [
        test_compila_el_starter,
        test_estatica_condicional_y_keyword,
        test_dependencia_entre_carta_estatica_y_definitoria,
        test_definicion_que_se_lee_a_si_misma_termina,
        test_comparacion_con_or,
        test_disparo_y_resolucion,
        test_x_y_reduccion_de_coste,
        test_hechizo_deja_un_efecto_continuo,
        test_druid_cuenta_ficha_que_no_es_criatura,
        test_mind_spring_pierde_si_la_biblioteca_se_vacia,
        test_catalogo_de_operandos,
    ]
    for prueba in pruebas:
        prueba()
        print("ok", prueba.__name__)


if __name__ == "__main__":
    main()
