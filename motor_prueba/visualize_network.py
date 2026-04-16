import argparse
from pathlib import Path

import torch
from tianshou.utils.net.common import Net
from tianshou.utils.net.discrete import DiscreteActor, DiscreteCritic


def _optional_import_torchviz():
    try:
        from torchviz import make_dot  # type: ignore

        return make_dot
    except Exception:
        return None


def build_nets(device: str = "cpu"):
    # Arquitectura: coherente con `motor_prueba/train.py`
    net_base = Net(state_shape=124, hidden_sizes=[128, 128]).to(device)

    # Reutilizamos el wrapper de train para extraer `batch.obs.obs` si hiciera falta.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from train import ObsExtractorNet  # noqa: E402

    net = ObsExtractorNet(net_base).to(device)

    actor = DiscreteActor(preprocess_net=net, action_shape=50).to(device)
    critic = DiscreteCritic(preprocess_net=net).to(device)
    return actor, critic, net_base


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", type=str, default="logs/net_graphs")
    p.add_argument("--which", type=str, default="both", choices=["actor", "critic", "both"])
    p.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda"])
    p.add_argument("--requires-grad", action="store_true", help="Activa requires_grad en el input dummy")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"

    make_dot = _optional_import_torchviz()
    if make_dot is None:
        print(
            "No se pudo importar `torchviz`.\n"
            "Instala dependencias para dibujar el grafo, por ejemplo:\n"
            "  pip install torchviz graphviz\n"
            "y además instala Graphviz del sistema si hace falta."
        )
        actor, critic, _ = build_nets(device)
        print("Actor:\n", actor)
        print("Critic:\n", critic)
        return

    actor, critic, _ = build_nets(device)

    dummy = torch.zeros(1, 124, device=device)
    if args.requires_grad:
        dummy.requires_grad_(True)

    if args.which in {"actor", "both"}:
        dummy_act = dummy.clone().detach().requires_grad_(args.requires_grad)
        out_act, _ = actor(dummy_act)
        params = dict(actor.named_parameters())
        dot = make_dot(out_act, params=params)
        out_path = out_dir / "actor_graph"
        try:
            dot.render(str(out_path), format="png", cleanup=True)
            print(f"Saved actor graph to: {out_path.with_suffix('.png')}")
        except Exception as e:
            # Si falta graphviz del sistema, guardamos el source para inspección.
            out_gv = out_path.with_suffix(".gv")
            try:
                out_gv.write_text(dot.source, encoding="utf-8")
                print(f"Saved actor graph source to: {out_gv} (render falló: {e})")
            except Exception:
                print(f"Failed to render actor graph: {e}")

    if args.which in {"critic", "both"}:
        dummy_val = dummy.clone().detach().requires_grad_(args.requires_grad)
        out_critic = critic(dummy_val)
        params = dict(critic.named_parameters())
        dot = make_dot(out_critic, params=params)
        out_path = out_dir / "critic_graph"
        try:
            dot.render(str(out_path), format="png", cleanup=True)
            print(f"Saved critic graph to: {out_path.with_suffix('.png')}")
        except Exception as e:
            out_gv = out_path.with_suffix(".gv")
            try:
                out_gv.write_text(dot.source, encoding="utf-8")
                print(f"Saved critic graph source to: {out_gv} (render falló: {e})")
            except Exception:
                print(f"Failed to render critic graph: {e}")


if __name__ == "__main__":
    main()

