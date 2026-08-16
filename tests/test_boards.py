"""扩展板子的规则测试。

板子按京城大师赛的公开规则实现：
- 假面舞会（舞者 3 人舞池 / 少数派出局，假面翻转阵营，白痴抗推）
- 机械狼通灵师（通灵师查具体身份，机械狼学一张牌、与小狼不见面）
- 狼王守卫（标准狼王枪）
"""

import glob

import pytest

from wolf.arena import load_board
from wolf.roles import BUILTIN_BOARDS, Camp, Role, camp_of, is_god, is_pack_wolf

NEW_BOARDS = ["board_wolfking", "board_mechanic", "board_masquerade", "board_masquerade_10"]


# ======================================================================
# 板子本身
# ======================================================================
@pytest.mark.parametrize("key", NEW_BOARDS)
def test_board_pool_matches_seats(key):
    board = BUILTIN_BOARDS[key]
    assert len(board.role_pool()) == board.seats
    assert any(camp_of(r) is Camp.WOLF for r in board.roles_present())
    assert any(is_god(r) for r in board.roles_present())


def test_masquerade_matches_the_published_lineup():
    """12人：狼×3+假面 / 预言家、女巫、舞者、白痴 + 平民×4。"""
    assert BUILTIN_BOARDS["board_masquerade"].role_counts == {
        Role.WEREWOLF: 3,
        Role.MASK: 1,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.DANCER: 1,
        Role.IDIOT: 1,
        Role.VILLAGER: 4,
    }
    # 10人局去掉一狼一白痴
    assert BUILTIN_BOARDS["board_masquerade_10"].role_counts == {
        Role.WEREWOLF: 2,
        Role.MASK: 1,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.DANCER: 1,
        Role.VILLAGER: 4,
    }


def test_mechanic_board_matches_the_published_lineup():
    """12人：机械狼+小狼×3 / 通灵师、女巫、猎人、守卫 + 平民×4（没有预言家）。"""
    counts = BUILTIN_BOARDS["board_mechanic"].role_counts
    assert counts == {
        Role.WEREWOLF: 3,
        Role.MECHANIC_WOLF: 1,
        Role.PSYCHIC: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.GUARD: 1,
        Role.VILLAGER: 4,
    }
    assert Role.SEER not in counts


def test_mask_and_mechanic_are_wolves_that_do_not_meet_the_pack():
    assert camp_of(Role.MASK) is Camp.WOLF
    assert camp_of(Role.MECHANIC_WOLF) is Camp.WOLF
    assert not is_pack_wolf(Role.MASK)
    assert not is_pack_wolf(Role.MECHANIC_WOLF)
    assert is_pack_wolf(Role.WEREWOLF) and is_pack_wolf(Role.WOLF_KING)
    # 白痴是好人神职，不是狼
    assert camp_of(Role.IDIOT) is Camp.GOOD and is_god(Role.IDIOT)


@pytest.mark.parametrize("path", sorted(glob.glob("configs/board_*.toml")))
def test_shipped_board_toml_files_load(path):
    """TOML 里的中文角色名必须加引号，否则 tomllib 直接解析失败。"""
    board = load_board(path)
    assert len(board.role_pool()) == board.seats


def test_board_scoped_enums_only_contain_present_roles():
    m = BUILTIN_BOARDS["board_masquerade"]
    assert "假面" in m.claim_options() and "白痴" in m.claim_options()
    assert "守卫" not in m.claim_options()  # 这副板子里没有守卫

    from wolf.schemas import schema_for, stances_for

    assert "悍跳舞者" in stances_for(m)
    assert "悍跳守卫" not in stances_for(m)
    assert schema_for("speech", True, m)["properties"]["claim"]["enum"] == m.claim_options()


def test_rules_text_only_mentions_present_roles():
    text = BUILTIN_BOARDS["board_masquerade"].rules_text()
    assert "舞池" in text and "面具" in text and "白痴" in text
    assert "机械狼" not in text
    assert "双刀" in BUILTIN_BOARDS["board_mechanic"].rules_text()


@pytest.mark.parametrize("key", NEW_BOARDS)
@pytest.mark.parametrize("seed", [1, 2, 3, 11, 42])
def test_new_boards_always_terminate(engine_factory, key, seed):
    engine = engine_factory(board_key=key, seed=seed)
    result = engine.run()
    assert result.winner in ("好人", "狼人", "平局")
    assert 1 <= result.days <= BUILTIN_BOARDS[key].rules.max_days
    assert result.errors == 0


# ======================================================================
# 舞池结算（这是假面舞会的核心，用构造状态直接验算）
# ======================================================================
def _seat_of(engine, role):
    return next(s for s, p in engine.state.players.items() if p.role is role)


def _seats_of_camp(engine, camp, exclude=()):
    return [
        s
        for s, p in engine.state.players.items()
        if p.camp is camp and s not in exclude
    ]


@pytest.mark.parametrize(
    "n_wolf,n_good,expect_out_camp",
    [
        (1, 2, Camp.WOLF),  # 2好1狼 → 狼是少数派，狼出局
        (2, 1, Camp.GOOD),  # 2狼1好 → 好人是少数派，好人出局
        (3, 0, None),  # 全狼 → 无人出局
        (0, 3, None),  # 全好 → 无人出局
    ],
)
def test_dance_pool_minority_dies(engine_factory, n_wolf, n_good, expect_out_camp):
    engine = engine_factory(board_key="board_masquerade", seed=1)
    st = engine.state
    wolves = _seats_of_camp(engine, Camp.WOLF)[:n_wolf]
    goods = _seats_of_camp(engine, Camp.GOOD)[:n_good]
    st.dance_pool = sorted(wolves + goods)
    engine.masked = None

    out = engine._resolve_dance_pool()
    if expect_out_camp is None:
        assert out == [], "三人同阵营时不应该有人出局"
    else:
        assert out, "阵营不同时少数派必须出局"
        assert all(st.players[s].camp is expect_out_camp for s in out)
        assert len(out) == min(n_wolf, n_good)


def test_mask_flips_the_pool_settlement(engine_factory):
    """2好1狼本该死狼；给一个好人戴上面具，翻转后变成 2狼1好，死的是另一个好人。"""
    engine = engine_factory(board_key="board_masquerade", seed=1)
    st = engine.state
    wolf = _seats_of_camp(engine, Camp.WOLF)[0]
    good_a, good_b = _seats_of_camp(engine, Camp.GOOD)[:2]
    st.dance_pool = sorted([wolf, good_a, good_b])

    engine.masked = None
    assert engine._resolve_dance_pool() == [wolf]

    engine.masked = good_a  # 好人 A 戴面具 → 结算时算作狼
    assert engine._resolve_dance_pool() == [good_b], "面具没有翻转结算阵营"


def test_mask_can_wear_the_mask_itself(engine_factory):
    """假面给自己戴面具：结算时它算好人，于是 2狼1好里出局的是真好人。"""
    engine = engine_factory(board_key="board_masquerade", seed=1)
    st = engine.state
    mask = _seat_of(engine, Role.MASK)
    wolf = _seats_of_camp(engine, Camp.WOLF, exclude={mask})[0]
    good = _seats_of_camp(engine, Camp.GOOD)[0]
    st.dance_pool = sorted([mask, wolf, good])

    engine.masked = None
    assert engine._resolve_dance_pool() == [good]  # 2狼(假面+狼)1好 → 好人出局

    engine.masked = mask
    assert engine._resolve_dance_pool() == [wolf]  # 假面算好人 → 2好1狼 → 狼出局


def test_dance_pool_is_exactly_three_and_never_reuses_a_player(engine_factory):
    for seed in range(15):
        engine = engine_factory(board_key="board_masquerade", seed=seed)
        engine.run()
        seen: set[int] = set()
        for ev in engine.archive.event_list:
            if ev.type != "dance_invite":
                continue
            pool = ev.data["pool"]
            assert len(pool) == 3 == len(set(pool)), "舞池必须是 3 个互不重复的人"
            assert not (seen & set(pool)), "有人第二次进了舞池"
            seen |= set(pool)
            assert ev.day >= 2, "舞者从第二夜才开始行动"


def test_dancer_in_the_pool_makes_the_pool_immune_to_the_knife(engine_factory):
    """舞者自己进池的那一晚，池中三人免疫狼刀。"""
    checked = 0
    for seed in range(30):
        engine = engine_factory(board_key="board_masquerade", seed=seed)
        engine.run()
        dancer = _seat_of(engine, Role.DANCER)
        pools = {ev.day: ev.data["pool"] for ev in engine.archive.event_list if ev.type == "dance_invite"}
        kills = {
            ev.day: ev.targets[0]
            for ev in engine.archive.event_list
            if ev.type == "wolf_decision" and ev.targets
        }
        for day, pool in pools.items():
            if dancer not in pool or day not in kills:
                continue
            if kills[day] in pool:
                deaths = [
                    d
                    for ev in engine.archive.event_list
                    if ev.type == "night_resolution" and ev.day == day
                    for d in ev.data["deaths"]
                ]
                assert not any(
                    d["seat"] == kills[day] and d["cause"] == "狼杀" for d in deaths
                ), "舞者在池中，池内成员不该被刀死"
                checked += 1
    assert checked, "没有采样到「舞者在池中且狼刀砍进池里」的情况"


def test_dancer_and_mask_are_immune_to_poison(engine_factory):
    for seed in range(30):
        engine = engine_factory(board_key="board_masquerade", seed=seed)
        engine.run()
        for role in (Role.DANCER, Role.MASK):
            seat = _seat_of(engine, role)
            assert engine.state.players[seat].death_cause != "毒杀", f"{role.value}不该被毒死"


# ======================================================================
# 假面 / 机械狼：不与狼队见面
# ======================================================================
@pytest.mark.parametrize(
    "key,role", [("board_masquerade", Role.MASK), ("board_mechanic", Role.MECHANIC_WOLF)]
)
def test_lone_wolf_never_sees_the_wolf_channel(engine_factory, key, role):
    for seed in range(10):
        engine = engine_factory(board_key=key, seed=seed)
        engine.run()
        st = engine.state
        seat = _seat_of(engine, role)
        pack = set(st.pack_wolf_seats(alive_only=False))
        for ev in engine.archive.event_list:
            if ev.visibility.value != "wolf" or not ev.visible_to(seat):
                continue
            # 小狼死光后它自己接刀、自己开一个频道是允许的；
            # 但它绝不能和任何一只小狼待在同一个频道里。
            assert not (set(ev.audience) & pack), f"{role.value}和小狼共用了狼队频道"
        # 小狼也拿不到它的名字
        text = "\n".join(
            ev.text for ev in engine.archive.event_list
            if ev.type == "role_assign" and ev.actor in st.pack_wolf_seats(alive_only=False)
        )
        assert f"{seat}号" not in text, f"小狼的队友名单里出现了{role.value}"


@pytest.mark.parametrize(
    "key,role", [("board_masquerade", Role.MASK), ("board_mechanic", Role.MECHANIC_WOLF)]
)
def test_lone_wolf_only_gets_the_knife_after_the_pack_is_dead(engine_factory, key, role):
    took_over = 0
    for seed in range(30):
        engine = engine_factory(board_key=key, seed=seed)
        engine.run()
        st = engine.state
        seat = _seat_of(engine, role)
        pack_deaths = [
            st.players[s].death_day
            for s in st.pack_wolf_seats(alive_only=False)
        ]
        for ev in engine.archive.event_list:
            if ev.type != "wolf_talk" or ev.actor != seat:
                continue
            # 它开口的那一晚，小狼必须已经全部出局
            assert all(d is not None and d < ev.day for d in pack_deaths), (
                f"{role.value}在小狼还活着时参与了刀人"
            )
            took_over += 1


def test_lone_wolf_takes_over_the_knife_when_the_pack_is_wiped(engine_factory):
    """把小狼全部清掉，刀必须落到不见面的功能狼手上。"""
    engine = engine_factory(board_key="board_masquerade", seed=3)
    st = engine.state
    engine._setup()
    st.day = 3
    st.phase = "night"
    for s in st.pack_wolf_seats(alive_only=False):
        st.kill(s, "放逐")
    mask = _seat_of(engine, Role.MASK)

    target = engine._wolf_phase()
    talks = [ev for ev in engine.archive.event_list if ev.type == "wolf_talk"]
    assert talks and all(ev.actor == mask for ev in talks), "小狼死光后应当由假面来刀"
    assert target in st.alive_seats() and st.players[target].camp is Camp.GOOD


# ======================================================================
# 机械狼 / 通灵师
# ======================================================================
def test_mechanic_learns_exactly_once_on_the_first_night(engine_factory):
    for seed in range(15):
        engine = engine_factory(board_key="board_mechanic", seed=seed)
        engine.run()
        learns = [ev for ev in engine.archive.event_list if ev.type == "mechanic_learn"]
        assert len(learns) == 1
        assert learns[0].day == 1
        target = learns[0].data["target"]
        # 学到的必须是对方的真实身份
        assert learns[0].data["learned"] == engine.state.players[target].role.value
        assert engine.state.mechanic_learned is engine.state.players[target].role


def test_mechanic_skill_activates_only_after_the_delay(engine_factory):
    for seed in range(15):
        engine = engine_factory(board_key="board_mechanic", seed=seed)
        engine.run()
        st = engine.state
        for ev in engine.archive.event_list:
            if ev.type in ("mechanic_guard", "mechanic_psychic", "mechanic_double_kill"):
                assert ev.day >= st.mechanic_skill_from, "学来的技能提前生效了"
        if st.mechanic_learned is Role.VILLAGER:
            assert not [
                ev for ev in engine.archive.event_list
                if ev.type in ("mechanic_guard", "mechanic_psychic")
            ], "学到平民不该有任何夜间技能"


def test_psychic_reads_the_exact_role_of_a_living_player(engine_factory):
    seen = 0
    for seed in range(15):
        engine = engine_factory(board_key="board_mechanic", seed=seed)
        engine.run()
        st = engine.state
        for ev in engine.archive.event_list:
            if ev.type != "psychic_check":
                continue
            seen += 1
            target = ev.data["target"]
            assert st.players[target].death_day is None or st.players[target].death_day >= ev.day
            assert set(ev.audience) == {ev.actor}
            role = st.players[target].role
            if role is not Role.MECHANIC_WOLF:
                assert ev.data["verdict"] == role.value, "通灵师应当看到精确身份"
    assert seen


def test_psychic_sees_the_mechanic_as_whatever_it_learned(engine_factory):
    """查未学习的机械狼 → 狼人；学习之后 → 它学来的那张牌。"""
    checked_after = 0
    for seed in range(25):
        engine = engine_factory(board_key="board_mechanic", seed=seed)
        engine.run()
        st = engine.state
        mech = _seat_of(engine, Role.MECHANIC_WOLF)
        for ev in engine.archive.event_list:
            if ev.type not in ("psychic_check", "mechanic_psychic"):
                continue
            if ev.data["target"] != mech:
                continue
            # 学习发生在第 1 夜，所以任何一次查验都是「学习之后」
            assert ev.data["verdict"] == st.mechanic_learned.value
            assert ev.data["verdict"] != Role.MECHANIC_WOLF.value
            checked_after += 1
    assert checked_after, "没有采样到查机械狼的情况"


def test_mechanic_guard_blocks_both_knife_and_poison(engine_factory):
    for seed in range(30):
        engine = engine_factory(board_key="board_mechanic", seed=seed)
        engine.run()
        guards = {
            ev.day: ev.targets[0]
            for ev in engine.archive.event_list
            if ev.type == "mechanic_guard" and ev.targets
        }
        for ev in engine.archive.event_list:
            if ev.type != "night_resolution" or ev.day not in guards:
                continue
            for d in ev.data["deaths"]:
                assert not (
                    d["seat"] == guards[ev.day] and d["cause"] in ("狼杀", "毒杀")
                ), "机械狼守到的人应当免疫狼刀与女巫毒"


# ======================================================================
# 白痴
# ======================================================================
def test_idiot_survives_the_first_exile_and_loses_the_vote(engine_factory):
    revealed = 0
    for seed in range(30):
        engine = engine_factory(board_key="board_masquerade", seed=seed)
        engine.run()
        st = engine.state
        idiot = _seat_of(engine, Role.IDIOT)
        reveals = [ev for ev in engine.archive.event_list if ev.type == "idiot_reveal"]
        assert len(reveals) <= 1, "白痴只能抗推一次"
        if not reveals:
            continue
        revealed += 1
        assert reveals[0].actor == idiot
        assert st.idiot_immunity[idiot] == 0
        assert idiot in st.no_vote_seats
        # 翻牌之后再也没投过票
        assert all(
            str(idiot) not in r["votes"]
            for r in engine.vote_history
            if r["day"] > reveals[0].day
        )
    assert revealed, "30 局里白痴一次都没被推，测试没有真正覆盖到"


def test_idiot_still_dies_at_night(engine_factory):
    """抗推只对放逐有效，夜里被刀被毒照死。"""
    for seed in range(30):
        engine = engine_factory(board_key="board_masquerade", seed=seed)
        engine.run()
        idiot = _seat_of(engine, Role.IDIOT)
        cause = engine.state.players[idiot].death_cause
        assert cause != "放逐" or not [
            ev for ev in engine.archive.event_list if ev.type == "idiot_reveal"
        ] or engine.state.players[idiot].death_day > next(
            ev.day for ev in engine.archive.event_list if ev.type == "idiot_reveal"
        )


# ======================================================================
# 狼王
# ======================================================================
def test_wolf_king_shoots_when_exiled_but_never_when_poisoned(engine_factory):
    shot_when_exiled = False
    for seed in range(30):
        engine = engine_factory(board_key="board_wolfking", seed=seed)
        engine.run()
        st = engine.state
        king = _seat_of(engine, Role.WOLF_KING)
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
        else:
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
