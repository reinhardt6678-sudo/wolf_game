import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wolf.agents.heuristic_agent import HeuristicAgent  # noqa: E402
from wolf.archive import Archive  # noqa: E402
from wolf.engine import Engine  # noqa: E402
from wolf.roles import BUILTIN_BOARDS  # noqa: E402


def build_engine(tmp_path, board_key="board_9", seed=7, agent_cls=None, parallel=1):
    board = BUILTIN_BOARDS[board_key]
    rng = random.Random(seed)
    cls = agent_cls or HeuristicAgent

    def factory(seat, model_key):
        return cls(model_key=model_key, rng=random.Random(rng.random()))

    archive = Archive(tmp_path, f"test-{board_key}-{seed}")
    return Engine(
        game_id=f"test-{board_key}-{seed}",
        board=board,
        agent_factory=factory,
        model_by_seat={i: "heuristic" for i in range(1, board.seats + 1)},
        archive=archive,
        seed=seed,
        parallel=parallel,
    )


@pytest.fixture
def engine_factory(tmp_path):
    def _make(**kw):
        return build_engine(tmp_path, **kw)

    return _make
