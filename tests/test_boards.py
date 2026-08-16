"""扩展板子与扩展角色的规则测试。

覆盖狼王开枪、机械狼扫描、通灵师查死人、舞者封技能、假面抗推，
以及「所有板子都能跑完、且不产生非法行动」。
"""

import glob

import pytest

from wolf.arena import load_board
from wolf.roles import BUILTIN_BOARDS, Camp, Role, camp_of, is_god

NEW_BOARDS = ["board_wolfking", "board_mechanic", "board_dancer", "board_mixed"]


# ======================================================================
# 板子本身
# ======================================================================
@pytest.mark.parametrize("key", NEW_BOARDS)
def test_board_pool_matches_seats(key):
    board = BUILTIN_BOARDS[key]
    assert len(board.role_pool()) == board.seats
    assert any(camp_of(r) is Camp.WOLF for r in board.roles_present())
    assert any(is_god(r) for r in board.roles_present())


@pytest.mark.parametrize("path", sorted(glob.glob("configs/board_*.toml")))
def test_shipped_board_toml_files_load(path):
    """TOML 里的中文角色名必须加引号，否则 tomllib 直接解析失败。"""
    board = load_board(path)
    assert len(board.role_pool()) == board.seats


def test_board_scoped_enums_only_contain_present_roles():
    dancer = BUILTIN_BOARDS["board_dancer"]
    assert "假面" in dancer.claim_options()
    assert "守卫" not in dancer.claim_options()  # 这副板子里没有守卫
    assert "隐藏" in dancer.claim_options()
    assert {"好人", "不确定"} <= set(dancer.guess_options())

    from wolf.schemas import schema_for, stances_for

    assert "悍跳舞者" in stances_for(dancer)
    assert "悍跳守卫" not in stances_for(dancer)
    assert schema_for("speech", True, dancer)["properties"]["claim"]["enum"] == dancer.claim_options()


def test_rules_text_only_mentions_present_roles():
    text = BUILTIN_BOARDS["board_dancer"].rules_text()
    assert "舞者" in text and "假面" in text
    assert "守卫" not in text and "猎人" not in text
    assert "狼王" in BUILTIN_BOARDS["board_wolfking"].rules_text()


@pytest.mark.parametrize("key", NEW_BOARDS)
@pytest.mark.parametrize("seed", [1, 2, 3, 11, 42])
def test_new_boards_always_terminate(engine_factory, key, seed):
    engine = engine_factory(board_key=key, seed=seed)
    result = engine.run()
    assert result.winner in ("好人", "狼人", "平局")
    assert 1 <= result.days <= BUILTIN_BOARDS[key].rules.max_days
    assert result.errors == 0


# ======================================================================
# 狼王
# ======================================================================
def test_wolf_king_shoots_when_exiled_but_never_when_poisoned(engine_factory):
    shot_when_exiled = False
    for seed in range(30):
        engine = engine_factory(board_key="board_wolfking", seed=seed)
        engine.run()
        st = engine.state
        king = next(s for s, p in st.players.items() if p.role is Role.WOLF_KING)
        shots = [
            ev for ev in engine.archive.event_list
            if ev.type == "wolf_king_shot" and ev.actor == king
        ]
        cause = st.players[king].death_cause
        if cause == "毒杀":
            assert not shots, "被毒死的狼王不应该开枪"
        elif cause in ("放逐", "枪杀"):
            assert shots, f"{cause}出局的狼王应该可以开枪"
            shot_when_exiled = True
        else:  # 存活 / 被狼刀（不可能）
            assert not shots
    assert shot_when_exiled, "30 局里一次狼王开枪都没触发，测试没有真正覆盖到"


def test_wolf_king_gun_kills_a_living_player(engine_factory):
    for seed in range(30):
        engine = engine_factory(board_key="board_wolfking", seed=seed)
        engine.run()
        for ev in engine.archive.event_list:
            if ev.type == "wolf_king_shot" and ev.targets:
                victim = engine.state.players[ev.targets[0]]
                assert not victim.alive
                assert victim.death_cause == "枪杀"


# ======================================================================
# 机械狼 / 通灵师
# ======================================================================
def test_mechanic_scan_verdict_is_truthful_and_stays_in_the_wolf_channel(engine_factory):
    seen = 0
    for seed in range(10):
        engine = engine_factory(board_key="board_mechanic", seed=seed)
        engine.run()
        st = engine.state
        wolves = set(st.wolf_seats())
        for ev in engine.archive.event_list:
            if ev.type != "mechanic_scan":
                continue
            seen += 1
            target = ev.data["target"]
            expect = "神职" if is_god(st.players[target].role) else "非神职"
            assert ev.data["verdict"] == expect
            assert set(ev.audience) <= wolves, "机械狼的扫描结果泄漏给了好人"
            assert target not in wolves, "机械狼不应该把扫描浪费在狼队友身上"
    assert seen, "没有产生任何扫描事件"


def test_psychic_only_reads_dead_players_and_reads_them_correctly(engine_factory):
    seen = 0
    for seed in range(10):
        engine = engine_factory(board_key="board_mechanic", seed=seed)
        engine.run()
        st = engine.state
        for ev in engine.archive.event_list:
            if ev.type != "psychic_check":
                continue
            seen += 1
            target = ev.data["target"]
            assert not st.players[target].alive, "通灵师不能查活人"
            assert st.players[target].death_day <= ev.day, "通灵师查到了还没出局的人"
            assert ev.data["verdict"] == st.players[target].role.value
            assert set(ev.audience) == {ev.actor}
    assert seen, "没有产生任何通灵事件"


def test_psychic_is_idle_on_the_first_night(engine_factory):
    engine = engine_factory(board_key="board_mechanic", seed=4)
    engine.run()
    first_night = [
        ev for ev in engine.archive.event_list
        if ev.day == 1 and ev.type in ("psychic_check", "psychic_idle")
    ]
    assert first_night and all(ev.type == "psychic_idle" for ev in first_night)


# ======================================================================
# 舞者
# ======================================================================
def test_dancer_never_invites_the_same_player_twice_in_a_row(engine_factory):
    for seed in range(10):
        engine = engine_factory(board_key="board_dancer", seed=seed)
        engine.run()
        last = None
        for ev in engine.archive.event_list:
            if ev.type == "dancer_dance" and ev.targets:
                assert ev.targets[0] != last, "舞者连续两晚邀请了同一个人"
                assert ev.targets[0] != ev.actor, "舞者不能邀请自己"
                last = ev.targets[0]


def test_danced_player_cannot_use_any_night_skill(engine_factory):
    """被封的人当晚不能留下任何技能事件（狼人则不参与刀人投票）。"""
    blocked_nights = 0
    skill_events = {
        "seer_check", "guard_protect", "witch_action", "psychic_check", "mechanic_scan",
    }
    for seed in range(20):
        engine = engine_factory(board_key="board_mixed", seed=seed)
        engine.run()
        danced: dict[int, int] = {}  # day -> 被封的座位
        for ev in engine.archive.event_list:
            if ev.type == "dancer_dance" and ev.targets:
                danced[ev.day] = ev.targets[0]
        for ev in engine.archive.event_list:
            seat = danced.get(ev.day)
            if seat is None or ev.actor != seat:
                continue
            assert ev.type not in skill_events, f"{seat}号被封了却在第{ev.day}夜用了 {ev.type}"
            if ev.type == "wolf_talk":
                raise AssertionError(f"{seat}号被封了却参与了第{ev.day}夜的狼队刀人")
        blocked_nights += len(danced)
    assert blocked_nights, "没有任何一晚封到人"


def test_danced_player_is_notified(engine_factory):
    """每一次共舞都恰好对应一条私密通知——被封的哪怕是平民也会知道。"""
    blocked_roles = set()
    for seed in range(25):
        engine = engine_factory(board_key="board_mixed", seed=seed)
        engine.run()
        dances = [
            (ev.day, ev.targets[0])
            for ev in engine.archive.event_list
            if ev.type == "dancer_dance" and ev.targets
        ]
        notices = [
            (ev.day, ev.actor)
            for ev in engine.archive.event_list
            if ev.type == "skill_blocked"
        ]
        assert dances == notices
        for ev in engine.archive.event_list:
            if ev.type == "skill_blocked":
                assert set(ev.audience) == {ev.actor}, "被封通知只能本人可见"
        blocked_roles |= {engine.state.players[s].role for _, s in dances}
    assert Role.VILLAGER in blocked_roles, "没有采样到「封平民」的情况"


def test_dancer_feedback_matches_the_partner_role(engine_factory):
    """「有夜间技能」的反馈必须和舞伴的真实身份一致。"""
    no_skill = {Role.VILLAGER, Role.HUNTER, Role.MASK}
    checked = 0
    for seed in range(15):
        engine = engine_factory(board_key="board_mixed", seed=seed)
        engine.run()
        st = engine.state
        for ev in engine.archive.event_list:
            if ev.type != "dancer_dance" or not ev.targets:
                continue
            role = st.players[ev.targets[0]].role
            if role in no_skill:
                assert "【没有】" in ev.text, f"{role.value} 不该被判成有夜间技能"
                checked += 1
            elif role in (Role.SEER, Role.GUARD, Role.DANCER) or camp_of(role) is Camp.WOLF:
                assert "【有】" in ev.text, f"{role.value} 应该被判成有夜间技能"
                checked += 1
    assert checked, "没有采样到任何共舞反馈"


# ======================================================================
# 假面
# ======================================================================
def test_mask_survives_the_first_exile_then_dies_on_the_second(engine_factory):
    revealed = 0
    for seed in range(30):
        engine = engine_factory(board_key="board_dancer", seed=seed)
        engine.run()
        st = engine.state
        mask = next(s for s, p in st.players.items() if p.role is Role.MASK)
        reveals = [ev for ev in engine.archive.event_list if ev.type == "mask_reveal"]
        exiles = [
            ev for ev in engine.archive.event_list
            if ev.type == "exile" and ev.targets == [mask]
        ]
        assert len(reveals) <= 1, "假面只能免疫一次放逐"
        if reveals:
            revealed += 1
            assert reveals[0].actor == mask
            assert st.mask_immunity[mask] == 0
            assert mask in st.no_vote_seats, "揭面后应当失去投票权"
            # 免疫的那一天，假面确实没有出局
            assert st.players[mask].death_day != reveals[0].day or st.players[mask].death_cause != "放逐"
        else:
            assert not exiles or st.players[mask].death_cause == "放逐"
    assert revealed, "30 局里假面一次都没被推，测试没有真正覆盖到"


def test_revealed_mask_never_votes_again(engine_factory):
    for seed in range(30):
        engine = engine_factory(board_key="board_dancer", seed=seed)
        engine.run()
        reveal = next(
            (ev for ev in engine.archive.event_list if ev.type == "mask_reveal"), None
        )
        if reveal is None:
            continue
        mask = reveal.actor
        for ev in engine.archive.event_list:
            if ev.type == "vote_declare" and ev.actor == mask:
                assert ev.day <= reveal.day, "揭面之后的假面还在投票"
        for round_ in engine.vote_history:
            if round_["day"] > reveal.day:
                assert str(mask) not in round_["votes"]


def test_revealed_mask_can_still_be_voted_out(engine_factory):
    """失去投票权 ≠ 不能被投：假面翻牌后仍然是合法的放逐目标，第二次被推就真的出局。"""
    engine = engine_factory(board_key="board_dancer", seed=28)
    engine.run()
    st = engine.state
    reveal = next(ev for ev in engine.archive.event_list if ev.type == "mask_reveal")
    mask = reveal.actor

    # 翻牌之后他还被投出去过一次，而且这一次真的死了
    assert st.players[mask].death_cause == "放逐"
    assert st.players[mask].death_day > reveal.day
    second = [
        ev for ev in engine.archive.event_list
        if ev.type == "exile" and ev.targets == [mask]
    ]
    assert len(second) == 1
    # 但他自己从翻牌那天起再也没投过票
    assert all(
        str(mask) not in r["votes"] for r in engine.vote_history if r["day"] > reveal.day
    )
