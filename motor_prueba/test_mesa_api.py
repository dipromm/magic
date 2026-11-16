"""Ejercita mesa_api: orden de zonas, checkpoints y una partida contra aleatorio.

Recorre una principal, un combate y una ventana de prioridad. Cementerio y
exilio van en el JSON, el de arriba primero. No toca reglas/.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_MOTOR = Path(__file__).resolve().parent
_RAIZ = _MOTOR.parent
if str(_MOTOR) not in sys.path:
    sys.path.insert(0, str(_MOTOR))
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from mesa_api import Mesa, validar_checkpoint  # noqa: E402

_COMBATE = {
    "Declarar Atacantes",
    "Declarar Bloqueadores",
    "Daño de combate",
    "Inicio del combate",
    "Fin del combate",
}
_PREFERENCIA = (
    "jugar_carta",
    "declarar_atacante",
    "elegir_bloqueador",
    "elegir_atacante",
    "activar",
    "elegir_objetivo",
    "elegir_scry",
    "elegir_x",
    "descartar",
    "pagar_opcional",
    "aceptar",
    "finalizar_ataques",
    "resolver_combate",
    "pasar_fase",
    "omitir_opcional",
    "rechazar",
    "confirmar_objetivos",
    "cancelar_bloqueador",
    "mantener_objetivos",
)


def _elegir(acciones: list[dict]) -> dict:
    for tipo in _PREFERENCIA:
        for accion in acciones:
            if accion.get("tipo") != tipo:
                continue
            if tipo == "elegir_scry" and accion.get("destino") != "top":
                continue
            return accion
    return acciones[0]


def _orden_de_zonas() -> None:
    mesa = Mesa(human_seat="p0", seed=1)
    adaptador = mesa.env.adaptador
    jugador = adaptador.jugadores[0]
    mano = list(jugador.zonas["Hand"])
    assert len(mano) >= 4, len(mano)
    for oid in mano[:2]:
        adaptador.partida.mover(adaptador.partida.objetos[oid], "Graveyard")
    for oid in mano[2:4]:
        adaptador.partida.mover(adaptador.partida.objetos[oid], "Exile")
    estado = mesa._serializar()
    for lado in ("tu", "rival"):
        assert isinstance(estado[lado]["cementerio"], list)
        assert isinstance(estado[lado]["exilio"], list)
        assert "mano_cantidad" in estado[lado]
    cementerio = [carta["id"] for carta in estado["tu"]["cementerio"]]
    exilio = [carta["id"] for carta in estado["tu"]["exilio"]]
    assert cementerio == [mano[1], mano[0]], cementerio
    assert exilio == [mano[3], mano[2]], exilio
    assert estado["tu"]["cementerio"][0]["boca_abajo"] is False
    assert estado["rival"]["mano_cantidad"] == len(estado["rival"]["mano"])
    assert all(carta.get("boca_abajo") for carta in estado["rival"]["mano"])
    assert estado["rival"]["mano"][0].get("nombre", "") == ""
    print("ok orden cementerio y exilio, mano rival boca abajo")


def _checkpoints(tmp: Path) -> None:
    import torch
    from play_adaptador import _build_policy_starter
    from red_fichas import build_actor_critic
    from train_modes import save_magic_checkpoint

    malo = tmp / "obs_198627.pth"
    torch.save({"obs_dim": 198627, "policy": {}}, malo)
    ok, mensaje = validar_checkpoint(str(malo))
    assert not ok and "198627" in mensaje and "198629" in mensaje, mensaje

    antiguo = tmp / "global41.pth"
    torch.save({
        "policy": {
            "actor.global_net.0.weight": torch.zeros(64, 41),
            "actor.torso.net.0.weight": torch.zeros(256, 3671),
            "actor.action_net.0.weight": torch.zeros(128, 276),
        }
    }, antiguo)
    ok, mensaje = validar_checkpoint(str(antiguo))
    assert not ok and "global 41" in mensaje and "198629" in mensaje, mensaje

    puente = tmp / "obs_124.pth"
    torch.save({"obs_dim": 124}, puente)
    ok, mensaje = validar_checkpoint(str(puente))
    assert not ok and "124" in mensaje and "198629" in mensaje, mensaje

    ok, mensaje = validar_checkpoint(str(tmp / "no_esta.pth"))
    assert not ok and "198629" in mensaje

    actor, critico, _torso = build_actor_critic()
    policy = _build_policy_starter()
    # El crítico no se carga en la mesa; solo hace falta un state_dict compatible.
    del actor
    bueno = tmp / "obs_198629.pth"
    save_magic_checkpoint(bueno, policy, critico)
    data = torch.load(bueno, map_location="cpu", weights_only=False)
    data["obs_dim"] = 198629
    data["env"] = "starter"
    torch.save(data, bueno)
    ok, mensaje = validar_checkpoint(str(bueno))
    assert ok, mensaje
    mesa = Mesa(human_seat="p0", ckpt=str(bueno), seed=2, max_steps=30)
    assert mesa.rival == "checkpoint", mesa.aviso
    assert mesa.aviso == ""
    print("ok checkpoint 198629 carga; 198627, global 41 y 124 se rechazan")


def _puerto() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _leer(url: str, cuerpo: dict | None = None) -> tuple[int, dict]:
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(url, data=datos, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _arrancar(port: int, extra: list[str]) -> tuple[subprocess.Popen, list[str]]:
    lineas: list[str] = []
    proc = subprocess.Popen(
        [sys.executable, str(_MOTOR / "mesa_api.py"), "--env", "starter",
         "--human-seat", "p0", "--port", str(port), *extra],
        cwd=_MOTOR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def drenar() -> None:
        assert proc.stdout is not None
        for linea in proc.stdout:
            lineas.append(linea)

    threading.Thread(target=drenar, daemon=True).start()
    limite = time.time() + 40
    while time.time() < limite:
        if proc.poll() is not None:
            raise RuntimeError("".join(lineas) or "la mesa terminó al arrancar")
        try:
            codigo, _cuerpo = _leer(f"http://127.0.0.1:{port}/salud")
            if codigo == 200:
                return proc, lineas
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.15)
    proc.kill()
    raise RuntimeError("la mesa no abrió: " + "".join(lineas))


def _partida_contra_aleatorio() -> None:
    port = _puerto()
    proc, lineas = _arrancar(port, ["--seed", "4", "--max-steps", "500"])
    base = f"http://127.0.0.1:{port}"
    try:
        codigo, estado = _leer(base + "/estado")
        assert codigo == 200, estado
        assert estado["fase"] == "Principal"
        assert estado["prioridad"] == "tu"
        assert estado["rival_politica"] == "aleatorio"
        for lado in ("tu", "rival"):
            assert isinstance(estado[lado]["cementerio"], list)
            assert isinstance(estado[lado]["exilio"], list)
            assert "vida" in estado[lado] and "mana" in estado[lado]
        assert estado["tu"]["mano"] and not estado["tu"]["mano"][0]["boca_abajo"]
        assert all(carta["boca_abajo"] for carta in estado["rival"]["mano"])
        assert "pila" in estado and "historial" in estado and "acciones" in estado
        indices = [a["indice"] for a in estado["acciones"]]
        assert indices == list(range(len(indices)))
        assert any(a["descripcion"] == "Pasar prioridad" for a in estado["acciones"])
        assert len(estado["acciones"]) > 1

        codigo, error = _leer(base + "/accion", {"indice": 999})
        assert codigo == 400 and "índice" in error["error"]

        fases = set()
        vio_prioridad = False
        vio_pase_solo = False
        vio_cementerio = False
        vio_rival = False
        for _ in range(360):
            if estado.get("terminada") or not estado.get("acciones"):
                break
            fases.add(estado.get("fase"))
            if any(a.get("descripcion") == "Pasar prioridad" for a in estado["acciones"]):
                if len(estado["acciones"]) == 1:
                    vio_pase_solo = True
                else:
                    vio_prioridad = True
            for lado in ("tu", "rival"):
                if estado[lado]["cementerio"] or estado[lado]["exilio"]:
                    vio_cementerio = vio_cementerio or bool(estado[lado]["cementerio"])
            if any(h.get("jugador") == "Rival" for h in estado.get("historial", [])):
                vio_rival = True
            accion = _elegir(estado["acciones"])
            codigo, estado = _leer(base + "/accion", {"indice": accion["indice"]})
            assert codigo == 200, estado
            for lado in ("tu", "rival"):
                assert isinstance(estado[lado]["cementerio"], list)
                assert isinstance(estado[lado]["exilio"], list)
        fases.add(estado.get("fase"))
        print("fases", sorted(f for f in fases if f))
        print("prioridad", vio_prioridad, "rival", vio_rival, "cementerio", vio_cementerio, "terminada", estado.get("terminada"))
        assert "Principal" in fases
        assert fases & _COMBATE, fases
        assert vio_prioridad
        assert not vio_pase_solo
        assert vio_rival
        assert vio_cementerio
        assert any(h.get("jugador") == "Tú" for h in estado.get("historial", []))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.returncode not in (0, -15, None) and proc.returncode != 0:
            print("".join(lineas[-30:]))


def _rechazo_en_el_proceso(tmp: Path) -> None:
    import torch
    malo = tmp / "obs_198627.pth"
    if not malo.is_file():
        torch.save({"obs_dim": 198627}, malo)
    port = _puerto()
    proc, _lineas = _arrancar(port, ["--ckpt", str(malo), "--seed", "1", "--max-steps", "20"])
    try:
        _codigo, estado = _leer(f"http://127.0.0.1:{port}/estado")
        assert estado["rival_politica"] == "aleatorio"
        assert "198627" in estado["aviso"] and "198629" in estado["aviso"]
        assert estado["acciones"], "el rechazo no debe tumbar la partida"
        print("ok el servidor sigue con rival aleatorio tras un checkpoint 198627")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main() -> None:
    tmp = Path("/tmp/mesa_api_ckpts")
    tmp.mkdir(parents=True, exist_ok=True)
    _orden_de_zonas()
    _checkpoints(tmp)
    _rechazo_en_el_proceso(tmp)
    _partida_contra_aleatorio()
    print("ok partida contra aleatorio")


if __name__ == "__main__":
    main()
