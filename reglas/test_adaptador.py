"""AddMana y equivalencia del adaptador con motor_prueba.Game."""
from __future__ import annotations

import random
import sys
from pathlib import Path

from reglas.adaptador import Adaptador, DemasiadasAcciones, mazo_prueba
from reglas.compilador import catalogo, criatura
from reglas.modelo import Definicion
from reglas.partida import Partida
from reglas.partida_aleatoria import jugar_starter
from reglas.primitivas import ejecutar

RAIZ = Path(__file__).resolve().parent.parent
JSONLD = RAIZ / "mazos" / "bloomburrow_jsonld"
MOTOR_PRUEBA = RAIZ / "motor_prueba"
if str(MOTOR_PRUEBA) not in sys.path:
    sys.path.insert(0, str(MOTOR_PRUEBA))

from cartas import Creature, Land  # noqa: E402
from ficha_rl import (  # noqa: E402
    ABILITIES,
    ABILITY_DIM,
    COSTS,
    EFFECT_DIM,
    EFFECTS,
    EVENTS,
    GLOBAL_DIM,
    HEADER_DIM,
    OBS_DIM,
    PHASES,
    ZONES,
    construir_slots,
    encode_accion,
    encode_definicion,
    encode_global,
    encode_instancia,
)
from jugador import Player  # noqa: E402
from motor import Game  # noqa: E402


def _mesa():
    cartas = catalogo(JSONLD)
    adaptador = Adaptador()
    adaptador.montar([], [])
    return cartas, adaptador, adaptador.jugadores[0], adaptador.jugadores[1]


def _tierras(adaptador: Adaptador, jugador, definicion: Definicion, n: int = 1):
    puestas = []
    for _ in range(n):
        tierra = adaptador.partida.instanciar(definicion, jugador.id, "Battlefield")
        tierra.entro_este_turno = False
        puestas.append(tierra)
    return puestas


def _por_id(acciones: list[dict], tipo: str, carta_id: str | None = None, destino: str | None = None) -> dict:
    for accion in acciones:
        if accion["tipo"] != tipo:
            continue
        if carta_id is not None and accion.get("carta_id") != carta_id:
            continue
        if destino is not None and accion.get("destino") != destino:
            continue
        return accion
    raise AssertionError(
        f"no hay {tipo} {carta_id} {destino} en "
        f"{[(a['tipo'], a.get('carta_id'), a.get('destino')) for a in acciones]}"
    )


def _menu(adaptador: Adaptador) -> list[dict]:
    assert adaptador.decision is not None, adaptador.fase
    return adaptador.acciones_legales()


def _jugar(adaptador: Adaptador, jugador, carta_id: str) -> None:
    adaptador.ejecutar_accion(jugador, {"tipo": "jugar_carta", "carta_id": carta_id})


def _info(zona: str, dueno: str = "CONTROLLER", mx: int = 1) -> dict:
    return {
        "requires_target": False,
        "target_owner": dueno,
        "target_zone": zona,
        "target_count": {"min": 0, "max": mx},
        "restrictions": {
            "logical_operator": "NONE",
            "types": [],
            "subtypes": [],
            "supertypes": [],
            "modifiers": [],
        },
    }


def _efecto(tipo: str, info: dict, **parametros) -> dict:
    return {
        "effect_order": 1,
        "mode_id": None,
        "is_optional": False,
        "effect_type": tipo,
        "target_info": info,
        "effect_conditions": [],
        "parameters": parametros,
    }


def _info_criatura() -> dict:
    return {
        "requires_target": True,
        "target_owner": None,
        "target_zone": "Battlefield",
        "target_count": {"min": 1, "max": 1},
        "restrictions": {
            "logical_operator": "AND",
            "types": ["Creature"],
            "subtypes": [],
            "supertypes": [],
            "modifiers": [],
        },
    }


def _instantaneo(nombre: str, efectos: list[dict]) -> Definicion:
    base = _conjuro(nombre, efectos)
    return Definicion(
        nombre=base.nombre,
        oracle_text="",
        mana_cost=None,
        supertypes=base.supertypes,
        card_types=("Instant",),
        subtypes=(),
        keywords=(),
        power=None,
        toughness=None,
        abilities=base.abilities,
    )


def _por_tipo(acciones: list[dict], tipo: str) -> dict:
    for accion in acciones:
        if accion["tipo"] == tipo:
            return accion
    raise AssertionError(f"no hay {tipo} en {[a['tipo'] for a in acciones]}")


def _pasar(adaptador: Adaptador) -> None:
    acciones = adaptador.acciones_legales()
    jugador = adaptador.jugadores[adaptador.quien_actua()]
    adaptador.ejecutar_accion(jugador, _por_tipo(acciones, "pasar_fase"))


def _conjuro(nombre: str, efectos: list[dict]) -> Definicion:
    for indice, efecto in enumerate(efectos, start=1):
        efecto["effect_order"] = indice
    return Definicion(
        nombre=nombre,
        oracle_text="",
        mana_cost=None,
        supertypes=("NONE",),
        card_types=("Sorcery",),
        subtypes=(),
        keywords=(),
        power=None,
        toughness=None,
        abilities=({
            "ability_order": 1,
            "ability_type": "SpellAbility",
            "zones_active": ["Stack"],
            "modal_choices": None,
            "costs": [],
            "trigger": {"event_type": "NONE", "conditions": []},
            "effects": efectos,
        },),
    )


def _bit_efecto(definicion: Definicion, habilidad: int, efecto: int, nombre: str) -> float:
    vector = encode_definicion(definicion)
    cabecera = len(ABILITIES) + len(EVENTS) + len(ZONES) + len(COSTS)
    origen = HEADER_DIM + habilidad * ABILITY_DIM + cabecera + efecto * EFFECT_DIM
    return vector[origen + EFFECTS.index(nombre)]


def test_add_mana_forest() -> None:
    cartas = catalogo(JSONLD)
    partida = Partida()
    jugador = partida.nuevo_jugador("A")
    partida.nuevo_jugador("B")
    forest = partida.instanciar(cartas["Forest"], jugador.id, "Battlefield")
    assert jugador.mana == {}
    forest.girada = True
    habilidad = forest.definicion.habilidad(1)
    ctx = partida.contexto(forest)
    for efecto in habilidad["effects"]:
        ejecutar(ctx, efecto)
    assert jugador.mana == {"GREEN": 1}


def _proyeccion_adaptador(adaptador: Adaptador) -> dict:
    partida = adaptador.partida
    jugadores = []
    for jugador in adaptador.jugadores:
        mesa = []
        for object_id in jugador.zonas["Battlefield"]:
            carta = partida.objetos[object_id]
            tipos = carta.definicion.card_types
            if "Land" in tipos:
                mesa.append(("Land", carta.girada, 0, False, 0, 0))
            else:
                mesa.append((
                    "Creature",
                    carta.girada,
                    carta.danio,
                    carta.entro_este_turno,
                    partida.poder(carta),
                    partida.resistencia(carta),
                ))
        jugadores.append({
            "vida": jugador.vida,
            "mano": len(jugador.zonas["Hand"]),
            "biblioteca": len(jugador.zonas["Library"]),
            "cementerio": len(jugador.zonas["Graveyard"]),
            "mesa": mesa,
        })
    return {
        "fase": adaptador.fase,
        "activo": adaptador.indice_activo,
        "jugadores": jugadores,
    }


def _proyeccion_motor(game: Game) -> dict:
    jugadores = []
    for jugador in game.players:
        mesa = []
        for carta in jugador.battlefield:
            if isinstance(carta, Land):
                mesa.append(("Land", carta.is_tapped, 0, False, 0, 0))
            else:
                mesa.append((
                    "Creature",
                    carta.is_tapped,
                    carta.damage_taken,
                    carta.summoning_sickness,
                    carta.power,
                    carta.toughness,
                ))
        jugadores.append({
            "vida": jugador.life,
            "mano": len(jugador.hand),
            "biblioteca": len(jugador.deck),
            "cementerio": len(jugador.graveyard),
            "mesa": mesa,
        })
    return {
        "fase": game.current_phase,
        "activo": game.current_turn,
        "jugadores": jugadores,
    }


def _elegir(acciones: list[dict], preferencia: str) -> dict:
    for accion in acciones:
        if accion["tipo"] == preferencia:
            if preferencia == "jugar_carta" and "tierra" in accion["descripcion"].lower():
                return accion
            if preferencia == "jugar_carta" and "criatura" in accion["descripcion"].lower():
                return accion
            if preferencia != "jugar_carta":
                return accion
    for accion in acciones:
        if accion["tipo"] == preferencia:
            return accion
    raise AssertionError(f"no hay acción {preferencia} en {[a['tipo'] for a in acciones]}")


def _paso_script(
    fase: str,
    puede_tierra: bool,
    puede_criatura: bool,
    puede_atacar: bool,
) -> str:
    if fase == "Principal":
        if puede_tierra:
            return "jugar_tierra"
        if puede_criatura:
            return "jugar_criatura"
        return "pasar_fase"
    if fase == "Declarar Atacantes":
        if puede_atacar:
            return "declarar_atacante"
        return "finalizar_ataques"
    if fase == "Declarar Bloqueadores":
        return "bloquear_o_resolver"
    raise AssertionError(fase)


def _aplicar_adaptador(adaptador: Adaptador, clave: str) -> None:
    if adaptador.fase == "Declarar Bloqueadores":
        jugador = adaptador.defensor()
    else:
        jugador = adaptador.jugador_activo()
    acciones = adaptador.acciones_legales(jugador)
    if clave == "jugar_tierra":
        accion = next(a for a in acciones if a["tipo"] == "jugar_carta" and "tierra" in a["descripcion"].lower())
    elif clave == "jugar_criatura":
        accion = next(a for a in acciones if a["tipo"] == "jugar_carta" and "criatura" in a["descripcion"].lower())
    elif clave == "bloquear_o_resolver":
        bloqueos = [a for a in acciones if a["tipo"] == "declarar_bloqueo"]
        accion = bloqueos[0] if bloqueos else next(a for a in acciones if a["tipo"] == "resolver_combate")
    else:
        accion = _elegir(acciones, clave)
    adaptador.ejecutar_accion(jugador, accion)


def _aplicar_motor(game: Game, clave: str) -> None:
    if game.current_phase == "Declarar Bloqueadores":
        jugador = game.players[1 - game.current_turn]
    else:
        jugador = game.players[game.current_turn]
    acciones = game.get_legal_actions(jugador)
    if clave == "jugar_tierra":
        accion = next(a for a in acciones if a["tipo"] == "jugar_carta" and "tierra" in a["descripcion"].lower())
    elif clave == "jugar_criatura":
        accion = next(a for a in acciones if a["tipo"] == "jugar_carta" and "criatura" in a["descripcion"].lower())
    elif clave == "bloquear_o_resolver":
        bloqueos = [a for a in acciones if a["tipo"] == "declarar_bloqueo"]
        accion = bloqueos[0] if bloqueos else next(a for a in acciones if a["tipo"] == "resolver_combate")
    else:
        accion = _elegir(acciones, clave)
    game.execute_action(jugador, accion)


def _estado_para_script_adaptador(adaptador: Adaptador) -> tuple[str, bool, bool, bool]:
    jugador = adaptador.jugador_activo()
    acciones = adaptador.acciones_legales(jugador)
    puede_tierra = any(
        a["tipo"] == "jugar_carta" and "tierra" in a["descripcion"].lower()
        for a in acciones
    )
    puede_criatura = any(
        a["tipo"] == "jugar_carta" and "criatura" in a["descripcion"].lower()
        for a in acciones
    )
    puede_atacar = any(a["tipo"] == "declarar_atacante" for a in acciones)
    return adaptador.fase, puede_tierra, puede_criatura, puede_atacar


def _estado_para_script_motor(game: Game) -> tuple[str, bool, bool, bool]:
    jugador = game.players[game.current_turn]
    acciones = game.get_legal_actions(jugador)
    puede_tierra = any(
        a["tipo"] == "jugar_carta" and "tierra" in a["descripcion"].lower()
        for a in acciones
    )
    puede_criatura = any(
        a["tipo"] == "jugar_carta" and "criatura" in a["descripcion"].lower()
        for a in acciones
    )
    puede_atacar = any(a["tipo"] == "declarar_atacante" for a in acciones)
    return game.current_phase, puede_tierra, puede_criatura, puede_atacar


def _alinear_motor(game: Game, adaptador: Adaptador) -> None:
    """El motor no salta fases vacías; repite el único pase o confirmación hasta empatar."""
    for _ in range(16):
        if game.current_phase == adaptador.fase and game.current_turn == adaptador.indice_activo:
            return
        if game.current_phase == "Declarar Bloqueadores":
            jugador = game.players[1 - game.current_turn]
        else:
            jugador = game.players[game.current_turn]
        acciones = game.get_legal_actions(jugador)
        if len(acciones) == 1 and acciones[0]["tipo"] in (
            "pasar_fase",
            "finalizar_ataques",
            "resolver_combate",
        ):
            game.execute_action(jugador, acciones[0])
            continue
        return


def _estado_de_acciones(fase: str, acciones: list[dict]) -> tuple[str, bool, bool, bool]:
    puede_tierra = any(
        a["tipo"] == "jugar_carta" and "tierra" in a["descripcion"].lower() for a in acciones
    )
    puede_criatura = any(
        a["tipo"] == "jugar_carta" and "criatura" in a["descripcion"].lower() for a in acciones
    )
    puede_atacar = any(a["tipo"] == "declarar_atacante" for a in acciones)
    return fase, puede_tierra, puede_criatura, puede_atacar


def test_equivalencia_con_motor() -> None:
    semilla = 7
    random.seed(semilla)
    adaptador = Adaptador(modo_menu="puente")
    adaptador.montar(mazo_prueba(6, 6), mazo_prueba(6, 6))

    random.seed(semilla)
    game = Game(Player("player_0", verbose=False), Player("player_1", verbose=False), verbose=False)
    deck0 = [Land() for _ in range(6)] + [Creature("Oso", 2, 2, 2) for _ in range(6)]
    deck1 = [Land() for _ in range(6)] + [Creature("Oso", 2, 2, 2) for _ in range(6)]
    game.setup_game(deck_p0=deck0, deck_p1=deck1)
    game.players[0].draw_card()

    assert _proyeccion_adaptador(adaptador) == _proyeccion_motor(game)

    for _ in range(40):
        if adaptador.jugadores[0].vida <= 0 or adaptador.jugadores[1].vida <= 0:
            break
        if game.players[0].life <= 0 or game.players[1].life <= 0:
            break
        if adaptador.fase == "Declarar Bloqueadores":
            acciones = adaptador.acciones_legales(adaptador.defensor())
        else:
            acciones = adaptador.acciones_legales(adaptador.jugador_activo())
        _alinear_motor(game, adaptador)
        if adaptador.jugadores[0].vida <= 0 or adaptador.jugadores[1].vida <= 0:
            break
        if game.players[0].life <= 0 or game.players[1].life <= 0:
            break
        assert _proyeccion_adaptador(adaptador) == _proyeccion_motor(game)
        if adaptador.fase == "Declarar Bloqueadores":
            clave = "bloquear_o_resolver"
        else:
            estado = _estado_de_acciones(adaptador.fase, acciones)
            assert estado == _estado_para_script_motor(game)
            clave = _paso_script(*estado)
        _aplicar_adaptador(adaptador, clave)
        _aplicar_motor(game, clave)
        assert _proyeccion_adaptador(adaptador) == _proyeccion_motor(game)


def test_tope_de_cincuenta_acciones() -> None:
    adaptador = Adaptador()
    adaptador.montar([], [])
    j0, j1 = adaptador.jugadores
    oso = criatura("Oso", 2, 2, mana_cost="{2}")
    for _ in range(8):
        carta = adaptador.partida.instanciar(oso, j0.id, "Battlefield")
        carta.entro_este_turno = False
        adaptador.atacantes.append(carta.object_id)
    for _ in range(8):
        carta = adaptador.partida.instanciar(oso, j1.id, "Battlefield")
        carta.entro_este_turno = False
    adaptador.fase = "Declarar Bloqueadores"
    try:
        adaptador.acciones_legales(j1)
    except DemasiadasAcciones as error:
        assert int(str(error)) > 50
    else:
        raise AssertionError("se esperaba DemasiadasAcciones")


def test_serra_resuelve_por_la_pila_del_menu() -> None:
    cartas = catalogo(JSONLD)
    adaptador = Adaptador()
    adaptador.montar([], [])
    j0 = adaptador.jugadores[0]
    adaptador.partida.instanciar(cartas["Serra Redeemer"], j0.id, "Battlefield")
    for _ in range(2):
        tierra = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Battlefield")
        tierra.entro_este_turno = False
    soldado = adaptador.partida.instanciar(criatura("Soldado", 1, 1, mana_cost="{2}"), j0.id, "Hand")
    adaptador.ejecutar_accion(j0, {"tipo": "jugar_carta", "carta_id": soldado.object_id})
    assert adaptador.partida.pila == []
    assert soldado.zona == "Battlefield"
    assert soldado.contadores.get("PlusOnePlusOne") == 2
    assert adaptador.partida.poder(soldado) == 3


def test_mana_de_color() -> None:
    cartas = catalogo(JSONLD)
    adaptador = Adaptador()
    adaptador.montar([], [])
    j0 = adaptador.jugadores[0]
    island = adaptador.partida.instanciar(cartas["Island"], j0.id, "Battlefield")
    mountain = adaptador.partida.instanciar(cartas["Mountain"], j0.id, "Battlefield")
    island.entro_este_turno = False
    mountain.entro_este_turno = False
    stormcatch = adaptador.partida.instanciar(cartas["Stormcatch Mentor"], j0.id, "Hand")
    coste = adaptador.partida.coste_de_lanzar(stormcatch)
    assert coste.colores == {"U": 1, "R": 1}
    assert adaptador.puede_pagar(j0, coste)

    adaptador2 = Adaptador()
    adaptador2.montar([], [])
    j = adaptador2.jugadores[0]
    for _ in range(2):
        f = adaptador2.partida.instanciar(cartas["Forest"], j.id, "Battlefield")
        f.entro_este_turno = False
    otra = adaptador2.partida.instanciar(cartas["Stormcatch Mentor"], j.id, "Hand")
    assert not adaptador2.puede_pagar(j, adaptador2.partida.coste_de_lanzar(otra))


def test_giant_growth_caduca_al_pasar_turno() -> None:
    cartas = catalogo(JSONLD)
    adaptador = Adaptador()
    adaptador.montar([], [])
    j0 = adaptador.jugadores[0]
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    crecimiento = adaptador.partida.instanciar(cartas["Giant Growth"], j0.id, "Hand")
    adaptador.partida.lanzar(crecimiento, objetivos={"TARGET_1": oso})
    adaptador._vaciar_pila()
    assert adaptador.partida.poder(oso) == 5
    adaptador._siguiente_turno()
    assert adaptador.partida.poder(oso) == 2


def test_enter_tapped_y_add_keyword() -> None:
    cartas = catalogo(JSONLD)
    adaptador = Adaptador()
    adaptador.montar([], [])
    j0 = adaptador.jugadores[0]
    tierra = adaptador.partida.instanciar(cartas["Blossoming Sands"], j0.id, "Library")
    adaptador.partida.entrar(tierra)
    adaptador._vaciar_pila()
    assert tierra.girada
    assert j0.vida == 21

def test_discard_y_scry() -> None:
    cartas = catalogo(JSONLD)
    adaptador = Adaptador()
    adaptador.montar([], [])
    j0 = adaptador.jugadores[0]
    bosques = [
        adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
        for _ in range(3)
    ]
    crier = adaptador.partida.instanciar(cartas["Bellowing Crier"], j0.id, "Library")
    isla = adaptador.partida.instanciar(cartas["Island"], j0.id, "Hand")
    mano_antes = len(j0.zonas["Hand"])
    adaptador.partida.entrar(crier)
    adaptador._vaciar_pila()
    assert adaptador.decision is not None and adaptador.decision["tipo"] == "descartar_efecto"
    acciones = adaptador.acciones_legales()
    adaptador.ejecutar_accion(j0, _por_id(acciones, "elegir_objetivo", isla.object_id))
    assert len(j0.zonas["Hand"]) == mano_antes  # +1 robo -1 descarte
    assert isla.zona == "Graveyard"
    assert all(bosque.zona != "Graveyard" for bosque in bosques)
    assert sum(bosque.zona == "Hand" for bosque in bosques) == 1


def test_pathmaker_exilia_una() -> None:
    cartas = catalogo(JSONLD)
    adaptador = Adaptador()
    adaptador.montar([], [])
    j0 = adaptador.jugadores[0]
    tope = adaptador.partida.instanciar(cartas["Mountain"], j0.id, "Library")
    resto = [
        adaptador.partida.instanciar(cartas["Island"], j0.id, "Library")
        for _ in range(4)
    ]
    pathmaker = adaptador.partida.instanciar(cartas["Alania's Pathmaker"], j0.id, "Library")
    lib_antes = len(j0.zonas["Library"])
    vida_antes = j0.vida
    adaptador.partida.entrar(pathmaker)
    adaptador._vaciar_pila()
    assert len(j0.zonas["Library"]) == lib_antes - 2  # Pathmaker sale + 1 exilio
    assert tope.zona == "Exile"
    assert all(c.zona == "Library" for c in resto)
    assert j0.vida == vida_antes
    assert adaptador.partida.carta_jugable_por_permiso(tope, j0.id)


def test_move_zone_max_estrella_mueve_todas_las_reveladas() -> None:
    from reglas.evaluar import Contexto

    cartas = catalogo(JSONLD)
    partida = Partida()
    j = partida.nuevo_jugador("A")
    partida.nuevo_jugador("B")
    reveladas = []
    for _ in range(3):
        c = partida.instanciar(cartas["Forest"], j.id, "Library")
        c.revelada = True
        reveladas.append(c)
    partida.instanciar(cartas["Island"], j.id, "Library")  # no revelada
    fuente = partida.instanciar(cartas["Clifftop Lookout"], j.id, "Battlefield")
    efecto = {
        "effect_type": "MoveZone",
        "target_info": {
            "requires_target": False,
            "target_owner": "CONTROLLER",
            "target_zone": "Library",
            "target_count": {"min": 0, "max": "*"},
            "restrictions": {
                "logical_operator": "AND",
                "types": [],
                "subtypes": [],
                "supertypes": [],
                "modifiers": ["Revealed"],
            },
        },
        "parameters": {
            "amount": None,
            "destination_zone": "Library",
            "placement": "BOTTOM_RANDOM",
        },
    }
    ctx = Contexto(partida=partida, fuente=fuente, controlador=j.id)
    ejecutar(ctx, efecto)
    assert all(c.zona == "Library" for c in reveladas)
    assert all(not c.revelada for c in reveladas)
    assert len(j.zonas["Library"]) == 4


def test_descarte_al_cerrar_turno() -> None:
    cartas = catalogo(JSONLD)
    adaptador = Adaptador(modo_menu="completo", max_acciones=16)
    adaptador.montar([], [])
    j0 = adaptador.jugadores[0]
    j1 = adaptador.jugadores[1]
    for _ in range(9):
        adaptador.partida.instanciar(cartas["Forest"], j0.id, "Hand")
    adaptador.partida.instanciar(cartas["Island"], j1.id, "Library")
    assert len(j0.zonas["Hand"]) == 9
    adaptador._cerrar_turno()
    assert adaptador.fase == "Descartar"
    assert adaptador.indice_activo == 0
    legales = adaptador.acciones_legales(j0)
    assert all(a["tipo"] == "descartar" for a in legales)
    assert len(legales) == 9
    adaptador.ejecutar_accion(j0, legales[0])
    assert adaptador.fase == "Descartar"
    assert len(j0.zonas["Hand"]) == 8
    legales = adaptador.acciones_legales(j0)
    adaptador.ejecutar_accion(j0, legales[0])
    assert len(j0.zonas["Hand"]) == 7
    assert adaptador.fase == "Principal"
    assert adaptador.indice_activo == 1
    assert len(j1.zonas["Hand"]) == 1  # robó al empezar su turno


def test_partida_starter_aleatoria() -> None:
    adaptador = jugar_starter(semilla=1, max_pasos=5000)
    assert adaptador.terminada()
    assert adaptador.partida.pila == []


def test_byrke_hasta_dos_y_dobla_al_atacar() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    byrke = adaptador.partida.instanciar(cartas["Byrke, Long Ear of the Law"], j0.id, "Library")
    una = adaptador.partida.instanciar(criatura("Una", 2, 2), j0.id, "Battlefield")
    otra = adaptador.partida.instanciar(criatura("Otra", 2, 2), j0.id, "Battlefield")
    tercera = adaptador.partida.instanciar(criatura("Tercera", 2, 2), j0.id, "Battlefield")
    adaptador.partida.entrar(byrke)
    adaptador._vaciar_pila()
    assert adaptador.decision is not None and adaptador.decision["tipo"] == "objetivos_n"
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", una.object_id))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", otra.object_id))
    assert adaptador.decision is None
    assert una.contadores.get("PlusOnePlusOne") == 1
    assert otra.contadores.get("PlusOnePlusOne") == 1
    assert tercera.contadores.get("PlusOnePlusOne", 0) == 0

    cartas, adaptador, j0, _j1 = _mesa()
    byrke = adaptador.partida.instanciar(cartas["Byrke, Long Ear of the Law"], j0.id, "Library")
    criaturas = [
        adaptador.partida.instanciar(criatura(f"C{i}", 2, 2), j0.id, "Battlefield")
        for i in range(2)
    ]
    adaptador.partida.entrar(byrke)
    adaptador._vaciar_pila()
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "confirmar_objetivos"))
    assert adaptador.decision is None
    assert byrke.contadores.get("PlusOnePlusOne", 0) == 0
    assert all(c.contadores.get("PlusOnePlusOne", 0) == 0 for c in criaturas)

    cartas, adaptador, j0, _j1 = _mesa()
    byrke = adaptador.partida.instanciar(cartas["Byrke, Long Ear of the Law"], j0.id, "Battlefield")
    byrke.entro_este_turno = False
    marcada = adaptador.partida.instanciar(criatura("Marcada", 2, 2), j0.id, "Battlefield")
    marcada.entro_este_turno = False
    marcada.contadores["PlusOnePlusOne"] = 1
    limpia = adaptador.partida.instanciar(criatura("Limpia", 2, 2), j0.id, "Battlefield")
    limpia.entro_este_turno = False
    adaptador.fase = "Declarar Atacantes"
    adaptador.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": marcada.object_id})
    assert marcada.contadores["PlusOnePlusOne"] == 1
    adaptador.ejecutar_accion(j0, {"tipo": "finalizar_ataques"})
    assert marcada.contadores["PlusOnePlusOne"] == 2
    assert limpia.contadores.get("PlusOnePlusOne", 0) == 0


def test_fecund_previous_target_y_opcional() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    adaptador.partida.instanciar(cartas["Fecund Greenshell"], j0.id, "Battlefield")
    tierra = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    entrante = adaptador.partida.instanciar(criatura("Alta", 1, 4), j0.id, "Library")
    adaptador.partida.entrar(entrante)
    adaptador._vaciar_pila()
    assert adaptador.decision is not None and adaptador.decision["tipo"] == "opcional"
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "aceptar"))
    assert tierra.zona == "Battlefield"
    assert tierra.girada
    assert adaptador.decision is None

    cartas, adaptador, j0, _j1 = _mesa()
    adaptador.partida.instanciar(cartas["Fecund Greenshell"], j0.id, "Battlefield")
    tierra = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    entrante = adaptador.partida.instanciar(criatura("Alta", 1, 4), j0.id, "Library")
    adaptador.partida.entrar(entrante)
    adaptador._vaciar_pila()
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "rechazar"))
    assert tierra.zona == "Library"
    assert tierra.object_id not in j0.zonas["Hand"]
    assert adaptador.decision is None

    cartas, adaptador, j0, _j1 = _mesa()
    adaptador.partida.instanciar(cartas["Fecund Greenshell"], j0.id, "Battlefield")
    no_tierra = adaptador.partida.instanciar(cartas["Giant Growth"], j0.id, "Library")
    entrante = adaptador.partida.instanciar(criatura("Alta", 1, 4), j0.id, "Library")
    adaptador.partida.entrar(entrante)
    adaptador._vaciar_pila()
    assert adaptador.decision is None
    assert no_tierra.zona == "Hand"
    assert adaptador.partida.pila == []


def test_finneas_contadores_y_robo() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    finneas = adaptador.partida.instanciar(cartas["Finneas, Ace Archer"], j0.id, "Battlefield")
    finneas.entro_este_turno = False
    ficha = adaptador.partida.instanciar(criatura("Ficha", 1, 1), j0.id, "Battlefield", es_ficha=True)
    liebre = adaptador.partida.instanciar(criatura("Liebre", 1, 1, subtypes=("Rabbit",)), j0.id, "Battlefield")
    oso = adaptador.partida.instanciar(criatura("Oso", 5, 5), j0.id, "Battlefield")
    for carta in (ficha, liebre, oso):
        carta.entro_este_turno = False
    robable = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    mano = len(j0.zonas["Hand"])
    adaptador.fase = "Declarar Atacantes"
    adaptador.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": finneas.object_id})
    assert ficha.contadores.get("PlusOnePlusOne", 0) == 0
    assert robable.zona == "Library"
    adaptador.ejecutar_accion(j0, {"tipo": "finalizar_ataques"})
    assert ficha.contadores.get("PlusOnePlusOne") == 1
    assert liebre.contadores.get("PlusOnePlusOne") == 1
    assert oso.contadores.get("PlusOnePlusOne", 0) == 0
    assert finneas.contadores.get("PlusOnePlusOne", 0) == 0
    assert robable.zona == "Hand"
    assert len(j0.zonas["Hand"]) == mano + 1


def test_pileated_no_ofrece_voladoras() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    voladora = adaptador.partida.instanciar(
        criatura("Ave", 2, 2, keywords=("Flying",)), j0.id, "Battlefield",
    )
    suelo = adaptador.partida.instanciar(criatura("Suelo", 2, 2), j0.id, "Battlefield")
    pajaro = adaptador.partida.instanciar(cartas["Pileated Provisioner"], j0.id, "Library")
    adaptador.partida.entrar(pajaro)
    adaptador._vaciar_pila()
    acciones = _menu(adaptador)
    ids = [a.get("carta_id") for a in acciones]
    assert suelo.object_id in ids
    assert voladora.object_id not in ids
    adaptador.ejecutar_accion(j0, _por_id(acciones, "elegir_objetivo", suelo.object_id))
    assert suelo.contadores.get("PlusOnePlusOne") == 1
    assert voladora.contadores.get("PlusOnePlusOne", 0) == 0

    cartas, adaptador, j0, _j1 = _mesa()
    voladora = adaptador.partida.instanciar(
        criatura("Ave", 2, 2, keywords=("Flying",)), j0.id, "Battlefield",
    )
    pajaro = adaptador.partida.instanciar(cartas["Pileated Provisioner"], j0.id, "Library")
    adaptador.partida.entrar(pajaro)
    adaptador._vaciar_pila()
    assert adaptador.decision is None
    assert voladora.contadores.get("PlusOnePlusOne", 0) == 0


def test_treeguard_elige_en_el_menu() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    duo = adaptador.partida.instanciar(cartas["Treeguard Duo"], j0.id, "Library")
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    adaptador.partida.entrar(duo)
    adaptador._vaciar_pila()
    acciones = _menu(adaptador)
    assert any(a.get("carta_id") == oso.object_id for a in acciones)
    adaptador.ejecutar_accion(j0, _por_id(acciones, "elegir_objetivo", oso.object_id))
    assert adaptador.partida.tiene_keyword(oso, "Vigilance")
    assert adaptador.partida.poder(oso) == 4
    assert adaptador.partida.resistencia(oso) == 4


def test_colossificacion_anexa_y_queda_huerfana() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    _tierras(adaptador, j0, cartas["Forest"], 7)
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    aura = adaptador.partida.instanciar(cartas["Colossification"], j0.id, "Hand")
    _jugar(adaptador, j0, aura.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", oso.object_id))
    assert adaptador.partida.poder(oso) == 22
    assert adaptador.partida.resistencia(oso) == 22
    assert oso.girada
    assert aura.anexada_a is not None and aura.anexada_a.object_id == oso.object_id
    _slots, mapa = construir_slots(adaptador, 0)
    assert encode_instancia(aura, adaptador.partida, j0.id, mapa)[-1] == mapa[oso.object_id]
    oso.danio = adaptador.partida.resistencia(oso)
    adaptador._acciones_de_estado()
    assert oso.zona == "Graveyard"
    assert aura.zona == "Graveyard"


def test_repel_solo_ofrece_criaturas_grandes() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    _tierras(adaptador, j0, cartas["Plains"], 2)
    pequena = adaptador.partida.instanciar(criatura("Pequeña", 2, 2), j0.id, "Battlefield")
    repel = adaptador.partida.instanciar(cartas["Repel Calamity"], j0.id, "Hand")
    acciones = adaptador._enumerar(j0)
    assert all(a.get("carta_id") != repel.object_id for a in acciones)
    grande = adaptador.partida.instanciar(criatura("Grande", 5, 2), j0.id, "Battlefield")
    alta = adaptador.partida.instanciar(criatura("Alta", 2, 5), j0.id, "Battlefield")
    acciones = adaptador._enumerar(j0)
    assert any(a.get("carta_id") == repel.object_id for a in acciones)
    _jugar(adaptador, j0, repel.object_id)
    acciones = _menu(adaptador)
    ids = [a.get("carta_id") for a in acciones]
    assert grande.object_id in ids
    assert alta.object_id in ids
    assert pequena.object_id not in ids
    adaptador.ejecutar_accion(j0, _por_id(acciones, "elegir_objetivo", grande.object_id))
    assert grande.zona == "Graveyard"
    assert alta.zona == "Battlefield"
    assert pequena.zona == "Battlefield"


def test_bria_prowess_y_no_bloqueable() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    bria = adaptador.partida.instanciar(cartas["Bria, Riptide Rogue"], j0.id, "Battlefield")
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    bria.entro_este_turno = False
    oso.entro_este_turno = False
    poder_bria = adaptador.partida.poder(bria)
    poder_oso = adaptador.partida.poder(oso)
    _tierras(adaptador, j0, cartas["Island"], 5)
    for _ in range(2):
        adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    perla = adaptador.partida.instanciar(cartas["Pearl of Wisdom"], j0.id, "Hand")
    _jugar(adaptador, j0, perla.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", oso.object_id))
    assert adaptador.decision is None
    assert adaptador.partida.poder(bria) == poder_bria + 1
    assert adaptador.partida.poder(oso) == poder_oso + 1
    assert not adaptador.partida.puede_ser_bloqueada(oso)
    assert adaptador.partida.puede_ser_bloqueada(bria)
    for object_id in j0.zonas["Battlefield"]:
        carta = adaptador.partida.objetos[object_id]
        if "Land" in carta.definicion.card_types:
            carta.girada = False
    soldado = adaptador.partida.instanciar(criatura("Soldado", 1, 1, mana_cost="{2}"), j0.id, "Hand")
    _jugar(adaptador, j0, soldado.object_id)
    assert adaptador.decision is None
    assert adaptador.partida.pila == []
    assert adaptador.partida.poder(bria) == poder_bria + 1
    assert adaptador.partida.poder(oso) == poder_oso + 1
    assert adaptador.partida.poder(soldado) == 1


def test_mockingbird_filtra_por_mana_gastado() -> None:
    _cartas, adaptador, j0, _j1 = _mesa()
    barato = adaptador.partida.instanciar(criatura("Barato", 2, 2, mana_cost="{1}"), j0.id, "Battlefield")
    caro = adaptador.partida.instanciar(criatura("Caro", 5, 5, mana_cost="{4}"), j0.id, "Battlefield")
    pajaro = adaptador.partida.instanciar(catalogo(JSONLD)["Mockingbird"], j0.id, "Hand")
    adaptador.partida.lanzar(pajaro, x=1)
    assert pajaro.mana_gastado == 2
    adaptador._vaciar_pila()
    acciones = _menu(adaptador)
    ids = [a.get("carta_id") for a in acciones]
    assert barato.object_id in ids
    assert caro.object_id not in ids
    assert any(a["tipo"] == "rechazar" for a in acciones)
    adaptador.ejecutar_accion(j0, _por_id(acciones, "rechazar"))
    assert pajaro.zona == "Battlefield"
    assert pajaro.definicion.nombre == "Mockingbird"

    otro = adaptador.partida.instanciar(catalogo(JSONLD)["Mockingbird"], j0.id, "Hand")
    adaptador.partida.lanzar(otro, x=1)
    adaptador._vaciar_pila()
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", barato.object_id))
    assert otro.definicion.nombre == "Barato"
    assert "Flying" in otro.definicion.keywords
    assert adaptador.partida.poder(otro) == 2


def test_alania_primer_hechizo_copia_y_reinicio() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    alania = adaptador.partida.instanciar(cartas["Alania, Divergent Storm"], j0.id, "Battlefield")
    alania.entro_este_turno = False
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    otro = adaptador.partida.instanciar(criatura("Otro", 2, 2), j0.id, "Battlefield")
    _tierras(adaptador, j0, cartas["Forest"], 4)
    _tierras(adaptador, j0, cartas["Island"], 4)
    _tierras(adaptador, j0, cartas["Mountain"], 2)
    for _ in range(4):
        adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
        adaptador.partida.instanciar(cartas["Island"], j1.id, "Library")
    soldado = adaptador.partida.instanciar(criatura("Soldado", 1, 1, mana_cost="{2}"), j0.id, "Hand")
    _jugar(adaptador, j0, soldado.object_id)
    assert adaptador.decision is None
    assert soldado.zona == "Battlefield"

    crecimiento = adaptador.partida.instanciar(cartas["Giant Growth"], j0.id, "Hand")
    mano_rival = len(j1.zonas["Hand"])
    _jugar(adaptador, j0, crecimiento.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", oso.object_id))
    rival = adaptador.partida.representantes[j1.id]
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", rival.object_id))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "aceptar"))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", otro.object_id))
    assert adaptador.decision is None
    assert adaptador.partida.poder(oso) == 5
    assert adaptador.partida.poder(otro) == 5
    assert len(j1.zonas["Hand"]) == mano_rival + 1

    perla = adaptador.partida.instanciar(cartas["Pearl of Wisdom"], j0.id, "Hand")
    biblioteca = len(j0.zonas["Library"])
    _jugar(adaptador, j0, perla.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", rival.object_id))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "rechazar"))
    assert adaptador.decision is None
    assert len(j0.zonas["Library"]) == biblioteca - 2
    assert len(j1.zonas["Hand"]) == mano_rival + 1

    mentor = adaptador.partida.instanciar(cartas["Stormcatch Mentor"], j0.id, "Hand")
    _jugar(adaptador, j0, mentor.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", rival.object_id))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "rechazar"))
    assert mentor.zona == "Battlefield"
    assert sum(
        1 for object_id in j0.zonas["Battlefield"]
        if adaptador.partida.objetos[object_id].definicion.nombre == "Stormcatch Mentor"
    ) == 1

    for object_id in j0.zonas["Battlefield"]:
        carta = adaptador.partida.objetos[object_id]
        if "Land" in carta.definicion.card_types:
            carta.girada = False
    segundo = adaptador.partida.instanciar(cartas["Giant Growth"], j0.id, "Hand")
    _jugar(adaptador, j0, segundo.object_id)
    assert adaptador.decision is not None and adaptador.decision.get("origen") == "hechizo"
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", oso.object_id))
    assert adaptador.decision is None
    assert adaptador.partida.pila == []
    assert len(j1.zonas["Hand"]) == mano_rival + 1
    assert adaptador.partida.poder(oso) == 8
    assert adaptador.partida.poder(otro) == 5

    adaptador._siguiente_turno()
    adaptador._siguiente_turno()
    assert adaptador.indice_activo == 0
    assert adaptador.partida.poder(oso) == 2
    assert adaptador.partida.poder(otro) == 2
    tercero = adaptador.partida.instanciar(cartas["Giant Growth"], j0.id, "Hand")
    mano_rival = len(j1.zonas["Hand"])
    _jugar(adaptador, j0, tercero.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", oso.object_id))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", rival.object_id))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "rechazar"))
    assert adaptador.decision is None
    assert adaptador.partida.poder(oso) == 5
    assert adaptador.partida.poder(otro) == 2
    assert len(j1.zonas["Hand"]) == mano_rival


def test_stormcatch_prisa_y_prowess() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    mentor = adaptador.partida.instanciar(cartas["Stormcatch Mentor"], j0.id, "Battlefield")
    vanilla = adaptador.partida.instanciar(criatura("Vanilla", 2, 2), j0.id, "Battlefield")
    mentor.entro_este_turno = True
    vanilla.entro_este_turno = True
    poder = adaptador.partida.poder(mentor)
    resistencia = adaptador.partida.resistencia(mentor)
    adaptador.fase = "Declarar Atacantes"
    acciones = adaptador._enumerar(j0)
    atacantes = [a.get("carta_id") for a in acciones if a["tipo"] == "declarar_atacante"]
    assert mentor.object_id in atacantes
    assert vanilla.object_id not in atacantes
    adaptador.fase = "Principal"
    _tierras(adaptador, j0, cartas["Forest"], 1)
    crecimiento = adaptador.partida.instanciar(cartas["Giant Growth"], j0.id, "Hand")
    _jugar(adaptador, j0, crecimiento.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", vanilla.object_id))
    assert adaptador.partida.poder(mentor) == poder + 1
    assert adaptador.partida.resistencia(mentor) == resistencia + 1
    assert adaptador.partida.poder(vanilla) == 5


def test_coruscation_offspring_copia_el_dano() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Mountain"], 2)
    mago = adaptador.partida.instanciar(cartas["Coruscation Mage"], j0.id, "Hand")
    _jugar(adaptador, j0, mago.object_id)
    assert adaptador.decision is not None and adaptador.decision["tipo"] == "offspring"
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "omitir_opcional"))
    assert mago.zona == "Battlefield"
    assert not any(adaptador.partida.objetos[i].es_ficha for i in j0.zonas["Battlefield"])

    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Mountain"], 4)
    _tierras(adaptador, j0, cartas["Forest"], 1)
    mago = adaptador.partida.instanciar(cartas["Coruscation Mage"], j0.id, "Hand")
    _jugar(adaptador, j0, mago.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "pagar_opcional"))
    assert adaptador.decision is None
    fichas = [
        adaptador.partida.objetos[i]
        for i in j0.zonas["Battlefield"]
        if adaptador.partida.objetos[i].es_ficha
    ]
    assert len(fichas) == 1
    assert adaptador.partida.poder(fichas[0]) == 1
    assert adaptador.partida.resistencia(fichas[0]) == 1
    assert any(
        efecto["effect_type"] == "DealDamage"
        for habilidad in fichas[0].definicion.abilities
        for efecto in habilidad["effects"]
    )
    crecimiento = adaptador.partida.instanciar(cartas["Giant Growth"], j0.id, "Hand")
    vida = j1.vida
    _jugar(adaptador, j0, crecimiento.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", mago.object_id))
    assert j1.vida == vida - 2


def test_thieving_otter_roba_solo_su_dano() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    nutria = adaptador.partida.instanciar(cartas["Thieving Otter"], j0.id, "Battlefield")
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    nutria.entro_este_turno = False
    oso.entro_este_turno = False
    carta = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    adaptador.atacantes = [oso.object_id, nutria.object_id]
    vida = j1.vida
    vida_propia = j0.vida
    adaptador._resolver_combate()
    assert j1.vida == vida - adaptador.partida.poder(oso) - adaptador.partida.poder(nutria)
    assert carta.zona == "Hand"
    assert j0.vida == vida_propia


def test_waterspout_vuela_si_otra_entro() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    guardian = adaptador.partida.instanciar(cartas["Waterspout Warden"], j0.id, "Battlefield")
    guardian.entro_este_turno = False
    otra = adaptador.partida.instanciar(criatura("Otra", 1, 1), j0.id, "Battlefield")
    otra.entro_este_turno = True
    companera = adaptador.partida.instanciar(criatura("Compañera", 1, 1), j0.id, "Battlefield")
    companera.entro_este_turno = False
    adaptador.partida.instanciar(criatura("Muro", 1, 1, keywords=("Flying",)), j1.id, "Battlefield")
    adaptador.fase = "Declarar Atacantes"
    adaptador.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": guardian.object_id})
    assert not adaptador.partida.tiene_keyword(guardian, "Flying")
    adaptador.ejecutar_accion(j0, {"tipo": "finalizar_ataques"})
    assert adaptador.partida.tiene_keyword(guardian, "Flying")

    cartas, adaptador, j0, j1 = _mesa()
    guardian = adaptador.partida.instanciar(cartas["Waterspout Warden"], j0.id, "Battlefield")
    guardian.entro_este_turno = False
    companera = adaptador.partida.instanciar(criatura("Compañera", 1, 1), j0.id, "Battlefield")
    companera.entro_este_turno = False
    adaptador.partida.instanciar(criatura("Muro", 1, 1, keywords=("Flying",)), j1.id, "Battlefield")
    adaptador.fase = "Declarar Atacantes"
    adaptador.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": guardian.object_id})
    adaptador.ejecutar_accion(j0, {"tipo": "finalizar_ataques"})
    assert not adaptador.partida.tiene_keyword(guardian, "Flying")


def test_quaketusk_ataca_el_turno_que_entra() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    jabali = adaptador.partida.instanciar(cartas["Quaketusk Boar"], j0.id, "Battlefield")
    vanilla = adaptador.partida.instanciar(criatura("Vanilla", 2, 2), j0.id, "Battlefield")
    jabali.entro_este_turno = True
    vanilla.entro_este_turno = True
    adaptador.fase = "Declarar Atacantes"
    acciones = adaptador._enumerar(j0)
    atacantes = [a.get("carta_id") for a in acciones if a["tipo"] == "declarar_atacante"]
    assert jabali.object_id in atacantes
    assert vanilla.object_id not in atacantes


def test_sword_prisa_first_strike_y_equipo_suelto() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Forest"], 3)
    espada = adaptador.partida.instanciar(cartas["Sword of Vengeance"], j0.id, "Battlefield")
    oso = adaptador.partida.instanciar(criatura("Oso", 1, 1), j0.id, "Battlefield")
    vanilla = adaptador.partida.instanciar(criatura("Vanilla", 2, 2), j0.id, "Battlefield")
    oso.entro_este_turno = True
    vanilla.entro_este_turno = True
    bloqueador = adaptador.partida.instanciar(criatura("Muro", 2, 2), j1.id, "Battlefield")
    adaptador.ejecutar_accion(j0, {
        "tipo": "activar", "carta_id": espada.object_id, "habilidad": 2,
    })
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", oso.object_id))
    assert espada.anexada_a is not None and espada.anexada_a.object_id == oso.object_id
    assert adaptador.partida.poder(oso) == 3
    assert adaptador.partida.tiene_keyword(oso, "Haste")
    assert adaptador.partida.tiene_keyword(oso, "FirstStrike")
    assert adaptador.partida.tiene_keyword(oso, "Vigilance")
    assert _bit_efecto(espada.definicion, 0, 3, "AddKeyword") == 1.0
    assert _bit_efecto(espada.definicion, 0, 4, "AddKeyword") == 1.0
    _slots, mapa = construir_slots(adaptador, 0)
    assert encode_instancia(espada, adaptador.partida, j0.id, mapa)[-1] == mapa[oso.object_id]
    adaptador.fase = "Declarar Atacantes"
    acciones = adaptador._enumerar(j0)
    atacantes = [a.get("carta_id") for a in acciones if a["tipo"] == "declarar_atacante"]
    assert oso.object_id in atacantes
    assert vanilla.object_id not in atacantes
    adaptador.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": oso.object_id})
    assert oso.atacando
    assert not oso.girada
    adaptador.bloqueos = {bloqueador.object_id: oso.object_id}
    vida = j1.vida
    adaptador._resolver_combate()
    assert bloqueador.zona == "Graveyard"
    assert oso.zona == "Battlefield"
    assert oso.danio == 0
    assert j1.vida == vida - 1
    oso.danio = adaptador.partida.resistencia(oso)
    adaptador._acciones_de_estado()
    assert oso.zona == "Graveyard"
    assert espada.zona == "Battlefield"
    assert espada.anexada_a is None


def test_charmed_sleep_aura_huerfana() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    _tierras(adaptador, j0, cartas["Island"], 3)
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    aura = adaptador.partida.instanciar(cartas["Charmed Sleep"], j0.id, "Hand")
    _jugar(adaptador, j0, aura.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", oso.object_id))
    assert oso.girada
    assert adaptador.partida.impide_enderezar(oso)
    assert aura.anexada_a is not None and aura.anexada_a.object_id == oso.object_id
    _slots, mapa = construir_slots(adaptador, 0)
    assert encode_instancia(aura, adaptador.partida, j0.id, mapa)[-1] == mapa[oso.object_id]
    oso.danio = adaptador.partida.resistencia(oso)
    adaptador._acciones_de_estado()
    assert oso.zona == "Graveyard"
    assert aura.zona == "Graveyard"


def test_flame_lash_puede_dar_a_un_jugador() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Mountain"], 8)
    ancla = adaptador.partida.instanciar(criatura("Ancla", 1, 1), j0.id, "Battlefield")
    ancla.entro_este_turno = False
    criatura_rival = adaptador.partida.instanciar(criatura("Rival", 5, 5), j1.id, "Battlefield")
    latigo = adaptador.partida.instanciar(cartas["Flame Lash"], j0.id, "Hand")
    acciones = adaptador._enumerar(j0)
    assert any(a.get("carta_id") == latigo.object_id for a in acciones)
    _jugar(adaptador, j0, latigo.object_id)
    acciones = _menu(adaptador)
    rival = adaptador.partida.representantes[j1.id]
    ids = [a.get("carta_id") for a in acciones]
    assert rival.object_id in ids
    assert criatura_rival.object_id in ids
    adaptador.ejecutar_accion(j0, _por_id(acciones, "elegir_objetivo", rival.object_id))
    assert j1.vida == 16
    for object_id in list(j0.zonas["Battlefield"]):
        carta = adaptador.partida.objetos[object_id]
        if "Land" in carta.definicion.card_types:
            carta.girada = False
    otro = adaptador.partida.instanciar(cartas["Flame Lash"], j0.id, "Hand")
    _jugar(adaptador, j0, otro.object_id)
    adaptador.ejecutar_accion(
        j0, _por_id(_menu(adaptador), "elegir_objetivo", criatura_rival.object_id),
    )
    assert criatura_rival.danio == 4
    assert criatura_rival.zona == "Battlefield"
    assert j1.vida == 16


def test_rabid_gnaw_dana_con_el_poder_ya_subido() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Mountain"], 2)
    propia = adaptador.partida.instanciar(criatura("Propia", 2, 2), j0.id, "Battlefield")
    mordisco = adaptador.partida.instanciar(cartas["Rabid Gnaw"], j0.id, "Hand")
    assert all(a.get("carta_id") != mordisco.object_id for a in adaptador._enumerar(j0))
    ajena = adaptador.partida.instanciar(criatura("Ajena", 5, 5), j1.id, "Battlefield")
    assert any(a.get("carta_id") == mordisco.object_id for a in adaptador._enumerar(j0))
    _jugar(adaptador, j0, mordisco.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", propia.object_id))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", ajena.object_id))
    assert adaptador.partida.poder(propia) == 3
    assert ajena.danio == 3
    assert propia.danio == 0
    assert propia.zona == "Battlefield"


def test_rabid_bite_exige_las_dos_y_dana_al_rival() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    _tierras(adaptador, j0, cartas["Forest"], 2)
    propia = adaptador.partida.instanciar(criatura("Propia", 2, 2), j0.id, "Battlefield")
    mordisco = adaptador.partida.instanciar(cartas["Rabid Bite"], j0.id, "Hand")
    assert all(a.get("carta_id") != mordisco.object_id for a in adaptador._enumerar(j0))

    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Forest"], 2)
    ajena = adaptador.partida.instanciar(criatura("Ajena", 5, 5), j1.id, "Battlefield")
    mordisco = adaptador.partida.instanciar(cartas["Rabid Bite"], j0.id, "Hand")
    assert all(a.get("carta_id") != mordisco.object_id for a in adaptador._enumerar(j0))

    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Forest"], 2)
    propia = adaptador.partida.instanciar(criatura("Propia", 2, 2), j0.id, "Battlefield")
    ajena = adaptador.partida.instanciar(criatura("Ajena", 5, 5), j1.id, "Battlefield")
    mordisco = adaptador.partida.instanciar(cartas["Rabid Bite"], j0.id, "Hand")
    assert any(a.get("carta_id") == mordisco.object_id for a in adaptador._enumerar(j0))
    _jugar(adaptador, j0, mordisco.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", ajena.object_id))
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", propia.object_id))
    assert ajena.danio == 2
    assert propia.danio == 0


def test_scry_aplica_el_orden() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    arriba = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    medio = adaptador.partida.instanciar(cartas["Island"], j0.id, "Library")
    fondo = adaptador.partida.instanciar(cartas["Mountain"], j0.id, "Library")
    hechizo = adaptador.partida.instanciar(
        _conjuro("Ordenar", [_efecto("Scry", _info("Library"), amount=2)]),
        j0.id,
        "Hand",
    )
    _jugar(adaptador, j0, hechizo.object_id)
    adaptador.ejecutar_accion(
        j0, _por_id(_menu(adaptador), "elegir_scry", arriba.object_id, "bottom"),
    )
    adaptador.ejecutar_accion(
        j0, _por_id(_menu(adaptador), "elegir_scry", medio.object_id, "bottom"),
    )
    assert j0.zonas["Library"] == [fondo.object_id, arriba.object_id, medio.object_id]


def test_look_usa_la_carta_elegida() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    primera = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    segunda = adaptador.partida.instanciar(cartas["Island"], j0.id, "Library")
    tercera = adaptador.partida.instanciar(cartas["Mountain"], j0.id, "Library")
    hechizo = adaptador.partida.instanciar(
        _conjuro("Mirar", [
            _efecto("Look", _info("Library"), amount=3),
            _efecto("MoveZone", _info("Library", "PREVIOUS_TARGET"), amount=1, destination_zone="Hand"),
        ]),
        j0.id,
        "Hand",
    )
    _jugar(adaptador, j0, hechizo.object_id)
    acciones = _menu(adaptador)
    assert {primera.object_id, segunda.object_id, tercera.object_id} <= {
        a.get("carta_id") for a in acciones
    }
    adaptador.ejecutar_accion(j0, _por_id(acciones, "elegir_objetivo", tercera.object_id))
    assert tercera.zona == "Hand"
    assert primera.zona == "Library"
    assert segunda.zona == "Library"


def test_move_zone_elige_fuera_de_la_biblioteca() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    primera = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Graveyard")
    segunda = adaptador.partida.instanciar(cartas["Island"], j0.id, "Graveyard")
    hechizo = adaptador.partida.instanciar(
        _conjuro("Mover", [
            _efecto("MoveZone", _info("Graveyard"), amount=1, destination_zone="Exile"),
        ]),
        j0.id,
        "Hand",
    )
    _jugar(adaptador, j0, hechizo.object_id)
    acciones = _menu(adaptador)
    assert {primera.object_id, segunda.object_id} <= {a.get("carta_id") for a in acciones}
    adaptador.ejecutar_accion(j0, _por_id(acciones, "elegir_objetivo", segunda.object_id))
    assert segunda.zona == "Exile"
    assert primera.zona == "Graveyard"

    cartas, adaptador, j0, _j1 = _mesa()
    tope = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    resto = [
        adaptador.partida.instanciar(cartas["Island"], j0.id, "Library")
        for _ in range(2)
    ]
    hechizo = adaptador.partida.instanciar(
        _conjuro("Tope", [
            _efecto("MoveZone", _info("Library"), amount=1, destination_zone="Exile"),
        ]),
        j0.id,
        "Hand",
    )
    _jugar(adaptador, j0, hechizo.object_id)
    assert adaptador.decision is None
    assert tope.zona == "Exile"
    assert all(carta.zona == "Library" for carta in resto)


def test_carrot_cake_dispara_el_sacrificio_antes_de_la_vida() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    _tierras(adaptador, j0, cartas["Plains"], 2)
    tarta = adaptador.partida.instanciar(cartas["Carrot Cake"], j0.id, "Battlefield")
    primera = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Library")
    segunda = adaptador.partida.instanciar(cartas["Island"], j0.id, "Library")
    vida = j0.vida
    adaptador.ejecutar_accion(j0, {"tipo": "activar", "carta_id": tarta.object_id, "habilidad": 3})
    assert tarta.zona == "Graveyard"
    assert j0.vida == vida
    assert adaptador.decision is not None and adaptador.decision["tipo"] == "scry"
    fichas = [
        adaptador.partida.objetos[i]
        for i in j0.zonas["Battlefield"]
        if adaptador.partida.objetos[i].es_ficha
    ]
    assert len(fichas) == 1
    assert adaptador.partida.poder(fichas[0]) == 1
    adaptador.ejecutar_accion(
        j0, _por_id(_menu(adaptador), "elegir_scry", primera.object_id, "bottom"),
    )
    assert j0.vida == vida + 3
    assert j0.zonas["Library"][0] == segunda.object_id
    assert j0.zonas["Library"][-1] == primera.object_id


def test_fases_vacias_se_saltan_y_el_descarte_no() -> None:
    cartas, adaptador, _j0, j1 = _mesa()
    adaptador.partida.instanciar(cartas["Forest"], j1.id, "Library")
    acciones = adaptador.acciones_legales()
    assert adaptador.indice_activo == 1
    assert adaptador.fase == "Principal"
    assert any(a["tipo"] == "jugar_carta" for a in acciones)

    _cartas, adaptador, j0, _j1 = _mesa()
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    oso.entro_este_turno = False
    adaptador.fase = "Declarar Atacantes"
    acciones = adaptador.acciones_legales()
    assert adaptador.fase == "Declarar Atacantes"
    assert any(a["tipo"] == "declarar_atacante" and a["carta_id"] == oso.object_id for a in acciones)

    cartas, adaptador, j0, _j1 = _mesa()
    for _ in range(9):
        adaptador.partida.instanciar(cartas["Forest"], j0.id, "Hand")
    adaptador.fase = "Descartar"
    acciones = adaptador.acciones_legales()
    assert adaptador.fase == "Descartar"
    assert len(acciones) == 9
    assert all(a["tipo"] == "descartar" for a in acciones)


def test_instantaneo_en_respuesta_resuelve_antes() -> None:
    _cartas, adaptador, j0, j1 = _mesa()
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    soldado = adaptador.partida.instanciar(criatura("Soldado", 1, 1), j0.id, "Hand")
    bomba = _efecto("ModifyPower", _info_criatura(), amount=3, duration="UNTIL_END_OF_TURN")
    arriba = adaptador.partida.instanciar(_instantaneo("Arriba", [bomba]), j0.id, "Hand")
    adaptador.partida.instanciar(_instantaneo("Reserva", [dict(bomba)]), j0.id, "Hand")
    adaptador.partida.instanciar(_instantaneo("Rival", [dict(bomba)]), j1.id, "Hand")

    _jugar(adaptador, j0, soldado.object_id)
    assert soldado.zona == "Stack"
    assert adaptador.partida.poder(oso) == 2
    assert any(a.get("carta_id") == arriba.object_id for a in adaptador.acciones_legales())

    _jugar(adaptador, j0, arriba.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", oso.object_id))
    assert soldado.zona == "Stack"
    assert adaptador.partida.poder(oso) == 2
    assert adaptador.partida.pila[-1] is arriba

    _pasar(adaptador)
    assert soldado.zona == "Stack"
    assert adaptador.partida.poder(oso) == 2
    _pasar(adaptador)
    assert adaptador.partida.poder(oso) == 5
    assert soldado.zona == "Stack"

    _pasar(adaptador)
    _pasar(adaptador)
    assert soldado.zona == "Battlefield"
    assert adaptador.partida.poder(oso) == 5


def test_conjuro_tierra_y_equip_solo_en_la_principal() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Forest"], 3)
    oso = adaptador.partida.instanciar(criatura("Oso", 2, 2), j0.id, "Battlefield")
    tierra = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Hand")
    lento = adaptador.partida.instanciar(_conjuro("Lento", []), j0.id, "Hand")
    criatura_lenta = adaptador.partida.instanciar(criatura("Lenta", 1, 1), j0.id, "Hand")
    rayo = adaptador.partida.instanciar(
        criatura("Rayo", 1, 1, keywords=("Flash",)), j0.id, "Hand",
    )
    instantanea = adaptador.partida.instanciar(
        _instantaneo("Toque", [_efecto("ModifyPower", _info_criatura(), amount=1, duration="UNTIL_END_OF_TURN")]),
        j0.id,
        "Hand",
    )
    espada = adaptador.partida.instanciar(cartas["Sword of Vengeance"], j0.id, "Battlefield")

    def ids(acciones: list[dict]) -> set:
        return {a.get("carta_id") for a in acciones}

    menu = adaptador._enumerar(j0)
    ofrecidas = ids(menu)
    assert {tierra.object_id, lento.object_id, criatura_lenta.object_id, rayo.object_id, instantanea.object_id} <= ofrecidas
    assert any(a["tipo"] == "activar" and a["carta_id"] == espada.object_id for a in menu)

    adaptador.fase = "Inicio del combate"
    menu = adaptador._enumerar(j0)
    ofrecidas = ids(menu)
    assert instantanea.object_id in ofrecidas
    assert rayo.object_id in ofrecidas
    assert tierra.object_id not in ofrecidas
    assert lento.object_id not in ofrecidas
    assert criatura_lenta.object_id not in ofrecidas
    assert all(not (a["tipo"] == "activar" and a["carta_id"] == espada.object_id) for a in menu)

    adaptador.fase = "Principal"
    adaptador.indice_activo = 1
    adaptador.prioridad_idx = 0
    menu = adaptador._enumerar(j0)
    ofrecidas = ids(menu)
    assert instantanea.object_id in ofrecidas
    assert rayo.object_id in ofrecidas
    assert tierra.object_id not in ofrecidas
    assert lento.object_id not in ofrecidas
    assert criatura_lenta.object_id not in ofrecidas
    assert all(not (a["tipo"] == "activar" and a["carta_id"] == espada.object_id) for a in menu)
    assert oso.zona == "Battlefield"
    assert j1.id == adaptador.jugadores[1].id


def test_crecimiento_entre_bloqueos_y_danio() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    _tierras(adaptador, j0, cartas["Forest"], 1)
    atacante = adaptador.partida.instanciar(criatura("Atacante", 2, 2), j0.id, "Battlefield")
    bloqueador = adaptador.partida.instanciar(criatura("Muro", 2, 2), j1.id, "Battlefield")
    atacante.entro_este_turno = False
    crecimiento = adaptador.partida.instanciar(cartas["Giant Growth"], j0.id, "Hand")
    adaptador.partida.instanciar(cartas["Forest"], j0.id, "Hand")
    adaptador.fase = "Declarar Atacantes"
    adaptador.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": atacante.object_id})
    adaptador.ejecutar_accion(j0, {"tipo": "finalizar_ataques"})
    _pasar(adaptador)
    assert adaptador.fase == "Declarar Bloqueadores"
    adaptador.ejecutar_accion(j1, {
        "tipo": "declarar_bloqueo",
        "blocker_id": bloqueador.object_id,
        "attacker_id": atacante.object_id,
    })
    adaptador.ejecutar_accion(j1, {"tipo": "resolver_combate"})
    assert atacante.danio == 0
    assert bloqueador.danio == 0
    assert any(a.get("carta_id") == crecimiento.object_id for a in adaptador.acciones_legales())
    _jugar(adaptador, j0, crecimiento.object_id)
    adaptador.ejecutar_accion(j0, _por_id(_menu(adaptador), "elegir_objetivo", atacante.object_id))
    assert bloqueador.zona == "Graveyard"
    assert bloqueador.danio == 5
    assert atacante.zona == "Battlefield"
    assert atacante.danio == 2
    assert adaptador.partida.poder(atacante) == 5


def test_attacks_espera_la_confirmacion_y_se_puede_responder() -> None:
    cartas, adaptador, j0, j1 = _mesa()
    byrke = adaptador.partida.instanciar(cartas["Byrke, Long Ear of the Law"], j0.id, "Battlefield")
    byrke.entro_este_turno = False
    marcada = adaptador.partida.instanciar(criatura("Marcada", 2, 2), j0.id, "Battlefield")
    marcada.entro_este_turno = False
    marcada.contadores["PlusOnePlusOne"] = 1
    bomba = _efecto("ModifyPower", _info_criatura(), amount=1, duration="UNTIL_END_OF_TURN")
    propia = adaptador.partida.instanciar(_instantaneo("Propia", [bomba]), j0.id, "Hand")
    adaptador.partida.instanciar(_instantaneo("Ajena", [dict(bomba)]), j1.id, "Hand")
    adaptador.fase = "Declarar Atacantes"
    adaptador.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": marcada.object_id})
    assert marcada.contadores["PlusOnePlusOne"] == 1
    assert adaptador.partida.pila == []
    adaptador.ejecutar_accion(j0, {"tipo": "finalizar_ataques"})
    assert marcada.contadores["PlusOnePlusOne"] == 1
    assert adaptador.partida.pila
    assert any(a.get("carta_id") == propia.object_id for a in adaptador.acciones_legales())
    _pasar(adaptador)
    assert marcada.contadores["PlusOnePlusOne"] == 1
    _pasar(adaptador)
    assert marcada.contadores["PlusOnePlusOne"] == 2


def test_first_strike_abre_prioridad_antes_del_danio_normal() -> None:
    _cartas, adaptador, j0, j1 = _mesa()
    atacante = adaptador.partida.instanciar(
        criatura("Veloz", 1, 1, keywords=("FirstStrike",)), j0.id, "Battlefield",
    )
    bloqueador = adaptador.partida.instanciar(criatura("Muro", 2, 2), j1.id, "Battlefield")
    atacante.entro_este_turno = False
    bomba = _efecto("ModifyPower", _info_criatura(), amount=1, duration="UNTIL_END_OF_TURN")
    instantanea = adaptador.partida.instanciar(_instantaneo("Parar", [bomba]), j0.id, "Hand")
    adaptador.fase = "Declarar Atacantes"
    adaptador.ejecutar_accion(j0, {"tipo": "declarar_atacante", "carta_id": atacante.object_id})
    adaptador.ejecutar_accion(j0, {"tipo": "finalizar_ataques"})
    _pasar(adaptador)
    adaptador.ejecutar_accion(j1, {
        "tipo": "declarar_bloqueo",
        "blocker_id": bloqueador.object_id,
        "attacker_id": atacante.object_id,
    })
    adaptador.ejecutar_accion(j1, {"tipo": "resolver_combate"})
    assert atacante.danio == 0
    assert bloqueador.danio == 0
    _pasar(adaptador)
    assert bloqueador.zona == "Battlefield"
    assert bloqueador.danio == 1
    assert atacante.zona == "Battlefield"
    assert atacante.danio == 0
    assert any(a.get("carta_id") == instantanea.object_id for a in adaptador.acciones_legales())
    _pasar(adaptador)
    assert atacante.zona == "Graveyard"
    assert bloqueador.zona == "Battlefield"
    assert bloqueador.danio == 1


def test_ventana_vacia_avanza_y_la_limpieza_pide_descarte() -> None:
    cartas, adaptador, j0, _j1 = _mesa()
    tierra = adaptador.partida.instanciar(cartas["Forest"], j0.id, "Hand")
    adaptador.fase = "Inicio del combate"
    acciones = adaptador.acciones_legales()
    assert adaptador.fase == "Principal"
    assert adaptador.segunda_principal
    assert any(a.get("carta_id") == tierra.object_id for a in acciones)

    cartas, adaptador, j0, _j1 = _mesa()
    for _ in range(8):
        adaptador.partida.instanciar(cartas["Island"], j0.id, "Hand")
    adaptador.fase = "Paso final"
    acciones = adaptador.acciones_legales()
    assert adaptador.fase == "Descartar"
    assert len(acciones) == 8
    assert all(a["tipo"] == "descartar" for a in acciones)


def test_bloque_global_de_pasos() -> None:
    assert "BeginningOfCombat" in PHASES
    assert "EndOfCombat" in PHASES
    assert GLOBAL_DIM == 45
    assert OBS_DIM == 198679
    vacio = Adaptador()
    vacio.montar([], [])
    vacio.modo_decision = "mulligan"
    mulligan = encode_global(vacio, 0)
    vacio.modo_decision = "fondo"
    fondo = encode_global(vacio, 0)
    vacio.modo_decision = "menu"
    menu = encode_global(vacio, 0)
    assert mulligan != fondo and mulligan != menu and fondo != menu
    quedarme = encode_accion({"tipo": "quedarme"}, {})
    devolver = encode_accion({"tipo": "mulligan"}, {})
    pasar = encode_accion({"tipo": "pasar_fase"}, {})
    desconocida = encode_accion({"tipo": "tipo_desconocido"}, {})
    assert any(v == 1.0 for v in quedarme)
    assert quedarme != devolver and quedarme != pasar and quedarme != desconocida
    assert not any(v == 1.0 for v in desconocida[:-2])


def _accion(adaptador: Adaptador, tipo: str) -> dict:
    return _por_tipo(adaptador.acciones_legales(), tipo)


def _partida_london():
    adaptador = Adaptador(modo_menu="completo")
    adaptador.montar(mazo_prueba(6, 6), mazo_prueba(6, 6))
    return adaptador, adaptador.jugadores[0], adaptador.jugadores[1]


def test_london_los_dos_se_quedan() -> None:
    adaptador, j0, j1 = _partida_london()
    assert adaptador.fase == "Mulligan"
    assert adaptador.quien_actua() == 0
    assert len(j0.zonas["Hand"]) == 7 and len(j1.zonas["Hand"]) == 7
    adaptador.ejecutar_accion(j0, _accion(adaptador, "quedarme"))
    assert adaptador.quien_actua() == 1
    adaptador.ejecutar_accion(j1, _accion(adaptador, "quedarme"))
    assert adaptador.etapa_mulligan == "robo"
    assert adaptador.fase == "Mulligan"
    assert len(j0.zonas["Hand"]) == 7 and len(j1.zonas["Hand"]) == 7
    acciones = adaptador.acciones_legales()
    assert adaptador.fase == "Principal"
    assert adaptador.etapa_mulligan is None
    assert len(j0.zonas["Hand"]) == 8 and len(j1.zonas["Hand"]) == 7
    assert adaptador.quien_actua() == 0
    assert all(a["tipo"] not in ("quedarme", "mulligan", "poner_fondo") for a in acciones)


def test_london_una_devolucion_baraja_y_fondo() -> None:
    adaptador = Adaptador(modo_menu="completo")
    mazo = [criatura(f"C{i}", 1, 1) for i in range(12)]
    adaptador.montar(list(mazo), list(mazo))
    j0, j1 = adaptador.jugadores
    todos = list(j0.zonas["Hand"]) + list(j0.zonas["Library"])
    j0.zonas["Hand"] = todos[:7]
    j0.zonas["Library"] = todos[7:]
    for object_id in j0.zonas["Hand"]:
        adaptador.partida.objetos[object_id].zona = "Hand"
    for object_id in j0.zonas["Library"]:
        adaptador.partida.objetos[object_id].zona = "Library"

    def reversa(xs: list) -> None:
        xs.reverse()

    original = random.shuffle
    random.shuffle = reversa
    try:
        adaptador.ejecutar_accion(j0, _accion(adaptador, "mulligan"))
    finally:
        random.shuffle = original
    mezclado = (todos[7:] + todos[:7])[::-1]
    assert j0.zonas["Hand"] == mezclado[:7]
    assert j0.zonas["Library"] == mezclado[7:]
    assert len(j0.zonas["Hand"]) == 7
    assert adaptador.quien_actua() == 1

    adaptador.ejecutar_accion(j1, _accion(adaptador, "quedarme"))
    assert adaptador.quien_actua() == 0
    adaptador.ejecutar_accion(j0, _accion(adaptador, "quedarme"))
    acciones = adaptador.acciones_legales()
    assert adaptador.modo_decision == "fondo"
    assert all(a["tipo"] == "poner_fondo" and a.get("carta_id") for a in acciones)
    elegida = j0.zonas["Hand"][3]
    mano = list(j0.zonas["Hand"])
    biblioteca = list(j0.zonas["Library"])
    adaptador.ejecutar_accion(j0, {"tipo": "poner_fondo", "carta_id": elegida})
    assert j0.zonas["Hand"] == [oid for oid in mano if oid != elegida]
    assert len(j0.zonas["Hand"]) == 6
    assert j0.zonas["Library"] == biblioteca + [elegida]
    assert j0.zonas["Library"][-1] == elegida
    assert adaptador.etapa_mulligan == "robo"
    adaptador.acciones_legales()
    assert adaptador.fase == "Principal"
    assert len(j0.zonas["Hand"]) == 7
    assert j1.zonas["Hand"] and len(j1.zonas["Hand"]) == 7
    assert elegida == adaptador.jugadores[0].zonas["Library"][-1]


def test_london_orden_del_fondo() -> None:
    adaptador, j0, j1 = _partida_london()
    adaptador.ejecutar_accion(j0, _accion(adaptador, "mulligan"))
    adaptador.ejecutar_accion(j1, _accion(adaptador, "quedarme"))
    adaptador.ejecutar_accion(j0, _accion(adaptador, "mulligan"))
    adaptador.ejecutar_accion(j0, _accion(adaptador, "quedarme"))
    assert adaptador.fondo_restante[0] == 2
    primera = j0.zonas["Hand"][0]
    segunda = j0.zonas["Hand"][1]
    adaptador.ejecutar_accion(j0, {"tipo": "poner_fondo", "carta_id": primera})
    assert adaptador.quien_actua() == 0
    assert len(j0.zonas["Hand"]) == 6
    adaptador.ejecutar_accion(j0, {"tipo": "poner_fondo", "carta_id": segunda})
    assert j0.zonas["Library"][-2] == primera
    assert j0.zonas["Library"][-1] == segunda
    assert len(j0.zonas["Hand"]) == 5
    assert adaptador.etapa_mulligan == "robo"


def test_london_quien_se_queda_no_repite() -> None:
    adaptador, j0, j1 = _partida_london()
    adaptador.ejecutar_accion(j0, _accion(adaptador, "quedarme"))
    mano = list(j0.zonas["Hand"])
    biblioteca = list(j0.zonas["Library"])
    adaptador.ejecutar_accion(j1, _accion(adaptador, "mulligan"))
    assert adaptador.quien_actua() == 1
    assert j0.zonas["Hand"] == mano and j0.zonas["Library"] == biblioteca
    acciones = adaptador.acciones_legales()
    assert adaptador.quien_actua() == 1
    assert {a["tipo"] for a in acciones} <= {"quedarme", "mulligan"}
    adaptador.ejecutar_accion(j1, _accion(adaptador, "quedarme"))
    assert adaptador.quien_actua() == 1
    assert adaptador.modo_decision == "fondo"
    assert j0.zonas["Hand"] == mano
    assert all(a["tipo"] == "poner_fondo" for a in adaptador.acciones_legales())


def test_london_septima_no_ofrece_otra() -> None:
    adaptador, j0, j1 = _partida_london()
    adaptador.ejecutar_accion(j0, _accion(adaptador, "mulligan"))
    adaptador.ejecutar_accion(j1, _accion(adaptador, "quedarme"))
    for _ in range(6):
        acciones = adaptador.acciones_legales()
        assert adaptador.quien_actua() == 0
        assert any(a["tipo"] == "mulligan" for a in acciones)
        adaptador.ejecutar_accion(j0, _accion(adaptador, "mulligan"))
    acciones = adaptador.acciones_legales()
    assert adaptador.quien_actua() == 0
    assert [a["tipo"] for a in acciones] == ["quedarme"]
    assert adaptador.veces_devueltas[0] == 7


def test_london_puente_no_entra() -> None:
    adaptador = Adaptador(modo_menu="puente")
    adaptador.montar(mazo_prueba(6, 6), mazo_prueba(6, 6))
    j0, j1 = adaptador.jugadores
    assert adaptador.etapa_mulligan is None
    assert adaptador.fase == "Principal"
    assert len(j0.zonas["Hand"]) == 8 and len(j1.zonas["Hand"]) == 7
    tipos = {a["tipo"] for a in adaptador.acciones_legales()}
    assert "quedarme" not in tipos and "mulligan" not in tipos and "poner_fondo" not in tipos


def test_london_starter_sale_a_principal() -> None:
    from reglas.adaptador import mazo_starter

    for semilla in range(4):
        random.seed(semilla)
        adaptador = Adaptador(modo_menu="completo")
        adaptador.montar(mazo_starter("hare_raising"), mazo_starter("otter_limits"))
        for _ in range(80):
            if adaptador.etapa_mulligan in (None, "robo"):
                break
            adaptador.paso_aleatorio()
        else:
            raise AssertionError(f"semilla {semilla} no salió del mulligan")
        adaptador.acciones_legales()
        assert adaptador.etapa_mulligan is None, semilla
        assert adaptador.fase == "Principal", (semilla, adaptador.fase)


def main() -> None:
    pruebas = [
        test_add_mana_forest,
        test_equivalencia_con_motor,
        test_tope_de_cincuenta_acciones,
        test_serra_resuelve_por_la_pila_del_menu,
        test_mana_de_color,
        test_giant_growth_caduca_al_pasar_turno,
        test_enter_tapped_y_add_keyword,
        test_discard_y_scry,
        test_pathmaker_exilia_una,
        test_move_zone_max_estrella_mueve_todas_las_reveladas,
        test_descarte_al_cerrar_turno,
        test_byrke_hasta_dos_y_dobla_al_atacar,
        test_fecund_previous_target_y_opcional,
        test_finneas_contadores_y_robo,
        test_pileated_no_ofrece_voladoras,
        test_treeguard_elige_en_el_menu,
        test_colossificacion_anexa_y_queda_huerfana,
        test_repel_solo_ofrece_criaturas_grandes,
        test_bria_prowess_y_no_bloqueable,
        test_mockingbird_filtra_por_mana_gastado,
        test_alania_primer_hechizo_copia_y_reinicio,
        test_stormcatch_prisa_y_prowess,
        test_coruscation_offspring_copia_el_dano,
        test_thieving_otter_roba_solo_su_dano,
        test_waterspout_vuela_si_otra_entro,
        test_quaketusk_ataca_el_turno_que_entra,
        test_sword_prisa_first_strike_y_equipo_suelto,
        test_charmed_sleep_aura_huerfana,
        test_flame_lash_puede_dar_a_un_jugador,
        test_rabid_gnaw_dana_con_el_poder_ya_subido,
        test_rabid_bite_exige_las_dos_y_dana_al_rival,
        test_scry_aplica_el_orden,
        test_look_usa_la_carta_elegida,
        test_move_zone_elige_fuera_de_la_biblioteca,
        test_carrot_cake_dispara_el_sacrificio_antes_de_la_vida,
        test_fases_vacias_se_saltan_y_el_descarte_no,
        test_instantaneo_en_respuesta_resuelve_antes,
        test_conjuro_tierra_y_equip_solo_en_la_principal,
        test_crecimiento_entre_bloqueos_y_danio,
        test_attacks_espera_la_confirmacion_y_se_puede_responder,
        test_first_strike_abre_prioridad_antes_del_danio_normal,
        test_ventana_vacia_avanza_y_la_limpieza_pide_descarte,
        test_bloque_global_de_pasos,
        test_london_los_dos_se_quedan,
        test_london_una_devolucion_baraja_y_fondo,
        test_london_orden_del_fondo,
        test_london_quien_se_queda_no_repite,
        test_london_septima_no_ofrece_otra,
        test_london_puente_no_entra,
        test_london_starter_sale_a_principal,
        test_partida_starter_aleatoria,
    ]
    for prueba in pruebas:
        prueba()
        print("ok", prueba.__name__)


if __name__ == "__main__":
    main()
