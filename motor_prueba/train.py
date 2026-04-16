# train.py (Actualizado para Tianshou 2.0.0+)
import torch
import numpy as np
import time
import argparse
from typing import Any, Callable
from datetime import datetime, timezone
from pathlib import Path
import json
import warnings
import sys
from tianshou.data import Collector, VectorReplayBuffer
from tianshou.env import DummyVectorEnv, PettingZooEnv

# 1. NUEVAS RUTAS DE IMPORTACIÓN (Tianshou 2.0.0)
from tianshou.algorithm import PPO
from tianshou.algorithm.multiagent.marl import MultiAgentOnPolicyAlgorithm
from tianshou.algorithm.modelfree.reinforce import ProbabilisticActorPolicy
from tianshou.algorithm.optim import AdamOptimizerFactory

# 2. LOS TRAINERS AHORA SON CLASES, NO FUNCIONES
from tianshou.trainer import OnPolicyTrainer, OnPolicyTrainerParams

from tianshou.utils.net.common import Net
from tianshou.utils.net.discrete import DiscreteActor, DiscreteCritic
from tianshou.utils.logger.tensorboard import TensorboardLogger
from torch.utils.tensorboard import SummaryWriter

from env_magic import MagicEnv

from train_modes import (
    FrozenPPO,
    apply_dense_reward_scale,
    apply_dense_reward_scale_petting,
    flip_learner_plays_p0_all,
    load_magic_checkpoint,
    save_magic_checkpoint,
    swap_anchor_learner_seats,
)

# Silencia warnings repetitivos de Tianshou sobre stats multi-agente 2D -> 1D.
# No afecta al entrenamiento; solo evita spam en consola (y algo de overhead de prints).
warnings.filterwarnings(
    "ignore",
    message=r"Sequence has shape .* but only 1D sequences are supported\..*",
)

# En algunas versiones, Tianshou imprime este aviso directamente (no como warning).
# Filtramos la línea para que no inunde la consola.
_NOISE_LINE_SUBSTR = "Sequence has shape ("
_NOISE_LINE_SUBSTR_2 = "but only 1D sequences are supported."


class _LineFilterStream:
    def __init__(self, base, *, drop_if_contains: tuple[str, ...]):
        self._base = base
        self._drop_if_contains = drop_if_contains
        self._buf = ""

    def write(self, s: str):
        if not s:
            return 0
        self._buf += s
        written = 0
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line_out = line + "\n"
            if any(tok in line for tok in self._drop_if_contains):
                continue
            written += self._base.write(line_out)
        return written

    def flush(self):
        if self._buf:
            line = self._buf
            self._buf = ""
            if not any(tok in line for tok in self._drop_if_contains):
                self._base.write(line)
        return self._base.flush()

    def isatty(self):
        return getattr(self._base, "isatty", lambda: False)()

    def __getattr__(self, name):
        return getattr(self._base, name)


sys.stdout = _LineFilterStream(sys.stdout, drop_if_contains=(_NOISE_LINE_SUBSTR, _NOISE_LINE_SUBSTR_2))
sys.stderr = _LineFilterStream(sys.stderr, drop_if_contains=(_NOISE_LINE_SUBSTR, _NOISE_LINE_SUBSTR_2))


def _float_if_scalar(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _entropy_by_agent_from_flat_update_dict(data: dict[str, Any]) -> dict[str, float]:
    """Extrae entropía media de la política (ent_loss en PPO) por agente desde el dict plano de log_update."""
    smoothed: dict[str, float] = {}
    raw: dict[str, float] = {}
    for k, v in data.items():
        fv = _float_if_scalar(v)
        if fv is None:
            continue
        lk = k.lower()
        if "ent_loss" not in lk:
            continue
        parts = k.split("/")
        # MARL: smoothed_loss/player_0/ent_loss (claves internas con slash ya aplanadas)
        if len(parts) >= 3 and parts[0] == "smoothed_loss" and parts[-1] == "ent_loss":
            smoothed[str(parts[1])] = fv
            continue
        if len(parts) >= 3 and parts[-2] == "ent_loss" and parts[-1] == "mean":
            agent = parts[0]
            if agent.startswith("player_"):
                raw[agent] = fv
            continue
        if k in ("ent_loss/mean",) or parts == ["ent_loss", "mean"]:
            raw.setdefault("player_0", fv)
    return smoothed if smoothed else raw


class _TensorboardLoggerWithJsonlEntropy(TensorboardLogger):
    """TensorBoard habitual + una línea JSONL por update con entropía por agente (para visualize_training).

    Tianshou por defecto usa update_interval=1000: no llama a log_update_data hasta la actualización 1000.
    Con pocos updates por run (p.ej. ~4 por epoch × N epochs) nunca se registran losses/entropía en TB ni aquí;
    por eso forzamos update_interval=1.
    """

    _UPDATE_STEP_TYPE = "update/update_step"

    def __init__(
        self,
        writer: SummaryWriter,
        *,
        append_jsonl: Callable[[dict[str, Any]], None],
        trainer_holder: list[Any],
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("update_interval", 1)
        super().__init__(writer, **kwargs)
        self._append_jsonl = append_jsonl
        self._trainer_holder = trainer_holder

    def write(self, step_type: str, step: int, data: dict[str, Any]) -> None:
        super().write(step_type, step, data)
        if step_type != self._UPDATE_STEP_TYPE:
            return
        by_agent = _entropy_by_agent_from_flat_update_dict(data)
        if not by_agent:
            return
        row: dict[str, Any] = {
            "type": "ppo_entropy",
            "update_step": int(step),
            "entropy_by_agent": by_agent,
        }
        tr = self._trainer_holder[0] if self._trainer_holder else None
        if tr is not None:
            row["epoch"] = int(getattr(tr, "_epoch", 0))
            tc = getattr(tr.params, "training_collector", None)
            if tc is not None:
                row["env_step"] = int(getattr(tc, "collect_step", 0))
        self._append_jsonl(row)


class ObsExtractorNet(torch.nn.Module):
    """
    Tianshou (multi-agent) puede pasar a la red un Batch con estructura:
      batch.obs.agent_id / batch.obs.obs / batch.obs.mask
    La red `Net` solo entiende el vector numérico, así que extraemos `batch.obs.obs`
    cuando sea necesario.
    """

    def __init__(self, net: Net) -> None:
        super().__init__()
        self.net = net
        # DiscreteActor/critic usan este método en init()
        self.output_dim = net.get_output_dim()

    def get_output_dim(self) -> int:
        return self.output_dim

    def forward(self, obs, state=None, info=None):
        # Si `obs` es un Batch con atributo `.obs`, tomamos ese contenido.
        if hasattr(obs, "obs"):
            inner_obs = getattr(obs, "obs")
            # Evita errores raros si por algún motivo `inner_obs` fuese string.
            if not isinstance(inner_obs, str):
                obs = inner_obs
        return self.net(obs, state=state, info=info)

class MaskedProbabilisticActorPolicy(ProbabilisticActorPolicy):
    """
    ProbabilisticActorPolicy no aplica action masking por defecto.
    Aquí forzamos que, si `batch.obs.mask` existe, las acciones inválidas tengan probabilidad 0.
    """

    def forward(self, batch, state=None):
        # actor puede devolver probs (softmax_output=True) o logits.
        action_dist_input, hidden = self.actor(batch.obs, state=state, info=batch.info)

        mask = getattr(batch.obs, "mask", None)
        if mask is not None:
            mask_t = torch.as_tensor(mask, device=action_dist_input.device, dtype=action_dist_input.dtype)

            # Si el actor ya produce probabilidades (softmax), enmascaramos y renormalizamos.
            probs = action_dist_input
            probs = probs * mask_t
            probs_sum = probs.sum(dim=-1, keepdim=True)
            # Evitar división por 0: si por algún bug no hay acciones legales, caemos a uniforme sobre máscara.
            safe = probs_sum > 0
            if not bool(safe.all()):
                uniform = mask_t / (mask_t.sum(dim=-1, keepdim=True) + 1e-8)
                probs = torch.where(safe, probs / (probs_sum + 1e-8), uniform)
            else:
                probs = probs / (probs_sum + 1e-8)
            action_dist_input = probs

        dist = self.dist_fn(action_dist_input)
        act = dist.mode if self.deterministic_eval and not self.is_within_training_step else dist.sample()
        from tianshou.data import Batch

        return Batch(logits=action_dist_input, act=act, state=hidden, dist=dist)

def obtener_entorno(
    *,
    env_id: int,
    verbose: bool,
    log_path: str,
    dataset_path: str | None,
    masking_enabled: bool,
    reward_mode: str,
    max_episode_steps: int | None,
    dense_reward_scale: float = 1.0,
    log_anchor_roles: bool = False,
    learner_plays_p0: bool = True,
):
    return PettingZooEnv(
        MagicEnv(
            verbose=verbose,
            log_path=log_path,
            env_id=env_id,
            dataset_path=dataset_path,
            masking_enabled=masking_enabled,
            reward_mode=reward_mode,
            max_episode_steps=max_episode_steps,
            dense_reward_scale=dense_reward_scale,
            log_anchor_roles=log_anchor_roles,
            learner_plays_p0=learner_plays_p0,
        )
    )


def _build_policy_critic_tuple(
    *,
    env_prueba: PettingZooEnv,
    masking_enabled: bool,
):
    """Un par (policy, critic) compatible con PPO."""
    obs_space = env_prueba.observation_space["observation"]
    action_shape = env_prueba.action_space.shape or env_prueba.action_space.n
    net_base = Net(
        state_shape=obs_space.shape or obs_space.n,
        hidden_sizes=[128, 128],
    ).to("cpu")
    net = ObsExtractorNet(net_base).to("cpu")
    actor = DiscreteActor(preprocess_net=net, action_shape=action_shape).to("cpu")
    critic = DiscreteCritic(preprocess_net=net).to("cpu")
    policy_cls = MaskedProbabilisticActorPolicy if masking_enabled else ProbabilisticActorPolicy
    policy = policy_cls(
        actor=actor,
        dist_fn=torch.distributions.Categorical,
        action_space=env_prueba.action_space,
        observation_space=env_prueba.observation_space,
        action_scaling=False,
    )
    return policy, critic


def _safe_run_name_for_file(run_name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in run_name).strip("_") or "run"


def _unique_checkpoint_path(checkpoint_dir: str | Path, run_name: str) -> Path:
    d = Path(checkpoint_dir)
    d.mkdir(parents=True, exist_ok=True)
    safe = _safe_run_name_for_file(run_name)
    base = d / f"{safe}_policy.pth"
    if not base.exists():
        return base
    n = 2
    while True:
        p = d / f"{safe}_policy_{n}.pth"
        if not p.exists():
            return p
        n += 1


def _path_for_manifest(p: str | Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(Path(p).resolve())


def _append_run_manifest(manifest_path: Path, row: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    # Defaults tuned to make learning curves visible without extra flags.
    parser.add_argument("--max-epochs", type=int, default=30)
    parser.add_argument("--epoch-steps", type=int, default=5000)
    parser.add_argument("--train-envs", type=int, default=1)
    parser.add_argument("--test-envs", type=int, default=1)
    parser.add_argument("--test-episodes", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--verbose-env", action="store_true", help="prints del entorno/motor (lento)")
    parser.add_argument("--run-name", type=str, default="")
    parser.add_argument("--dataset-path", type=str, default="", help="ruta a dataset Archidekt JSON")
    parser.add_argument("--deck-idx", type=int, default=0, help="índice de mazo dentro del dataset")
    parser.add_argument("--disable-mask", action="store_true")
    parser.add_argument(
        "--reward-mode",
        type=str,
        default="dense",
        choices=["dense", "sparse", "curriculum"],
        help="curriculum: shaping denso -> 0 lineal en --curriculum-steps (terminal ±10 igual).",
    )
    parser.add_argument(
        "--curriculum-steps",
        type=int,
        default=150_000,
        help="Pasos de entorno hasta sparse shaping (solo reward-mode=curriculum).",
    )
    parser.add_argument(
        "--warm-start",
        type=str,
        default="",
        help="Ruta .pth: copia pesos policy(+critic si bundle) y self-play normal.",
    )
    parser.add_argument(
        "--anchor-checkpoint",
        type=str,
        default="",
        help="Ruta .pth: oponente congelado; se entrena una policy nueva contra él.",
    )
    parser.add_argument(
        "--anchor-seat",
        type=str,
        default="p1",
        choices=["p0", "p1"],
        help="Asiento inicial del ancla; el aprendiz alterna con --anchor-swap-epochs>0.",
    )
    parser.add_argument(
        "--anchor-swap-epochs",
        type=int,
        default=1,
        help="Con --anchor-checkpoint: cada cuántos cambios de epoch intercambiar aprendiz/ancla entre P0 y P1 (1=cada epoch). 0=desactivar.",
    )
    parser.add_argument(
        "--max-episode-steps",
        type=int,
        default=500,
        help="Corta episodios muy largos (truncation) para poder calcular winrate/episode_length.",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=1e-3,
        help="Learning rate de Adam (PPO). Default 1e-3 como antes.",
    )
    parser.add_argument(
        "--eps-clip",
        type=float,
        default=0.2,
        help="PPO eps_clip. Default 0.2 como antes.",
    )
    parser.add_argument(
        "--entropy-coef",
        type=float,
        default=0.01,
        help=(
            "PPO ent_coef: peso del bonus de entropía (exploración). "
            "Subirlo (p.ej. 0.03–0.08) reduce políticas demasiado picadas en la acción 0 / pasar siempre."
        ),
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=str,
        default="modelos",
        help="Directorio donde guardar checkpoint policy+critic (.pth).",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="No añadir línea a logs/runs_manifest.jsonl al terminar.",
    )
    args = parser.parse_args()

    if args.warm_start and args.anchor_checkpoint:
        parser.error("No uses --warm-start y --anchor-checkpoint a la vez.")
    if args.reward_mode == "curriculum" and args.curriculum_steps < 1:
        parser.error("--curriculum-steps debe ser >= 1.")
    if args.anchor_swap_epochs < 0:
        parser.error("--anchor-swap-epochs debe ser >= 0.")

    torch.manual_seed(int(args.seed))
    np.random.seed(int(args.seed))

    run_name = args.run_name or datetime.now().strftime("run_%Y%m%d_%H%M%S")
    log_path = f"logs/{run_name}.jsonl"
    tb_log_dir = f"logs/tb/{run_name}"
    # Ensure directories exist (helps on Windows and prevents missing TB logs).
    Path("logs").mkdir(parents=True, exist_ok=True)
    Path(tb_log_dir).mkdir(parents=True, exist_ok=True)

    def append_jsonl(event: dict):
        ev = dict(event)
        ev.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    last_epoch_start: dict[str, int | None] = {"epoch": None}
    anchor_swap_counter = [0]
    # policy_manager se asigna más abajo; training_fn solo se ejecuta tras existir (trainer.run).
    policy_manager_holder: list[Any] = []

    def training_fn(num_epoch: int, step_idx: int) -> None:
        if reward_mode == "curriculum":
            h = max(1, int(args.curriculum_steps))
            w = min(1.0, float(step_idx) / float(h))
            dense_scale_state["v"] = 1.0 - w
            apply_dense_reward_scale(train_envs, dense_scale_state["v"])
            apply_dense_reward_scale(test_envs, dense_scale_state["v"])
            apply_dense_reward_scale_petting(env_prueba, dense_scale_state["v"])

        if last_epoch_start["epoch"] != num_epoch:
            prev_ep = last_epoch_start["epoch"]
            if (
                args.anchor_checkpoint.strip()
                and int(args.anchor_swap_epochs) > 0
                and prev_ep is not None
                and policy_manager_holder
            ):
                anchor_swap_counter[0] += 1
                if anchor_swap_counter[0] % int(args.anchor_swap_epochs) == 0:
                    swap_anchor_learner_seats(policy_manager_holder[0])
                    flip_learner_plays_p0_all(train_envs, test_envs, env_prueba)
                    append_jsonl(
                        {
                            "type": "anchor_swap",
                            "epoch": int(num_epoch),
                            "env_step": int(step_idx),
                            "swap_count": int(anchor_swap_counter[0]),
                        }
                    )
            last_epoch_start["epoch"] = num_epoch
            epoch_row: dict[str, Any] = {
                "type": "epoch_start",
                "epoch": int(num_epoch),
                "env_step": int(step_idx),
                "dense_shaping_scale": float(dense_scale_state["v"]),
                "reward_mode": reward_mode,
            }
            if anchor_log_roles:
                epoch_row["learner_plays_p0"] = bool(env_prueba.env.learner_plays_p0)
            append_jsonl(epoch_row)

    def test_fn(num_epoch: int, step_idx: int | None) -> None:
        append_jsonl({"type": "epoch_end", "epoch": int(num_epoch), "env_step": int(step_idx or 0)})

    print("Preparando el gimnasio y clonando el entorno...")
    start_time = time.time()
    dataset_path = args.dataset_path or None
    masking_enabled = not args.disable_mask
    reward_mode = args.reward_mode

    if reward_mode == "dense":
        init_dense_scale = 1.0
    elif reward_mode == "sparse":
        init_dense_scale = 0.0
    else:
        init_dense_scale = 1.0

    dense_scale_state: dict[str, float] = {"v": float(init_dense_scale)}

    anchor_log_roles = bool(args.anchor_checkpoint.strip())
    learner_p0_init = args.anchor_seat == "p1"

    train_envs = DummyVectorEnv(
        [
            (
                lambda i=i: obtener_entorno(
                    env_id=i,
                    verbose=args.verbose_env,
                    log_path=log_path,
                    dataset_path=dataset_path,
                    masking_enabled=masking_enabled,
                    reward_mode=reward_mode,
                    max_episode_steps=args.max_episode_steps,
                    dense_reward_scale=init_dense_scale,
                    log_anchor_roles=anchor_log_roles,
                    learner_plays_p0=learner_p0_init,
                )
            )
            for i in range(args.train_envs)
        ]
    )
    test_envs = DummyVectorEnv(
        [
            (
                lambda i=i: obtener_entorno(
                    env_id=1000 + i,
                    verbose=args.verbose_env,
                    log_path=log_path,
                    dataset_path=dataset_path,
                    masking_enabled=masking_enabled,
                    reward_mode=reward_mode,
                    max_episode_steps=args.max_episode_steps,
                    dense_reward_scale=init_dense_scale,
                    log_anchor_roles=anchor_log_roles,
                    learner_plays_p0=learner_p0_init,
                )
            )
            for i in range(args.test_envs)
        ]
    )

    env_prueba = obtener_entorno(
        env_id=9999,
        verbose=args.verbose_env,
        log_path=log_path,
        dataset_path=dataset_path,
        masking_enabled=masking_enabled,
        reward_mode=reward_mode,
        max_episode_steps=args.max_episode_steps,
        dense_reward_scale=init_dense_scale,
        log_anchor_roles=anchor_log_roles,
        learner_plays_p0=learner_p0_init,
    )

    print("Creando la Red Neuronal...")
    policy, critic = _build_policy_critic_tuple(env_prueba=env_prueba, masking_enabled=masking_enabled)

    if args.warm_start.strip():
        wp, wc = load_magic_checkpoint(
            args.warm_start.strip(),
            policy,
            critic,
            strict_policy=False,
            strict_critic=False,
        )
        print(f"[warm-start] policy cargada ok={wp}, critic cargada ok={wc} desde {args.warm_start!r}")

    ppo_learn = PPO(
        policy=policy,
        critic=critic,
        optim=AdamOptimizerFactory(lr=args.lr),
        eps_clip=args.eps_clip,
        ent_coef=args.entropy_coef,
        gamma=0.99,
    )

    if args.anchor_checkpoint.strip():
        policy_a, critic_a = _build_policy_critic_tuple(env_prueba=env_prueba, masking_enabled=masking_enabled)
        load_magic_checkpoint(
            args.anchor_checkpoint.strip(),
            policy_a,
            critic_a,
            strict_policy=False,
            strict_critic=False,
        )
        policy_a.eval()
        critic_a.eval()
        for p in policy_a.parameters():
            p.requires_grad_(False)
        for p in critic_a.parameters():
            p.requires_grad_(False)
        ppo_anchor = FrozenPPO(
            policy=policy_a,
            critic=critic_a,
            optim=AdamOptimizerFactory(lr=1e-5),
            eps_clip=args.eps_clip,
            ent_coef=0.0,
            gamma=0.99,
        )
        if args.anchor_seat == "p1":
            algorithms = [ppo_learn, ppo_anchor]
        else:
            algorithms = [ppo_anchor, ppo_learn]
        swap_n = int(args.anchor_swap_epochs)
        if swap_n > 0:
            print(
                f"[anchor] oponente congelado desde {args.anchor_checkpoint!r} asiento inicial {args.anchor_seat}; "
                f"swap aprendiz/ancla cada {swap_n} cambio(s) de epoch (--anchor-swap-epochs)."
            )
        else:
            print(
                f"[anchor] oponente congelado desde {args.anchor_checkpoint!r} asiento {args.anchor_seat} "
                f"(swap desactivado: --anchor-swap-epochs 0)."
            )
    else:
        algorithms = [ppo_learn, ppo_learn]

    policy_manager = MultiAgentOnPolicyAlgorithm(
        algorithms=algorithms,
        env=env_prueba,
    )
    policy_manager_holder.append(policy_manager)

    buffer = VectorReplayBuffer(20000, len(train_envs))
    train_collector = Collector(policy_manager, train_envs, buffer, exploration_noise=True)
    test_collector = Collector(policy_manager, test_envs, exploration_noise=True)

    # Windows/PowerShell a veces no soporta emojis (cp1252). Mantenemos ASCII.
    print("\nIniciando el entrenamiento de la IA (Motor Tianshou 2.0.0)...")
    print(f"PPO: lr={args.lr} eps_clip={args.eps_clip} entropy_coef={args.entropy_coef}")

    def _marl_return_stats(r: np.ndarray) -> np.ndarray:
        """
        Tianshou pasa returns con shape (n_episodios, n_agentes).
        Con reward sparse y fin de partida (+10/-10), la media entre agentes es ~0 siempre,
        aunque haya ganador: eso hace que test_reward/TensorBoard parezcan '0' sin ser un bug.
        En sparse usamos mean(|r_i|) por episodio (~10 si hubo victoria clara, ~0 si truncado en 0).
        """
        if r.ndim != 2 or r.shape[1] < 2:
            return r.reshape(-1) if r.ndim == 2 and r.shape[1] == 1 else r
        if reward_mode == "sparse":
            return np.mean(np.abs(r), axis=1)
        if reward_mode == "curriculum" and float(dense_scale_state["v"]) < 0.5:
            return np.mean(np.abs(r), axis=1)
        return np.mean(r, axis=1)

    if reward_mode == "sparse":
        print(
            "[reward sparse] test_reward / TB usan mean(|return|) por episodio (no mean(r0+r1)/2), "
            "para no ver ~0 con (+10,-10)."
        )
    elif reward_mode == "curriculum":
        print(
            f"[reward curriculum] shaping denso -> 0 en {args.curriculum_steps} env steps; "
            "test_reward usa mean(|return|) cuando dense_shaping_scale < 0.5."
        )

    # 5. Nueva sintaxis para el trainer en Tianshou 2.0.0
    trainer_holder: list[Any] = []
    tb_logger = _TensorboardLoggerWithJsonlEntropy(
        SummaryWriter(log_dir=tb_log_dir),
        append_jsonl=append_jsonl,
        trainer_holder=trainer_holder,
    )
    trainer_params = OnPolicyTrainerParams(
        max_epochs=args.max_epochs,
        epoch_num_steps=args.epoch_steps,
        training_collector=train_collector,
        test_collector=test_collector,
        test_step_num_episodes=args.test_episodes,
        batch_size=args.batch_size,
        # Para multi-agente: reducir returns (num_ep, num_agents) -> (num_ep,) solo para ESTADÍSTICAS/log
        multi_agent_return_reduction=_marl_return_stats,
        logger=tb_logger,
        training_fn=training_fn,
        test_fn=test_fn,
    )

    trainer = OnPolicyTrainer(
        algorithm=policy_manager,
        params=trainer_params
    )
    trainer_holder.append(trainer)
    
    # En la versión 2.0, el entrenamiento se ejecuta invocando al método .run()
    result = trainer.run()
    elapsed = time.time() - start_time

    print(f"\nEntrenamiento terminado.")
    print(f"Tiempo total: {elapsed:.2f}s")
    print(f"Log JSONL: {log_path}")
    
    # 4. EXTRACCIÓN DE RESULTADOS ACTUALIZADA
    # Ahora 'result' es un objeto con atributos, no un diccionario
    best_reward = getattr(result, 'best_reward', 0)
    print(f"Recompensa media final en los tests: {best_reward:.2f}")
    
    checkpoint_path = _unique_checkpoint_path(args.checkpoint_dir, run_name)
    save_magic_checkpoint(checkpoint_path, policy, critic)
    print(f"Checkpoint policy+critic guardado en {checkpoint_path}")

    if not args.no_manifest:
        _append_run_manifest(
            Path("logs/runs_manifest.jsonl"),
            {
                "run_name": run_name,
                "checkpoint": _path_for_manifest(checkpoint_path),
                "reward_mode": reward_mode,
                "curriculum_steps": args.curriculum_steps if reward_mode == "curriculum" else None,
                "warm_start": args.warm_start or None,
                "anchor_checkpoint": args.anchor_checkpoint or None,
                "anchor_seat": args.anchor_seat if args.anchor_checkpoint else None,
                "anchor_swap_epochs": int(args.anchor_swap_epochs) if args.anchor_checkpoint else None,
                "seed": args.seed,
                "max_epochs": args.max_epochs,
                "epoch_steps": args.epoch_steps,
                "train_envs": args.train_envs,
                "test_envs": args.test_envs,
                "test_episodes": args.test_episodes,
                "max_episode_steps": args.max_episode_steps,
                "masking": masking_enabled,
                "lr": args.lr,
                "eps_clip": args.eps_clip,
                "entropy_coef": args.entropy_coef,
                "gamma": 0.99,
                "batch_size": args.batch_size,
                "dataset_path": dataset_path,
                "duration_s": round(elapsed, 3),
                "log_jsonl": _path_for_manifest(log_path),
                "tb_dir": _path_for_manifest(tb_log_dir),
            },
        )
        print(f"Manifiesto actualizado: logs/runs_manifest.jsonl")

    # Flush TensorBoard events (ensures event files are written).
    try:
        logger_obj = trainer_params.logger
        writer = getattr(logger_obj, "writer", None)
        if writer is not None:
            writer.flush()
            writer.close()
    except Exception:
        pass