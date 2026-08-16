"""角色、阵营与板子定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Camp(str, Enum):
    GOOD = "好人"
    WOLF = "狼人"


class Role(str, Enum):
    VILLAGER = "平民"
    SEER = "预言家"
    WITCH = "女巫"
    HUNTER = "猎人"
    GUARD = "守卫"
    PSYCHIC = "通灵师"
    DANCER = "舞者"
    MASK = "假面"
    WEREWOLF = "狼人"
    WOLF_KING = "狼王"
    MECHANIC_WOLF = "机械狼"


GOD_ROLES = frozenset(
    {Role.SEER, Role.WITCH, Role.HUNTER, Role.GUARD, Role.PSYCHIC, Role.DANCER, Role.MASK}
)
WOLF_ROLES = frozenset({Role.WEREWOLF, Role.WOLF_KING, Role.MECHANIC_WOLF})

#: 拥有夜间技能的角色（舞者的"封技能"反馈按这个判定）。
#: 女巫（药已用完）与通灵师（场上还没有死人）在运行时另有判定，见 Engine。
NIGHT_SKILL_ROLES = frozenset(
    {
        Role.SEER,
        Role.WITCH,
        Role.GUARD,
        Role.PSYCHIC,
        Role.DANCER,
        Role.WEREWOLF,
        Role.WOLF_KING,
        Role.MECHANIC_WOLF,
    }
)

#: 出局时可以开枪的角色 → 死因为该值时**不能**开枪。
GUN_ROLES = frozenset({Role.HUNTER, Role.WOLF_KING})

#: 玩家在发言中可以宣称的身份（含"隐藏"）。板子会把它收窄到本局存在的角色，
#: 见 :meth:`Board.claim_options`。
CLAIMABLE = [r.value for r in Role] + ["隐藏"]


def camp_of(role: Role) -> Camp:
    return Camp.WOLF if role in WOLF_ROLES else Camp.GOOD


def is_god(role: Role) -> bool:
    return role in GOD_ROLES


def is_villager(role: Role) -> bool:
    return role is Role.VILLAGER


ROLE_BRIEF = {
    Role.VILLAGER: "你没有任何技能，只有一张嘴和一票。你的价值在于分析发言、找出狼人、把票投对。",
    Role.SEER: "每晚可以查验一名玩家，得知其为「好人」或「狼人」。你是好人阵营的核心信息源。",
    Role.WITCH: "你有一瓶解药和一瓶毒药，各只能用一次。每晚你会得知今晚被狼人刀的是谁。",
    Role.HUNTER: "当你被狼人刀杀或被投票放逐出局时，可以开枪带走一名玩家；被女巫毒死则无法开枪。",
    Role.GUARD: "每晚可以守护一名玩家（含自己），使其免于狼人刀杀。不能连续两晚守护同一人。",
    Role.PSYCHIC: (
        "每晚可以通灵一名**已出局**的玩家，得知他的真实身份。首夜场上还没有死人，"
        "你无事可做；从第二夜开始，你是好人阵营最硬的后期信息源——"
        "被狼刀的一定不是狼，被放逐的却未必是狼。"
    ),
    Role.DANCER: (
        "每晚可以邀请一名玩家共舞，被邀请者当晚**所有技能失效**"
        "（狼人无法参与刀人、神职技能作废），并且他会知道自己被封了。"
        "不能连续两晚邀请同一人，也不能邀请自己。"
        "共舞之后你会得知：对方今晚**原本是否拥有夜间技能**（平民、猎人、假面都是「没有」）。"
    ),
    Role.MASK: (
        "你戴着一张假面。第一次被投票放逐时你不会出局，而是当场揭下假面、"
        "向全场公开你的真实身份，然后继续留在场上——但从此失去投票权。"
        "夜里被狼刀或被毒依然会正常死亡。你是好人阵营的「抗推位」：被冤枉一次不亏。"
    ),
    Role.WEREWOLF: "每晚与狼队友商议后共同刀杀一名玩家。白天你需要伪装成好人，混淆视听、带偏节奏。",
    Role.WOLF_KING: (
        "你是狼王：每晚和狼队友一起刀人。当你被投票放逐、或被猎人/狼王的枪带走时，"
        "可以开枪带走一名玩家；**被女巫毒死则无法开枪**。"
        "白天你既要伪装成好人，也要让好人不敢轻易推你。"
    ),
    Role.MECHANIC_WOLF: (
        "你是机械狼：每晚和狼队友一起刀人，此外还可以扫描一名玩家，"
        "得知他【是神职】还是【不是神职】（但不知道具体是哪个神），"
        "扫描结果会同步给整个狼队。你是狼队的查神机器——用它来精准屠神。"
    ),
}


@dataclass(frozen=True)
class Board:
    """一副板子（座位数 + 角色配置 + 规则）。"""

    name: str
    seats: int
    role_counts: dict[Role, int]
    rules: "Rules"

    def role_pool(self) -> list[Role]:
        pool: list[Role] = []
        for role, n in self.role_counts.items():
            pool.extend([role] * n)
        if len(pool) != self.seats:
            raise ValueError(
                f"板子 {self.name} 角色数({len(pool)})与座位数({self.seats})不符"
            )
        return pool

    def describe(self) -> str:
        parts = [f"{r.value}×{n}" for r, n in self.role_counts.items() if n]
        return f"{self.name}：{self.seats} 人局，{'、'.join(parts)}"

    def has(self, role: Role) -> bool:
        return bool(self.role_counts.get(role))

    def roles_present(self) -> list[Role]:
        """本局存在的角色，按 :class:`Role` 的声明顺序。"""
        return [r for r in Role if self.has(r)]

    def rules_text(self) -> str:
        """只讲本局用得上的规则（板子里没有的角色不会出现在提示词里）。"""
        return self.rules.describe(self.roles_present())

    def claim_options(self) -> list[str]:
        """发言里可以宣称的身份：只允许本局真实存在的角色。"""
        return [r.value for r in self.roles_present()] + ["隐藏"]

    def guess_options(self) -> list[str]:
        """beliefs 里可以填的身份猜测。"""
        return [r.value for r in self.roles_present()] + ["好人", "不确定"]


@dataclass(frozen=True)
class Rules:
    victory: str = "屠边"  # 屠边 | 屠城
    witch_self_save_day: int = 1  # 女巫可自救的最后一个夜晚（0=永不可自救）
    witch_sees_kill: str = "always"  # always | first_night
    witch_same_night_both: bool = False  # 同一晚是否可以既用解药又用毒药
    guard_repeat: bool = False  # 守卫是否可以连守同一人
    guard_save_conflict_dies: bool = True  # 同守同救是否致死（奶死）
    first_night_last_words: bool = True  # 首夜死者是否有遗言
    night_death_last_words: bool = False  # 非首夜的夜间死者是否有遗言
    tie_rule: str = "revote"  # revote | none
    max_days: int = 8

    # ---- 扩展角色 ----
    wolf_king_shoot_on_poison: bool = False  # 狼王被毒死能否开枪
    mechanic_wolf_shares: bool = True  # 机械狼的扫描结果是否同步给整个狼队
    psychic_reveals_role: bool = True  # 通灵师看到具体身份(True) 还是只看好人/狼人(False)
    dancer_repeat: bool = False  # 舞者能否连续两晚邀请同一人
    dancer_feedback: bool = True  # 舞者是否得知舞伴「原本有无夜间技能」
    mask_exile_immunity: int = 1  # 假面能免疫几次放逐
    mask_loses_vote: bool = True  # 假面揭面后是否失去投票权

    def describe(self, roles: "list[Role] | set[Role] | None" = None) -> str:
        """规则说明。传入 ``roles`` 时只输出这些角色相关的条目。"""
        present = set(roles) if roles is not None else set(Role)

        def has(*rs: Role) -> bool:
            return any(r in present for r in rs)

        lines = [
            f"- 胜利条件：{self.victory}"
            + ("（狼人杀光所有神职 或 杀光所有平民即获胜）" if self.victory == "屠边" else "（狼人数量≥好人数量即获胜）"),
        ]
        if has(Role.WITCH):
            lines += [
                "- 女巫：解药与毒药各一瓶，"
                + (
                    f"仅第 {self.witch_self_save_day} 夜可以自救"
                    if self.witch_self_save_day
                    else "不可自救"
                )
                + ("；同一晚不可同时使用解药和毒药" if not self.witch_same_night_both else ""),
                "- 女巫每晚都能得知刀口" if self.witch_sees_kill == "always" else "- 女巫仅首夜能得知刀口",
            ]
        if has(Role.GUARD):
            lines.append(
                "- 守卫" + ("可以" if self.guard_repeat else "不可以") + "连续两晚守护同一人"
                + ("；同守同救会导致目标死亡（奶死）" if self.guard_save_conflict_dies else "")
            )
        if has(Role.HUNTER):
            lines.append("- 猎人被狼刀或被放逐出局时可以开枪带走一人；被女巫毒死则不能开枪")
        if has(Role.WOLF_KING):
            lines.append(
                "- 狼王被放逐或被枪杀出局时可以开枪带走一人；"
                + ("被女巫毒死也可以开枪" if self.wolf_king_shoot_on_poison else "被女巫毒死则不能开枪")
            )
        if has(Role.MECHANIC_WOLF):
            lines.append(
                "- 机械狼每晚可以扫描一名玩家，得知其【是神职 / 不是神职】"
                + ("，结果同步给整个狼队" if self.mechanic_wolf_shares else "，结果只有他自己知道")
            )
        if has(Role.PSYCHIC):
            lines.append(
                "- 通灵师每晚可以通灵一名已出局的玩家，得知其"
                + ("真实身份" if self.psychic_reveals_role else "阵营（好人/狼人）")
                + "；场上没有死人时无法通灵"
            )
        if has(Role.DANCER):
            lines.append(
                "- 舞者每晚邀请一名玩家共舞，该玩家当晚所有技能失效（本人会知道自己被封）；"
                + ("可以" if self.dancer_repeat else "不可以")
                + "连续两晚邀请同一人"
                + ("；舞者会得知舞伴当晚原本有无夜间技能" if self.dancer_feedback else "")
            )
        if has(Role.MASK):
            lines.append(
                f"- 假面被投票放逐时不会出局（每局 {self.mask_exile_immunity} 次），"
                "当场翻牌公开身份"
                + ("，此后失去投票权" if self.mask_loses_vote else "")
                + "；夜间被刀或被毒正常死亡"
            )
        lines += [
            "- 平票处理：" + ("重新投票一次，仍平票则本轮无人出局" if self.tie_rule == "revote" else "本轮无人出局"),
            "- 死亡不公布死因（不知道是被刀还是被毒）",
        ]
        return "\n".join(lines)


DEFAULT_RULES = Rules()

BUILTIN_BOARDS: dict[str, Board] = {
    "board_9": Board(
        name="9人局(预女猎)",
        seats=9,
        role_counts={
            Role.WEREWOLF: 3,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.HUNTER: 1,
            Role.VILLAGER: 3,
        },
        rules=DEFAULT_RULES,
    ),
    "board_12": Board(
        name="12人标准局(预女猎守)",
        seats=12,
        role_counts={
            Role.WEREWOLF: 4,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.HUNTER: 1,
            Role.GUARD: 1,
            Role.VILLAGER: 4,
        },
        rules=DEFAULT_RULES,
    ),
    "board_wolfking": Board(
        name="12人狼王守卫局(预女猎守 VS 狼王)",
        seats=12,
        role_counts={
            Role.WEREWOLF: 3,
            Role.WOLF_KING: 1,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.HUNTER: 1,
            Role.GUARD: 1,
            Role.VILLAGER: 4,
        },
        rules=DEFAULT_RULES,
    ),
    "board_mechanic": Board(
        name="12人机械狼通灵师局(信息战)",
        seats=12,
        role_counts={
            Role.WEREWOLF: 3,
            Role.MECHANIC_WOLF: 1,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.GUARD: 1,
            Role.PSYCHIC: 1,
            Role.VILLAGER: 4,
        },
        rules=DEFAULT_RULES,
    ),
    "board_dancer": Board(
        name="12人舞者假面局(封技能与抗推)",
        seats=12,
        role_counts={
            Role.WEREWOLF: 4,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.DANCER: 1,
            Role.MASK: 1,
            Role.VILLAGER: 4,
        },
        rules=DEFAULT_RULES,
    ),
    "board_mixed": Board(
        name="12人群英乱斗局(狼王机械狼 VS 通灵舞者假面)",
        seats=12,
        role_counts={
            Role.WEREWOLF: 2,
            Role.WOLF_KING: 1,
            Role.MECHANIC_WOLF: 1,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.PSYCHIC: 1,
            Role.DANCER: 1,
            Role.MASK: 1,
            Role.VILLAGER: 3,
        },
        rules=DEFAULT_RULES,
    ),
}
