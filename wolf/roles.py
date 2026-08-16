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
    IDIOT = "白痴"
    WEREWOLF = "狼人"
    WOLF_KING = "狼王"
    MECHANIC_WOLF = "机械狼"
    MASK = "假面"


GOD_ROLES = frozenset(
    {Role.SEER, Role.WITCH, Role.HUNTER, Role.GUARD, Role.PSYCHIC, Role.DANCER, Role.IDIOT}
)
WOLF_ROLES = frozenset({Role.WEREWOLF, Role.WOLF_KING, Role.MECHANIC_WOLF, Role.MASK})

#: 「不见面」的功能狼：不认识狼队友、不进狼队频道、不能自爆，
#: 有普通狼存活时也不参与刀人；狼队友全部出局后才拿到刀。
LONE_WOLF_ROLES = frozenset({Role.MECHANIC_WOLF, Role.MASK})

#: 出局时可以开枪的角色（被毒死时哑火，见 Rules）。
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


def is_pack_wolf(role: Role) -> bool:
    """是否是「见面的狼」——参与狼队频道与狼刀的那一批。"""
    return role in WOLF_ROLES and role not in LONE_WOLF_ROLES


ROLE_BRIEF = {
    Role.VILLAGER: "你没有任何技能，只有一张嘴和一票。你的价值在于分析发言、找出狼人、把票投对。",
    Role.SEER: "每晚可以查验一名玩家，得知其为「好人」或「狼人」。你是好人阵营的核心信息源。",
    Role.WITCH: "你有一瓶解药和一瓶毒药，各只能用一次。每晚你会得知今晚被狼人刀的是谁。",
    Role.HUNTER: "当你被狼人刀杀或被投票放逐出局时，可以开枪带走一名玩家；被女巫毒死则无法开枪。",
    Role.GUARD: "每晚可以守护一名玩家（含自己），使其免于狼人刀杀。不能连续两晚守护同一人。",
    Role.PSYCHIC: (
        "你是通灵师（本局没有预言家，你就是好人的查验位）：每晚可以查验一名玩家的"
        "**具体身份**——不是「好人/狼人」，而是「预言家」「女巫」「平民」这样的精确牌面。"
        "注意机械狼：它没学习过时你会查到「狼人」，学习之后你查到的是**它学来的那张牌**，"
        "所以你可能会查出两个「守卫」。"
    ),
    Role.DANCER: (
        "你是舞者。第二夜起，你每晚必须点 3 名玩家进入**舞池**（可以点自己）。"
        "天亮时结算舞池：3 人若同属一个阵营，则相安无事；"
        "若阵营不同，**少数派全部出局**（2 好 1 狼 → 那只狼出局；2 狼 1 好 → 那个好人出局）。"
        "每名玩家整局**只能进一次舞池**，进过就不能再点。"
        "你自己进池的那一晚，池中三人当晚免疫狼刀；你本人免疫女巫毒。"
        "法官不会告诉你结算细节——谁死了是公开的，池子里发生过什么要你自己推。"
    ),
    Role.IDIOT: (
        "你是白痴。第一次被投票放逐时你不会出局，而是当场翻牌亮出白痴身份继续留在场上，"
        "但从此**失去投票权**。夜里被狼刀或被女巫毒依然会正常死亡。"
        "你是好人的抗推位：被冤枉一次不亏，翻牌之后你的话反而最可信。"
    ),
    Role.WEREWOLF: "每晚与狼队友商议后共同刀杀一名玩家。白天你需要伪装成好人，混淆视听、带偏节奏。",
    Role.WOLF_KING: (
        "你是狼王：每晚和狼队友一起刀人。当你被投票放逐、或被猎人/狼王的枪带走时，"
        "可以开枪带走一名玩家；**被女巫毒死则无法开枪**。"
        "白天你既要伪装成好人，也要让好人不敢轻易推你。"
    ),
    Role.MECHANIC_WOLF: (
        "你是机械狼，狼队阵营，但**你和小狼互不认识**：你看不到狼队频道，小狼也不知道你是谁，"
        "他们完全可能把你当好人刀掉。你不能自爆。\n"
        "首夜你单独睁眼，选择一名存活玩家**学习**——你会知道他的真实身份，并习得他的技能"
        "（整局只能学一次，技能从下一夜开始生效）。学到守卫就能守人（免刀免毒），"
        "学到猎人就能在出局时开枪，学到通灵师就能查具体身份，学到狼人则获得一次双刀。\n"
        "小狼全部出局之后，刀才会交到你手上。在那之前你不参与刀人。\n"
        "通灵师查你：没学习过显示「狼人」，学习之后显示你学来的那张牌。"
    ),
    Role.MASK: (
        "你是假面，狼队阵营，但**你不与狼队见面**：你看不到狼队频道，小狼也不认识你，"
        "你不能自爆。你免疫女巫的毒。\n"
        "第二夜起，你在舞者之后行动：可以先向法官打听**某一名玩家今晚是否在舞池里**，"
        "然后给任意一名玩家（可以是你自己）戴上面具，或者空过。"
        "戴着面具的人如果正好在舞池里，他在舞池结算时的**阵营会翻转**——"
        "好人算作狼、狼算作好人，从而改变谁是少数派、谁出局。"
        "不能连续两晚给同一个人戴面具。\n"
        "狼队友全部出局之后，刀才会交到你手上。"
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

    # ---- 狼王 ----
    wolf_king_shoot_on_poison: bool = False  # 狼王被毒死能否开枪

    # ---- 假面舞会：舞者 ----
    dance_from_night: int = 2  # 舞者从第几夜开始共舞
    dance_size: int = 3  # 每晚进舞池的人数
    dance_once_per_player: bool = True  # 每名玩家整局只能进一次舞池
    dance_pool_immune_to_kill: bool = True  # 舞者本人在池中时，池内全员免疫狼刀
    dancer_poison_immune: bool = True  # 舞者免疫女巫毒
    dancer_learns_pool_result: bool = False  # 法官是否私下告诉舞者舞池结算（桌面规则：不告诉）

    # ---- 假面舞会：假面 ----
    mask_from_night: int = 2  # 假面从第几夜开始行动
    mask_repeat: bool = False  # 能否连续两晚给同一人戴面具
    mask_poison_immune: bool = True  # 假面免疫女巫毒

    # ---- 白痴 ----
    idiot_exile_immunity: int = 1  # 白痴能免疫几次放逐
    idiot_loses_vote: bool = True  # 翻牌后是否失去投票权

    # ---- 机械狼 / 通灵师 ----
    mechanic_learn_night: int = 1  # 机械狼在第几夜学习
    mechanic_skill_delay: int = 1  # 学到的技能几夜之后生效
    mechanic_double_kill: bool = True  # 学到狼人是否获得一次双刀
    psychic_sees_exact_role: bool = True  # 通灵师看具体身份(True) 还是只看阵营(False)

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
        if has(Role.DANCER):
            lines.append(
                f"- 舞者第 {self.dance_from_night} 夜起，每晚必须点 {self.dance_size} 人进舞池"
                + ("（每人整局只能进一次）" if self.dance_once_per_player else "")
                + "；天亮时舞池内**少数派阵营全部出局**，三人同阵营则无人出局"
                + ("；舞者本人在池中时池内全员免疫狼刀" if self.dance_pool_immune_to_kill else "")
                + ("；舞者免疫女巫毒" if self.dancer_poison_immune else "")
            )
            if not self.dancer_learns_pool_result:
                lines.append("- 法官不会告诉舞者舞池的结算过程，只有死讯是公开的")
        if has(Role.MASK):
            lines.append(
                f"- 假面第 {self.mask_from_night} 夜起在舞者之后行动：可以询问一名玩家是否在舞池中，"
                "并给一名玩家（可含自己）戴面具；戴面具者若在舞池中，其**结算阵营翻转**"
                + ("；不能连续两晚给同一人戴面具" if not self.mask_repeat else "")
                + ("；假面免疫女巫毒" if self.mask_poison_immune else "")
            )
        if has(Role.MECHANIC_WOLF):
            lines.append(
                f"- 机械狼第 {self.mechanic_learn_night} 夜单独学习一名存活玩家（整局仅一次），"
                f"得知其真实身份并习得其技能，{self.mechanic_skill_delay} 夜后生效"
                + ("；学到狼人可获得一次双刀" if self.mechanic_double_kill else "")
            )
        if has(Role.MASK, Role.MECHANIC_WOLF):
            lone = "、".join(
                r.value for r in (Role.MECHANIC_WOLF, Role.MASK) if r in present
            )
            lines.append(
                f"- {lone}与普通狼**互不认识**，看不到狼队频道、不能自爆；"
                "普通狼全部出局后才拿到刀"
            )
        if has(Role.PSYCHIC):
            lines.append(
                "- 通灵师每晚查验一名玩家的"
                + ("具体身份" if self.psychic_sees_exact_role else "阵营（好人/狼人）")
                + ("；查未学习的机械狼显示「狼人」，查已学习的显示其学来的身份"
                   if has(Role.MECHANIC_WOLF) else "")
            )
        if has(Role.IDIOT):
            lines.append(
                f"- 白痴被投票放逐时不会出局（每局 {self.idiot_exile_immunity} 次），当场翻牌公开身份"
                + ("，此后失去投票权" if self.idiot_loses_vote else "")
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
    # 京城大师赛「机械狼通灵师」：通灵师查具体身份，机械狼学一张牌。
    "board_mechanic": Board(
        name="12人机械狼通灵师局",
        seats=12,
        role_counts={
            Role.WEREWOLF: 3,
            Role.MECHANIC_WOLF: 1,
            Role.PSYCHIC: 1,
            Role.WITCH: 1,
            Role.HUNTER: 1,
            Role.GUARD: 1,
            Role.VILLAGER: 4,
        },
        rules=DEFAULT_RULES,
    ),
    # 京城大师赛原创「假面舞会」（2023-09-07 首播）。
    "board_masquerade": Board(
        name="12人假面舞会(舞者+白痴 VS 假面)",
        seats=12,
        role_counts={
            Role.WEREWOLF: 3,
            Role.MASK: 1,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.DANCER: 1,
            Role.IDIOT: 1,
            Role.VILLAGER: 4,
        },
        rules=DEFAULT_RULES,
    ),
    "board_masquerade_10": Board(
        name="10人假面舞会(无白痴)",
        seats=10,
        role_counts={
            Role.WEREWOLF: 2,
            Role.MASK: 1,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.DANCER: 1,
            Role.VILLAGER: 4,
        },
        rules=DEFAULT_RULES,
    ),
}
