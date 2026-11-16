"""Intérprete. Las estáticas se recalculan al leer; los disparos crean objetos de pila."""
from __future__ import annotations

from typing import Any, Callable

from reglas.evaluar import (
    Contexto,
    afecta,
    cantidad,
    condiciones_ok,
    cumple_restricciones,
    parse_mana,
)
from reglas.modelo import (
    Coste,
    Definicion,
    EfectoContinuo,
    Jugador,
    Objeto,
    ObjetoPila,
    Ref,
)
from reglas.primitivas import _objetivos, ejecutar

_ESTATICAS_VIVAS = {"WHILE_STATIC_ACTIVE", "PERMANENT", None}
_PERMANENTES = {
    "Creature", "Land", "Artifact", "Enchantment", "Planeswalker", "Battle",
}


class DecisionRequerida(Exception):
    """La resolución espera una elección del jugador. El objeto sigue en la pila."""

    def __init__(self, tipo: str, opciones: list[Any]) -> None:
        super().__init__(tipo)
        self.tipo = tipo
        self.opciones = opciones


_INTERCEPTAR = {"optional_effect", "scry", "discard", "look", "enter_as_copy", "move_zone"}


def _definicion_jugador(nombre: str) -> Definicion:
    return Definicion(
        nombre=nombre,
        oracle_text="",
        mana_cost=None,
        supertypes=(),
        card_types=("Player",),
        subtypes=(),
        keywords=(),
        power=None,
        toughness=None,
        abilities=(),
    )


def _eleccion_automatica(tipo: str, opciones: list[Any], ctx: Contexto) -> Any:
    if not opciones:
        return None
    if tipo == "scry":
        return {"top": list(opciones), "bottom": []}
    if tipo == "optional_effect":
        return False
    return opciones[0]


class Partida:
    def __init__(self) -> None:
        self._sec = 0
        self.jugadores: dict[str, Jugador] = {}
        self.objetos: dict[str, Objeto] = {}
        self.pila: list[Objeto | ObjetoPila] = []
        self.continuos: list[EfectoContinuo] = []
        self.calculando: set[tuple[str, int, str]] = set()
        self.lanzamientos: list[dict[str, Any]] = []
        self.permisos_juego: list[dict[str, Any]] = []
        self.elegir_fn: Callable[[str, list[Any], Contexto], Any] = _eleccion_automatica
        self.interceptar = False
        self.respuesta: Any = None
        self.respuesta_tipo: str | None = None
        self.respuesta_lista = False
        self.eleccion_abierta: tuple[Any, ...] | None = None
        self.representantes: dict[str, Objeto] = {}

    def nuevo_id(self, prefijo: str) -> str:
        self._sec += 1
        return f"{prefijo}-{self._sec}"

    def nuevo_jugador(self, nombre: str) -> Jugador:
        jugador = Jugador(self.nuevo_id("j"), nombre)
        self.jugadores[jugador.id] = jugador
        self.representar_jugador(jugador)
        return jugador

    def representar_jugador(self, jugador: Jugador) -> Objeto:
        """Un Objeto de tipo Player, fuera del campo, para objetivos y sujetos."""
        objeto = Objeto(
            object_id=self.nuevo_id("pl"),
            generacion=0,
            pieza_id=None,
            definicion=_definicion_jugador(jugador.nombre),
            zona="CommandZone",
            dueno=jugador.id,
            controlador=jugador.id,
        )
        self.objetos[objeto.object_id] = objeto
        jugador.zonas["CommandZone"].append(objeto.object_id)
        self.representantes[jugador.id] = objeto
        return objeto

    def oponente(self, jugador_id: str) -> Jugador:
        for jugador in self.jugadores.values():
            if jugador.id != jugador_id:
                return jugador
        raise KeyError(jugador_id)

    def instanciar(
        self,
        definicion: Definicion,
        dueno: str,
        zona: str = "Library",
        *,
        es_ficha: bool = False,
    ) -> Objeto:
        objeto = Objeto(
            object_id=self.nuevo_id("obj"),
            generacion=0,
            pieza_id=None if es_ficha else self.nuevo_id("pieza"),
            definicion=definicion,
            zona=zona,
            dueno=dueno,
            controlador=dueno,
            es_ficha=es_ficha,
        )
        self.objetos[objeto.object_id] = objeto
        self.jugadores[dueno].zonas[zona].append(objeto.object_id)
        return objeto

    def objeto_vivo(self, ref: Ref | None) -> Objeto | None:
        if ref is None:
            return None
        objeto = self.objetos.get(ref.object_id)
        if objeto is None or objeto.generacion != ref.generacion:
            return None
        return objeto

    def en_zona(self, zona: str, controlador: str | None = None) -> list[Objeto]:
        resultado = []
        jugadores = [controlador] if controlador else list(self.jugadores)
        for jugador_id in jugadores:
            for object_id in self.jugadores[jugador_id].zonas.get(zona, []):
                resultado.append(self.objetos[object_id])
        return resultado

    def mover(self, objeto: Objeto, zona: str, controlador: str | None = None) -> None:
        origen = self.jugadores[objeto.controlador].zonas[objeto.zona]
        origen.remove(objeto.object_id)
        objeto.generacion += 1
        objeto.zona = zona
        if controlador is not None:
            objeto.controlador = controlador
        self.jugadores[objeto.controlador].zonas[zona].append(objeto.object_id)

    def robar(self, jugador: Jugador, n: int) -> list[Objeto]:
        robadas = []
        for _ in range(n):
            if not jugador.zonas["Library"]:
                jugador.vida = 0
                break
            carta = self.objetos[jugador.zonas["Library"][0]]
            self.mover(carta, "Hand")
            robadas.append(carta)
        return robadas

    def contexto(self, fuente: Objeto, **extra: Any) -> Contexto:
        return Contexto(
            partida=self,
            fuente=fuente,
            controlador=extra.pop("controlador", fuente.controlador),
            **extra,
        )

    def elegir(self, tipo: str, opciones: list[Any], ctx: Contexto) -> Any:
        if self.respuesta_lista and self.respuesta_tipo == tipo:
            valor = self.respuesta
            self.respuesta_lista = False
            self.respuesta = None
            self.respuesta_tipo = None
            return valor
        if self.interceptar and tipo in _INTERCEPTAR:
            if tipo == "look" and len(opciones) <= 1:
                return opciones[0] if opciones else None
            self.eleccion_abierta = (tipo, list(opciones), ctx)
            raise DecisionRequerida(tipo, list(opciones))
        return self.elegir_fn(tipo, opciones, ctx)

    def impide_enderezar(self, objeto: Objeto) -> bool:
        return bool(self._estaticos(objeto, {"PreventUntap"}))

    def puede_ser_bloqueada(self, objeto: Objeto) -> bool:
        if any(
            c.tipo == "CantBeBlocked" and self.objeto_vivo(c.objetivo) is objeto
            for c in self.continuos
        ):
            return False
        return True

    def carta_jugable_por_permiso(self, carta: Objeto, controlador: str) -> bool:
        vivos = []
        for permiso in self.permisos_juego:
            vivo = self.objeto_vivo(permiso["carta"])
            if vivo is None:
                continue
            vivos.append(permiso)
            if vivo.object_id == carta.object_id and permiso["controlador"] == controlador:
                return True
        self.permisos_juego = vivos
        return False

    def _estaticos(
        self,
        objeto: Objeto,
        tipos: set[str],
        *,
        en_lanzamiento: bool = False,
    ) -> list[tuple[Objeto, dict]]:
        encontrados: list[tuple[Objeto, dict]] = []
        for fuente in self.objetos.values():
            for habilidad in fuente.definicion.abilities:
                if habilidad["ability_type"] != "StaticAbility":
                    continue
                if fuente.zona not in habilidad["zones_active"]:
                    continue
                for efecto in habilidad["effects"]:
                    if efecto["effect_type"] not in tipos:
                        continue
                    if efecto["parameters"].get("duration") not in _ESTATICAS_VIVAS:
                        continue
                    ctx = self.contexto(fuente, en_lanzamiento=en_lanzamiento)
                    if not afecta(efecto, fuente, objeto, ctx):
                        continue
                    if not condiciones_ok(
                        efecto.get("effect_conditions"), efecto.get("condition_operator"), ctx,
                    ):
                        continue
                    encontrados.append((fuente, efecto))
        return encontrados

    def _modificadores_continuos(self, objeto: Objeto, tipo: str) -> list[EfectoContinuo]:
        vivos = []
        for efecto in self.continuos:
            if efecto.tipo != tipo or efecto.duracion != "UNTIL_END_OF_TURN":
                continue
            if self.objeto_vivo(efecto.objetivo) is objeto:
                vivos.append(efecto)
        return vivos

    def _base(self, objeto: Objeto, atributo: str) -> int:
        impresa = objeto.definicion.power if atributo == "Power" else objeto.definicion.toughness
        valor = 0 if impresa in (None, "*") else int(impresa)
        valor += objeto.contadores.get("PlusOnePlusOne", 0)
        valor -= objeto.contadores.get("MinusOneMinusOne", 0)
        return valor

    def _caracteristica(self, objeto: Objeto, atributo: str) -> int:
        clave = (objeto.object_id, objeto.generacion, atributo)
        if clave in self.calculando:
            return self._base(objeto, atributo)
        self.calculando.add(clave)
        try:
            cdas = self._estaticos(objeto, {"SetPowerToughness"})
            if cdas:
                fuente, efecto = cdas[-1]
                valor = cantidad(efecto["parameters"].get("amount"), self.contexto(fuente))
            else:
                impresa = objeto.definicion.power if atributo == "Power" else objeto.definicion.toughness
                valor = 0 if impresa in (None, "*") else int(impresa)
            tipo = "ModifyPower" if atributo == "Power" else "ModifyToughness"
            for fuente, efecto in self._estaticos(objeto, {tipo}):
                valor += cantidad(efecto["parameters"].get("amount"), self.contexto(fuente))
            for continuo in self._modificadores_continuos(objeto, tipo):
                valor += continuo.amount
            valor += objeto.contadores.get("PlusOnePlusOne", 0)
            valor -= objeto.contadores.get("MinusOneMinusOne", 0)
            return valor
        finally:
            self.calculando.remove(clave)

    def poder(self, objeto: Objeto) -> int:
        return self._caracteristica(objeto, "Power")

    def resistencia(self, objeto: Objeto) -> int:
        return self._caracteristica(objeto, "Toughness")

    def tiene_keyword(self, objeto: Objeto, keyword: str) -> bool:
        clave = (objeto.object_id, objeto.generacion, f"kw:{keyword}")
        if clave in self.calculando:
            return keyword in objeto.definicion.keywords
        self.calculando.add(clave)
        try:
            if keyword in objeto.definicion.keywords:
                return True
            for _fuente, efecto in self._estaticos(objeto, {"AddKeyword"}):
                if efecto["parameters"].get("string_value") == keyword:
                    return True
            return any(
                efecto.string_value == keyword
                for efecto in self._modificadores_continuos(objeto, "AddKeyword")
            )
        finally:
            self.calculando.remove(clave)

    def coste_de_lanzar(self, carta: Objeto, x: int = 0) -> Any:
        impreso = parse_mana(carta.definicion.mana_cost)
        generico = impreso.generico + (x if impreso.tiene_x else 0)
        for fuente, efecto in self._estaticos(carta, {"ReduceManaCost"}, en_lanzamiento=True):
            generico -= cantidad(efecto["parameters"].get("amount"), self.contexto(fuente, x=x))
        return Coste(
            max(0, generico), dict(impreso.colores), impreso.tiene_x, x if impreso.tiene_x else 0,
        )

    def condiciones_de(
        self,
        fuente: Objeto,
        efecto: dict[str, Any],
        objetivo: Objeto | None = None,
        evento: Objeto | None = None,
    ) -> bool:
        objetivos = {}
        if objetivo is not None:
            objetivos["TARGET_1"] = objetivo.ref()
        ctx = self.contexto(fuente, evento=evento, objetivos=objetivos)
        return condiciones_ok(efecto.get("effect_conditions"), efecto.get("condition_operator"), ctx)

    def _sujeto_ok(self, fuente: Objeto, sujeto: Objeto, subject: dict[str, Any]) -> bool:
        dueno = subject.get("target_owner")
        if dueno == "CONTROLLER" and sujeto.controlador != fuente.controlador:
            return False
        if dueno == "OPPONENT" and sujeto.controlador == fuente.controlador:
            return False
        zona = subject.get("target_zone")
        if zona and sujeto.zona != zona:
            return False
        return cumple_restricciones(subject.get("restrictions"), sujeto, fuente)

    def disparar(
        self,
        evento: str,
        sujeto: Objeto,
        solo_fuente: Objeto | None = None,
    ) -> list[ObjetoPila]:
        creados = []
        fuentes = [solo_fuente] if solo_fuente is not None else list(self.objetos.values())
        for fuente in fuentes:
            for habilidad in fuente.definicion.abilities:
                disparo = habilidad.get("trigger") or {}
                if habilidad["ability_type"] != "TriggeredAbility":
                    continue
                if disparo.get("event_type") != evento:
                    continue
                if fuente.zona not in habilidad["zones_active"]:
                    continue
                subject = disparo.get("subject")
                grupos = disparo.get("condition_groups")
                if subject is None and not grupos:
                    if sujeto.object_id != fuente.object_id:
                        continue
                elif subject is not None and not self._sujeto_ok(fuente, sujeto, subject):
                    continue
                ctx = self.contexto(fuente, evento=sujeto)
                if grupos:
                    cumple = False
                    for grupo in grupos:
                        sujeto_grupo = grupo.get("subject") or {}
                        if not self._sujeto_ok(fuente, sujeto, sujeto_grupo):
                            continue
                        ctx.filtro_lanzamiento = sujeto_grupo
                        try:
                            if condiciones_ok(grupo.get("conditions"), "AND", ctx):
                                cumple = True
                                break
                        finally:
                            ctx.filtro_lanzamiento = None
                    if not cumple:
                        continue
                elif not condiciones_ok(disparo.get("conditions"), disparo.get("condition_operator"), ctx):
                    continue
                objeto = ObjetoPila(
                    object_id=self.nuevo_id("pila"),
                    fuente=fuente.ref(),
                    habilidad=habilidad["ability_order"],
                    controlador=fuente.controlador,
                    evento=sujeto.ref(),
                )
                self.pila.append(objeto)
                creados.append(objeto)
        return creados

    def entrar(self, objeto: Objeto, controlador: str | None = None) -> list[ObjetoPila]:
        self._aplicar_enter_as_copy(objeto)
        self.mover(objeto, "Battlefield", controlador)
        objeto.entro_este_turno = True
        for _fuente, efecto in self._estaticos(objeto, {"EnterTapped"}):
            if efecto["target_info"].get("target_owner") in ("SOURCE", None):
                objeto.girada = True
                break
        return self.disparar("EnterTheBattlefield", objeto)

    def _aplicar_enter_as_copy(self, carta: Objeto) -> None:
        for habilidad in carta.definicion.abilities:
            if habilidad["ability_type"] != "StaticAbility":
                continue
            if "Stack" not in habilidad["zones_active"] and "Battlefield" not in habilidad["zones_active"]:
                continue
            for efecto in habilidad["effects"]:
                if efecto["effect_type"] != "EnterAsCopy":
                    continue
                if not efecto.get("is_optional", False):
                    continue
                info = efecto.get("target_info") or {}
                candidatos = [
                    o for o in self.en_zona("Battlefield")
                    if cumple_restricciones(info.get("restrictions"), o, carta)
                ]
                legales = []
                for candidato in candidatos:
                    ctx_filtro = self.contexto(
                        carta,
                        objetivos={"TARGET_1": candidato.ref()},
                        x=carta.x,
                    )
                    if condiciones_ok(
                        efecto.get("effect_conditions"),
                        efecto.get("condition_operator"),
                        ctx_filtro,
                    ):
                        legales.append(candidato)
                if not legales:
                    return
                elegida = self.elegir("enter_as_copy", legales, self.contexto(carta, x=carta.x))
                if elegida is None:
                    return
                objetivos = {"TARGET_1": elegida.ref()}
                ctx = self.contexto(carta, objetivos=objetivos, x=carta.x)
                ejecutar(ctx, efecto)
                return

    def lanzar(self, carta: Objeto, x: int = 0, objetivos: dict[str, Objeto] | None = None) -> Objeto:
        coste = self.coste_de_lanzar(carta, x)
        carta.x = x if coste.tiene_x else None
        carta.mana_gastado = coste.total
        self.mover(carta, "Stack")
        self.pila.append(carta)
        self.lanzamientos.append({
            "controlador": carta.controlador,
            "nombre": carta.definicion.nombre,
            "object_id": carta.object_id,
        })
        carta._objetivos_de_lanzamiento = {  # type: ignore[attr-defined]
            clave: objetivo.ref() for clave, objetivo in (objetivos or {}).items()
        }
        self._prowess(carta)
        self.disparar("Cast", carta)
        return carta

    def _prowess(self, carta: Objeto) -> None:
        """+1/+1 hasta el final del turno a cada criatura con Prowess del controlador."""
        if "Creature" in carta.definicion.card_types:
            return
        for criatura in self.en_zona("Battlefield", carta.controlador):
            if "Creature" not in criatura.definicion.card_types:
                continue
            if not self.tiene_keyword(criatura, "Prowess"):
                continue
            for tipo in ("ModifyPower", "ModifyToughness"):
                self.continuos.append(EfectoContinuo(
                    effect_id=self.nuevo_id("fx"),
                    fuente=criatura.ref(),
                    objetivo=criatura.ref(),
                    tipo=tipo,
                    amount=1,
                    string_value=None,
                    duracion="UNTIL_END_OF_TURN",
                ))

    @staticmethod
    def _es_permanente(carta: Objeto) -> bool:
        return bool(set(carta.definicion.card_types) & _PERMANENTES)

    def resolver_tope(self) -> Objeto | ObjetoPila:
        if not self.pila:
            raise IndexError("pila vacía")
        item = self.pila.pop()
        try:
            if isinstance(item, ObjetoPila):
                self._ejecutar_objeto_pila(item)
            else:
                self._resolver_carta(item)
        except DecisionRequerida:
            self.pila.append(item)
            raise
        return item

    def resolver_hechizo(self, carta: Objeto) -> None:
        if carta in self.pila:
            self.pila.remove(carta)
        self._resolver_carta(carta)

    def resolver_pila(self, objeto: ObjetoPila) -> None:
        if objeto in self.pila:
            self.pila.remove(objeto)
        self._ejecutar_objeto_pila(objeto)

    def _resolver_carta(self, carta: Objeto) -> None:
        objetivos = getattr(carta, "_objetivos_de_lanzamiento", {})
        ctx = self.contexto(carta, objetivos=objetivos, x=carta.x)
        ctx.listas = dict(getattr(carta, "_listas", {}) or {})
        if self._es_permanente(carta):
            if not getattr(carta, "_estaticas_hechas", False):
                for habilidad in carta.definicion.abilities:
                    if habilidad["ability_type"] != "StaticAbility":
                        continue
                    if "Stack" not in habilidad["zones_active"]:
                        continue
                    self._resolver_efectos(habilidad, ctx)
                carta._estaticas_hechas = True  # type: ignore[attr-defined]
            if carta.zona != "Battlefield":
                self.entrar(carta)
            return
        habilidad = carta.definicion.hechizo()
        self._resolver_efectos(habilidad, ctx, item=carta)
        if carta.zona == "Stack":
            self.mover(carta, "Graveyard")

    def _ejecutar_objeto_pila(self, objeto: ObjetoPila) -> None:
        fuente = self.objetos.get(objeto.fuente.object_id)
        if fuente is None or fuente.generacion != objeto.fuente.generacion:
            # La fuente cambió de zona: la habilidad en pila sigue resolviéndose si existe.
            fuente = self.objetos.get(objeto.fuente.object_id)
            if fuente is None:
                return
        habilidad = fuente.definicion.habilidad(objeto.habilidad)
        evento = self.objeto_vivo(objeto.evento)
        ctx = self.contexto(
            fuente,
            controlador=objeto.controlador,
            evento=evento,
            objetivos=dict(objeto.objetivos),
            x=objeto.x,
        )
        ctx.listas = {clave: list(refs) for clave, refs in objeto.listas.items()}
        if objeto.resultado_anterior is not None:
            ctx.resultado_anterior = self.objeto_vivo(objeto.resultado_anterior)
        self._resolver_efectos(habilidad, ctx, aceptados=objeto.aceptados, item=objeto)

    def _resolver_efectos(
        self,
        habilidad: dict[str, Any],
        ctx: Contexto,
        aceptados: set[int] | None = None,
        item: Any = None,
    ) -> None:
        hechos: set[int] = set() if aceptados is None else set(aceptados)
        efectos = list(habilidad["effects"])
        inicio = int(getattr(item, "efecto_actual", 0) or 0) if item is not None else 0
        if item is not None and getattr(item, "resultado_anterior", None) is not None:
            vivo = self.objeto_vivo(item.resultado_anterior)
            if vivo is not None:
                ctx.resultado_anterior = vivo
                ctx.objetivos["PREVIOUS_TARGET"] = vivo.ref()
        for indice, efecto in enumerate(efectos):
            if indice < inicio:
                continue
            if efecto["effect_type"] == "EnterAsCopy":
                self._marcar_efecto(item, indice, ctx, hechos)
                continue
            enlace = (efecto.get("parameters") or {}).get("if_effect_accepted")
            if enlace is not None and enlace not in hechos:
                self._marcar_efecto(item, indice, ctx, hechos)
                continue
            ctx.clave_objetivo = self._clave_de_efecto(efectos, indice)
            if not condiciones_ok(efecto.get("effect_conditions"), efecto.get("condition_operator"), ctx):
                self._marcar_efecto(item, indice, ctx, hechos)
                continue
            if efecto.get("is_optional"):
                info = efecto.get("target_info") or {}
                if info.get("target_owner") == "PREVIOUS_TARGET" and not _objetivos(ctx, efecto):
                    self._marcar_efecto(item, indice, ctx, hechos)
                    continue
                if not self.elegir("optional_effect", [True, False], ctx):
                    self._marcar_efecto(item, indice, ctx, hechos)
                    continue
                hechos.add(efecto["effect_order"])
            ejecutar(ctx, efecto)
            if ctx.resultado_anterior is not None:
                ctx.objetivos["PREVIOUS_TARGET"] = ctx.resultado_anterior.ref()
            self._marcar_efecto(item, indice, ctx, hechos)

    @staticmethod
    def _marcar_efecto(item: Any, indice: int, ctx: Contexto, hechos: set[int]) -> None:
        if item is None:
            return
        item.efecto_actual = indice + 1
        item.resultado_anterior = (
            ctx.resultado_anterior.ref() if ctx.resultado_anterior is not None else None
        )
        if hasattr(item, "aceptados"):
            item.aceptados = set(hechos)

    @staticmethod
    def _clave_de_efecto(efectos: list[dict[str, Any]], indice: int) -> str:
        efecto = efectos[indice]
        info = efecto.get("target_info") or {}
        if not info.get("requires_target"):
            return "TARGET_1"
        if info.get("target_owner") == "TARGET_2":
            return "TARGET_2"
        primero = None
        for candidato in efectos:
            datos = candidato.get("target_info") or {}
            if datos.get("requires_target"):
                primero = datos
                break
        if primero is None or Partida._mismo_filtro(primero, info):
            return "TARGET_1"
        return "TARGET_2"

    @staticmethod
    def _mismo_filtro(a: dict[str, Any], b: dict[str, Any]) -> bool:
        return (
            a.get("target_owner") == b.get("target_owner")
            and a.get("target_zone") == b.get("target_zone")
            and a.get("restrictions") == b.get("restrictions")
        )

    def _coincide_lanzamiento(self, fuente: Objeto, sujeto: Objeto, subject: dict[str, Any]) -> bool:
        """El recuento del turno mira tipo y controlador, no la zona actual."""
        dueno = subject.get("target_owner")
        if dueno == "CONTROLLER" and sujeto.controlador != fuente.controlador:
            return False
        if dueno == "OPPONENT" and sujeto.controlador == fuente.controlador:
            return False
        return cumple_restricciones(subject.get("restrictions"), sujeto, fuente)

    def hechizos_lanzados(self, controlador: str, ctx: Contexto) -> int:
        sujeto = ctx.filtro_lanzamiento
        total = 0
        for lanzamiento in self.lanzamientos:
            if lanzamiento["controlador"] != controlador:
                continue
            if sujeto:
                objeto = self.objetos.get(lanzamiento["object_id"])
                if objeto is None or not self._coincide_lanzamiento(ctx.fuente, objeto, sujeto):
                    continue
            total += 1
        return total

    def limpiar_fin_de_turno(self, jugador_activo: str | None = None) -> None:
        self.lanzamientos = []
        self.continuos = [
            efecto for efecto in self.continuos
            if efecto.duracion not in ("UNTIL_END_OF_TURN",)
        ]
        if jugador_activo is not None:
            vivos = []
            for permiso in self.permisos_juego:
                if permiso["controlador"] != jugador_activo:
                    vivos.append(permiso)
                    continue
                permiso["turnos_del_controlador"] -= 1
                if permiso["turnos_del_controlador"] > 0:
                    vivos.append(permiso)
            self.permisos_juego = vivos
        for objeto in self.objetos.values():
            objeto.atacando = False
            objeto.bloqueando = False
