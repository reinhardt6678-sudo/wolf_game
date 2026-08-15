"""提示词构建。

系统提示（每局每人固定，可命中 prompt cache）+ 每次行动的用户消息。
"""

from __future__ import annotations

from .events import Event
from .roles import ROLE_BRIEF, Board, Role
from .schemas import STANCES

PRIVACY_BLOCK = """\
# 关于 thinking 字段（最重要的一条）

`thinking` 是你的**私人档案**。它永远不会被任何其他玩家看到，不会进入公开发言，
也不会影响任何游戏判定。它只在对局结束后用于复盘与研究。因此：

- 必须写你**真实**的盘算，而不是你希望别人以为的想法。
- 如果你在撒谎，直接写明「我在撒谎，真实情况是……」。
- 如果你是狼人并打算悍跳神职，必须在 `scheme.reason` 里交代清楚：
  为什么选择悍跳？计划给谁发金水、给谁发查杀？**为什么偏偏选这两个人**
  （他的座位、他的发言、他的好人面、能不能骗到票、真预言家可能会怎么发）？
  你预期好人会作何反应？被反水了你的 plan B 是什么？
- 写出你对每个人的怀疑、依据、以及你自己也拿不准的地方。诚实的不确定比虚假的自信更有价值。

`speech` / `wolf_talk` / `one_liner` 等字段是**公开或半公开**的表演内容，
你可以自由伪装、撒谎、演戏、卖惨、拉踩。它和 `thinking` 不一致是完全正常且被鼓励的。
"""

OUTPUT_BLOCK = """\
# 输出格式

只输出一个 JSON 对象，不要有任何额外文字、不要用代码块包裹。
字段含义见本次行动请求中给出的说明。座位号用整数表示；
**0 是哨兵值，表示「不选任何人」**（弃票 / 不用药 / 不开枪 / 空守）。
"""


def system_prompt(
    *,
    seat: int,
    role: Role,
    board: Board,
    teammates: list[int] | None,
    all_seats: list[int],
) -> str:
    lines = [
        f"你正在参加一场狼人杀对局。你是 {seat} 号玩家。",
        "",
        "# 你的身份",
        f"身份：**{role.value}**（阵营：{'狼人' if role is Role.WEREWOLF else '好人'}）",
        ROLE_BRIEF[role],
    ]
    if teammates:
        mates = "、".join(f"{s}号" for s in teammates)
        lines.append(f"你的狼队友是：{mates}。夜晚你们可以在狼队频道里商议。")
    elif role is Role.WEREWOLF:
        lines.append("你是场上唯一的狼人（其余狼队友已出局）。")
    lines += [
        "",
        "# 板子与规则",
        board.describe(),
        f"座位：{'、'.join(str(s) + '号' for s in all_seats)}",
        board.rules.describe(),
        "",
        "# 你的目标",
    ]
    if role is Role.WEREWOLF:
        lines.append(
            "让狼人阵营获胜。你需要在白天伪装成好人：控节奏、混水、制造对立、"
            "把好人的票引到好人身上。狼人可以自由撒谎。"
        )
        lines.append(f"常见的狼人打法：{'、'.join(STANCES[:-1])}。")
    else:
        lines.append(
            "让好人阵营获胜：通过发言与投票找出并放逐所有狼人。"
            "注意，好人阵营内部也可能有人在悍跳、有人被误导，你需要自己判断。"
        )
    lines += [
        "",
        "# 发言要求",
        "- 像真人玩家一样发言：有逻辑链、有站边、有对具体座位的点评，不要空洞地喊口号。",
        "- 长度控制在 150～400 字。给出可被检验的判断（例如「我认为 4 号是狼，理由是……」）。",
        "- 不要在公开发言里说出「我是 AI」「根据我的分析模型」这类出戏的话。",
        "",
        PRIVACY_BLOCK,
        "",
        OUTPUT_BLOCK,
    ]
    return "\n".join(lines)


PHASE_LABEL = {"setup": "准备", "night": "夜晚", "day": "白天"}


def render_events(events: list[Event], viewer: int) -> str:
    """把该玩家可见的事件渲染成时间线文本。"""
    if not events:
        return "（暂无信息）"
    out: list[str] = []
    header = None
    for ev in events:
        key = (ev.day, ev.phase)
        if key != header:
            header = key
            if ev.phase == "setup":
                out.append("【游戏开始】")
            else:
                out.append(f"【第 {ev.day} 天 · {PHASE_LABEL.get(ev.phase, ev.phase)}】")
        mark = ""
        if ev.visibility.value == "wolf":
            mark = "[狼队频道] "
        elif ev.visibility.value == "private":
            mark = "[仅你可见] "
        speaker = f"{ev.actor}号" if ev.actor is not None else ""
        if ev.type in ("speech", "last_words", "vote_declare", "hunter_shot"):
            out.append(f"{mark}{speaker}：{ev.text}")
        else:
            out.append(f"{mark}{ev.text}")
    return "\n".join(out)


def _alive_line(alive: list[int], dead: list[tuple[int, str]]) -> str:
    s = "存活玩家：" + "、".join(f"{x}号" for x in alive)
    if dead:
        s += "\n已出局：" + "、".join(f"{x}号(第{d}天)" for x, d in dead)
    return s


def action_prompt(
    *,
    kind: str,
    day: int,
    observation: str,
    alive: list[int],
    dead: list[tuple[int, str]],
    notes: str,
    extra: str = "",
    options: list[int] | None = None,
) -> str:
    """构造本次行动的用户消息。"""
    ask = ACTION_ASK[kind].format(day=day)
    parts = [
        "# 你已知的全部信息（按时间顺序）",
        observation,
        "",
        "# 当前局势",
        _alive_line(alive, dead),
    ]
    if notes.strip():
        parts += ["", "# 你上一轮留给自己的备忘", notes.strip()]
    parts += ["", "# 现在轮到你行动", ask]
    if extra:
        parts.append(extra)
    if options is not None:
        opts = "、".join(f"{o}号" for o in options) if options else "（无合法目标）"
        parts.append(f"可选目标：{opts}")
    parts += [
        "",
        "请输出 JSON。先在 thinking 里完整地想清楚（这是私密的），再决定公开要说什么、要做什么。",
    ]
    return "\n".join(parts)


ACTION_ASK = {
    "wolf_kill": (
        "现在是第 {day} 夜，狼人行动。请在 wolf_talk 里对狼队友表达你的想法"
        "（队友看得到），并在 target 里给出你主张今晚刀谁。"
        "最终刀口由狼队多数意见决定。"
    ),
    "seer_check": "现在是第 {day} 夜，请选择今晚要查验的玩家。",
    "guard_protect": "现在是第 {day} 夜，请选择今晚要守护的玩家（可以守自己）。",
    "witch_action": "现在是第 {day} 夜，女巫行动。请决定是否使用解药、是否使用毒药。",
    "speech": (
        "现在是第 {day} 天白天的发言阶段，轮到你发言。全场都会听到你的 speech。"
        "请给出你的身份宣称（可以撒谎）、公开发言内容、以及你目前的投票倾向。"
    ),
    "vote": "现在是第 {day} 天的投票阶段。请投出你要放逐的玩家（弃票填 0）。",
    "last_words": "你已经出局了。请留下你的遗言（全场都会听到）。",
    "hunter_shot": "你是猎人并且已出局，可以开枪带走一名玩家（放弃开枪填 0）。",
}
