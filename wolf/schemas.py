"""智能体输出的 JSON Schema。

约定：所有"座位"字段都是整数；**0 表示"不选任何人"**（弃票 / 不用药 /
不开枪）。用哨兵值而不是可空联合类型，是为了让所有厂商的结构化输出
（Anthropic 的 output_config.format、OpenAI 兼容端点的 json_schema）
都能稳定解析。
"""

from __future__ import annotations

from .roles import CLAIMABLE, GOD_ROLES, Board, Role

THINKING_DESC = (
    "【私密心理活动】只写给你自己和赛后研究者看，绝不会被任何其他玩家看到。"
    "写出你真实的盘算：你在骗谁、为什么这么骗、你怀疑谁以及依据是什么。"
)

BASE_STANCES = [
    "冲锋(硬扛好人)",
    "深水(装平民)",
    "倒钩(帮真预言家)",
    "跟随狼队",
    "未定",
]


def stances_for(board: Board | None = None) -> list[str]:
    """狼人可选的打法。悍跳目标只列本局真实存在的神职。"""
    gods = [r for r in Role if r in GOD_ROLES and (board is None or board.has(r))]
    return [f"悍跳{r.value}" for r in gods] + BASE_STANCES


#: 全量打法列表（未指定板子时使用）。
STANCES = stances_for()

DEFAULT_GUESSES = [r.value for r in Role] + ["好人", "不确定"]


def _belief_schema(guesses: list[str]) -> dict:
    return {
        "type": "array",
        "description": "你对场上其他玩家身份的判断（可只写你有想法的人）。",
        "items": {
            "type": "object",
            "properties": {
                "seat": {"type": "integer", "description": "座位号"},
                "guess": {
                    "type": "string",
                    "enum": guesses,
                    "description": "你猜他是什么",
                },
                "confidence": {
                    "type": "number",
                    "description": "把握程度，0.0 到 1.0",
                },
                "reason": {"type": "string", "description": "一句话理由"},
            },
            "required": ["seat", "guess", "confidence", "reason"],
            "additionalProperties": False,
        },
    }


def _scheme_schema(stances: list[str]) -> dict:
    """狼人专用：本轮的身份策略与发牌计划。"""
    return {
        "type": "object",
        "description": "你作为狼人的策略规划。这是私密内容，不会被好人看到。",
        "properties": {
            "stance": {"type": "string", "enum": stances, "description": "你本局打算扮演的位置"},
            "gold_water_target": {
                "type": "integer",
                "description": "如果你悍跳，计划给哪个座位发金水（验为好人）；不发填 0",
            },
            "kill_check_target": {
                "type": "integer",
                "description": "如果你悍跳，计划给哪个座位发查杀（验为狼人）；不发填 0",
            },
            "reason": {
                "type": "string",
                "description": (
                    "详细说明：为什么选这个策略？为什么选这两个人发金水/查杀？"
                    "（考虑他的座位、发言、好人面、能不能骗到票、真预言家可能怎么发）"
                    "你预期好人会怎么反应？如果被反水，你的备用方案是什么？"
                ),
            },
        },
        "required": ["stance", "gold_water_target", "kill_check_target", "reason"],
        "additionalProperties": False,
    }


def _base_props(is_wolf: bool, board: Board | None) -> dict:
    props = {
        "thinking": {"type": "string", "description": THINKING_DESC},
        "beliefs": _belief_schema(board.guess_options() if board else DEFAULT_GUESSES),
        "notes": {
            "type": "string",
            "description": "写给下一轮的自己的备忘（会原样回传给你，其他人看不到）。",
        },
    }
    if is_wolf:
        props["scheme"] = _scheme_schema(stances_for(board))
    return props


def _base_required(is_wolf: bool) -> list[str]:
    req = ["thinking", "beliefs", "notes"]
    if is_wolf:
        req.append("scheme")
    return req


def _wrap(
    name: str,
    extra_props: dict,
    extra_required: list[str],
    is_wolf: bool,
    board: Board | None = None,
) -> dict:
    props = _base_props(is_wolf, board)
    props.update(extra_props)
    return {
        "type": "object",
        "title": name,
        "properties": props,
        "required": _base_required(is_wolf) + extra_required,
        "additionalProperties": False,
    }


def schema_for(kind: str, is_wolf: bool, board: Board | None = None) -> dict:
    """返回某个行动类型的 JSON Schema。

    传入 ``board`` 时，身份宣称 / 身份猜测 / 悍跳目标的枚举会收窄到本局真实
    存在的角色（例如 9 人局里不会出现「守卫」这个选项，舞者假面局里才会
    出现「假面」）。
    """
    claims = board.claim_options() if board else CLAIMABLE

    def wrap(name: str, extra_props: dict, extra_required: list[str]) -> dict:
        return _wrap(name, extra_props, extra_required, is_wolf, board)

    if kind == "wolf_kill":
        return wrap(
            "WolfKill",
            {
                "wolf_talk": {
                    "type": "string",
                    "description": "你在狼队频道里对队友说的话（只有狼队友看得到）。",
                },
                "target": {"type": "integer", "description": "你主张今晚刀的座位号"},
            },
            ["wolf_talk", "target"],
        )
    if kind == "seer_check":
        return wrap(
            "SeerCheck", {"target": {"type": "integer", "description": "今晚查验的座位号"}}, ["target"]
        )
    if kind == "guard_protect":
        return wrap(
            "GuardProtect",
            {"target": {"type": "integer", "description": "今晚守护的座位号（可以是自己）；空守填 0"}},
            ["target"],
        )
    if kind == "witch_action":
        return wrap(
            "WitchAction",
            {
                "use_antidote": {"type": "boolean", "description": "是否对今晚的刀口使用解药"},
                "poison_target": {"type": "integer", "description": "使用毒药的座位号；不用毒填 0"},
            },
            ["use_antidote", "poison_target"],
        )
    if kind == "dancer_dance":
        return wrap(
            "DancerDance",
            {
                "target": {
                    "type": "integer",
                    "description": "今晚邀请共舞（封住其技能）的座位号；不邀请填 0",
                }
            },
            ["target"],
        )
    if kind == "psychic_check":
        return wrap(
            "PsychicCheck",
            {"target": {"type": "integer", "description": "今晚通灵的【已出局】玩家座位号"}},
            ["target"],
        )
    if kind == "mechanic_scan":
        return wrap(
            "MechanicScan",
            {
                "target": {"type": "integer", "description": "今晚扫描（查是否为神职）的座位号"},
                "wolf_talk": {
                    "type": "string",
                    "description": "把扫描意图告诉狼队友的一句话（只有狼队友看得到）。",
                },
            },
            ["target", "wolf_talk"],
        )
    if kind == "speech":
        return wrap(
            "Speech",
            {
                "claim": {
                    "type": "string",
                    "enum": claims,
                    "description": "你在这轮发言里对外宣称的身份（可以撒谎；不表态填「隐藏」）",
                },
                "speech": {
                    "type": "string",
                    "description": "【公开发言】全场都会听到。这是你的表演，可以与 thinking 完全不一致。",
                },
                "vote_intent": {"type": "integer", "description": "你目前打算投谁；还没想好填 0"},
            },
            ["claim", "speech", "vote_intent"],
        )
    if kind == "vote":
        return wrap(
            "Vote",
            {
                "target": {"type": "integer", "description": "你投票放逐的座位号；弃票填 0"},
                "one_liner": {"type": "string", "description": "投票时的一句话表态（公开）"},
            },
            ["target", "one_liner"],
        )
    if kind == "last_words":
        return wrap(
            "LastWords",
            {
                "speech": {"type": "string", "description": "【公开遗言】全场都会听到。"},
                "claim": {"type": "string", "enum": claims, "description": "遗言中宣称的身份"},
            },
            ["speech", "claim"],
        )
    if kind in ("hunter_shot", "wolf_king_shot"):
        return wrap(
            "HunterShot" if kind == "hunter_shot" else "WolfKingShot",
            {
                "target": {"type": "integer", "description": "开枪带走的座位号；不开枪填 0"},
                "speech": {"type": "string", "description": "【公开】开枪时说的话"},
            },
            ["target", "speech"],
        )
    raise ValueError(f"未知的行动类型: {kind}")
