import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _parse_ts(ts: str | None) -> float | None:
    if not ts:
        return None
    # JsonlLogger usa ISO con timezone: 2026-...+00:00
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _iter_jsonl(path: str | Path) -> Iterable[dict[str, Any]]:
    path_str = str(path)
    if "<" in path_str or ">" in path_str:
        raise RuntimeError(
            f"--log-jsonl parece contener un placeholder sin sustituir: {path_str!r}. "
            "Sustituye <run> por el nombre real del run (p.ej. run_20260326_144615)."
        )
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        yield json.loads(line)


def _try_import_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore

        return plt
    except Exception:
        return None


def _pick_existing_log_file(logs_dir: Path) -> Path:
    options = sorted(logs_dir.glob("*.jsonl"), key=lambda p: p.name)
    if not options:
        raise FileNotFoundError(f"No .jsonl files found in: {logs_dir}")

    print("Select a log file:")
    for i, p in enumerate(options, start=1):
        print(f"{i:>2}) {p.name}")

    while True:
        raw = input(f"Enter number (1-{len(options)}) [default 1]: ").strip()
        if not raw:
            return options[0]
        try:
            idx = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= idx <= len(options):
            return options[idx - 1]
        print("Out of range.")


def _prompt_choice(prompt: str, options: list[str], default: str) -> str:
    assert default in options
    print(prompt)
    for i, opt in enumerate(options, start=1):
        marker = " (default)" if opt == default else ""
        print(f"{i:>2}) {opt}{marker}")
    while True:
        raw = input(f"Enter number (1-{len(options)}) [default {options.index(default)+1}]: ").strip()
        if not raw:
            return default
        try:
            idx = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= idx <= len(options):
            return options[idx - 1]
        print("Out of range.")


def _moving_average(y: list[float], window: int) -> list[float]:
    if window <= 1:
        return y
    window = min(window, len(y))
    out: list[float] = []
    s = 0.0
    for i, v in enumerate(y):
        s += v
        if i >= window:
            s -= y[i - window]
        denom = window if i >= window - 1 else (i + 1)
        out.append(s / denom)
    return out


@dataclass
class EpochAccum:
    total_steps: int = 0
    invalid_steps: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    env_step_end: int | None = None


def _compute_invalid_ratio_by_epoch(log_jsonl: str | Path) -> dict[int, EpochAccum]:
    acc: dict[int, EpochAccum] = {}
    active_epoch: int | None = None
    env_step_end: dict[int, int] = {}

    last_invalid_count: dict[str, int] = {}

    for ev in _iter_jsonl(log_jsonl):
        ev_type = ev.get("type")
        if ev_type == "epoch_start":
            active_epoch = int(ev["epoch"])
            acc.setdefault(active_epoch, EpochAccum())
            continue
        if ev_type == "epoch_end":
            ep = int(ev.get("epoch"))
            value = int(ev.get("env_step", 0) or 0)
            env_step_end[ep] = value
            acc.setdefault(ep, EpochAccum()).env_step_end = value
            continue
        if ev_type != "step":
            continue
        if active_epoch is None:
            continue

        ts = _parse_ts(ev.get("ts"))
        if ts is not None:
            acc[active_epoch].first_ts = acc[active_epoch].first_ts or ts
            acc[active_epoch].last_ts = ts

        agent = str(ev.get("agent", "unknown"))
        cur_invalid = int(ev.get("invalid_action_count", 0) or 0)

        prev_invalid = last_invalid_count.get(agent)
        if prev_invalid is None:
            last_invalid_count[agent] = cur_invalid
        else:
            # Si baja (reinicio por episodio), reseteamos la referencia.
            if cur_invalid < prev_invalid:
                last_invalid_count[agent] = cur_invalid
            else:
                delta = cur_invalid - prev_invalid
                if delta > 0:
                    acc[active_epoch].invalid_steps += int(delta)
                last_invalid_count[agent] = cur_invalid

        acc[active_epoch].total_steps += 1

    return acc


def _compute_reward_mean_by_epoch(log_jsonl: str | Path) -> dict[int, dict[str, float | int | None]]:
    """
    Extrae reward medio por epoch desde el JSONL.

    Espera marcadores `epoch_start/epoch_end` y eventos con campo `reward` dentro de la epoch activa.
    """
    active_epoch: int | None = None
    sum_reward: dict[int, float] = {}
    cnt_reward: dict[int, int] = {}
    first_ts: dict[int, float] = {}
    env_step_end: dict[int, int] = {}

    for ev in _iter_jsonl(log_jsonl):
        ev_type = ev.get("type")
        if ev_type == "epoch_start":
            active_epoch = int(ev["epoch"])
            continue
        if ev_type == "epoch_end":
            ep = int(ev.get("epoch"))
            env_step_end[ep] = int(ev.get("env_step", 0) or 0)
            continue
        if active_epoch is None:
            continue

        if "reward" not in ev:
            continue
        try:
            r = float(ev.get("reward"))  # type: ignore[arg-type]
        except Exception:
            continue

        sum_reward[active_epoch] = float(sum_reward.get(active_epoch, 0.0)) + r
        cnt_reward[active_epoch] = int(cnt_reward.get(active_epoch, 0)) + 1

        ts = _parse_ts(ev.get("ts"))
        if ts is not None and active_epoch not in first_ts:
            first_ts[active_epoch] = float(ts)

    out: dict[int, dict[str, float | int | None]] = {}
    for ep in sorted(set(sum_reward.keys()) | set(env_step_end.keys()) | set(first_ts.keys())):
        c = int(cnt_reward.get(ep, 0))
        mean = (float(sum_reward.get(ep, 0.0)) / c) if c else None
        out[ep] = {"mean_reward": mean, "env_step_end": env_step_end.get(ep), "first_ts": first_ts.get(ep)}
    return out


def _compute_reward_series(log_jsonl: str | Path) -> list[tuple[float | None, float]]:
    out: list[tuple[float | None, float]] = []
    for ev in _iter_jsonl(log_jsonl):
        if "reward" not in ev:
            continue
        try:
            r = float(ev.get("reward"))  # type: ignore[arg-type]
        except Exception:
            continue
        ts = _parse_ts(ev.get("ts"))
        out.append((ts, r))
    return out


def _episode_env_matches(ev: dict[str, Any], episode_env: str) -> bool:
    """train.py usa env_id 0..N-1 para train y 1000+i para test."""
    if episode_env == "all":
        return True
    eid = ev.get("env_id")
    if eid is None:
        return False
    try:
        v = int(eid)
    except (TypeError, ValueError):
        return False
    if episode_env == "test":
        return v >= 1000
    if episode_env == "train":
        return v < 1000
    raise ValueError(f"episode_env desconocido: {episode_env!r}")


def _compute_episode_length_and_winrate_by_epoch(
    log_jsonl: str | Path, *, episode_env: str = "all"
):
    # Asigna cada `episode_end` a la epoch activa usando los marcadores `epoch_start/epoch_end`.
    by_epoch: dict[int, list[dict[str, Any]]] = {}
    active_epoch: int | None = None

    saw_matching = False
    for ev in _iter_jsonl(log_jsonl):
        ev_type = ev.get("type")
        if ev_type == "epoch_start":
            active_epoch = int(ev["epoch"])
            continue
        if ev_type == "epoch_end":
            continue
        if ev_type != "episode_end":
            continue
        if not _episode_env_matches(ev, episode_env):
            continue
        saw_matching = True
        if active_epoch is None:
            continue
        by_epoch.setdefault(active_epoch, []).append(ev)

    return by_epoch if saw_matching else None


def _resolve_tensorboard_event_dir(tb_dir: Path, log_jsonl: Path | None) -> Path:
    """Apunta al directorio que contiene los .tfevents (run concreto bajo logs/tb/…)."""
    if "<" in str(tb_dir) or ">" in str(tb_dir):
        raise RuntimeError(
            f"--tb-dir parece contener un placeholder sin sustituir: {tb_dir!r}. "
            "Sustituye <run> por el nombre real del run (p.ej. run_20260326_144615)."
        )
    p = tb_dir.resolve()
    if not p.exists():
        candidates: list[str] = []
        try:
            base = Path("logs") / "tb"
            if base.exists():
                candidates = sorted([x.name for x in base.iterdir() if x.is_dir()])
        except Exception:
            candidates = []

        msg = f"No existe el directorio de TensorBoard: {tb_dir}."
        if candidates:
            msg += f" Opciones disponibles en logs/tb/: {candidates[:20]}"
        else:
            msg += (
                " No encontré runs en logs/tb/. Asegúrate de que el entrenamiento escribió TB "
                "y que la ruta apunta al subdirectorio del run (p.ej. logs/tb/run_…)."
            )
        raise RuntimeError(msg)

    def has_events(d: Path) -> bool:
        if any(d.glob("events.out.tfevents.*")):
            return True
        return any(d.glob("*tfevents*"))

    if p.is_file():
        return p.parent
    if has_events(p):
        return p
    if log_jsonl is not None:
        cand = p / log_jsonl.stem
        if cand.is_dir() and has_events(cand):
            return cand
    best: Path | None = None
    best_m = -1.0
    try:
        for c in p.iterdir():
            if not c.is_dir():
                continue
            evs = list(c.glob("events.out.tfevents.*")) + list(c.glob("*tfevents*"))
            if not evs:
                continue
            m = max(f.stat().st_mtime for f in evs)
            if m > best_m:
                best_m = m
                best = c
    except OSError:
        pass
    return best if best is not None else p


def _load_tensorboard_scalars(
    tb_dir: str | Path, log_jsonl: str | Path | None = None
) -> dict[str, list[Any]]:
    # Usa TensorBoard event accumulator.
    from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

    tb_s = str(tb_dir)
    if "<" in tb_s or ">" in tb_s:
        raise RuntimeError(
            f"--tb-dir parece contener un placeholder sin sustituir: {tb_s!r}. "
            "Sustituye <run> por el nombre real del run (p.ej. run_20260326_144615)."
        )
    lj = Path(log_jsonl) if log_jsonl else None
    resolved = _resolve_tensorboard_event_dir(Path(tb_dir), lj)
    root = str(resolved)
    ea = EventAccumulator(root, size_guidance={"scalars": 0})
    ea.Reload()
    tags = ea.Tags().get("scalars", [])
    out: dict[str, list[Any]] = {}
    for tag in tags:
        out[tag] = ea.Scalars(tag)
    return out


def _epoch_x_coordinate(
    ep: int,
    events: list[dict[str, Any]],
    *,
    x_mode: str,
    epoch_env_step_end: dict[int, int],
    base_time: float | None,
) -> float | int | None:
    if x_mode == "epoch":
        return ep
    if x_mode == "env_step":
        return epoch_env_step_end.get(ep)
    ts_candidates = [_parse_ts(e.get("ts")) for e in events]
    ts_candidates = [t for t in ts_candidates if t is not None]
    if not ts_candidates or base_time is None:
        return None
    return float(min(ts_candidates)) - float(base_time)


def _episodes_have_anchor_identity(episodes_by_epoch: dict[int, list[dict[str, Any]]]) -> bool:
    for events in episodes_by_epoch.values():
        for e in events:
            if "reward_learner" in e and "learner_plays_p0" in e:
                return True
    return False


def _series_mean_reward_by_learner_seat(
    episodes_by_epoch: dict[int, list[dict[str, Any]]],
    reward_key: str,
    *,
    learner_is_p0: bool,
    x_mode: str,
    epoch_env_step_end: dict[int, int],
    base_time: float | None,
) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for ep in sorted(episodes_by_epoch.keys()):
        events = episodes_by_epoch[ep]
        vals: list[float] = []
        for e in events:
            if bool(e.get("learner_plays_p0")) != learner_is_p0:
                continue
            try:
                vals.append(float(e.get(reward_key, 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        if not vals:
            continue
        x = _epoch_x_coordinate(
            ep, events, x_mode=x_mode, epoch_env_step_end=epoch_env_step_end, base_time=base_time
        )
        if x is None:
            continue
        xs.append(float(x))
        ys.append(sum(vals) / len(vals))
    return xs, ys


def _series_winrate_by_learner_seat(
    episodes_by_epoch: dict[int, list[dict[str, Any]]],
    win_key: str,
    *,
    learner_is_p0: bool,
    x_mode: str,
    epoch_env_step_end: dict[int, int],
    base_time: float | None,
) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for ep in sorted(episodes_by_epoch.keys()):
        events = episodes_by_epoch[ep]
        wins: list[float] = []
        for e in events:
            if e.get("winner") is None:
                continue
            if bool(e.get("learner_plays_p0")) != learner_is_p0:
                continue
            wins.append(1.0 if e.get(win_key) else 0.0)
        if not wins:
            continue
        x = _epoch_x_coordinate(
            ep, events, x_mode=x_mode, epoch_env_step_end=epoch_env_step_end, base_time=base_time
        )
        if x is None:
            continue
        xs.append(float(x))
        ys.append(sum(wins) / len(wins))
    return xs, ys


def _pick_entropy_scalar_tags(scalars: dict[str, list[Any]]) -> tuple[str | None, str | None]:
    keys = sorted(scalars.keys())
    p0 = next((k for k in keys if "ent" in k.lower() and "player_0" in k), None)
    p1 = next((k for k in keys if "ent" in k.lower() and "player_1" in k), None)
    if p0 is None:
        p0 = next((k for k in keys if "ent_loss" in k.lower()), None)
    return p0, p1


def _ppo_entropy_series_from_jsonl(
    log_jsonl: str | Path,
    *,
    x_mode: str,
    base_time: float | None,
) -> dict[str, list[tuple[float, float]]]:
    """Eventos `type: ppo_entropy` escritos por train.py (entropía de la política por agente)."""
    out: dict[str, list[tuple[float, float]]] = {"player_0": [], "player_1": []}
    for ev in _iter_jsonl(log_jsonl):
        if ev.get("type") != "ppo_entropy":
            continue
        eb = ev.get("entropy_by_agent")
        if not isinstance(eb, dict):
            continue
        if x_mode == "epoch":
            x = ev.get("epoch")
            if x is None:
                continue
            xf = float(x)
        elif x_mode == "env_step":
            x = ev.get("env_step")
            if x is None:
                continue
            xf = float(x)
        else:
            ts = _parse_ts(ev.get("ts"))
            if ts is None or base_time is None:
                continue
            xf = float(ts) - float(base_time)
        for agent_key in ("player_0", "player_1"):
            if agent_key not in eb:
                continue
            try:
                y = float(eb[agent_key])
            except (TypeError, ValueError):
                continue
            out[agent_key].append((xf, y))
    for k in out:
        out[k].sort(key=lambda t: t[0])
    return out


def _epoch_curriculum_series(
    log_jsonl: str | Path,
    *,
    x_mode: str,
) -> tuple[list[float], list[float]]:
    """Devuelve (xs, dense_scale) desde epoch_start.

    Si reanudas el entrenamiento en el mismo .jsonl, suele haber **dos** `epoch_start` por epoch;
    ordenar solo por número de epoch y pintar líneas une (t≈0s) con (t≈+2000s) y luego el siguiente
    epoch con t pequeño → zigzag. Aquí: **un punto por epoch** (el primero en el fichero) y **xs ordenados**.
    """
    rows: list[tuple[int, int, float | None, str | None, float | None]] = []
    for ev in _iter_jsonl(log_jsonl):
        if ev.get("type") != "epoch_start":
            continue
        ep = int(ev["epoch"])
        step = int(ev.get("env_step", 0) or 0)
        scale = ev.get("dense_shaping_scale")
        rm = ev.get("reward_mode")
        ts = _parse_ts(ev.get("ts"))
        rows.append((ep, step, float(scale) if scale is not None else None, str(rm) if rm else None, ts))

    if not rows:
        return [], []

    first_by_epoch: dict[int, tuple[int, int, float | None, str | None, float | None]] = {}
    for r in rows:
        if r[0] not in first_by_epoch:
            first_by_epoch[r[0]] = r
    uniq = list(first_by_epoch.values())

    # Origen temporal: primer epoch_start del log (incl. duplicados), no solo el de `uniq`.
    all_start_ts = [r[4] for r in rows if r[4] is not None]
    t_ref = min(all_start_ts) if all_start_ts else None
    uniq_ts = [r[4] for r in uniq if r[4] is not None]
    time_ok = bool(uniq_ts) and len(uniq_ts) == len(uniq)

    points: list[tuple[float, float]] = []
    for ep, step, scale, rm, ts in uniq:
        if scale is None:
            if rm == "sparse":
                y = 0.0
            else:
                y = 1.0
        else:
            y = float(scale)
        if x_mode == "epoch":
            x = float(ep)
        elif x_mode == "env_step":
            x = float(step)
        else:
            if time_ok and t_ref is not None and ts is not None:
                x = float(ts) - float(t_ref)
            else:
                x = float(step)
        points.append((x, y))

    points.sort(key=lambda p: p[0])
    if not points:
        return [], []
    return [p[0] for p in points], [p[1] for p in points]


def plot_dashboard(args: argparse.Namespace) -> None:
    plt = _try_import_matplotlib()
    if plt is None:
        raise RuntimeError("matplotlib no está disponible.")

    log_jsonl = Path(args.log_jsonl)
    tb_dir: Path | None = Path(args.tb_dir) if (args.tb_dir and str(args.tb_dir).strip()) else None
    if tb_dir is None:
        auto_tb = log_jsonl.resolve().parent / "tb" / log_jsonl.stem
        if auto_tb.is_dir():
            tb_dir = auto_tb
    x_mode = args.x
    smooth = max(1, int(args.smooth or 1))
    episode_env = getattr(args, "episode_env", "all")

    episodes_by_epoch = _compute_episode_length_and_winrate_by_epoch(log_jsonl, episode_env=episode_env)
    if episodes_by_epoch is None:
        raise RuntimeError(f"No episode_end en {log_jsonl} (filtro episode_env={episode_env}).")

    epoch_env_step_end: dict[int, int] = {}
    for ev in _iter_jsonl(log_jsonl):
        if ev.get("type") == "epoch_end":
            epoch_env_step_end[int(ev["epoch"])] = int(ev.get("env_step", 0) or 0)

    all_ts: list[float] = []
    for evs in episodes_by_epoch.values():
        for e in evs:
            t = _parse_ts(e.get("ts"))
            if t is not None:
                all_ts.append(float(t))
    base_time = min(all_ts) if all_ts else None

    use_anchor_identity = _episodes_have_anchor_identity(episodes_by_epoch)

    fig, axes = plt.subplots(4, 2, figsize=(18, 16), constrained_layout=True)
    ax = axes.ravel()

    if use_anchor_identity:
        # Retorno: modelo (aprendiz) y ancla, desglosado por asiento físico P0/P1.
        for learner_is_p0, label, color in (
            (True, "modelo como P0", "C0"),
            (False, "modelo como P1", "C1"),
        ):
            xs, ys = _series_mean_reward_by_learner_seat(
                episodes_by_epoch,
                "reward_learner",
                learner_is_p0=learner_is_p0,
                x_mode=x_mode,
                epoch_env_step_end=epoch_env_step_end,
                base_time=base_time,
            )
            ys_s = _moving_average([float(v) for v in ys], smooth)
            ax[0].plot(xs, ys_s, color=color, label=label)
        ax[0].set_title("Episode return mean (modelo por asiento)")
        ax[0].legend(loc="best", fontsize=8)
        ax[0].set_xlabel(x_mode)
        ax[0].grid(True, alpha=0.3)

        for learner_is_p0, label, color in (
            (False, "ancla como P0", "C2"),
            (True, "ancla como P1", "C3"),
        ):
            xs, ys = _series_mean_reward_by_learner_seat(
                episodes_by_epoch,
                "reward_anchor",
                learner_is_p0=learner_is_p0,
                x_mode=x_mode,
                epoch_env_step_end=epoch_env_step_end,
                base_time=base_time,
            )
            ys_s = _moving_average([float(v) for v in ys], smooth)
            ax[1].plot(xs, ys_s, color=color, label=label)
        ax[1].set_title("Episode return mean (ancla por asiento)")
        ax[1].legend(loc="best", fontsize=8)
        ax[1].set_xlabel(x_mode)
        ax[1].grid(True, alpha=0.3)

        for learner_is_p0, label, color in (
            (True, "modelo (P0)", "C0"),
            (False, "modelo (P1)", "C1"),
        ):
            xs, ys = _series_winrate_by_learner_seat(
                episodes_by_epoch,
                "win_learner",
                learner_is_p0=learner_is_p0,
                x_mode=x_mode,
                epoch_env_step_end=epoch_env_step_end,
                base_time=base_time,
            )
            ys_s = _moving_average([float(v) for v in ys], smooth)
            ax[2].plot(xs, ys_s, color=color, label=label)
        ax[2].set_ylim(-0.05, 1.05)
        ax[2].set_title("Winrate (modelo por asiento)")
        ax[2].legend(loc="best", fontsize=8)
        ax[2].set_xlabel(x_mode)
        ax[2].grid(True, alpha=0.3)

        for learner_is_p0, label, color in (
            (False, "ancla (P0)", "C2"),
            (True, "ancla (P1)", "C3"),
        ):
            xs, ys = _series_winrate_by_learner_seat(
                episodes_by_epoch,
                "win_anchor",
                learner_is_p0=learner_is_p0,
                x_mode=x_mode,
                epoch_env_step_end=epoch_env_step_end,
                base_time=base_time,
            )
            ys_s = _moving_average([float(v) for v in ys], smooth)
            ax[3].plot(xs, ys_s, color=color, label=label)
        ax[3].set_ylim(-0.05, 1.05)
        ax[3].set_title("Winrate (ancla por asiento)")
        ax[3].legend(loc="best", fontsize=8)
        ax[3].set_xlabel(x_mode)
        ax[3].grid(True, alpha=0.3)
    else:
        # --- Reward P0 / P1 (retorno de episodio por asiento) ---
        for idx_player, key_r, title in (
            (0, "reward_player_0", "Episode return mean (player_0)"),
            (1, "reward_player_1", "Episode return mean (player_1)"),
        ):
            xs, ys = [], []
            for ep in sorted(episodes_by_epoch.keys()):
                events = episodes_by_epoch[ep]
                vals = []
                for e in events:
                    try:
                        vals.append(float(e.get(key_r, 0.0) or 0.0))
                    except (TypeError, ValueError):
                        continue
                if not vals:
                    continue
                x = _epoch_x_coordinate(
                    ep, events, x_mode=x_mode, epoch_env_step_end=epoch_env_step_end, base_time=base_time
                )
                if x is None:
                    continue
                xs.append(float(x))
                ys.append(sum(vals) / len(vals))
            ys_s = _moving_average([float(v) for v in ys], smooth)
            ax[idx_player].plot(xs, ys_s, color="C0" if idx_player == 0 else "C1")
            ax[idx_player].set_title(title)
            ax[idx_player].set_xlabel(x_mode)
            ax[idx_player].grid(True, alpha=0.3)

        # --- Winrate P0 / P1 (solo episodios con ganador explícito) ---
        for idx_ax, use_win, title in (
            (2, "win0", "Winrate (player_0 wins)"),
            (3, "win1", "Winrate (player_1 wins)"),
        ):
            xs, ys = [], []
            for ep in sorted(episodes_by_epoch.keys()):
                events = episodes_by_epoch[ep]
                decisive = [e for e in events if e.get("winner") is not None]
                if not decisive:
                    continue
                if use_win == "win1" and not any("win1" in e for e in decisive):
                    wins = [1.0 - float(bool(e.get("win0"))) for e in decisive]
                else:
                    wins = [float(bool(e.get(use_win))) for e in decisive]
                x = _epoch_x_coordinate(
                    ep, events, x_mode=x_mode, epoch_env_step_end=epoch_env_step_end, base_time=base_time
                )
                if x is None:
                    continue
                xs.append(float(x))
                ys.append(sum(wins) / max(1, len(wins)))
            ys_s = _moving_average([float(v) for v in ys], smooth)
            ax[idx_ax].plot(xs, ys_s)
            ax[idx_ax].set_ylim(-0.05, 1.05)
            ax[idx_ax].set_title(title)
            ax[idx_ax].set_xlabel(x_mode)
            ax[idx_ax].grid(True, alpha=0.3)

    # --- Episode length ---
    xs, ys = [], []
    for ep in sorted(episodes_by_epoch.keys()):
        events = episodes_by_epoch[ep]
        lens = [int(e.get("episode_len_steps", 0) or 0) for e in events]
        if not lens:
            continue
        x = _epoch_x_coordinate(ep, events, x_mode=x_mode, epoch_env_step_end=epoch_env_step_end, base_time=base_time)
        if x is None:
            continue
        xs.append(float(x))
        ys.append(sum(lens) / len(lens))
    ys_s = _moving_average([float(v) for v in ys], smooth)
    ax[4].plot(xs, ys_s, color="C2")
    ax[4].set_title("Episode length (mean)")
    ax[4].set_xlabel(x_mode)
    ax[4].grid(True, alpha=0.3)

    # --- Curriculum / dense shaping scale ---
    cx, cy = _epoch_curriculum_series(log_jsonl, x_mode=x_mode)
    if cx:
        cy_s = _moving_average([float(v) for v in cy], smooth)
        ax[5].plot(cx, cy_s, color="C3", label="dense_shaping_scale")
        ax[5].plot(cx, [1.0 - float(v) for v in cy_s], "--", color="C4", alpha=0.7, label="sparse_weight (1-scale)")
        ax[5].legend(loc="best", fontsize=8)
    ax[5].set_title("Curriculum: dense shaping vs sparse")
    ax[5].set_xlabel(x_mode)
    ax[5].set_ylim(-0.05, 1.05)
    ax[5].grid(True, alpha=0.3)

    # --- Entropía (TensorBoard o JSONL `ppo_entropy` desde train.py) ---
    jsonl_entropy = _ppo_entropy_series_from_jsonl(log_jsonl, x_mode=x_mode, base_time=base_time)
    has_jsonl_entropy = any(jsonl_entropy[a] for a in ("player_0", "player_1"))

    def _plot_entropy_from_jsonl() -> None:
        print("[dashboard] Entropía desde JSONL (eventos type=ppo_entropy).", flush=True)
        for sub_i, agent in enumerate(("player_0", "player_1")):
            ax_i = 6 + sub_i
            pts = jsonl_entropy.get(agent) or []
            if not pts:
                ax[ax_i].set_title(f"Entropy ({agent}) — sin datos jsonl")
                ax[ax_i].set_xlabel(x_mode)
                ax[ax_i].grid(True, alpha=0.3)
                continue
            xs_e = [p[0] for p in pts]
            ys_e = [p[1] for p in pts]
            sm = min(smooth, max(1, len(ys_e) // 3)) or 1
            ys_es = _moving_average(ys_e, sm)
            ax[ax_i].plot(xs_e, ys_es, label="jsonl ppo_entropy")
            ax[ax_i].set_title(f"Entropy ({agent}) — jsonl")
            ax[ax_i].set_xlabel(x_mode)
            ax[ax_i].legend(fontsize=7)
            ax[ax_i].grid(True, alpha=0.3)

    tb_ok = tb_dir is not None and Path(tb_dir).exists()
    scalars: dict[str, list[Any]] | None = None
    t0: str | None = None
    t1: str | None = None
    has_tb_entropy = False
    tb_load_error: str | None = None
    if tb_ok:
        try:
            scalars = _load_tensorboard_scalars(tb_dir, log_jsonl)
            t0, t1 = _pick_entropy_scalar_tags(scalars)
            has_tb_entropy = bool(
                (t0 and scalars.get(t0)) or (t1 and scalars.get(t1))
            )
        except Exception as ex:
            tb_load_error = str(ex)
            scalars = None

    if has_tb_entropy and scalars is not None:
        wall_base = None
        for tag in (t0, t1):
            if tag and scalars.get(tag):
                wall_base = min(e.wall_time for e in scalars[tag])
                break
        for sub_i, tag in enumerate((t0, t1)):
            ax_i = 6 + sub_i
            if not tag or tag not in scalars or not scalars[tag]:
                ax[ax_i].set_title(f"Entropy (player_{sub_i}) — sin tag TB")
                ax[ax_i].set_xlabel(x_mode)
                ax[ax_i].grid(True, alpha=0.3)
                continue
            evs = scalars[tag]
            if x_mode == "env_step":
                xs_e = [float(e.step) for e in evs]
            elif x_mode == "time" and wall_base is not None:
                xs_e = [e.wall_time - wall_base for e in evs]
            else:
                xs_e = list(range(len(evs)))
            ys_e = [float(e.value) for e in evs]
            ys_es = _moving_average(ys_e, min(smooth, max(1, len(ys_e) // 3)) or 1)
            ax[ax_i].plot(xs_e, ys_es, label=tag[:40])
            ax[ax_i].set_title(f"Entropy / ent_loss ({tag})")
            ax[ax_i].set_xlabel(x_mode)
            ax[ax_i].legend(fontsize=7)
            ax[ax_i].grid(True, alpha=0.3)
    elif has_jsonl_entropy:
        if tb_ok and not has_tb_entropy and tb_load_error is None:
            keys_preview = sorted(scalars.keys())[:25] if scalars else []
            print(
                "[dashboard] Sin ent_loss útil en TensorBoard; uso jsonl. Tags TB (muestra):",
                keys_preview,
                flush=True,
            )
        if tb_load_error is not None:
            print(f"[dashboard] TensorBoard no cargado ({tb_load_error}); entropía desde jsonl.", flush=True)
        _plot_entropy_from_jsonl()
    else:
        if tb_load_error is not None:
            ax[6].text(0.1, 0.5, f"TB error: {tb_load_error}", transform=ax[6].transAxes)
        elif tb_ok and scalars is not None:
            keys_preview = sorted(scalars.keys())[:25]
            print(
                "[dashboard] Sin entropía en TB ni jsonl (ppo_entropy). Tags TB (muestra):",
                keys_preview,
                flush=True,
            )
            ax[6].set_title("Entropy — sin datos (TB / jsonl)")
            ax[6].text(
                0.05,
                0.45,
                "Lista de tags TB en consola; re-entrena con train.py actual para jsonl.",
                transform=ax[6].transAxes,
                fontsize=10,
            )
        elif not tb_ok:
            ax[6].set_title("Entropy — sin TB (--tb-dir)")
            ax[6].text(
                0.05,
                0.45,
                "Sin eventos ppo_entropy en jsonl o sin tb-dir.",
                transform=ax[6].transAxes,
                fontsize=10,
            )
        else:
            ax[6].set_title("Entropy — sin datos")
        ax[6].set_xlabel(x_mode)
        ax[6].grid(True, alpha=0.3)
        ax[7].set_title("Entropy — (sin datos)")
        ax[7].set_xlabel(x_mode)
        ax[7].grid(True, alpha=0.3)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    print(f"Saved dashboard: {out}")


def plot_metric(args: argparse.Namespace) -> None:
    plt = _try_import_matplotlib()
    if plt is None:
        raise RuntimeError("matplotlib no está disponible. Instálalo o usa export CSV.")

    metric = args.metric
    x_mode = args.x
    smooth = int(args.smooth or 1)
    if smooth < 1:
        smooth = 1

    log_jsonl = Path(args.log_jsonl)
    tb_dir = Path(args.tb_dir) if args.tb_dir else None

    if metric == "invalid_ratio":
        acc = _compute_invalid_ratio_by_epoch(log_jsonl)
        xs, ys = [], []
        for ep in sorted(acc.keys()):
            denom = acc[ep].total_steps
            y = (acc[ep].invalid_steps / denom) if denom else 0.0

            if x_mode == "epoch":
                x = ep
            elif x_mode == "env_step":
                if acc[ep].env_step_end is None:
                    continue
                x = acc[ep].env_step_end
            else:
                # time
                if acc[ep].first_ts is None:
                    continue
                base = min(a.first_ts for a in acc.values() if a.first_ts is not None)  # type: ignore[arg-type]
                x = (acc[ep].first_ts - base)  # type: ignore[operator]
            xs.append(x)
            ys.append(y)
        ys2 = _moving_average([float(v) for v in ys], smooth)
        plt.plot(xs, ys2)
        plt.xlabel(x_mode)
        plt.ylabel("invalid_action_ratio")

    elif metric in {"episode_length", "winrate"}:
        episode_env = getattr(args, "episode_env", "all")
        episodes_by_epoch = _compute_episode_length_and_winrate_by_epoch(
            log_jsonl, episode_env=episode_env
        )
        if episodes_by_epoch is None:
            filt = ""
            if episode_env != "all":
                filt = f" (con --episode-env={episode_env}; train: env_id<1000, test: env_id>=1000)"
            raise RuntimeError(
                f"No encontré eventos de tipo `episode_end`{filt} en {log_jsonl}. "
                "Primero añade logging de episode_end en el entorno."
            )

        # Mapas auxiliares para ejes
        epoch_env_step_end: dict[int, int] = {}
        epoch_min_ts: dict[int, float] = {}
        for ev in _iter_jsonl(log_jsonl):
            if ev.get("type") == "epoch_end":
                ep = int(ev["epoch"])
                epoch_env_step_end[ep] = int(ev.get("env_step", 0) or 0)
            if ev.get("type") == "episode_end":
                # tiempo lo usamos por evento
                ts = _parse_ts(ev.get("ts"))
                if ts is None:
                    continue
                # el episodio ya está en una epoch activa durante el parseo anterior,
                # así que aquí no conocemos ep. Solo guardamos ts y luego ajustamos
                # iterando episodes_by_epoch.
                # (Se recalculará abajo por epoch con events.)
                _ = ts

        all_ts: list[float] = []
        for ep_events in episodes_by_epoch.values():
            for e in ep_events:
                t = _parse_ts(e.get("ts"))
                if t is not None:
                    all_ts.append(float(t))
        base_time = min(all_ts) if all_ts else None

        xs, ys = [], []
        for ep in sorted(episodes_by_epoch.keys()):
            events = episodes_by_epoch[ep]
            if metric == "episode_length":
                y = sum(int(e.get("episode_len_steps", 0) or 0) for e in events) / max(1, len(events))
            else:
                # win0: bool o 0/1
                wins = sum(1 for e in events if bool(e.get("win0")))
                y = wins / max(1, len(events))

            if x_mode == "epoch":
                x = ep
            elif x_mode == "env_step":
                x = epoch_env_step_end.get(ep)
                if x is None:
                    continue
            else:
                # time
                ts_candidates = [_parse_ts(e.get("ts")) for e in events]
                ts_candidates = [t for t in ts_candidates if t is not None]
                if not ts_candidates:
                    continue
                epoch_t = min(ts_candidates)
                if base_time is None:
                    continue
                x = epoch_t - base_time
            xs.append(x)
            ys.append(float(y))
        ys2 = _moving_average([float(v) for v in ys], smooth)
        plt.plot(xs, ys2)
        plt.xlabel(x_mode)
        ylab = metric if episode_env == "all" else f"{metric} ({episode_env})"
        plt.ylabel(ylab)

    elif metric == "reward":
        # Preferimos TensorBoard si existe y está disponible; si no, usamos JSONL (campo `reward`).
        used_tb = False
        if tb_dir is not None and Path(tb_dir).exists():
            scalars = _load_tensorboard_scalars(tb_dir, log_jsonl)
            # Heurística: busca test/*returns_stat/*/mean
            candidate_keys = [k for k in scalars.keys() if "test" in k and "returns_stat" in k and "mean" in k]
            if not candidate_keys:
                # fallback: cualquier key con returns_stat y mean
                candidate_keys = [k for k in scalars.keys() if "returns_stat" in k and "mean" in k]
            if candidate_keys:
                key = sorted(candidate_keys)[0]
                events = scalars[key]

                xs, ys = [], []
                wall_times = [e.wall_time for e in events]
                base_time = min(wall_times) if wall_times else 0.0
                for i, e in enumerate(events):
                    if x_mode == "env_step":
                        x = e.step
                    elif x_mode == "time":
                        x = e.wall_time - base_time
                    else:
                        x = i  # epoch index aproximado
                    xs.append(x)
                    ys.append(float(e.value))

                ys2 = _moving_average([float(v) for v in ys], smooth)
                plt.plot(xs, ys2)
                plt.xlabel(x_mode)
                plt.ylabel("reward(test)")
                used_tb = True

        if not used_tb:
            # 1) Intento por epoch si existen epoch_start/epoch_end
            reward_by_epoch = _compute_reward_mean_by_epoch(log_jsonl)
            xs, ys = [], []
            if reward_by_epoch:
                base = None
                if x_mode == "time":
                    ts_vals = [v.get("first_ts") for v in reward_by_epoch.values() if v.get("first_ts") is not None]
                    base = min(ts_vals) if ts_vals else None

                for ep in sorted(reward_by_epoch.keys()):
                    info = reward_by_epoch[ep]
                    y = info.get("mean_reward")
                    if y is None:
                        continue

                    if x_mode == "epoch":
                        x = ep
                    elif x_mode == "env_step":
                        x2 = info.get("env_step_end")
                        if x2 is None:
                            continue
                        x = int(x2)
                    else:
                        t = info.get("first_ts")
                        if t is None or base is None:
                            continue
                        x = float(t) - float(base)

                    xs.append(x)
                    ys.append(float(y))

                if xs:
                    ys2 = _moving_average([float(v) for v in ys], smooth)
                    plt.plot(xs, ys2)
                    plt.xlabel(x_mode)
                    plt.ylabel("reward(mean, jsonl by epoch)")
                    # NOTE: no return here; we still need to save the figure below.

            # 2) Fallback: serie cruda de rewards (sin epochs), eje por tiempo o índice
            series = _compute_reward_series(log_jsonl)
            if not series:
                raise RuntimeError(f"No encontré ningún evento con campo `reward` en {log_jsonl}.")

            if x_mode == "time":
                ts_vals = [t for (t, _) in series if t is not None]
                if not ts_vals:
                    raise RuntimeError(
                        f"metric=reward con x=time requiere timestamps `ts` parseables en {log_jsonl}."
                    )
                base_time = min(ts_vals)
                xs = [(t - base_time) for (t, _) in series if t is not None]
                ys = [r for (t, r) in series if t is not None]
                ys2 = _moving_average([float(v) for v in ys], smooth)
                plt.plot(xs, ys2)
                plt.xlabel("time")
                plt.ylabel("reward(jsonl)")
            else:
                ys = [r for (_, r) in series]
                xs = list(range(len(ys)))
                ys2 = _moving_average([float(v) for v in ys], smooth)
                plt.plot(xs, ys2)
                plt.xlabel("index" if x_mode == "env_step" else "epoch")
                plt.ylabel("reward(jsonl)")
    else:
        raise ValueError(f"metric desconocida: {metric}")

    plt.grid(True, alpha=0.3)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out)
    print(f"Saved: {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "run",
        type=str,
        nargs="?",
        help="Nombre del run (opcional). Si se indica, se asume logs/<run>.jsonl y logs/tb/<run>.",
    )
    p.add_argument("--log-jsonl", type=str, default="", help="logs JSONL del run (p.ej. logs/run_...jsonl)")
    p.add_argument("--tb-dir", type=str, default="", help="dir de TensorBoard del run (opcional si metric!=reward)")
    p.add_argument(
        "--dashboard",
        action="store_true",
        help="Una sola figura: return P0/P1, winrate P0/P1, episode length, curriculum dense/sparse, entropía TB.",
    )
    p.add_argument("--metric", type=str, default="", choices=["reward", "invalid_ratio", "episode_length", "winrate"])
    p.add_argument("--x", type=str, default="", choices=["time", "epoch", "env_step"])
    p.add_argument("--out", type=str, default="", help="ruta salida png")
    p.add_argument(
        "--reward-source",
        type=str,
        default="auto",
        choices=["auto", "tb", "jsonl"],
        help="Fuente para metric=reward. auto: usa TensorBoard si existe, si no JSONL.",
    )
    p.add_argument(
        "--smooth",
        type=int,
        default=1,
        help="Suavizado (media móvil). 1 = sin suavizar. Útil para reward muy ruidosa.",
    )
    p.add_argument(
        "--episode-env",
        type=str,
        default="all",
        choices=["all", "train", "test"],
        help="Solo para episode_length/winrate: filtrar episode_end por env_id "
        "(train: <1000, test: >=1000, alineado con train.py).",
    )
    args = p.parse_args()

    script_dir = Path(__file__).resolve().parent
    logs_dir = script_dir / "logs"

    # Modo asistente si no se pasó ningún argumento relevante.
    interactive = (len(sys.argv) == 1) or (not args.log_jsonl and not args.run)

    # Resolver run -> rutas por defecto (sin obligar al usuario a pasar todo el CLI).
    if args.run and not args.log_jsonl:
        run = args.run
        if run.endswith(".jsonl"):
            args.log_jsonl = str(logs_dir / run)
            run_name = run[:-5]
        else:
            args.log_jsonl = str(logs_dir / f"{run}.jsonl")
            run_name = run
        if not args.tb_dir:
            args.tb_dir = str(logs_dir / "tb" / run_name)

    if interactive:
        picked = _pick_existing_log_file(logs_dir)
        args.log_jsonl = str(picked)
        default_metric = "reward"
        args.metric = _prompt_choice(
            "Select metric:",
            ["reward", "invalid_ratio", "episode_length", "winrate"],
            default_metric,
        )
        args.x = _prompt_choice("Select x-axis:", ["time", "epoch", "env_step"], "time")
        # Si existe TensorBoard para este run, por defecto lo usamos (curva más limpia).
        picked_stem = picked.stem
        tb_candidate = logs_dir / "tb" / picked_stem
        if args.metric == "reward" and tb_candidate.exists():
            src = _prompt_choice(
                f"Reward source detected TensorBoard at {tb_candidate}. Select source:",
                ["auto", "tb", "jsonl"],
                "tb",
            )
            args.reward_source = src
            if src in {"auto", "tb"} and not args.tb_dir:
                args.tb_dir = str(tb_candidate)
        elif args.metric == "reward":
            # Sin TB: forzamos jsonl/auto (equivalentes)
            args.reward_source = "jsonl"
        if not args.out:
            stem = picked.stem
            args.out = str(logs_dir / "plots" / f"{stem}_{args.metric}.png")
        raw = input(f"Output path [default {args.out}]: ").strip()
        if raw:
            args.out = raw
        raw = input(f"Smoothing window (>=1) [default {args.smooth}]: ").strip()
        if raw:
            try:
                args.smooth = int(raw)
            except ValueError:
                pass

    # Validaciones finales (mantiene CLI completo).
    if not args.log_jsonl:
        raise RuntimeError("Falta --log-jsonl (o ejecuta sin args para modo interactivo).")
    if "<" in args.log_jsonl or ">" in args.log_jsonl:
        raise RuntimeError(
            f"--log-jsonl parece contener un placeholder sin sustituir: {args.log_jsonl!r}. "
            "Sustituye <run> por el nombre real del run (p.ej. run_20260326_144615)."
        )
    if args.dashboard:
        if not args.x:
            args.x = "time"
        if not args.out:
            stem = Path(args.log_jsonl).stem
            args.out = str(logs_dir / "plots" / f"{stem}_dashboard.png")
        if not args.tb_dir or not str(args.tb_dir).strip():
            auto_tb = (Path(args.log_jsonl).resolve().parent / "tb" / Path(args.log_jsonl).stem)
            if auto_tb.is_dir():
                args.tb_dir = str(auto_tb)
        plot_dashboard(args)
        return
    if not args.metric:
        raise RuntimeError("Falta --metric (o ejecuta sin args para modo interactivo).")
    if not args.x:
        args.x = "time"
    if not args.out:
        args.out = "logs/plot.png"

    # Aplicar reward-source en modo CLI también.
    if args.metric == "reward":
        if args.reward_source == "tb":
            if not args.tb_dir:
                raise RuntimeError("--reward-source=tb requiere --tb-dir (o pasar el run posicional).")
        elif args.reward_source == "jsonl":
            # Forzar a no usar TB aunque exista.
            args.tb_dir = None

    args.tb_dir = args.tb_dir or None
    plot_metric(args)


if __name__ == "__main__":
    main()

