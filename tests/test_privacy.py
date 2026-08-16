"""隐私边界测试。

这是整个项目最重要的一组断言：心理活动必须只存在于档案里，
绝不能通过任何路径流回其他智能体的上下文。
"""

import json
import random

import pytest

from wolf.agents.heuristic_agent import HeuristicAgent
from wolf.events import Visibility
from wolf.prompts import render_events
from wolf.roles import Camp, Role

TOKEN = "秘密标记"


class LeakyAgent(HeuristicAgent):
    """在心理活动/备忘/信念里塞入唯一标记，用来追踪泄漏。"""

    def act(self, req):
        d = super().act(req)
        d.thinking = f"{TOKEN}-THINK-{self.seat} " + d.thinking
        d.notes = f"{TOKEN}-NOTES-{self.seat}"
        d.beliefs = [{"seat": 1, "guess": "狼人", "confidence": 0.9, "reason": f"{TOKEN}-BELIEF-{self.seat}"}]
        if d.scheme:
            d.scheme["reason"] = f"{TOKEN}-SCHEME-{self.seat} " + d.scheme.get("reason", "")
        return d


@pytest.fixture(params=["board_9", "board_12", "board_wolfking", "board_mechanic", "board_masquerade"])
def leaky_game(engine_factory, request):
    """每副板子都跑一遍：新角色的私密事件同样不能泄漏。"""
    engine = engine_factory(board_key=request.param, seed=17, agent_cls=LeakyAgent)
    engine.run()
    return engine


def test_thinking_never_appears_in_any_event(leaky_game):
    raw = (leaky_game.archive.dir / "events.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in raw, "心理活动泄漏进了事件流"


def test_thinking_never_appears_in_any_agent_observation(leaky_game):
    """逐座位重建可见时间线，确认没有任何人能看到别人的心理活动。"""
    for seat in leaky_game.state.seats():
        visible = leaky_game.state.log.visible(seat)
        text = render_events(visible, seat)
        assert TOKEN not in text, f"{seat}号的可见信息里出现了心理活动标记"


def test_thinking_is_present_in_the_archive(leaky_game):
    """反向断言：档案里必须留下心理活动，否则上面的测试是假阳性。"""
    raw = (leaky_game.archive.dir / "minds.jsonl").read_text(encoding="utf-8")
    assert f"{TOKEN}-THINK" in raw
    assert f"{TOKEN}-NOTES" in raw
    assert f"{TOKEN}-BELIEF" in raw


def test_wolf_channel_is_visible_only_to_wolves(leaky_game):
    """狼队频道（狼聊、刀口、机械狼的扫描结果）只能被狼看到。

    注意反向不成立：已经出局的狼不在当晚的听众里，这是对的。
    """
    st = leaky_game.state
    wolves = set(st.wolf_seats())
    wolf_events = 0
    for ev in leaky_game.archive.event_list:
        if ev.visibility is not Visibility.WOLF:
            continue
        wolf_events += 1
        assert set(ev.audience) <= wolves, "狼队频道被好人看到了"
        for seat in st.seats():
            if ev.visible_to(seat):
                assert seat in wolves, f"{seat}号不是狼，却能看到狼队频道"
    assert wolf_events, "这一局没有产生任何狼队频道事件"


PRIVATE_TYPES = (
    "seer_check",
    "witch_action",
    "guard_protect",
    "role_assign",
    "psychic_check",
    "dance_invite",
    "dance_skipped",
    "dance_feedback",
    "mask_action",
    "mechanic_learn",
    "mechanic_guard",
    "mechanic_psychic",
    "mechanic_double_kill",
)


def test_seer_results_are_private(leaky_game):
    """所有单人可见的技能结果（含通灵、共舞、被封通知）只有本人看得到。"""
    st = leaky_game.state
    for ev in leaky_game.archive.event_list:
        if ev.type in PRIVATE_TYPES:
            assert ev.visibility is Visibility.PRIVATE
            assert set(ev.audience) == {ev.actor}
            for seat in st.seats():
                assert ev.visible_to(seat) == (seat == ev.actor)


def test_god_events_are_visible_to_nobody(leaky_game):
    st = leaky_game.state
    god_events = [e for e in leaky_game.archive.event_list if e.visibility is Visibility.GOD]
    assert god_events, "应当存在上帝视角事件（身份表、夜间结算）"
    for ev in god_events:
        assert all(not ev.visible_to(seat) for seat in st.seats())


def test_roles_are_not_leaked_before_game_over(leaky_game):
    """在游戏结束前，公开事件里不应出现别人的身份。"""
    st = leaky_game.state
    for ev in leaky_game.archive.event_list:
        if ev.visibility is not Visibility.PUBLIC or ev.type == "game_over":
            continue
        if ev.type in ("setup", "speech_order"):
            continue
        for seat, p in st.players.items():
            if ev.actor == seat:
                continue  # 自己宣称身份是允许的（可能是假的）
            marker = f"{seat}号={p.role.value}"
            assert marker not in ev.text


def test_wolves_know_teammates_and_others_do_not(leaky_game):
    """只有「见面的狼」拿得到队友名单；机械狼/假面和好人一样什么都看不到。"""
    st = leaky_game.state
    pack = set(st.pack_wolf_seats(alive_only=False))
    for seat in st.seats():
        text = render_events(st.log.visible(seat), seat)
        if seat in pack and pack - {seat}:
            assert "狼队友" in text
        else:
            assert "狼队友" not in text, f"{seat}号不该拿到狼队友名单"


def test_lone_wolves_are_told_they_are_alone(leaky_game):
    """机械狼/假面必须知道自己是不见面的那一只，否则它会以为狼队没人说话。"""
    st = leaky_game.state
    for seat in st.lone_wolf_seats(alive_only=False):
        text = render_events(st.log.visible(seat), seat)
        assert "你不与狼队见面" in text
