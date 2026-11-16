"""Mesa localhost del starter para el cliente Godot.

El motor no se reescribe. Godot pide el estado y devuelve el índice de una
acción que ya devolvió el adaptador. Si el asiento de la IA tiene la prioridad,
elige y ejecuta antes de responder: con checkpoint de la observación 198629
usa load_starter_policy y act_from_obs; sin checkpoint, o si el archivo es de
la observación 198627 o de global 41, juega una legal al azar.

Arranque, desde motor_prueba:

    python mesa_api.py --env starter --human-seat p0 --ckpt ruta

La escena está en mesa/escenas/Mesa.tscn.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np

_MOTOR = Path(__file__).resolve().parent
_RAIZ = _MOTOR.parent
if str(_MOTOR) not in sys.path:
    sys.path.insert(0, str(_MOTOR))
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from env_starter import MagicStarterEnv  # noqa: E402

OBS_REQUERIDA = 198629
_N_SLOTS = 54
_N_ACCIONES = 16
_CARTAS = _RAIZ / "mesa" / "cartas"
_COLORES = ("WHITE", "BLUE", "BLACK", "RED", "GREEN", "COLORLESS")
_CAMPOS_ACCION = (
    "tipo",
    "descripcion",
    "carta_id",
    "blocker_id",
    "attacker_id",
    "habilidad",
    "x",
    "destino",
)


def _rechazo(detalle: str) -> str:
    return (
        f"Checkpoint rechazado: {detalle}. "
        "Esta mesa exige la observación 198629. El rival juega al azar."
    )


def _leer_ckpt(torch: Any, ruta: Path) -> Any:
    try:
        return torch.load(str(ruta), map_location="cpu", weights_only=True)
    except TypeError:
        return torch.load(str(ruta), map_location="cpu")
    except Exception:
        try:
            return torch.load(str(ruta), map_location="cpu", weights_only=False)
        except TypeError:
            return torch.load(str(ruta), map_location="cpu")


def _tensores(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    policy = data.get("policy")
    if isinstance(policy, dict):
        return policy
    return {k: v for k, v in data.items() if hasattr(v, "shape")}


def _entrada(tensores: dict[str, Any], sufijo: str) -> int | None:
    for clave, tensor in tensores.items():
        if str(clave).endswith(sufijo) and getattr(tensor, "ndim", 0) == 2:
            return int(tensor.shape[1])
    return None


def _obs_de_pesos(tensores: dict[str, Any]) -> tuple[int | None, int | None]:
    """Devuelve (obs calculada, global) a partir de las capas de la ficha."""
    ficha = _entrada(tensores, "torso.net.0.weight")
    global_dim = _entrada(tensores, "global_net.0.weight")
    accion_in = _entrada(tensores, "action_net.0.weight")
    if ficha is None or global_dim is None or accion_in is None:
        return None, global_dim
    tipo_accion = accion_in - 256
    if tipo_accion <= 0:
        return None, global_dim
    obs = _N_SLOTS * ficha + global_dim + _N_ACCIONES * (tipo_accion + 2)
    return obs, global_dim


def validar_checkpoint(path: str) -> tuple[bool, str]:
    """Acepta solo un checkpoint cuya red es la observación 198629.

    Los de observación 198627 y los de global 41 se rechazan con un mensaje
    explícito. No carga la política: el llamador decide el respaldo.
    """
    ruta = Path(path)
    if not ruta.is_file():
        return False, _rechazo(f"no existe {ruta}")
    try:
        import torch
    except Exception as exc:  # pragma: no cover - entorno sin torch
        return False, _rechazo(f"PyTorch no está disponible ({exc})")
    try:
        data = _leer_ckpt(torch, ruta)
    except Exception as exc:
        return False, _rechazo(f"no se pudo leer ({exc})")

    obs_guardada: int | None = None
    if isinstance(data, dict) and data.get("obs_dim") is not None:
        try:
            obs_guardada = int(data["obs_dim"])
        except (TypeError, ValueError):
            obs_guardada = None
    obs_pesos, global_dim = _obs_de_pesos(_tensores(data))

    razones: list[str] = []
    if global_dim == 41:
        razones.append("global 41")
    if obs_guardada == 198627 or obs_pesos == 198627:
        razones.append("observación 198627")
    if razones:
        return False, _rechazo(", ".join(razones))
    if obs_pesos is not None and obs_pesos != OBS_REQUERIDA:
        return False, _rechazo(f"la observación de los pesos es {obs_pesos}")
    if obs_guardada is not None and obs_guardada != OBS_REQUERIDA:
        return False, _rechazo(f"obs_dim guardada es {obs_guardada}")
    if obs_pesos is None and obs_guardada is None:
        return False, _rechazo("no trae obs_dim ni el tamaño de la red de fichas")
    return True, ""


class Mesa:
    """Una partida del starter. La IA resuelve su prioridad antes de cada respuesta."""

    def __init__(
        self,
        *,
        human_seat: str = "p0",
        ckpt: str = "",
        seed: int | None = None,
        max_steps: int = 800,
        stochastic: bool = False,
    ) -> None:
        if human_seat not in ("p0", "p1"):
            raise ValueError("human-seat debe ser p0 o p1")
        self.human = "player_0" if human_seat == "p0" else "player_1"
        self.human_seat = human_seat
        self.max_steps = max_steps
        self.ckpt = ckpt
        self.policy = None
        self.rival = "aleatorio"
        self.aviso = ""
        self.historial: list[dict[str, Any]] = []
        self.eventos: list[dict[str, Any]] = []
        self._pasos = 0
        self._lock = threading.Lock()
        self.env = MagicStarterEnv(verbose=False, max_episode_steps=max_steps)
        self._cargar_politica(ckpt, stochastic=stochastic)
        if seed is None:
            self.env.reset()
        else:
            random.seed(seed)
            np.random.seed(seed)
            try:
                import torch
                torch.manual_seed(seed)
            except Exception:
                pass
            self.env.reset(seed=seed)

    def _cargar_politica(self, ckpt: str, *, stochastic: bool) -> None:
        if not ckpt:
            print("sin --ckpt: el rival juega al azar")
            return
        ok, mensaje = validar_checkpoint(ckpt)
        if not ok:
            self.aviso = mensaje
            print(mensaje)
            return
        try:
            from play_adaptador import load_starter_policy
            self.policy = load_starter_policy(ckpt, stochastic=stochastic)
        except Exception as exc:
            self.aviso = _rechazo(f"no entra en la red ({exc})")
            self.policy = None
            print(self.aviso)
            return
        self.rival = "checkpoint"
        print(f"checkpoint cargado ({ckpt}), observación {OBS_REQUERIDA}")

    def estado(self) -> dict[str, Any]:
        with self._lock:
            self._resolver_ia()
            return self._serializar()

    def actuar(self, indice: int) -> dict[str, Any]:
        with self._lock:
            self._resolver_ia()
            if not self._turno_humano():
                raise IndexError("ahora no tienes la prioridad")
            legales = self.env.current_legal_actions
            if indice < 0 or indice >= len(legales):
                raise IndexError(f"índice {indice} fuera de 0..{len(legales) - 1}")
            self._jugar(indice)
            self._resolver_ia()
            return self._serializar()

    def _turno_humano(self) -> bool:
        return self._en_juego() and self.env.agent_selection == self.human

    def _acabada(self) -> bool:
        if self.env.adaptador.terminada():
            return True
        return any(self.env.terminations.values()) or any(self.env.truncations.values())

    def _en_juego(self) -> bool:
        if self._acabada():
            return False
        if not self.env.agents or self.env.agent_selection is None:
            return False
        return bool(self.env.current_legal_actions)

    def _resolver_ia(self) -> None:
        while self._en_juego() and self.env.agent_selection != self.human:
            if self._pasos >= self.max_steps:
                self.env.truncations = {a: True for a in self.env.agents}
                break
            self._jugar(self._elegir_ia())

    def _elegir_ia(self) -> int:
        agent = self.env.agent_selection
        raw = self.env.observe(agent)
        mask = np.asarray(raw["action_mask"], dtype=np.float32)
        if self.policy is not None:
            from match_checkpoints import act_from_obs
            return int(act_from_obs(self.policy, {"obs": raw["observation"], "mask": mask}))
        return int(np.random.choice(np.flatnonzero(mask)))

    def _jugar(self, indice: int) -> None:
        agent = self.env.agent_selection
        legales = self.env.current_legal_actions
        accion = legales[indice] if 0 <= indice < len(legales) else {"tipo": "", "descripcion": str(indice)}
        antes = self._foto()
        self.env.step(indice)
        self._pasos += 1
        self.eventos.extend(self._diff(antes, accion))
        self.historial.append({
            "jugador": "Tú" if agent == self.human else "Rival",
            "asiento": "p0" if agent == "player_0" else "p1",
            "tipo": accion.get("tipo", ""),
            "descripcion": str(accion.get("descripcion", "")),
        })

    def _foto(self) -> dict[str, Any]:
        ad = self.env.adaptador
        zonas: dict[str, dict[str, list[str]]] = {}
        vidas: dict[str, int] = {}
        for i, jugador in enumerate(ad.jugadores):
            clave = f"player_{i}"
            zonas[clave] = {
                zona: list(jugador.zonas[zona])
                for zona in ("Library", "Hand", "Battlefield", "Graveyard", "Exile")
            }
            vidas[clave] = jugador.vida
        pila = [item.object_id for item in ad.partida.pila]
        return {
            "zonas": zonas,
            "vidas": vidas,
            "pila": pila,
            "danio": {oid: obj.danio for oid, obj in ad.partida.objetos.items()},
            "girada": {oid: obj.girada for oid, obj in ad.partida.objetos.items()},
            "nombres": {oid: obj.definicion.nombre for oid, obj in ad.partida.objetos.items()},
        }

    def _diff(self, antes: dict[str, Any], accion: dict[str, Any]) -> list[dict[str, Any]]:
        despues = self._foto()
        eventos: list[dict[str, Any]] = []
        manos_antes: set[str] = set()
        for clave, zonas in antes["zonas"].items():
            manos_antes.update(zonas["Hand"])
            biblioteca = set(zonas["Library"])
            for oid in despues["zonas"][clave]["Hand"]:
                if oid not in set(zonas["Hand"]) and oid in biblioteca:
                    evento: dict[str, Any] = {
                        "tipo": "robo",
                        "asiento": self._lado(clave),
                        "carta_id": oid,
                    }
                    if clave == self.human:
                        evento["nombre"] = despues["nombres"].get(oid, "")
                    eventos.append(evento)
        lanzadas: set[str] = set()
        for oid in despues["pila"]:
            if oid in manos_antes and oid not in antes["pila"]:
                eventos.append(self._evento_carta("lanzar", oid, despues))
                lanzadas.add(oid)
        if accion.get("tipo") == "jugar_carta":
            cid = accion.get("carta_id")
            if cid and cid not in lanzadas and cid in manos_antes:
                sigue = any(cid in zonas["Hand"] for zonas in despues["zonas"].values())
                if not sigue:
                    obj = self.env.adaptador.partida.objetos.get(cid)
                    if obj is not None and "Land" in obj.definicion.card_types:
                        eventos.append(self._evento_carta("jugar_tierra", cid, despues))
                    elif obj is not None:
                        evento = self._evento_carta("lanzar", cid, despues)
                        evento["resuelto"] = True
                        eventos.append(evento)
        for clave, vida in despues["vidas"].items():
            delta = antes["vidas"][clave] - vida
            if delta > 0:
                eventos.append({
                    "tipo": "dano",
                    "asiento": self._lado(clave),
                    "cantidad": int(delta),
                    "vida": int(vida),
                })
        for oid, obj in self.env.adaptador.partida.objetos.items():
            previo = antes["danio"].get(oid, 0)
            if obj.danio > previo:
                eventos.append({
                    "tipo": "dano",
                    "carta_id": oid,
                    "nombre": obj.definicion.nombre,
                    "cantidad": int(obj.danio - previo),
                })
        for oid, girada in despues["girada"].items():
            if girada and not antes["girada"].get(oid, False):
                eventos.append(self._evento_carta("girar", oid, despues))
        return eventos

    def _evento_carta(self, tipo: str, oid: str, foto: dict[str, Any]) -> dict[str, Any]:
        return {
            "tipo": tipo,
            "carta_id": oid,
            "nombre": foto["nombres"].get(oid, ""),
            "asiento": self._dueno(oid, foto),
        }

    def _dueno(self, oid: str, foto: dict[str, Any]) -> str:
        for clave, zonas in foto["zonas"].items():
            for lista in zonas.values():
                if oid in lista:
                    return self._lado(clave)
        obj = self.env.adaptador.partida.objetos.get(oid)
        if obj is None:
            return "tu"
        for i, jugador in enumerate(self.env.adaptador.jugadores):
            if jugador.id == obj.controlador:
                return self._lado(f"player_{i}")
        return "tu"

    def _lado(self, agent: str) -> str:
        return "tu" if agent == self.human else "rival"

    def _serializar(self) -> dict[str, Any]:
        ad = self.env.adaptador
        eventos = self.eventos
        self.eventos = []
        acabada = self._acabada()
        agente = self.env.agent_selection
        if acabada or agente is None:
            prioridad = None
            prioridad_nombre = ""
        else:
            prioridad = self._lado(agente)
            prioridad_nombre = "Tú" if prioridad == "tu" else "Rival"
        acciones = [] if acabada else [
            self._accion_json(i, accion)
            for i, accion in enumerate(self.env.current_legal_actions)
        ]
        fase = ad.fase
        if fase == "Principal" and ad.segunda_principal:
            fase_visible = "Segunda principal"
        else:
            fase_visible = fase
        return {
            "terminada": acabada,
            "fase": fase,
            "fase_visible": fase_visible,
            "segunda_principal": bool(ad.segunda_principal),
            "modo": ad.modo_decision,
            "prioridad": prioridad,
            "prioridad_asiento": None if agente is None else ("p0" if agente == "player_0" else "p1"),
            "prioridad_nombre": prioridad_nombre,
            "tu": self._jugador_json(0 if self.human == "player_0" else 1, boca_abajo=False),
            "rival": self._jugador_json(1 if self.human == "player_0" else 0, boca_abajo=True),
            "pila": self._pila_json(),
            "historial": list(self.historial),
            "acciones": acciones,
            "eventos": eventos,
            "rival_politica": self.rival,
            "aviso": self.aviso,
            "cartas_dir": str(_CARTAS),
            "pasos": self._pasos,
        }

    def _jugador_json(self, indice: int, *, boca_abajo: bool) -> dict[str, Any]:
        ad = self.env.adaptador
        jugador = ad.jugadores[indice]
        asiento = "p0" if indice == 0 else "p1"
        mano = [
            self._carta_json(oid, boca_abajo=boca_abajo)
            for oid in jugador.zonas["Hand"]
        ]
        return {
            "asiento": asiento,
            "nombre": "Tú" if f"player_{indice}" == self.human else "Rival",
            "mazo_nombre": "Hare Raising" if indice == 0 else "Otter Limits",
            "vida": jugador.vida,
            "mana": {color: int(jugador.mana.get(color, 0)) for color in _COLORES},
            "mana_tierras": self._tierras_libres(jugador),
            "mano": mano,
            "mano_cantidad": len(jugador.zonas["Hand"]),
            "campo": [self._carta_json(oid, boca_abajo=False) for oid in jugador.zonas["Battlefield"]],
            "cementerio": [
                self._carta_json(oid, boca_abajo=False)
                for oid in reversed(jugador.zonas["Graveyard"])
            ],
            "exilio": [
                self._carta_json(oid, boca_abajo=False)
                for oid in reversed(jugador.zonas["Exile"])
            ],
            "mazo": len(jugador.zonas["Library"]),
        }

    def _tierras_libres(self, jugador: Any) -> list[dict[str, Any]]:
        ad = self.env.adaptador
        libres = []
        for oid in jugador.zonas["Battlefield"]:
            carta = ad.partida.objetos[oid]
            if "Land" not in carta.definicion.card_types or carta.girada:
                continue
            colores = ad._colores_de_tierra(carta) or ["COLORLESS"]
            libres.append({
                "id": oid,
                "nombre": carta.definicion.nombre,
                "colores": list(colores),
            })
        return libres

    def _carta_json(self, oid: str, *, boca_abajo: bool) -> dict[str, Any]:
        if boca_abajo:
            return {"id": oid, "boca_abajo": True, "nombre": "", "foto": ""}
        ad = self.env.adaptador
        carta = ad.partida.objetos[oid]
        criatura = "Creature" in carta.definicion.card_types
        ancla = None
        ancla_nombre = None
        if carta.anexada_a is not None:
            ancla = carta.anexada_a.object_id
            vivo = ad.partida.objetos.get(ancla)
            ancla_nombre = vivo.definicion.nombre if vivo is not None else ancla
        return {
            "id": oid,
            "nombre": carta.definicion.nombre,
            "boca_abajo": False,
            "foto": f"{carta.definicion.nombre}.jpg",
            "tipos": list(carta.definicion.card_types),
            "subtipos": list(carta.definicion.subtypes),
            "girada": bool(carta.girada),
            "atacando": bool(carta.atacando),
            "bloqueando": bool(carta.bloqueando),
            "anexada_a": ancla,
            "anexada_a_nombre": ancla_nombre,
            "poder": ad.partida.poder(carta) if criatura else None,
            "resistencia": ad.partida.resistencia(carta) if criatura else None,
            "es_ficha": bool(carta.es_ficha),
            "danio": int(carta.danio),
        }

    def _pila_json(self) -> list[dict[str, Any]]:
        ad = self.env.adaptador
        items = []
        for item in ad.partida.pila:
            if hasattr(item, "definicion"):
                nombre = item.definicion.nombre
                items.append({
                    "id": item.object_id,
                    "nombre": nombre,
                    "foto": f"{nombre}.jpg",
                    "controlador": self._lado_de_id(item.controlador),
                    "es_habilidad": False,
                })
                continue
            fuente = ad.partida.objetos.get(item.fuente.object_id)
            nombre = fuente.definicion.nombre if fuente is not None else "Habilidad"
            items.append({
                "id": item.object_id,
                "nombre": f"{nombre} (habilidad)",
                "foto": f"{nombre}.jpg" if fuente is not None else "",
                "controlador": self._lado_de_id(item.controlador),
                "es_habilidad": True,
                "fuente_id": item.fuente.object_id,
            })
        return items

    def _lado_de_id(self, jugador_id: str) -> str:
        for i, jugador in enumerate(self.env.adaptador.jugadores):
            if jugador.id == jugador_id:
                return self._lado(f"player_{i}")
        return "tu"

    def _accion_json(self, indice: int, accion: dict[str, Any]) -> dict[str, Any]:
        salida: dict[str, Any] = {"indice": indice}
        for clave in _CAMPOS_ACCION:
            if clave not in accion:
                continue
            valor = accion[clave]
            if isinstance(valor, (str, int, float, bool)) or valor is None:
                salida[clave] = valor
        return salida


def servir(mesa: Mesa, host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            ruta = urlparse(self.path).path
            if ruta == "/estado":
                self._json(200, mesa.estado())
            elif ruta == "/salud":
                self._json(200, {"ok": True, "rival": mesa.rival})
            else:
                self._json(404, {"error": "no existe"})

        def do_POST(self) -> None:  # noqa: N802
            ruta = urlparse(self.path).path
            if ruta != "/accion":
                self._json(404, {"error": "no existe"})
                return
            largo = int(self.headers.get("Content-Length", "0") or 0)
            bruto = self.rfile.read(largo) if largo else b"{}"
            try:
                cuerpo = json.loads(bruto.decode("utf-8") or "{}")
                indice = int(cuerpo["indice"])
            except (ValueError, KeyError, json.JSONDecodeError):
                self._json(400, {"error": 'el cuerpo debe ser {"indice": n}'})
                return
            try:
                self._json(200, mesa.actuar(indice))
            except IndexError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:
            print("mesa", fmt % args)

        def _json(self, code: int, obj: dict[str, Any]) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"mesa en http://{host}:{port}  (GET /estado, POST /accion)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("mesa cerrada")
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mesa localhost del starter para Godot")
    parser.add_argument("--env", choices=("starter",), default="starter")
    parser.add_argument("--human-seat", choices=("p0", "p1"), default="p0")
    parser.add_argument("--ckpt", default="", help="Checkpoint del rival. Vacío = aleatorio")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--max-steps", type=int, default=800)
    parser.add_argument("--stochastic", action="store_true")
    args = parser.parse_args()
    if args.env != "starter":
        raise SystemExit("mesa_api solo abre el starter. El puente Forest+Oso no entra en esta vista.")
    mesa = Mesa(
        human_seat=args.human_seat,
        ckpt=args.ckpt,
        seed=args.seed,
        max_steps=args.max_steps,
        stochastic=args.stochastic,
    )
    print(f"asiento humano {args.human_seat}  rival {mesa.rival}")
    servir(mesa, args.host, args.port)


if __name__ == "__main__":
    main()
