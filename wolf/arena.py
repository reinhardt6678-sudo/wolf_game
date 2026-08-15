"""训练营：批量对局、模型轮转、并发调度。"""

from __future__ import annotations

import random
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agents.heuristic_agent import HeuristicAgent
from .agents.llm_agent import LLMAgent
from .archive import Archive
from .engine import Engine, GameResult
from .llm import build_client
from .roles import BUILTIN_BOARDS, Board, Role, Rules

HEURISTIC_KEYS = {"heuristic", "baseline", "rule"}


def load_toml(path: str | Path) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def load_board(spec: Any) -> Board:
    """``spec`` 可以是内置板子名，也可以是一个 dict。"""
    if isinstance(spec, str):
        if spec in BUILTIN_BOARDS:
            return BUILTIN_BOARDS[spec]
        return load_board(load_toml(spec))
    rules = Rules(**(spec.get("rules") or {}))
    counts = {Role(k): int(v) for k, v in (spec.get("roles") or {}).items()}
    return Board(
        name=spec.get("name", "自定义板子"),
        seats=int(spec["seats"]),
        role_counts=counts,
        rules=rules,
    )


@dataclass
class MatchConfig:
    board: Board
    players: list[str]
    games: int = 1
    rotate: bool = True
    seed: int | None = None
    archive_root: str = "archive"
    parallel_games: int = 1
    parallel_agents: int = 1
    tag: str = "match"

    @classmethod
    def from_dict(cls, d: dict) -> "MatchConfig":
        board = load_board(d.get("board", "board_9"))
        players = list(d["players"])
        if len(players) != board.seats:
            raise ValueError(
                f"players 数量({len(players)})必须等于板子座位数({board.seats})"
            )
        return cls(
            board=board,
            players=players,
            games=int(d.get("games", 1)),
            rotate=bool(d.get("rotate", True)),
            seed=d.get("seed"),
            archive_root=d.get("archive", "archive"),
            parallel_games=int(d.get("parallel_games", 1)),
            parallel_agents=int(d.get("parallel_agents", 1)),
            tag=d.get("tag", "match"),
        )


class Arena:
    def __init__(self, models: dict[str, dict], cfg: MatchConfig, *, verbose: bool = True) -> None:
        self.models = models
        self.cfg = cfg
        self.verbose = verbose
        self._clients: dict[str, Any] = {}
        for key in set(cfg.players):
            if key in HEURISTIC_KEYS:
                continue
            if key not in models:
                raise ValueError(f"players 里用到的模型 `{key}` 不在 models 配置中")
            self._clients[key] = build_client(key, models[key])

    # ------------------------------------------------------------------
    def _seating(self, game_idx: int) -> dict[int, str]:
        players = list(self.cfg.players)
        if self.cfg.rotate:
            k = game_idx % len(players)
            players = players[k:] + players[:k]
        return {i + 1: players[i] for i in range(len(players))}

    def _make_agent(self, seat: int, model_key: str, rng: random.Random):
        if model_key in HEURISTIC_KEYS:
            return HeuristicAgent(model_key=model_key, rng=random.Random(rng.random()))
        return LLMAgent(self._clients[model_key], model_key=model_key, rng=random.Random(rng.random()))

    def run_game(self, game_idx: int) -> GameResult:
        cfg = self.cfg
        seed = None if cfg.seed is None else cfg.seed + game_idx
        rng = random.Random(seed)
        game_id = f"{cfg.tag}-{time.strftime('%Y%m%d-%H%M%S')}-{game_idx:03d}"
        model_by_seat = self._seating(game_idx)
        archive = Archive(cfg.archive_root, game_id)

        def factory(seat: int, model_key: str):
            return self._make_agent(seat, model_key, rng)

        def progress(msg: str) -> None:
            if self.verbose:
                print(f"  [{game_id}] {msg}", flush=True)

        engine = Engine(
            game_id=game_id,
            board=cfg.board,
            agent_factory=factory,
            model_by_seat=model_by_seat,
            archive=archive,
            seed=seed,
            parallel=cfg.parallel_agents,
            on_progress=progress,
        )
        result = engine.run()
        if self.verbose:
            print(
                f"✔ {game_id}: {result.winner}获胜（{result.end_reason}），"
                f"{result.days} 天，调用失败 {result.errors} 次",
                flush=True,
            )
        return result

    def run(self) -> list[GameResult]:
        n = self.cfg.games
        if self.cfg.parallel_games <= 1:
            return [self.run_game(i) for i in range(n)]
        results: list[GameResult] = []
        with ThreadPoolExecutor(max_workers=self.cfg.parallel_games) as ex:
            futures = [ex.submit(self.run_game, i) for i in range(n)]
            for f in as_completed(futures):
                results.append(f.result())
        return sorted(results, key=lambda r: r.game_id)
