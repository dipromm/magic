"""Cada effect_type del JSON-LD es una función. La carta no trae código propio."""
from __future__ import annotations

import random
from typing import Any, Callable

from reglas.evaluar import Contexto, cantidad, cumple_restricciones
from reglas.modelo import Definicion, EfectoContinuo


class PrimitivaNoImplementada(Exception):
    pass


def _objetivos(ctx: Contexto, efecto: dict[str, Any]):
    info = efecto["target_info"]
    dueno = info.get("target_owner")
    if dueno == "EVENT_OBJECT":
        return [ctx.evento] if ctx.evento is not None else []
    if dueno == "SOURCE":
        return [ctx.fuente]
    if dueno == "PREVIOUS_TARGET":
        anterior = ctx.resultado_anterior
        if anterior is None:
            return []
        zona = info.get("target_zone")
        if zona and anterior.zona != zona:
            return []
        if not cumple_restricciones(info.get("restrictions"), anterior, ctx.fuente):
            return []
        return [anterior]
    if dueno == "ATTACHED" and ctx.fuente.anexada_a is not None:
        vivo = ctx.partida.objeto_vivo(ctx.fuente.anexada_a)
        return [vivo] if vivo is not None else []
    if info.get("requires_target"):
        clave = getattr(ctx, "clave_objetivo", None) or (
            "TARGET_2" if dueno == "TARGET_2" else "TARGET_1"
        )
        varios = (getattr(ctx, "listas", None) or {}).get(clave)
        if varios:
            vivos = []
            for ref in varios:
                vivo = ctx.partida.objeto_vivo(ref)
                if vivo is not None:
                    vivos.append(vivo)
            return vivos
        ref = ctx.objetivos.get(clave)
        vivo = ctx.partida.objeto_vivo(ref) if ref else None
        return [vivo] if vivo is not None else []
    zona = info.get("target_zone") or "Battlefield"
    if zona == "Library" and dueno in ("CONTROLLER", "OPPONENT", None):
        jugador = _jugador(ctx, efecto) if dueno != "OPPONENT" else ctx.partida.oponente(ctx.controlador)
        cartas = [ctx.partida.objetos[i] for i in jugador.zonas["Library"]]
        return [
            c for c in cartas
            if cumple_restricciones(info.get("restrictions"), c, ctx.fuente)
        ]
    objetos = ctx.partida.en_zona(zona)
    return [
        objeto for objeto in objetos
        if (dueno not in ("CONTROLLER", "OPPONENT", "NOT_CONTROLLER") or _dueno_ok(dueno, objeto, ctx))
        and cumple_restricciones(info.get("restrictions"), objeto, ctx.fuente)
    ]


def _dueno_ok(dueno: str, objeto, ctx: Contexto) -> bool:
    if dueno == "CONTROLLER":
        return objeto.controlador == ctx.controlador
    if dueno in ("OPPONENT", "NOT_CONTROLLER"):
        return objeto.controlador != ctx.controlador
    return True


def _jugador(ctx: Contexto, efecto: dict[str, Any]):
    dueno = efecto["target_info"].get("target_owner")
    if dueno == "OPPONENT":
        return ctx.partida.oponente(ctx.controlador)
    return ctx.partida.jugadores[ctx.controlador]


def _es_de_jugador(efecto: dict[str, Any]) -> bool:
    tipos = (efecto["target_info"].get("restrictions") or {}).get("types") or []
    return tipos == ["Player"]


def _continuo(ctx: Contexto, efecto: dict[str, Any], tipo: str) -> None:
    valor = cantidad(efecto["parameters"].get("amount"), ctx)
    duracion = efecto["parameters"].get("duration") or "UNTIL_END_OF_TURN"
    for objetivo in _objetivos(ctx, efecto):
        ctx.partida.continuos.append(EfectoContinuo(
            effect_id=ctx.partida.nuevo_id("fx"),
            fuente=ctx.fuente.ref(),
            objetivo=objetivo.ref(),
            tipo=tipo,
            amount=valor,
            string_value=efecto["parameters"].get("string_value"),
            duracion=duracion,
        ))
        ctx.resultado_anterior = objetivo


def modificar_poder(ctx: Contexto, efecto: dict[str, Any]) -> None:
    _continuo(ctx, efecto, "ModifyPower")


def modificar_resistencia(ctx: Contexto, efecto: dict[str, Any]) -> None:
    _continuo(ctx, efecto, "ModifyToughness")


def anadir_contador(ctx: Contexto, efecto: dict[str, Any]) -> None:
    n = cantidad(efecto["parameters"].get("amount"), ctx)
    tipo = efecto["parameters"].get("string_value") or "PlusOnePlusOne"
    for objetivo in _objetivos(ctx, efecto):
        objetivo.contadores[tipo] = objetivo.contadores.get(tipo, 0) + n
        ctx.resultado_anterior = objetivo


def robar(ctx: Contexto, efecto: dict[str, Any]) -> None:
    n = cantidad(efecto["parameters"].get("amount"), ctx)
    ctx.partida.robar(_jugador(ctx, efecto), n)


def ganar_vida(ctx: Contexto, efecto: dict[str, Any]) -> None:
    _jugador(ctx, efecto).vida += cantidad(efecto["parameters"].get("amount"), ctx)


def danio(ctx: Contexto, efecto: dict[str, Any]) -> None:
    n = cantidad(efecto["parameters"].get("amount"), ctx)
    if _es_de_jugador(efecto):
        _jugador(ctx, efecto).vida -= n
        return
    for objetivo in _objetivos(ctx, efecto):
        if "Player" in objetivo.definicion.card_types:
            ctx.partida.jugadores[objetivo.controlador].vida -= n
        else:
            objetivo.danio += n
        ctx.resultado_anterior = objetivo


def destruir(ctx: Contexto, efecto: dict[str, Any]) -> None:
    for objetivo in list(_objetivos(ctx, efecto)):
        ctx.partida.mover(objetivo, "Graveyard")


def girar(ctx: Contexto, efecto: dict[str, Any]) -> None:
    for objetivo in _objetivos(ctx, efecto):
        objetivo.girada = True
        ctx.resultado_anterior = objetivo


def anadir_mana(ctx: Contexto, efecto: dict[str, Any]) -> None:
    n = cantidad(efecto["parameters"].get("amount"), ctx)
    color = efecto["parameters"].get("string_value") or "COLORLESS"
    jugador = _jugador(ctx, efecto)
    jugador.mana[color] = jugador.mana.get(color, 0) + n


def entrar_girado(ctx: Contexto, efecto: dict[str, Any]) -> None:
    for objetivo in _objetivos(ctx, efecto):
        objetivo.girada = True
        ctx.resultado_anterior = objetivo


def impedir_enderezar(ctx: Contexto, efecto: dict[str, Any]) -> None:
    """Estática: se consulta al enderezar; no escribe estado."""
    return


def anadir_keyword(ctx: Contexto, efecto: dict[str, Any]) -> None:
    _continuo(ctx, efecto, "AddKeyword")


def no_puede_ser_bloqueada(ctx: Contexto, efecto: dict[str, Any]) -> None:
    _continuo(ctx, efecto, "CantBeBlocked")


def descartar(ctx: Contexto, efecto: dict[str, Any]) -> None:
    n = cantidad(efecto["parameters"].get("amount"), ctx) or 1
    jugador = _jugador(ctx, efecto)
    mano = [ctx.partida.objetos[i] for i in list(jugador.zonas["Hand"])]
    for _ in range(min(n, len(mano))):
        elegida = ctx.partida.elegir("discard", mano, ctx)
        if elegida is None:
            break
        ctx.partida.mover(elegida, "Graveyard")
        mano = [c for c in mano if c.object_id != elegida.object_id]
        ctx.resultado_anterior = elegida


def scry(ctx: Contexto, efecto: dict[str, Any]) -> None:
    n = cantidad(efecto["parameters"].get("amount"), ctx) or 1
    jugador = _jugador(ctx, efecto)
    biblioteca = jugador.zonas["Library"]
    miradas = [ctx.partida.objetos[i] for i in biblioteca[:n]]
    if not miradas:
        return
    orden = ctx.partida.elegir("scry", miradas, ctx)
    if isinstance(orden, dict):
        arriba = list(orden.get("top") or [])
        fondo = list(orden.get("bottom") or [])
    else:
        arriba = list(miradas)
        fondo = []
    vistos = {c.object_id for c in miradas}
    resto = [i for i in biblioteca if i not in vistos]
    biblioteca[:] = (
        [c.object_id for c in arriba]
        + resto
        + [c.object_id for c in fondo]
    )


def mirar(ctx: Contexto, efecto: dict[str, Any]) -> None:
    n = cantidad(efecto["parameters"].get("amount"), ctx) or 1
    jugador = _jugador(ctx, efecto)
    cartas = [ctx.partida.objetos[i] for i in jugador.zonas["Library"][:n]]
    if not cartas:
        return
    elegida = ctx.partida.elegir("look", cartas, ctx)
    if elegida is None:
        elegida = cartas[0]
    ctx.resultado_anterior = elegida


def revelar_hasta(ctx: Contexto, efecto: dict[str, Any]) -> None:
    jugador = _jugador(ctx, efecto)
    restricciones = efecto["target_info"].get("restrictions")
    encontrada = None
    for object_id in list(jugador.zonas["Library"]):
        carta = ctx.partida.objetos[object_id]
        carta.revelada = True
        if cumple_restricciones(restricciones, carta, ctx.fuente):
            encontrada = carta
            break
    ctx.resultado_anterior = encontrada


def _tope_move_zone(efecto: dict[str, Any]) -> int | None:
    """Cuántos objetos mueve MoveZone. None = todos (max '*')."""
    info = efecto.get("target_info") or {}
    max_t = (info.get("target_count") or {}).get("max")
    amount = (efecto.get("parameters") or {}).get("amount")
    if max_t == "*":
        return None
    tope: int | None = None
    if isinstance(max_t, int):
        tope = max_t
    if isinstance(amount, int):
        tope = amount if tope is None else min(tope, amount)
    return tope


def mover_zona(ctx: Contexto, efecto: dict[str, Any]) -> None:
    destino = efecto["parameters"].get("destination_zone")
    if not destino:
        return
    placement = efecto["parameters"].get("placement")
    objetivos = list(_objetivos(ctx, efecto))
    tope = _tope_move_zone(efecto)
    zona = (efecto.get("target_info") or {}).get("target_zone")
    if zona != "Library" and tope is not None and len(objetivos) > tope:
        elegidos = []
        pool = list(objetivos)
        for _ in range(tope):
            if not pool:
                break
            elegida = ctx.partida.elegir("move_zone", pool, ctx)
            if elegida is None:
                break
            elegidos.append(elegida)
            pool = [c for c in pool if c.object_id != elegida.object_id]
        objetivos = elegidos
    elif tope is not None:
        objetivos = objetivos[:tope]
    if placement == "BOTTOM_RANDOM":
        random.shuffle(objetivos)
    for objetivo in objetivos:
        if destino == "Battlefield":
            ctx.partida.entrar(objetivo)
        else:
            ctx.partida.mover(objetivo, destino)
            if placement in ("BOTTOM", "BOTTOM_RANDOM") and destino == "Library":
                zona = ctx.partida.jugadores[objetivo.controlador].zonas["Library"]
                if objetivo.object_id in zona:
                    zona.remove(objetivo.object_id)
                    zona.append(objetivo.object_id)
        objetivo.revelada = False
        ctx.resultado_anterior = objetivo


def anexar(ctx: Contexto, efecto: dict[str, Any]) -> None:
    objetivos = _objetivos(ctx, efecto)
    if not objetivos:
        return
    destino = objetivos[0]
    ctx.fuente.anexada_a = destino.ref()
    ctx.resultado_anterior = destino


def conceder_juego(ctx: Contexto, efecto: dict[str, Any]) -> None:
    for objetivo in _objetivos(ctx, efecto):
        duracion = efecto["parameters"].get("duration") or "UNTIL_END_OF_TURN"
        ctx.partida.permisos_juego.append({
            "carta": objetivo.ref(),
            "controlador": ctx.controlador,
            "duracion": duracion,
            "turnos_del_controlador": 2 if duracion == "UNTIL_END_OF_YOUR_NEXT_TURN" else 1,
        })
        ctx.resultado_anterior = objetivo


def copiar_hechizo(ctx: Contexto, efecto: dict[str, Any]) -> None:
    objetivos = _objetivos(ctx, efecto)
    if not objetivos and ctx.evento is not None:
        objetivos = [ctx.evento]
    for original in objetivos:
        copia = ctx.partida.instanciar(
            original.definicion,
            ctx.controlador,
            "Stack",
            es_ficha=True,
        )
        copia.x = original.x
        copia.mana_gastado = original.mana_gastado
        objetivos_orig = getattr(original, "_objetivos_de_lanzamiento", {})
        copia._objetivos_de_lanzamiento = dict(objetivos_orig)  # type: ignore[attr-defined]
        copia._listas = dict(getattr(original, "_listas", {}) or {})  # type: ignore[attr-defined]
        copia._reetiquetar = True  # type: ignore[attr-defined]
        ctx.partida.pila.append(copia)
        ctx.resultado_anterior = copia


def entrar_como_copia(ctx: Contexto, efecto: dict[str, Any]) -> None:
    objetivos = _objetivos(ctx, efecto)
    if not objetivos:
        return
    modelo = objetivos[0]
    extra = efecto["parameters"].get("token_definition") or {}
    subtypes = tuple(dict.fromkeys(list(modelo.definicion.subtypes) + list(extra.get("subtypes") or [])))
    keywords = tuple(dict.fromkeys(list(modelo.definicion.keywords) + list(extra.get("keywords") or [])))
    ctx.fuente.definicion = Definicion(
        nombre=modelo.definicion.nombre,
        oracle_text=modelo.definicion.oracle_text,
        mana_cost=modelo.definicion.mana_cost,
        supertypes=modelo.definicion.supertypes,
        card_types=modelo.definicion.card_types,
        subtypes=subtypes,
        keywords=keywords,
        power=modelo.definicion.power,
        toughness=modelo.definicion.toughness,
        abilities=modelo.definicion.abilities,
    )
    ctx.resultado_anterior = ctx.fuente


def crear_ficha_copia(ctx: Contexto, efecto: dict[str, Any]) -> None:
    n = cantidad(efecto["parameters"].get("amount"), ctx) or 1
    ficha = efecto["parameters"].get("token_definition") or {}
    fuente = ctx.fuente
    habilidades = ficha.get("abilities") or fuente.definicion.abilities
    definicion = Definicion(
        nombre=fuente.definicion.nombre,
        oracle_text=fuente.definicion.oracle_text,
        mana_cost=None,
        supertypes=fuente.definicion.supertypes,
        card_types=tuple(ficha.get("card_types") or fuente.definicion.card_types),
        subtypes=tuple(ficha.get("subtypes") or fuente.definicion.subtypes),
        keywords=tuple(ficha.get("keywords") or fuente.definicion.keywords),
        power=ficha.get("power", 1),
        toughness=ficha.get("toughness", 1),
        abilities=tuple(habilidades),
    )
    jugador = ctx.partida.jugadores[ctx.controlador]
    for _ in range(n):
        creada = ctx.partida.instanciar(definicion, jugador.id, "Battlefield", es_ficha=True)
        creada.entro_este_turno = True
        ctx.resultado_anterior = creada
        ctx.partida.disparar("EnterTheBattlefield", creada)


def crear_ficha(ctx: Contexto, efecto: dict[str, Any]) -> None:
    ficha = efecto["parameters"]["token_definition"]
    n = cantidad(efecto["parameters"].get("amount"), ctx)
    definicion = Definicion(
        nombre="Token",
        oracle_text="",
        mana_cost=None,
        supertypes=("NONE",),
        card_types=tuple(ficha.get("card_types") or ["Creature"]),
        subtypes=tuple(ficha.get("subtypes") or []),
        keywords=tuple(ficha.get("keywords") or []),
        power=ficha.get("power"),
        toughness=ficha.get("toughness"),
        abilities=tuple(ficha.get("abilities") or []),
    )
    jugador = _jugador(ctx, efecto)
    for _ in range(n):
        creada = ctx.partida.instanciar(definicion, jugador.id, "Battlefield", es_ficha=True)
        creada.entro_este_turno = True
        ctx.resultado_anterior = creada
        ctx.partida.disparar("EnterTheBattlefield", creada)


PRIMITIVAS: dict[str, Callable[[Contexto, dict[str, Any]], None]] = {
    "ModifyPower": modificar_poder,
    "ModifyToughness": modificar_resistencia,
    "AddCounter": anadir_contador,
    "DrawCard": robar,
    "GainLife": ganar_vida,
    "DealDamage": danio,
    "Destroy": destruir,
    "Tap": girar,
    "AddMana": anadir_mana,
    "CreateToken": crear_ficha,
    "EnterTapped": entrar_girado,
    "PreventUntap": impedir_enderezar,
    "AddKeyword": anadir_keyword,
    "CantBeBlocked": no_puede_ser_bloqueada,
    "Discard": descartar,
    "Scry": scry,
    "Look": mirar,
    "RevealUntil": revelar_hasta,
    "MoveZone": mover_zona,
    "Attach": anexar,
    "GrantPlay": conceder_juego,
    "CopySpell": copiar_hechizo,
    "EnterAsCopy": entrar_como_copia,
    "CreateCopyToken": crear_ficha_copia,
}


def ejecutar(ctx: Contexto, efecto: dict[str, Any]) -> None:
    tipo = efecto["effect_type"]
    funcion = PRIMITIVAS.get(tipo)
    if funcion is None:
        raise PrimitivaNoImplementada(tipo)
    funcion(ctx, efecto)
