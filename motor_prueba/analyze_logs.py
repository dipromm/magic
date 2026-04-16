import argparse
import json
from pathlib import Path


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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path", type=str, nargs="?")
    args = p.parse_args()

    steps = 0
    invalid_counts = {}
    rewards_sum = 0.0

    script_dir = Path(__file__).resolve().parent
    logs_dir = script_dir / "logs"

    if not args.path:
        resolved_path = _pick_existing_log_file(logs_dir)
        tried: list[Path] = [resolved_path]
    else:
        path_raw = Path(args.path)
        tried = []
        resolved_path: Path | None = None

        for cand in (
            path_raw,
            (Path.cwd() / path_raw),
            (script_dir / path_raw),
            (script_dir.parent / path_raw),
        ):
            tried.append(cand)
            if cand.is_file():
                resolved_path = cand
                break

    if resolved_path is None:
        suggestions = sorted(
            logs_dir.glob("*.jsonl"),
            key=lambda p: p.name,
        )
        hint = ""
        if suggestions:
            hint = "\nExisting files in motor_prueba/logs:\n" + "\n".join(
                f"- {p.as_posix()}" for p in suggestions[:10]
            )
        raise FileNotFoundError(
            f"Could not find input file: {args.path}\nTried:\n"
            + "\n".join(f"- {p}" for p in tried)
            + hint
        )

    text = resolved_path.read_text(encoding="utf-8")

    for line in text.splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") != "step":
            continue
        steps += 1
        rewards_sum += float(ev.get("reward", 0.0) or 0.0)
        agent = ev.get("agent", "unknown")
        invalid_counts[agent] = max(invalid_counts.get(agent, 0), int(ev.get("invalid_action_count", 0) or 0))

    total_invalid = sum(invalid_counts.values())
    print(f"steps={steps}")
    print(f"reward_sum={rewards_sum:.4f}")
    print(f"invalid_action_counts={invalid_counts}")
    if steps:
        print(f"invalid_per_1k_steps={total_invalid / steps * 1000:.3f}")


if __name__ == "__main__":
    main()

