import json

import pytest

from wolf.roles import BUILTIN_BOARDS, Camp, Role


@pytest.mark.parametrize("board_key", ["board_9", "board_12"])
@pytest.mark.parametrize("seed", [1, 2, 3, 11, 42])
def test_game_always_terminates_with_a_result(engine_factory, board_key, seed):
    engine = engine_factory(board_key=board_key, seed=seed)
    result = engine.run()

    assert result.winner in ("好人", "狼人", "平局")
    assert 1 <= result.days <= BUILTIN_BOARDS[board_key].rules.max_days
    assert len(result.players) == BUILTIN_BOARDS[board_key].seats
    assert result.errors == 0


def test_win_condition_matches_final_state(engine_factory):
    engine = engine_factory(seed=5)
    result = engine.run()
    counts = engine.state.counts()

    if result.winner == "好人":
        assert counts["wolf"] == 0
    elif result.winner == "狼人":
        # 屠边：神职或平民被杀光
        assert counts["god"] == 0 or counts["villager"] == 0


def test_dead_players_never_act_again(engine_factory):
    engine = engine_factory(seed=9)
    engine.run()
    deaths = {
        s: p.death_day for s, p in engine.state.players.items() if not p.alive
    }
    for rec in engine.archive.mind_records:
        if rec.seat in deaths and rec.kind not in ("last_words", "hunter_shot"):
            assert rec.day <= deaths[rec.seat], (
                f"{rec.seat}号在第{deaths[rec.seat]}天出局，却在第{rec.day}天执行了 {rec.kind}"
            )


def test_witch_potions_are_single_use(engine_factory):
    engine = engine_factory(seed=3)
    engine.run()
    antidotes = poisons = 0
    for ev in engine.archive.event_list:
        if ev.type == "witch_action":
            antidotes += int(bool(ev.data.get("antidote")))
            poisons += int(bool(ev.data.get("poison")))
    assert antidotes <= 1
    assert poisons <= 1


def test_guard_never_protects_same_target_twice_in_a_row(engine_factory):
    engine = engine_factory(board_key="board_12", seed=13)
    engine.run()
    last = None
    for ev in engine.archive.event_list:
        if ev.type == "guard_protect" and ev.targets:
            assert ev.targets[0] != last, "守卫连守了同一个人"
            last = ev.targets[0]


def test_hunter_cannot_shoot_after_being_poisoned(engine_factory):
    for seed in range(20):
        engine = engine_factory(seed=seed)
        engine.run()
        hunter = engine.state.role_seat(Role.HUNTER) or next(
            (s for s, p in engine.state.players.items() if p.role is Role.HUNTER), None
        )
        if hunter is None:
            continue
        p = engine.state.players[hunter]
        if p.death_cause == "毒杀":
            shots = [
                ev for ev in engine.archive.event_list
                if ev.type == "hunter_shot" and ev.actor == hunter
            ]
            assert not shots, "被毒死的猎人不应该开枪"


def test_archive_files_are_written(engine_factory, tmp_path):
    engine = engine_factory(seed=4)
    result = engine.run()
    d = engine.archive.dir
    for name in ("meta.json", "events.jsonl", "minds.jsonl", "transcript.md"):
        assert (d / name).exists(), f"缺少 {name}"

    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["winner"] == result.winner
    minds = [json.loads(l) for l in (d / "minds.jsonl").read_text(encoding="utf-8").splitlines()]
    assert minds, "没有记录任何心理活动"
    assert all("thinking" in m for m in minds)
    # 每条心理活动都必须能对应回一个事件
    idxs = {ev.idx for ev in engine.archive.event_list}
    assert all(m["meta"]["event_idx"] in idxs for m in minds)


def test_parallel_voting_matches_sequential_shape(engine_factory):
    engine = engine_factory(seed=8, parallel=4)
    result = engine.run()
    assert result.winner in ("好人", "狼人", "平局")
    for round_ in engine.vote_history:
        voters = set(int(k) for k in round_["votes"])
        # 没有人投自己
        assert all(int(k) != v for k, v in round_["votes"].items() if v)
        assert voters, "投票轮里没有任何投票者"
