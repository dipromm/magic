"""Partida reducida sobre Partida: tierras, hechizos, activadas, combate."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from reglas.compilador import catalogo, criatura
from reglas.evaluar import exigir_resolutores, parse_mana
from reglas.modelo import Coste, Definicion, Jugador, Objeto, ObjetoPila
from reglas.partida import DecisionRequerida, Partida
from reglas.primitivas import ejecutar

MAX_ACCIONES = 50
# Tope del starter tras fase 9: max observado 15 en 20 partidas aleatorias
# (bloqueo partido, seed 0); p95 global 10 < 50 → tope = max, redondeado a 16.
MAX_ACCIONES_STARTER = 16
_JSONLD = Path(__file__).resolve().parent.parent / "mazos" / "bloomburrow_jsonld"
_INDICE = _JSONLD / "_indice.json"

_PIP_A_RESERVA = {
    "W": "WHITE",
    "U": "BLUE",
    "B": "BLACK",
    "R": "RED",
    "G": "GREEN",
}

class DemasiadasAcciones(Exception):
    """El menú supera el tope de 50; la máscara del entrenamiento no puede representarlo."""


class Adaptador:
    def __init__(
        self,
        *,
        modo_menu: str = "completo",
        medir: bool = False,
        max_acciones: int | None = None,
        bloqueo_partido: bool = False,
    ) -> None:
        self.partida = Partida()
        self.jugadores: list[Jugador] = []
        self.indice_activo = 0
        self.fase = "Principal"
        self.tierras_jugadas = 0
        self.atacantes: list[str] = []
        self.bloqueos: dict[str, str] = {}
        # "puente": solo tierra/criatura (equivalencia 124/50). "completo": starter.
        self.modo_menu = modo_menu
        self.medir = medir
        self.max_acciones = MAX_ACCIONES if max_acciones is None else max_acciones
        self.bloqueo_partido = bloqueo_partido
        self.bloqueador_pendiente: str | None = None
        self.decision: dict[str, Any] | None = None
        self.modo_decision = "menu"
        self.prioridad_idx = 0
        self.pases = 0
        self.ataques_confirmados = False
        self.bloqueos_confirmados = False
        self.segunda_principal = False
        self.danio_fase = "ninguno"
        self.actor_idx = 0
        self._en_autopase = False
        self._objeto_en_resolucion: str | None = None

    def montar(self, mazo_p0: list[Definicion], mazo_p1: list[Definicion]) -> None:
        self.partida = Partida()
        self.jugadores = [
            self.partida.nuevo_jugador("player_0"),
            self.partida.nuevo_jugador("player_1"),
        ]
        self.indice_activo = 0
        self.fase = "Principal"
        self.tierras_jugadas = 0
        self.atacantes = []
        self.bloqueos = {}
        self.bloqueador_pendiente = None
        self.decision = None
        self.modo_decision = "menu"
        self.prioridad_idx = 0
        self.pases = 0
        self.ataques_confirmados = False
        self.bloqueos_confirmados = False
        self.segunda_principal = False
        self.danio_fase = "ninguno"
        self.actor_idx = 0
        self._en_autopase = False
        self._objeto_en_resolucion = None
        self.partida.interceptar = self.modo_menu == "completo"
        exigir_resolutores(list(mazo_p0) + list(mazo_p1))
        for jugador, mazo in zip(self.jugadores, (mazo_p0, mazo_p1)):
            cartas = list(mazo)
            random.shuffle(cartas)
            for definicion in cartas:
                self.partida.instanciar(definicion, jugador.id, "Library")
            n = min(7, len(jugador.zonas["Library"]))
            if n:
                self._robar(jugador, n)
        if self.jugadores[0].zonas["Library"]:
            self._robar(self.jugadores[0], 1)

    def jugador_activo(self) -> Jugador:
        return self.jugadores[self.indice_activo]

    def defensor(self) -> Jugador:
        return self.jugadores[1 - self.indice_activo]

    def maná_disponible(self, jugador: Jugador) -> int:
        return sum(
            1
            for object_id in jugador.zonas["Battlefield"]
            if "Land" in self.partida.objetos[object_id].definicion.card_types
            and not self.partida.objetos[object_id].girada
        )

    def _colores_de_tierra(self, tierra: Objeto) -> list[str]:
        colores: list[str] = []
        for habilidad in tierra.definicion.abilities:
            if habilidad["ability_type"] != "ActivatedAbility":
                continue
            costes = habilidad.get("costs") or []
            if not any(c.get("cost_type") == "TapCost" for c in costes):
                continue
            for efecto in habilidad["effects"]:
                if efecto["effect_type"] == "AddMana":
                    color = efecto["parameters"].get("string_value")
                    if color:
                        colores.append(color)
        return colores

    def _tierras_enderezadas(self, jugador: Jugador) -> list[Objeto]:
        return [
            self.partida.objetos[object_id]
            for object_id in jugador.zonas["Battlefield"]
            if "Land" in self.partida.objetos[object_id].definicion.card_types
            and not self.partida.objetos[object_id].girada
        ]

    def puede_pagar(self, jugador: Jugador, coste: Coste) -> bool:
        pool = []
        for tierra in self._tierras_enderezadas(jugador):
            colores = self._colores_de_tierra(tierra)
            pool.append(colores if colores else ["COLORLESS"])
        for pip, cantidad in coste.colores.items():
            reserva = _PIP_A_RESERVA[pip]
            for _ in range(cantidad):
                idx = next((i for i, cols in enumerate(pool) if reserva in cols), None)
                if idx is None:
                    return False
                pool.pop(idx)
        return len(pool) >= coste.generico

    def _objetivos_legales(
        self,
        carta: Objeto,
        efecto: dict[str, Any] | None,
        info: dict[str, Any] | None = None,
    ) -> list[Objeto]:
        info = info if info is not None else ((efecto or {}).get("target_info") or {})
        if not info.get("requires_target"):
            return []
        restricciones = info.get("restrictions") or {}
        tipos = list(restricciones.get("types") or [])
        operador = restricciones.get("logical_operator") or "NONE"
        zona = info.get("target_zone")
        dueno = info.get("target_owner")
        incluye_jugador = "Player" in tipos
        if incluye_jugador and not zona:
            candidatos = list(self.partida.representantes.values())
        else:
            if zona:
                candidatos = self.partida.en_zona(zona)
            else:
                candidatos = [
                    objeto for objeto in self.partida.objetos.values()
                    if "Player" not in objeto.definicion.card_types
                ]
            if incluye_jugador and (operador == "OR" or tipos == ["Player"]):
                candidatos = list(candidatos) + list(self.partida.representantes.values())
        from reglas.evaluar import cumple_restricciones

        filtrados = []
        for objeto in candidatos:
            if dueno == "CONTROLLER" and objeto.controlador != carta.controlador:
                continue
            if dueno in ("OPPONENT", "NOT_CONTROLLER") and objeto.controlador == carta.controlador:
                continue
            if not cumple_restricciones(restricciones, objeto, carta):
                continue
            if efecto is not None and efecto.get("effect_conditions"):
                if not self.partida.condiciones_de(carta, efecto, objeto):
                    continue
            filtrados.append(objeto)
        return filtrados

    def _efectos_con_objetivo(self, carta: Objeto) -> list[dict[str, Any]]:
        efectos = []
        for habilidad in carta.definicion.abilities:
            if habilidad["ability_type"] in ("SpellAbility", "StaticAbility"):
                if habilidad["ability_type"] == "StaticAbility" and "Stack" not in habilidad.get("zones_active", []):
                    continue
                for efecto in habilidad["effects"]:
                    if (efecto.get("target_info") or {}).get("requires_target"):
                        efectos.append(efecto)
            if habilidad["ability_type"] == "TriggeredAbility":
                continue
        if not efectos:
            for habilidad in carta.definicion.abilities:
                if habilidad["ability_type"] == "TriggeredAbility":
                    continue
                for efecto in habilidad["effects"]:
                    if (efecto.get("target_info") or {}).get("requires_target"):
                        efectos.append(efecto)
                        break
                if efectos:
                    break
        return efectos

    def _ranuras(self, carta: Objeto) -> list[dict[str, Any]]:
        ranuras: list[dict[str, Any]] = []
        for efecto in self._efectos_con_objetivo(carta):
            info = efecto.get("target_info") or {}
            if not info.get("requires_target"):
                continue
            if not ranuras or not Partida._mismo_filtro(ranuras[0]["info"], info):
                clave = "TARGET_1" if not any(r["clave"] == "TARGET_1" for r in ranuras) else "TARGET_2"
                if info.get("target_owner") == "TARGET_2":
                    clave = "TARGET_2"
                ranuras.append({"clave": clave, "info": info, "efecto": efecto})
            for extra in info.get("additional_targets") or []:
                if extra.get("requires_target"):
                    ranuras.append({"clave": "TARGET_2", "info": extra, "efecto": None})
        return ranuras

    def _min_objetivos(self, info: dict[str, Any]) -> int:
        return (info.get("target_count") or {}).get("min", 1) or 0

    def _max_objetivos(self, info: dict[str, Any], legales: int) -> int:
        mx = (info.get("target_count") or {}).get("max", 1)
        if mx == "*" or mx is None:
            return max(legales, 1)
        return int(mx)

    def _tiene_objetivos_de_lanzamiento(self, carta: Objeto) -> bool:
        for ranura in self._ranuras(carta):
            if self._min_objetivos(ranura["info"]) > 0 and not self._objetivos_legales(
                carta, ranura["efecto"], ranura["info"],
            ):
                return False
        return True

    def _elegir_objetivos(self, carta: Objeto) -> dict[str, Objeto] | None:
        ranuras = self._ranuras(carta)
        if not ranuras:
            return {}
        puestos: dict[str, Objeto] = {}
        for ranura in ranuras:
            legales = self._objetivos_legales(carta, ranura["efecto"], ranura["info"])
            if not legales:
                if self._min_objetivos(ranura["info"]) == 0:
                    continue
                return None
            elegida = self.partida.elegir("target", legales, self.partida.contexto(carta))
            if elegida is None:
                if self._min_objetivos(ranura["info"]) == 0:
                    continue
                return None
            puestos[ranura["clave"]] = elegida
        return puestos

    def _x_automatica(self, jugador: Jugador, carta: Objeto) -> int:
        coste = self.partida.coste_de_lanzar(carta, x=0)
        libres = self.maná_disponible(jugador) - coste.total
        return max(0, min(libres, 5))

    @property
    def _con_prioridad(self) -> bool:
        return self.modo_menu == "completo"

    def _idx(self, jugador: Jugador) -> int:
        return 0 if jugador is self.jugadores[0] else 1

    def _en_declaracion(self) -> bool:
        """Elegir atacantes o bloqueadores, sin instantáneos entre criaturas.

        Si la pila tiene algo, primero se pasa prioridad y se resuelve el tope.
        """
        if not self._con_prioridad or self.partida.pila:
            return False
        if self.fase == "Declarar Atacantes" and not self.ataques_confirmados:
            return True
        if self.fase == "Declarar Bloqueadores" and not self.bloqueos_confirmados:
            return True
        return False

    def quien_actua(self) -> int:
        """Índice de quien debe elegir: prioridad, declaración o descarte."""
        if self.decision is not None:
            return self.actor_idx
        if self.fase == "Descartar":
            return self.indice_activo
        if self._con_prioridad and self._en_declaracion():
            if self.fase == "Declarar Bloqueadores":
                return 1 - self.indice_activo
            return self.indice_activo
        if self._con_prioridad:
            return self.prioridad_idx
        if self.fase == "Declarar Bloqueadores":
            return 1 - self.indice_activo
        return self.indice_activo

    def _fijar_actor(self, controlador: str) -> None:
        self.actor_idx = 0 if controlador == self.jugadores[0].id else 1

    def acciones_legales(self, jugador: Jugador | None = None) -> list[dict[str, Any]]:
        if self.decision is not None:
            return self._acciones_decision(self.jugadores[self.quien_actua()])
        if self._con_prioridad:
            self._autopasar_completo()
        else:
            self._avanzar_fases_vacias()
        if self.decision is not None:
            return self._acciones_decision(self.jugadores[self.quien_actua()])
        if self.terminada():
            return []
        return self._enumerar(self.jugadores[self.quien_actua()])

    def _avanzar_fases_vacias(self) -> None:
        """Principal, atacantes y bloqueadores se saltan si solo queda pasar o confirmar."""
        for _ in range(24):
            if self.terminada() or self.decision is not None or self.fase == "Descartar":
                return
            if self.fase == "Declarar Bloqueadores":
                quien = self.defensor()
            else:
                quien = self.jugador_activo()
            acciones = self._enumerar(quien)
            if not self._solo_confirmacion(acciones):
                return
            self.ejecutar_accion(quien, acciones[0])

    def _solo_confirmacion(self, acciones: list[dict[str, Any]]) -> bool:
        if len(acciones) != 1:
            return False
        tipo = acciones[0]["tipo"]
        if self.fase == "Principal":
            return tipo == "pasar_fase"
        if self.fase == "Declarar Atacantes":
            return tipo == "finalizar_ataques"
        if self.fase == "Declarar Bloqueadores":
            return tipo == "resolver_combate"
        return False

    def _dar_prioridad(self, idx: int) -> None:
        self._acciones_de_estado()
        self.prioridad_idx = idx
        self.pases = 0

    def _es_instantanea(self, carta: Objeto) -> bool:
        if "Instant" in carta.definicion.card_types:
            return True
        return self.partida.tiene_keyword(carta, "Flash")

    def _conjuro_lento(self, jugador: Jugador) -> bool:
        """Conjuro, permanente, tierra y Equip: tu principal, pila vacía, eres el activo."""
        return (
            self.fase == "Principal"
            and not self.partida.pila
            and jugador.id == self.jugador_activo().id
        )

    def _es_equipar(self, habilidad: dict[str, Any]) -> bool:
        return habilidad.get("implements_keyword") == "Equip"

    def _menu_prioridad(self, jugador: Jugador) -> list[dict[str, Any]]:
        acciones: list[dict[str, Any]] = [{
            "tipo": "pasar_fase",
            "descripcion": "Pasar prioridad",
        }]
        lento = self._conjuro_lento(jugador)
        origenes = list(jugador.zonas["Hand"]) + [
            oid for oid in jugador.zonas["Exile"]
            if self.partida.carta_jugable_por_permiso(self.partida.objetos[oid], jugador.id)
        ]
        for object_id in origenes:
            carta = self.partida.objetos[object_id]
            if "Land" in carta.definicion.card_types:
                if lento and carta.zona == "Hand" and self.tierras_jugadas < 1:
                    acciones.append({
                        "tipo": "jugar_carta",
                        "carta_id": carta.object_id,
                        "descripcion": f"Jugar tierra: {carta.definicion.nombre} [{carta.object_id}]",
                    })
                continue
            rapida = self._es_instantanea(carta)
            if not rapida and not lento:
                continue
            x = self._x_automatica(jugador, carta) if parse_mana(carta.definicion.mana_cost).tiene_x else 0
            coste = self.partida.coste_de_lanzar(carta, x)
            if not self.puede_pagar(jugador, coste):
                continue
            if not self._tiene_objetivos_de_lanzamiento(carta):
                continue
            if "Creature" in carta.definicion.card_types:
                desc = (
                    f"Jugar criatura: {carta.definicion.nombre} "
                    f"(Coste: {coste.total}) [{carta.object_id}]"
                )
            else:
                desc = (
                    f"Jugar: {carta.definicion.nombre} "
                    f"(Coste: {coste.total}) [{carta.object_id}]"
                )
            acciones.append({
                "tipo": "jugar_carta",
                "carta_id": carta.object_id,
                "x": x,
                "descripcion": desc,
            })
        for object_id in jugador.zonas["Battlefield"]:
            fuente = self.partida.objetos[object_id]
            for habilidad in fuente.definicion.abilities:
                if habilidad["ability_type"] != "ActivatedAbility":
                    continue
                if "Battlefield" not in habilidad["zones_active"]:
                    continue
                if self._es_habilidad_de_mana(habilidad):
                    continue
                if self._es_equipar(habilidad) and not lento:
                    continue
                if not self._puede_activar(jugador, fuente, habilidad):
                    continue
                acciones.append({
                    "tipo": "activar",
                    "carta_id": fuente.object_id,
                    "habilidad": habilidad["ability_order"],
                    "descripcion": (
                        f"Activar {fuente.definicion.nombre} "
                        f"#{habilidad['ability_order']} [{fuente.object_id}]"
                    ),
                })
        return acciones

    def _autopasar_completo(self) -> None:
        """Si solo se puede pasar, pasan los dos. El menú queda cuando hay algo que hacer."""
        if self._en_autopase or not self._con_prioridad:
            return
        self._en_autopase = True
        try:
            for _ in range(128):
                if self.decision is not None or self.fase == "Descartar":
                    return
                if self.terminada():
                    if self.partida.pila:
                        self._vaciar_pila()
                    return
                if self._en_declaracion():
                    quien = (
                        self.defensor()
                        if self.fase == "Declarar Bloqueadores"
                        else self.jugador_activo()
                    )
                    acciones = self._enumerar(quien)
                    if self._solo_confirmacion(acciones):
                        self._dispatch(quien, acciones[0])
                        continue
                    return
                jugador = self.jugadores[self.prioridad_idx]
                acciones = self._enumerar(jugador)
                if len(acciones) != 1 or acciones[0]["tipo"] != "pasar_fase":
                    return
                self._dispatch(jugador, acciones[0])
        finally:
            self._en_autopase = False

    def _pasar(self, jugador: Jugador) -> None:
        if self._idx(jugador) != self.prioridad_idx:
            raise RuntimeError("no tiene la prioridad")
        self.pases += 1
        if self.pases < 2:
            self.prioridad_idx = 1 - self.prioridad_idx
            return
        self.pases = 0
        if self.partida.pila:
            self._resolver_un_objeto()
            if self.decision is not None:
                return
            self.prioridad_idx = self.indice_activo
            return
        self._vaciar_maná()
        self._avanzar_paso()

    def _id_tope(self) -> str | None:
        if not self.partida.pila:
            return None
        return self.partida.pila[-1].object_id

    def _resolver_un_objeto(self) -> None:
        if not self.partida.pila:
            return
        self._objeto_en_resolucion = self._id_tope()
        self._seguir_objeto_actual()
        if self.decision is None:
            self._objeto_en_resolucion = None
            self._acciones_de_estado()

    def _seguir_objeto_actual(self) -> None:
        oid = self._objeto_en_resolucion or self._id_tope()
        if oid is None:
            return
        self._objeto_en_resolucion = oid
        for _ in range(16):
            if self._id_tope() != oid:
                self._objeto_en_resolucion = None
                return
            if self._preflight():
                return
            try:
                self.partida.resolver_tope()
            except DecisionRequerida as exc:
                self._abrir_eleccion(exc)
                return
            self._objeto_en_resolucion = None
            return

    def _despues_de_eleccion(self) -> None:
        if not self._con_prioridad:
            self._vaciar_pila()
            return
        self._seguir_objeto_actual()
        if self.decision is not None:
            return
        self._acciones_de_estado()
        self.prioridad_idx = self.indice_activo
        self.pases = 0

    def _avanzar_paso(self) -> None:
        if self.fase == "Mantenimiento":
            self._entrar_robo()
        elif self.fase == "Robar":
            self._entrar_principal(False)
        elif self.fase == "Principal" and not self.segunda_principal:
            self._entrar_inicio_combate()
        elif self.fase == "Inicio del combate":
            self._entrar_declarar_atacantes()
        elif self.fase == "Declarar Atacantes":
            self.ataques_confirmados = False
            if self.atacantes:
                self.fase = "Declarar Bloqueadores"
                self.bloqueos_confirmados = False
                self.bloqueador_pendiente = None
                return
            self._entrar_fin_combate()
        elif self.fase == "Declarar Bloqueadores":
            self.bloqueos_confirmados = False
            self._entrar_danio()
        elif self.fase == "Daño de combate":
            if self.danio_fase == "first":
                self._aplicar_danio(False)
                self.danio_fase = "normal"
                self._dar_prioridad(self.indice_activo)
                return
            self._entrar_fin_combate()
        elif self.fase == "Fin del combate":
            self._entrar_principal(True)
        elif self.fase == "Principal":
            self._entrar_paso_final()
        elif self.fase == "Paso final":
            self._entrar_limpieza()
        elif self.fase in ("Limpieza", "Descartar"):
            self._siguiente_turno()

    def _entrar_robo(self) -> None:
        self.fase = "Robar"
        self._robar(self.jugador_activo(), 1)
        if self.terminada():
            return
        self._dar_prioridad(self.indice_activo)

    def _entrar_principal(self, segunda: bool) -> None:
        self.fase = "Principal"
        self.segunda_principal = segunda
        self._dar_prioridad(self.indice_activo)

    def _entrar_inicio_combate(self) -> None:
        self.fase = "Inicio del combate"
        self._dar_prioridad(self.indice_activo)

    def _entrar_declarar_atacantes(self) -> None:
        self.fase = "Declarar Atacantes"
        self.ataques_confirmados = False
        self.atacantes = []
        self.bloqueos = {}
        self.bloqueos_confirmados = False
        self.bloqueador_pendiente = None

    def _entrar_fin_combate(self) -> None:
        self.fase = "Fin del combate"
        self.danio_fase = "ninguno"
        self._dar_prioridad(self.indice_activo)

    def _entrar_paso_final(self) -> None:
        self.fase = "Paso final"
        self._dar_prioridad(self.indice_activo)

    def _entrar_limpieza(self) -> None:
        if len(self.jugador_activo().zonas["Hand"]) > 7:
            self.fase = "Descartar"
            self.modo_decision = "menu"
            return
        self._cerrar_limpieza()

    def _cerrar_limpieza(self) -> None:
        self._acciones_de_estado()
        if self.partida.pila:
            self.fase = "Limpieza"
            self._dar_prioridad(self.indice_activo)
            return
        self._siguiente_turno()

    def _entrar_danio(self) -> None:
        self.fase = "Daño de combate"
        participantes = [
            self.partida.objetos[oid]
            for oid in list(self.atacantes) + list(self.bloqueos)
            if oid in self.partida.objetos and self.partida.objetos[oid].zona == "Battlefield"
        ]
        if any(self._tiene_first_strike(carta) for carta in participantes):
            self._aplicar_danio(True)
            self.danio_fase = "first"
        else:
            self._aplicar_danio(False)
            self.danio_fase = "normal"
        if self.terminada():
            return
        self._dar_prioridad(self.indice_activo)

    def _aplicar_danio(self, paso_first: bool) -> None:
        self._paso_danio(paso_first, resolver=False)
        self._acciones_de_estado()

    def _enumerar(self, jugador: Jugador) -> list[dict[str, Any]]:
        if self.partida.pila and self.decision is None and not self._con_prioridad:
            raise RuntimeError("la pila no está vacía al enumerar el menú")
        acciones: list[dict[str, Any]] = []
        self.modo_decision = "menu"
        # La declaración no se mezcla con la pila: un instantáneo en ese paso
        # se responde antes de seguir eligiendo criaturas.
        en_ataques = self.fase == "Declarar Atacantes" and not self.partida.pila and not (
            self._con_prioridad and self.ataques_confirmados
        )
        en_bloqueos = self.fase == "Declarar Bloqueadores" and not self.partida.pila and not (
            self._con_prioridad and self.bloqueos_confirmados
        )
        if self._con_prioridad and self.fase != "Descartar" and not en_ataques and not en_bloqueos:
            acciones = self._menu_prioridad(jugador)
            if not self.medir and len(acciones) > self.max_acciones:
                raise DemasiadasAcciones(len(acciones))
            return acciones
        if self.fase == "Principal":
            acciones.append({"tipo": "pasar_fase", "descripcion": "Pasar a Declarar Atacantes"})
            for object_id in list(jugador.zonas["Hand"]) + [
                oid for oid in jugador.zonas["Exile"]
                if self.partida.carta_jugable_por_permiso(self.partida.objetos[oid], jugador.id)
            ]:
                carta = self.partida.objetos[object_id]
                if "Land" in carta.definicion.card_types:
                    if carta.zona == "Hand" and self.tierras_jugadas < 1:
                        acciones.append({
                            "tipo": "jugar_carta",
                            "carta_id": carta.object_id,
                            "descripcion": f"Jugar tierra: {carta.definicion.nombre} [{carta.object_id}]",
                        })
                    continue
                if self.modo_menu == "puente" and "Creature" not in carta.definicion.card_types:
                    continue
                x = self._x_automatica(jugador, carta) if parse_mana(carta.definicion.mana_cost).tiene_x else 0
                coste = self.partida.coste_de_lanzar(carta, x)
                if not self.puede_pagar(jugador, coste):
                    continue
                if not self._tiene_objetivos_de_lanzamiento(carta):
                    continue
                if "Creature" in carta.definicion.card_types:
                    desc = (
                        f"Jugar criatura: {carta.definicion.nombre} "
                        f"(Coste: {coste.total}) [{carta.object_id}]"
                    )
                else:
                    desc = (
                        f"Jugar: {carta.definicion.nombre} "
                        f"(Coste: {coste.total}) [{carta.object_id}]"
                    )
                acciones.append({
                    "tipo": "jugar_carta",
                    "carta_id": carta.object_id,
                    "x": x,
                    "descripcion": desc,
                })
            if self.modo_menu != "puente":
                for object_id in jugador.zonas["Battlefield"]:
                    fuente = self.partida.objetos[object_id]
                    for habilidad in fuente.definicion.abilities:
                        if habilidad["ability_type"] != "ActivatedAbility":
                            continue
                        if "Battlefield" not in habilidad["zones_active"]:
                            continue
                        if self._es_habilidad_de_mana(habilidad):
                            continue
                        if not self._puede_activar(jugador, fuente, habilidad):
                            continue
                        acciones.append({
                            "tipo": "activar",
                            "carta_id": fuente.object_id,
                            "habilidad": habilidad["ability_order"],
                            "descripcion": (
                                f"Activar {fuente.definicion.nombre} "
                                f"#{habilidad['ability_order']} [{fuente.object_id}]"
                            ),
                        })
        elif self.fase == "Declarar Atacantes":
            acciones.append({"tipo": "finalizar_ataques", "descripcion": "Confirmar ataques"})
            for object_id in jugador.zonas["Battlefield"]:
                carta = self.partida.objetos[object_id]
                if "Creature" not in carta.definicion.card_types:
                    continue
                if carta.entro_este_turno and not self.partida.tiene_keyword(carta, "Haste"):
                    continue
                if carta.girada and not self.partida.tiene_keyword(carta, "Vigilance"):
                    continue
                if carta.object_id in self.atacantes:
                    continue
                if self.partida.tiene_keyword(carta, "Defender"):
                    continue
                poder = self.partida.poder(carta)
                resistencia = self.partida.resistencia(carta)
                acciones.append({
                    "tipo": "declarar_atacante",
                    "carta_id": carta.object_id,
                    "descripcion": f"Atacar con: {carta.definicion.nombre} ({poder}/{resistencia})",
                })
        elif self.fase == "Declarar Bloqueadores":
            acciones.extend(self._acciones_bloqueo(jugador))
        elif self.fase == "Descartar":
            self.modo_decision = "menu"
            for object_id in jugador.zonas["Hand"]:
                carta = self.partida.objetos[object_id]
                acciones.append({
                    "tipo": "descartar",
                    "carta_id": carta.object_id,
                    "descripcion": f"Descartar: {carta.definicion.nombre} [{carta.object_id}]",
                })
            # Mano grande: enseñar un trozo; al descartar entran las siguientes.
            if len(acciones) > self.max_acciones:
                return acciones[: self.max_acciones]
        if not self.medir and len(acciones) > self.max_acciones:
            raise DemasiadasAcciones(len(acciones))
        return acciones

    def _acciones_bloqueo(self, jugador: Jugador) -> list[dict[str, Any]]:
        acciones: list[dict[str, Any]] = [{
            "tipo": "resolver_combate",
            "descripcion": "Confirmar bloqueos y resolver daño",
        }]
        atacantes_libres = [
            attacker_id
            for attacker_id in self.atacantes
            if attacker_id not in self.bloqueos.values()
            and self.partida.puede_ser_bloqueada(self.partida.objetos[attacker_id])
        ]
        if self.bloqueo_partido:
            if self.bloqueador_pendiente is None:
                self.modo_decision = "elegir_bloqueador"
                for object_id in jugador.zonas["Battlefield"]:
                    bloqueador = self.partida.objetos[object_id]
                    if "Creature" not in bloqueador.definicion.card_types:
                        continue
                    if bloqueador.girada or bloqueador.object_id in self.bloqueos:
                        continue
                    if not any(
                        self._puede_bloquear(bloqueador, self.partida.objetos[a])
                        for a in atacantes_libres
                    ):
                        continue
                    acciones.append({
                        "tipo": "elegir_bloqueador",
                        "blocker_id": bloqueador.object_id,
                        "descripcion": f"Elegir bloqueador: {bloqueador.definicion.nombre}",
                    })
                return acciones
            self.modo_decision = "elegir_atacante"
            bloqueador = self.partida.objetos[self.bloqueador_pendiente]
            for attacker_id in atacantes_libres:
                if not self._puede_bloquear(bloqueador, self.partida.objetos[attacker_id]):
                    continue
                atacante = self.partida.objetos[attacker_id]
                acciones.append({
                    "tipo": "elegir_atacante",
                    "blocker_id": bloqueador.object_id,
                    "attacker_id": attacker_id,
                    "descripcion": f"Bloquear a {atacante.definicion.nombre}",
                })
            acciones.append({
                "tipo": "cancelar_bloqueador",
                "descripcion": "Cancelar bloqueador",
            })
            return acciones
        self.modo_decision = "menu"
        for object_id in jugador.zonas["Battlefield"]:
            bloqueador = self.partida.objetos[object_id]
            if "Creature" not in bloqueador.definicion.card_types:
                continue
            if bloqueador.girada or bloqueador.object_id in self.bloqueos:
                continue
            for attacker_id in atacantes_libres:
                if not self._puede_bloquear(bloqueador, self.partida.objetos[attacker_id]):
                    continue
                atacante = self.partida.objetos[attacker_id]
                poder = self.partida.poder(bloqueador)
                resistencia = self.partida.resistencia(bloqueador)
                acciones.append({
                    "tipo": "declarar_bloqueo",
                    "blocker_id": bloqueador.object_id,
                    "attacker_id": attacker_id,
                    "descripcion": (
                        f"Bloquear a {atacante.definicion.nombre} con "
                        f"{bloqueador.definicion.nombre} ({poder}/{resistencia})"
                    ),
                })
        return acciones

    def _coste_opcional(self, carta: Objeto) -> str | None:
        for habilidad in carta.definicion.abilities:
            for coste in habilidad.get("costs") or []:
                if coste.get("cost_type") == "OptionalPayMana":
                    return coste.get("amount")
        return None

    def _puede_pagar_ambos(self, jugador: Jugador, primero: Coste, segundo: Coste) -> bool:
        colores: dict[str, int] = {}
        for origen in (primero.colores, segundo.colores):
            for pip, n in origen.items():
                colores[pip] = colores.get(pip, 0) + n
        return self.puede_pagar(jugador, Coste(primero.generico + segundo.generico, colores, False))

    def _ranuras_de_habilidad(self, habilidad: dict[str, Any]) -> list[dict[str, Any]]:
        ranuras: list[dict[str, Any]] = []
        for efecto in habilidad.get("effects") or []:
            info = efecto.get("target_info") or {}
            if not info.get("requires_target"):
                continue
            if ranuras and Partida._mismo_filtro(ranuras[0]["info"], info):
                continue
            clave = "TARGET_1" if not ranuras else "TARGET_2"
            if info.get("target_owner") == "TARGET_2":
                clave = "TARGET_2"
            ranuras.append({"clave": clave, "info": info, "efecto": efecto})
        return ranuras

    def _acciones_decision(self, jugador: Jugador) -> list[dict[str, Any]]:
        assert self.decision is not None
        tipo = self.decision["tipo"]
        if tipo == "x":
            self.modo_decision = "x"
            carta = self.partida.objetos[self.decision["carta_id"]]
            max_x = max(0, self.maná_disponible(jugador) - self.partida.coste_de_lanzar(carta, 0).total)
            max_x = min(max_x, 5)
            return [
                {"tipo": "elegir_x", "x": x, "descripcion": f"X = {x}"}
                for x in range(max_x + 1)
            ]
        if tipo == "offspring":
            self.modo_decision = "offspring"
            carta = self.partida.objetos[self.decision["carta_id"]]
            x = int(self.decision.get("x") or 0)
            base = self.partida.coste_de_lanzar(carta, x)
            extra = parse_mana(self._coste_opcional(carta))
            acciones = []
            if self._puede_pagar_ambos(jugador, base, extra):
                acciones.append({"tipo": "pagar_opcional", "descripcion": "Pagar Offspring"})
            acciones.append({"tipo": "omitir_opcional", "descripcion": "No pagar Offspring"})
            return acciones
        if tipo == "objetivo":
            self.modo_decision = "objetivo"
            carta = self.partida.objetos[self.decision["carta_id"]]
            if self.decision.get("origen") == "activar":
                efecto = self.decision["efecto"]
                info = efecto.get("target_info")
            else:
                ranura = self._ranuras(carta)[self.decision["indice"]]
                efecto = ranura["efecto"]
                info = ranura["info"]
            return [
                {
                    "tipo": "elegir_objetivo",
                    "carta_id": o.object_id,
                    "descripcion": f"Objetivo: {o.definicion.nombre}",
                }
                for o in self._objetivos_legales(carta, efecto, info)
            ]
        if tipo == "objetivos_n":
            self.modo_decision = "objetivo"
            fuente = self.partida.objetos[self.decision["fuente_id"]]
            elegidos = set(self.decision["elegidos"])
            acciones = [
                {
                    "tipo": "elegir_objetivo",
                    "carta_id": o.object_id,
                    "descripcion": f"Objetivo: {o.definicion.nombre}",
                }
                for o in self._objetivos_legales(fuente, self.decision["efecto"], self.decision["info"])
                if o.object_id not in elegidos
            ]
            if len(self.decision["elegidos"]) >= self.decision["min"]:
                acciones.append({
                    "tipo": "confirmar_objetivos",
                    "descripcion": "No elegir más objetivos",
                })
            return acciones
        if tipo == "opcional":
            self.modo_decision = "opcional"
            return [
                {"tipo": "aceptar", "descripcion": "Aceptar efecto"},
                {"tipo": "rechazar", "descripcion": "Rechazar efecto"},
            ]
        if tipo == "scry":
            self.modo_decision = "scry"
            acciones = []
            for object_id in self.decision["restantes"]:
                carta = self.partida.objetos[object_id]
                acciones.append({
                    "tipo": "elegir_scry",
                    "carta_id": object_id,
                    "destino": "top",
                    "descripcion": f"Dejar arriba: {carta.definicion.nombre}",
                })
                acciones.append({
                    "tipo": "elegir_scry",
                    "carta_id": object_id,
                    "destino": "bottom",
                    "descripcion": f"Al fondo: {carta.definicion.nombre}",
                })
            return acciones
        if tipo in ("descartar_efecto", "look", "move_zone", "enter_as_copy"):
            self.modo_decision = "objetivo"
            acciones = [
                {
                    "tipo": "elegir_objetivo",
                    "carta_id": object_id,
                    "descripcion": f"Elegir: {self.partida.objetos[object_id].definicion.nombre}",
                }
                for object_id in self.decision["opciones"]
            ]
            if tipo == "enter_as_copy":
                acciones.append({"tipo": "rechazar", "descripcion": "No entrar como copia"})
            return acciones
        if tipo == "reetiquetar":
            self.modo_decision = "objetivo"
            carta = self.partida.objetos[self.decision["carta_id"]]
            ranuras = self._ranuras(carta)
            acciones = []
            if self.decision["indice"] == 0:
                acciones.append({"tipo": "mantener_objetivos", "descripcion": "Mantener objetivos"})
            if self.decision["indice"] < len(ranuras):
                ranura = ranuras[self.decision["indice"]]
                acciones.extend(
                    {
                        "tipo": "elegir_objetivo",
                        "carta_id": o.object_id,
                        "descripcion": f"Nuevo objetivo: {o.definicion.nombre}",
                    }
                    for o in self._objetivos_legales(carta, ranura["efecto"], ranura["info"])
                )
            return acciones
        return []

    def _puede_bloquear(self, bloqueador: Objeto, atacante: Objeto) -> bool:
        if self.partida.tiene_keyword(atacante, "Flying"):
            return (
                self.partida.tiene_keyword(bloqueador, "Flying")
                or self.partida.tiene_keyword(bloqueador, "Reach")
            )
        return True

    def _es_habilidad_de_mana(self, habilidad: dict[str, Any]) -> bool:
        efectos = habilidad.get("effects") or []
        return bool(efectos) and all(e["effect_type"] == "AddMana" for e in efectos)

    def _puede_activar(self, jugador: Jugador, fuente: Objeto, habilidad: dict[str, Any]) -> bool:
        for coste in habilidad.get("costs") or []:
            tipo = coste.get("cost_type")
            if tipo == "TapCost" and fuente.girada:
                return False
            if tipo == "PayMana":
                if not self.puede_pagar(jugador, parse_mana(coste.get("amount"))):
                    return False
            if tipo == "SacrificeCost":
                pass
        for efecto in habilidad["effects"]:
            if (efecto.get("target_info") or {}).get("requires_target"):
                if not self._objetivos_legales(fuente, efecto):
                    return False
        return True

    def ejecutar_accion(self, jugador: Jugador, accion: dict[str, Any]) -> None:
        self.actor_idx = self._idx(jugador)
        self._dispatch(jugador, accion)
        if self._con_prioridad:
            self._autopasar_completo()

    def _dispatch(self, jugador: Jugador, accion: dict[str, Any]) -> None:
        tipo = accion["tipo"]
        if tipo == "jugar_carta":
            self._iniciar_o_jugar(jugador, accion["carta_id"], x=int(accion.get("x") or 0))
        elif tipo == "elegir_x":
            assert self.decision is not None
            carta_id = self.decision["carta_id"]
            self.decision = None
            self._iniciar_o_jugar(jugador, carta_id, x=int(accion["x"]), x_ya_elegida=True)
        elif tipo in ("pagar_opcional", "omitir_opcional"):
            assert self.decision is not None and self.decision["tipo"] == "offspring"
            carta_id = self.decision["carta_id"]
            x = int(self.decision.get("x") or 0)
            self.decision = None
            self._iniciar_o_jugar(
                jugador, carta_id, x=x, x_ya_elegida=True, offspring=tipo == "pagar_opcional",
            )
        elif tipo == "elegir_objetivo":
            self._elegir_objetivo(jugador, accion)
        elif tipo == "confirmar_objetivos":
            self._cerrar_objetivos_n()
        elif tipo == "aceptar":
            self._responder_eleccion(True)
        elif tipo == "rechazar":
            self._responder_eleccion(False if (self.decision or {}).get("tipo") == "opcional" else None)
        elif tipo == "elegir_scry":
            self._anotar_scry(accion)
        elif tipo == "mantener_objetivos":
            self._mantener_objetivos()
        elif tipo == "activar":
            self._activar(jugador, accion["carta_id"], int(accion["habilidad"]))
        elif tipo == "pasar_fase":
            if self._con_prioridad:
                self._pasar(jugador)
            else:
                self._vaciar_maná()
                self.fase = "Declarar Atacantes"
        elif tipo == "declarar_atacante":
            carta = self.partida.objetos[accion["carta_id"]]
            if not self.partida.tiene_keyword(carta, "Vigilance"):
                carta.girada = True
            carta.atacando = True
            self.atacantes.append(carta.object_id)
            if not self._con_prioridad:
                self.partida.disparar("Attacks", carta)
                self._vaciar_pila()
        elif tipo == "finalizar_ataques":
            if not self._con_prioridad:
                self._vaciar_maná()
                self.bloqueador_pendiente = None
                if self.atacantes:
                    self.fase = "Declarar Bloqueadores"
                else:
                    self._cerrar_turno()
            else:
                self.ataques_confirmados = True
                for attacker_id in list(self.atacantes):
                    atacante = self.partida.objetos[attacker_id]
                    if atacante.zona == "Battlefield":
                        self.partida.disparar("Attacks", atacante)
                self._dar_prioridad(self.indice_activo)
        elif tipo == "declarar_bloqueo":
            self.bloqueos[accion["blocker_id"]] = accion["attacker_id"]
            self.partida.objetos[accion["blocker_id"]].bloqueando = True
        elif tipo == "elegir_bloqueador":
            self.bloqueador_pendiente = accion["blocker_id"]
        elif tipo == "elegir_atacante":
            self.bloqueos[accion["blocker_id"]] = accion["attacker_id"]
            self.partida.objetos[accion["blocker_id"]].bloqueando = True
            self.bloqueador_pendiente = None
        elif tipo == "cancelar_bloqueador":
            self.bloqueador_pendiente = None
        elif tipo == "resolver_combate":
            self.bloqueador_pendiente = None
            if not self._con_prioridad:
                self._resolver_combate()
                self._vaciar_maná()
                self._cerrar_turno()
            else:
                self.bloqueos_confirmados = True
                for blocker_id in list(self.bloqueos):
                    bloqueador = self.partida.objetos.get(blocker_id)
                    if bloqueador is not None and bloqueador.zona == "Battlefield":
                        self.partida.disparar("Blocks", bloqueador)
                self._dar_prioridad(self.indice_activo)
        elif tipo == "descartar":
            carta = self.partida.objetos[accion["carta_id"]]
            if carta.zona != "Hand" or carta.controlador != jugador.id:
                raise RuntimeError("descarte ilegal")
            self.partida.mover(carta, "Graveyard")
            if len(jugador.zonas["Hand"]) <= 7:
                if self._con_prioridad:
                    self._cerrar_limpieza()
                else:
                    self._siguiente_turno()

    def _iniciar_o_jugar(
        self,
        jugador: Jugador,
        carta_id: str,
        x: int = 0,
        *,
        x_ya_elegida: bool = False,
        offspring: bool | None = None,
        puestos: dict[str, str] | None = None,
        indice: int = 0,
    ) -> None:
        carta = self.partida.objetos[carta_id]
        if "Land" in carta.definicion.card_types:
            self._jugar_carta(jugador, carta_id)
            return
        if self.modo_menu == "completo" and not x_ya_elegida:
            if parse_mana(carta.definicion.mana_cost).tiene_x:
                self.decision = {"tipo": "x", "carta_id": carta_id}
                return
        if self.modo_menu == "completo" and offspring is None and self._coste_opcional(carta):
            self.decision = {"tipo": "offspring", "carta_id": carta_id, "x": x}
            return
        puestos = dict(puestos or {})
        if self.modo_menu == "completo":
            ranuras = self._ranuras(carta)
            while indice < len(ranuras) and self._min_objetivos(ranuras[indice]["info"]) == 0:
                indice += 1
            if indice < len(ranuras):
                self.decision = {
                    "tipo": "objetivo",
                    "origen": "hechizo",
                    "carta_id": carta_id,
                    "x": x,
                    "offspring": bool(offspring),
                    "indice": indice,
                    "puestos": puestos,
                }
                return
        objetivos = {clave: self.partida.objetos[oid] for clave, oid in puestos.items()} or None
        self._jugar_carta(jugador, carta_id, x=x, objetivos=objetivos, offspring=bool(offspring))

    def _jugar_carta(
        self,
        jugador: Jugador,
        carta_id: str,
        x: int = 0,
        objetivos: dict[str, Objeto] | None = None,
        offspring: bool = False,
    ) -> None:
        carta = self.partida.objetos[carta_id]
        if "Land" in carta.definicion.card_types:
            self.partida.entrar(carta)
            self.tierras_jugadas += 1
            if self._con_prioridad:
                self._dar_prioridad(self._idx(jugador))
                return
            self._vaciar_pila()
            return
        if objetivos is None:
            objetivos = self._elegir_objetivos(carta)
        if objetivos is None:
            raise RuntimeError(f"sin objetivos legales para {carta.definicion.nombre}")
        coste = self.partida.coste_de_lanzar(carta, x)
        self._pagar(jugador, coste)
        if offspring:
            extra = self._coste_opcional(carta)
            if extra:
                self._pagar(jugador, parse_mana(extra))
            carta.coste_opcional_pagado = True
        self.partida.lanzar(carta, x=x, objetivos=objetivos)
        if self._con_prioridad:
            self._dar_prioridad(self._idx(jugador))
            return
        self._vaciar_pila()

    def _activar(
        self,
        jugador: Jugador,
        carta_id: str,
        orden: int,
        objetivos: dict[str, Objeto] | None = None,
    ) -> None:
        fuente = self.partida.objetos[carta_id]
        habilidad = fuente.definicion.habilidad(orden)
        if objetivos is None and self.modo_menu == "completo":
            for efecto in habilidad["effects"]:
                if (efecto.get("target_info") or {}).get("requires_target"):
                    self.decision = {
                        "tipo": "objetivo",
                        "origen": "activar",
                        "carta_id": carta_id,
                        "habilidad": orden,
                        "efecto": efecto,
                    }
                    return
        if objetivos is None:
            objetivos = {}
            for efecto in habilidad["effects"]:
                if (efecto.get("target_info") or {}).get("requires_target"):
                    legales = self._objetivos_legales(fuente, efecto)
                    elegida = self.partida.elegir("target", legales, self.partida.contexto(fuente))
                    if elegida is None:
                        raise RuntimeError("sin objetivo para activar")
                    objetivos["TARGET_1"] = elegida
                    break
        sacrificar = False
        for coste in habilidad.get("costs") or []:
            tipo = coste.get("cost_type")
            if tipo == "PayMana":
                self._pagar(jugador, parse_mana(coste.get("amount")))
            elif tipo == "TapCost":
                fuente.girada = True
            elif tipo == "SacrificeCost":
                sacrificar = True
        objeto = ObjetoPila(
            object_id=self.partida.nuevo_id("pila"),
            fuente=fuente.ref(),
            habilidad=orden,
            controlador=jugador.id,
            objetivos={k: v.ref() for k, v in objetivos.items()},
        )
        self.partida.pila.append(objeto)
        if sacrificar:
            self.partida.disparar("Sacrifice", fuente)
            self.partida.mover(fuente, "Graveyard")
        if self._con_prioridad:
            self._dar_prioridad(self._idx(jugador))
            return
        self._vaciar_pila()

    def _vaciar_pila(self) -> None:
        self.partida.interceptar = self.modo_menu == "completo"
        for _ in range(80):
            if not self.partida.pila:
                break
            if self._preflight():
                return
            try:
                self.partida.resolver_tope()
            except DecisionRequerida as exc:
                self._abrir_eleccion(exc)
                return
            self._acciones_de_estado()
        self._acciones_de_estado()

    def _preflight(self) -> bool:
        item = self.partida.pila[-1]
        if isinstance(item, Objeto) and getattr(item, "_reetiquetar", False):
            if self.modo_menu == "completo" and self._ranuras(item):
                self._fijar_actor(item.controlador)
                self.decision = {
                    "tipo": "reetiquetar",
                    "carta_id": item.object_id,
                    "indice": 0,
                    "puestos": {},
                }
                return True
            item._reetiquetar = False  # type: ignore[attr-defined]
        if isinstance(item, ObjetoPila) and not item.objetivos_cerrados:
            if self._abrir_objetivos_disparo(item):
                return True
        return False

    def _abrir_objetivos_disparo(self, item: ObjetoPila) -> bool:
        fuente = self.partida.objetos.get(item.fuente.object_id)
        if fuente is None:
            item.objetivos_cerrados = True
            return False
        habilidad = fuente.definicion.habilidad(item.habilidad)
        ranuras = self._ranuras_de_habilidad(habilidad)
        if item.slot_actual >= len(ranuras):
            item.objetivos_cerrados = True
            return False
        ranura = ranuras[item.slot_actual]
        if item.objetivos.get(ranura["clave"]) is not None or item.listas.get(ranura["clave"]):
            item.slot_actual += 1
            if item.slot_actual >= len(ranuras):
                item.objetivos_cerrados = True
                return False
            return self._abrir_objetivos_disparo(item)
        legales = self._objetivos_legales(fuente, ranura["efecto"], ranura["info"])
        mn = self._min_objetivos(ranura["info"])
        mx = self._max_objetivos(ranura["info"], len(legales))
        if self.modo_menu != "completo" or (not legales and mn == 0):
            if self.modo_menu != "completo" and mn > 0 and legales:
                item.listas[ranura["clave"]] = [legales[0].ref()]
                item.objetivos[ranura["clave"]] = legales[0].ref()
            item.slot_actual += 1
            if item.slot_actual >= len(ranuras):
                item.objetivos_cerrados = True
                return False
            return self._abrir_objetivos_disparo(item)
        if not legales:
            item.slot_actual += 1
            if item.slot_actual >= len(ranuras):
                item.objetivos_cerrados = True
            return False
        self._fijar_actor(item.controlador)
        self.decision = {
            "tipo": "objetivos_n",
            "item_id": item.object_id,
            "clave": ranura["clave"],
            "efecto": ranura["efecto"],
            "info": ranura["info"],
            "fuente_id": fuente.object_id,
            "min": mn,
            "max": mx,
            "elegidos": [],
        }
        return True

    def _abrir_eleccion(self, exc: DecisionRequerida) -> None:
        if self.partida.pila:
            self._fijar_actor(self.partida.pila[-1].controlador)
        tipo = exc.tipo
        ids = [o.object_id for o in exc.opciones if isinstance(o, Objeto)]
        if tipo == "optional_effect":
            self.decision = {"tipo": "opcional"}
        elif tipo == "scry":
            self.decision = {"tipo": "scry", "restantes": ids, "top": [], "bottom": []}
        elif tipo == "discard":
            self.decision = {"tipo": "descartar_efecto", "opciones": ids}
        elif tipo == "look":
            self.decision = {"tipo": "look", "opciones": ids}
        elif tipo == "enter_as_copy":
            self.decision = {"tipo": "enter_as_copy", "opciones": ids}
        elif tipo == "move_zone":
            self.decision = {"tipo": "move_zone", "opciones": ids}
        else:
            raise RuntimeError(f"elección no cableada: {tipo}")

    def _responder_eleccion(self, valor: Any) -> None:
        assert self.decision is not None
        tipo_decision = self.decision["tipo"]
        tipo_elegir = {
            "opcional": "optional_effect",
            "scry": "scry",
            "descartar_efecto": "discard",
            "look": "look",
            "enter_as_copy": "enter_as_copy",
            "move_zone": "move_zone",
        }[tipo_decision]
        self.partida.respuesta = valor
        self.partida.respuesta_tipo = tipo_elegir
        self.partida.respuesta_lista = True
        self.decision = None
        self._despues_de_eleccion()

    def _elegir_objetivo(self, jugador: Jugador, accion: dict[str, Any]) -> None:
        assert self.decision is not None
        objetivo = self.partida.objetos[accion["carta_id"]]
        tipo = self.decision["tipo"]
        if tipo == "objetivos_n":
            self.decision["elegidos"].append(objetivo.object_id)
            if len(self.decision["elegidos"]) >= self.decision["max"]:
                self._cerrar_objetivos_n()
            return
        if tipo in ("descartar_efecto", "look", "enter_as_copy", "move_zone"):
            self._responder_eleccion(objetivo)
            return
        if tipo == "reetiquetar":
            self._anotar_reetiqueta(objetivo)
            return
        if tipo == "objetivo" and self.decision.get("origen") == "activar":
            carta_id = self.decision["carta_id"]
            orden = int(self.decision["habilidad"])
            self.decision = None
            self._activar(jugador, carta_id, orden, objetivos={"TARGET_1": objetivo})
            return
        puestos = dict(self.decision["puestos"])
        carta = self.partida.objetos[self.decision["carta_id"]]
        ranura = self._ranuras(carta)[self.decision["indice"]]
        puestos[ranura["clave"]] = objetivo.object_id
        carta_id = self.decision["carta_id"]
        x = int(self.decision.get("x") or 0)
        offspring = bool(self.decision.get("offspring"))
        indice = int(self.decision["indice"]) + 1
        self.decision = None
        self._iniciar_o_jugar(
            jugador,
            carta_id,
            x=x,
            x_ya_elegida=True,
            offspring=offspring,
            puestos=puestos,
            indice=indice,
        )

    def _cerrar_objetivos_n(self) -> None:
        assert self.decision is not None
        item = next(
            entrada for entrada in self.partida.pila
            if isinstance(entrada, ObjetoPila) and entrada.object_id == self.decision["item_id"]
        )
        refs = [self.partida.objetos[oid].ref() for oid in self.decision["elegidos"]]
        item.listas[self.decision["clave"]] = refs
        if refs:
            item.objetivos[self.decision["clave"]] = refs[0]
        item.slot_actual += 1
        if item.slot_actual >= len(self._ranuras_de_habilidad(
            self.partida.objetos[item.fuente.object_id].definicion.habilidad(item.habilidad),
        )):
            item.objetivos_cerrados = True
        self.decision = None
        self._despues_de_eleccion()

    def _anotar_scry(self, accion: dict[str, Any]) -> None:
        assert self.decision is not None
        object_id = accion["carta_id"]
        self.decision["restantes"].remove(object_id)
        self.decision[accion["destino"]].append(object_id)
        if self.decision["restantes"]:
            return
        arriba = [self.partida.objetos[oid] for oid in self.decision["top"]]
        fondo = [self.partida.objetos[oid] for oid in self.decision["bottom"]]
        self._responder_eleccion({"top": arriba, "bottom": fondo})

    def _mantener_objetivos(self) -> None:
        assert self.decision is not None
        carta = self.partida.objetos[self.decision["carta_id"]]
        carta._reetiquetar = False  # type: ignore[attr-defined]
        self.decision = None
        self._despues_de_eleccion()

    def _anotar_reetiqueta(self, objetivo: Objeto) -> None:
        assert self.decision is not None
        carta = self.partida.objetos[self.decision["carta_id"]]
        ranuras = self._ranuras(carta)
        ranura = ranuras[self.decision["indice"]]
        self.decision["puestos"][ranura["clave"]] = objetivo.object_id
        self.decision["indice"] += 1
        if self.decision["indice"] < len(ranuras):
            return
        carta._objetivos_de_lanzamiento = {  # type: ignore[attr-defined]
            clave: self.partida.objetos[oid].ref()
            for clave, oid in self.decision["puestos"].items()
        }
        carta._reetiquetar = False  # type: ignore[attr-defined]
        self.decision = None
        self._despues_de_eleccion()

    def _vaciar_maná(self) -> None:
        for jugador in self.jugadores:
            jugador.mana.clear()

    def _pagar(self, jugador: Jugador, coste: Coste) -> None:
        if not self.puede_pagar(jugador, coste):
            raise RuntimeError("no hay maná suficiente")
        for pip, cantidad in coste.colores.items():
            reserva = _PIP_A_RESERVA[pip]
            for _ in range(cantidad):
                tierra = self._elegir_tierra(jugador, reserva)
                self._activar_mana(tierra, reserva)
                self._gastar(jugador, reserva)
        for _ in range(coste.generico):
            tierra = self._elegir_tierra(jugador, None)
            color = self._colores_de_tierra(tierra)[0]
            self._activar_mana(tierra, color)
            self._gastar(jugador, None)
        jugador.mana.clear()

    def _elegir_tierra(self, jugador: Jugador, color: str | None) -> Objeto:
        for tierra in self._tierras_enderezadas(jugador):
            colores = self._colores_de_tierra(tierra)
            if not colores:
                continue
            if color is None or color in colores:
                return tierra
        raise RuntimeError("no hay tierra adecuada")

    def _activar_mana(self, tierra: Objeto, color: str) -> None:
        tierra.girada = True
        for habilidad in tierra.definicion.abilities:
            if habilidad["ability_type"] != "ActivatedAbility":
                continue
            costes = habilidad.get("costs") or []
            if not any(c.get("cost_type") == "TapCost" for c in costes):
                continue
            ctx = self.partida.contexto(tierra)
            modos = [e for e in habilidad["effects"] if e["effect_type"] == "AddMana"]
            elegidos = [e for e in modos if e["parameters"].get("string_value") == color]
            for efecto in elegidos or modos[:1]:
                ejecutar(ctx, efecto)
            return
        raise RuntimeError(f"{tierra.definicion.nombre} no produce maná")

    def _gastar(self, jugador: Jugador, color: str | None) -> None:
        if color is not None:
            if jugador.mana.get(color, 0) <= 0:
                raise RuntimeError(f"falta {color} en la reserva")
            jugador.mana[color] -= 1
            if jugador.mana[color] == 0:
                del jugador.mana[color]
            return
        for nombre, cantidad in list(jugador.mana.items()):
            if cantidad > 0:
                jugador.mana[nombre] = cantidad - 1
                if jugador.mana[nombre] == 0:
                    del jugador.mana[nombre]
                return
        raise RuntimeError("la reserva está vacía")

    def _tiene_first_strike(self, carta: Objeto) -> bool:
        return self.partida.tiene_keyword(carta, "FirstStrike") or self.partida.tiene_keyword(
            carta, "DoubleStrike",
        )

    def _golpea_en_paso(self, carta: Objeto, paso_first: bool) -> bool:
        primero = self._tiene_first_strike(carta)
        doble = self.partida.tiene_keyword(carta, "DoubleStrike")
        if paso_first:
            return primero
        if doble:
            return True
        return not primero

    def _danio_jugador(
        self, fuente: Objeto, jugador: Jugador, n: int, *, resolver: bool = True,
    ) -> None:
        if n <= 0:
            return
        jugador.vida -= n
        representante = self.partida.representantes[jugador.id]
        self.partida.disparar("DealsDamage", representante, solo_fuente=fuente)
        if resolver:
            self._vaciar_pila()

    def _resolver_combate(self) -> None:
        participantes = [
            self.partida.objetos[oid]
            for oid in list(self.atacantes) + list(self.bloqueos)
            if oid in self.partida.objetos
        ]
        if any(self._tiene_first_strike(carta) for carta in participantes):
            self._paso_danio(True)
            self._acciones_de_estado()
        self._paso_danio(False)
        self._acciones_de_estado()

    def _paso_danio(self, paso_first: bool, *, resolver: bool = True) -> None:
        defensor = self.defensor()
        for attacker_id in list(self.atacantes):
            atacante = self.partida.objetos[attacker_id]
            if atacante.zona != "Battlefield":
                continue
            blocker_id = next((b for b, a in self.bloqueos.items() if a == attacker_id), None)
            bloqueador = self.partida.objetos[blocker_id] if blocker_id else None
            if bloqueador is not None and bloqueador.zona != "Battlefield":
                bloqueador = None
            if bloqueador is None:
                if self._golpea_en_paso(atacante, paso_first):
                    self._danio_jugador(
                        atacante, defensor, self.partida.poder(atacante), resolver=resolver,
                    )
                continue
            poder_atq = self.partida.poder(atacante)
            res_blk = self.partida.resistencia(bloqueador)
            if self._golpea_en_paso(atacante, paso_first):
                bloqueador.danio += poder_atq
                if self.partida.tiene_keyword(atacante, "Trample") and poder_atq > res_blk:
                    self._danio_jugador(
                        atacante, defensor, poder_atq - res_blk, resolver=resolver,
                    )
            if self._golpea_en_paso(bloqueador, paso_first):
                atacante.danio += self.partida.poder(bloqueador)

    def _es_aura(self, carta: Objeto) -> bool:
        return "Aura" in carta.definicion.subtypes or (
            "Enchantment" in carta.definicion.card_types
            and "Enchant" in carta.definicion.keywords
        )

    def _es_equipo(self, carta: Objeto) -> bool:
        return "Equipment" in carta.definicion.subtypes or "Equip" in carta.definicion.keywords

    def _anexo_legal(self, carta: Objeto) -> bool:
        vivo = self.partida.objeto_vivo(carta.anexada_a)
        return (
            vivo is not None
            and vivo.zona == "Battlefield"
            and "Creature" in vivo.definicion.card_types
        )

    def _soltar_anexos(self) -> bool:
        cambio = False
        for jugador in self.jugadores:
            for object_id in list(jugador.zonas["Battlefield"]):
                carta = self.partida.objetos[object_id]
                if self._es_aura(carta) and not self._anexo_legal(carta):
                    self.partida.mover(carta, "Graveyard")
                    cambio = True
                    continue
                if self._es_equipo(carta) and carta.anexada_a is not None and not self._anexo_legal(carta):
                    carta.anexada_a = None
                    cambio = True
        return cambio

    def _acciones_de_estado(self) -> None:
        for _ in range(8):
            cambio = False
            for jugador in self.jugadores:
                if jugador.vida <= 0:
                    continue
                for object_id in list(jugador.zonas["Battlefield"]):
                    carta = self.partida.objetos[object_id]
                    if "Creature" not in carta.definicion.card_types:
                        continue
                    if self.partida.resistencia(carta) <= 0 or carta.danio >= self.partida.resistencia(carta):
                        self.partida.mover(carta, "Graveyard")
                        cambio = True
            if self._soltar_anexos():
                cambio = True
            if not cambio:
                return

    def _cerrar_turno(self) -> None:
        """Limpieza: si la mano supera 7, descartar; si no, pasar el turno."""
        activo = self.jugador_activo()
        if len(activo.zonas["Hand"]) > 7:
            self.fase = "Descartar"
            self.modo_decision = "menu"
            return
        self._siguiente_turno()

    def _siguiente_turno(self) -> None:
        activo = self.jugador_activo()
        self.partida.limpiar_fin_de_turno(activo.id)
        self._vaciar_maná()
        self.indice_activo = 1 - self.indice_activo
        self.atacantes = []
        self.bloqueos = {}
        self.bloqueador_pendiente = None
        self.ataques_confirmados = False
        self.bloqueos_confirmados = False
        self.tierras_jugadas = 0
        self.segunda_principal = False
        self.danio_fase = "ninguno"
        self.modo_decision = "menu"
        jugador = self.jugador_activo()
        for object_id in jugador.zonas["Battlefield"]:
            carta = self.partida.objetos[object_id]
            if self.partida.impide_enderezar(carta):
                continue
            carta.girada = False
            carta.danio = 0
            carta.entro_este_turno = False
        if not self._con_prioridad:
            self.fase = "Principal"
            self._robar(jugador, 1)
            return
        self.fase = "Mantenimiento"
        self._dar_prioridad(self.indice_activo)

    def _robar(self, jugador: Jugador, n: int) -> None:
        for _ in range(n):
            if not jugador.zonas["Library"]:
                jugador.vida = 0
                return
            self.partida.robar(jugador, 1)

    def terminada(self) -> bool:
        return any(j.vida <= 0 for j in self.jugadores)

    def paso_aleatorio(self) -> None:
        try:
            acciones = self.acciones_legales()
        except DemasiadasAcciones:
            if self.fase == "Declarar Bloqueadores":
                self.ejecutar_accion(self.defensor(), {"tipo": "resolver_combate"})
                return
            raise
        if not acciones or self.terminada():
            return
        self.ejecutar_accion(self.jugadores[self.quien_actua()], random.choice(acciones))


def mazo_prueba(tierras: int = 20, criaturas: int = 20) -> list[Definicion]:
    """Mazo plano equivalente al de motor_prueba: Forest + Oso 2/2 de coste 2."""
    forest = catalogo(_JSONLD)["Forest"]
    oso = criatura("Oso", 2, 2, mana_cost="{2}")
    return [forest] * tierras + [oso] * criaturas


def mazo_starter(mazo_id: str) -> list[Definicion]:
    indice = json.loads(_INDICE.read_text(encoding="utf-8"))
    cartas = catalogo(_JSONLD)
    for mazo in indice["mazos"]:
        if mazo["id"] != mazo_id:
            continue
        resultado: list[Definicion] = []
        for entrada in mazo["cartas"]:
            definicion = cartas[entrada["nombre"]]
            resultado.extend([definicion] * int(entrada["cantidad"]))
        return resultado
    raise KeyError(mazo_id)
