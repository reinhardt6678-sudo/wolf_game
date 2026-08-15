"""命令行入口：python -m wolf ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .arena import Arena, MatchConfig, load_toml
from .metrics import leaderboard
from .report import render_leaderboard, render_replay
from .roles import BUILTIN_BOARDS

DEFAULT_MODELS = Path("configs/models.toml")
DEFAULT_MATCH = Path("configs/match.toml")


def _load_models(path: Path) -> dict:
    if not path.exists():
        example = path.with_name("models.example.toml")
        if example.exists():
            print(f"未找到 {path}，改用 {example}（只含离线基线模型）", file=sys.stderr)
            path = example
        else:
            raise SystemExit(f"找不到模型配置：{path}")
    data = load_toml(path)
    return data.get("models", data)


def cmd_run(args: argparse.Namespace) -> int:
    models = _load_models(Path(args.models))
    match_raw = load_toml(args.match) if Path(args.match).exists() else {}
    match_raw = match_raw.get("match", match_raw)
    for key in ("games", "board", "parallel_games", "parallel_agents", "seed", "archive", "tag"):
        val = getattr(args, key, None)
        if val is not None:
            match_raw[key] = val
    if args.players:
        match_raw["players"] = args.players.split(",")
    cfg = MatchConfig.from_dict(match_raw)

    print(f"板子：{cfg.board.describe()}")
    print(f"座位分配：{'、'.join(cfg.players)}（rotate={cfg.rotate}）")
    print(f"共 {cfg.games} 局，并发对局 {cfg.parallel_games}，局内并发 {cfg.parallel_agents}\n")

    arena = Arena(models, cfg)
    results = arena.run()

    good = sum(1 for r in results if r.winner == "好人")
    wolf = sum(1 for r in results if r.winner == "狼人")
    print(f"\n=== 完成 {len(results)} 局：好人 {good} 胜 / 狼人 {wolf} 胜 ===")
    print(f"档案目录：{Path(cfg.archive_root).resolve()}")

    for r in results:
        render_replay(Path(cfg.archive_root) / r.game_id)
    lb = render_leaderboard(cfg.archive_root)
    print(f"排行榜：{lb.resolve()}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.game)
    if not (path / "meta.json").exists():
        path = Path(args.archive) / args.game
    out = render_replay(path)
    print(f"已生成：{out.resolve()}")
    return 0


def cmd_leaderboard(args: argparse.Namespace) -> int:
    lb = leaderboard(args.archive)
    if args.json:
        print(json.dumps(lb, ensure_ascii=False, indent=2))
    else:
        s = lb["summary"]
        print(
            f"共 {s['games']} 局 | 好人胜率 {s['good_win_rate']}% | "
            f"狼人胜率 {s['wolf_win_rate']}% | 平均 {s['avg_days']} 天\n"
        )
        cols = ["model", "games", "win_rate", "good_win_rate", "wolf_win_rate",
                "vote_accuracy", "belief_accuracy", "bluff_rate", "error_rate"]
        widths = {c: max(len(c), 12) for c in cols}
        print(" ".join(c.ljust(widths[c]) for c in cols))
        for r in lb["models"]:
            print(" ".join(str(r.get(c) if r.get(c) is not None else "—").ljust(widths[c]) for c in cols))
    out = render_leaderboard(args.archive)
    print(f"\nHTML：{out.resolve()}")
    return 0


def cmd_boards(_args: argparse.Namespace) -> int:
    for key, board in BUILTIN_BOARDS.items():
        print(f"{key}: {board.describe()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("wolf", description="本地狼人杀 LLM 训练营")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="跑对局")
    r.add_argument("--models", default=str(DEFAULT_MODELS))
    r.add_argument("--match", default=str(DEFAULT_MATCH))
    r.add_argument("--games", type=int)
    r.add_argument("--board")
    r.add_argument("--players", help="逗号分隔的模型 key，长度需等于座位数")
    r.add_argument("--parallel-games", dest="parallel_games", type=int)
    r.add_argument("--parallel-agents", dest="parallel_agents", type=int)
    r.add_argument("--seed", type=int)
    r.add_argument("--archive")
    r.add_argument("--tag")
    r.set_defaults(func=cmd_run)

    rp = sub.add_parser("replay", help="为某一局生成 HTML 复盘")
    rp.add_argument("game", help="game_id 或档案目录")
    rp.add_argument("--archive", default="archive")
    rp.set_defaults(func=cmd_replay)

    lb = sub.add_parser("leaderboard", help="统计排行榜")
    lb.add_argument("--archive", default="archive")
    lb.add_argument("--json", action="store_true")
    lb.set_defaults(func=cmd_leaderboard)

    b = sub.add_parser("boards", help="列出内置板子")
    b.set_defaults(func=cmd_boards)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
