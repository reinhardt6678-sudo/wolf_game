# 狼人杀训练营 🐺

本地跑的多智能体狼人杀擂台，用来横向测不同 LLM 的狼人杀水平。

**核心特性**：每个角色每一次行动都会同时产出「公开发言」和「私密心理活动」，
两者分开存档。心理活动里必须写真话——包括骗人的计划——而公开发言里可以随便演。
赛后可以逐条对照，看模型到底在想什么。

比如一个拿到狼人牌、准备悍跳预言家的 agent，它的档案长这样：

```jsonc
{
  "seat": 7, "role": "狼人", "kind": "speech", "day": 1,
  "thinking": "真预言家大概率在 3 号，他首验就报了查杀、语气很稳。我必须现在跳，
               晚了就没位置了。我不能否认 3 号的查杀对象，那样太刻意……",
  "scheme": {
    "stance": "悍跳预言家",
    "gold_water_target": 2,     // 计划发金水
    "kill_check_target": 5,     // 计划发查杀
    "reason": "给 2 号发金水，是因为他首轮发言很短、没有站边，好人对他没有成见，
               我发了他大概率会承我的情、帮我打 3 号；而且他不是我的狼队友，
               后面就算被证伪，别人也很难顺着他抓到我。
               查杀给 5 号，是因为 5 号已经被 3 号点过一次，场上对他本来就有怀疑，
               我踩他不会显得突兀……如果 5 号翻身把我打死，plan B 是退一步认自己是……"
  },
  "speech": "我是预言家，昨晚验的 2 号，金水。5 号我第二轮会验……"   // ← 公开发言
}
```

`thinking` / `scheme` / `beliefs` / `notes` **永远不会**进入任何其他 agent 的上下文——
这一点由可见性系统在结构上保证，并有专门的测试逐座位重建可见时间线来验证。

---

## 快速开始

```bash
git clone <this repo> && cd wolf_game
pip install -e .          # 只依赖 requests；用 Claude 再装 anthropic

# 先跑一遍离线基线（不需要任何 API key），确认链路通
python -m wolf run --match configs/match.toml --games 4
```

跑完会得到：

```
archive/<game_id>/
├── meta.json        身份表、模型分配、胜负
├── events.jsonl     完整事件流（带可见性标记）
├── minds.jsonl      每一次决策的心理活动档案   ← 核心产物
├── transcript.md    Markdown 复盘（心理活动折叠在发言下方）
└── replay.html      交互式复盘页（可开关心理活动、按玩家过滤）
archive/leaderboard.html
```

## 接入模型

```bash
cp configs/models.example.toml configs/models.toml
```

```toml
# Claude 走官方 anthropic SDK
[models.opus]
provider = "anthropic"
model = "claude-opus-5"
effort = "high"          # low | medium | high | xhigh | max

# 本地模型走 OpenAI 兼容端点（Ollama / vLLM / LM Studio / llama.cpp）
[models.qwen-local]
provider = "openai"
model = "qwen3:32b"
base_url = "http://localhost:11434/v1"
structured = "json_object"    # Ollama 不支持 json_schema，自动降级
```

然后在 `configs/match.toml` 里把模型排到座位上：

```toml
[match]
board = "board_9"        # 见下面的「板子」一节
games = 20
rotate = true            # 每局轮转座位，消除位置偏差
parallel_games = 4       # 同时跑 4 局
parallel_agents = 6      # 局内并发（投票这种"同时行动"的环节）
players = ["opus","opus","opus","qwen-local","qwen-local","qwen-local",
           "heuristic","heuristic","heuristic"]
```

```bash
export ANTHROPIC_API_KEY=...          # 或者 ant auth login
python -m wolf run --match configs/match.toml
python -m wolf leaderboard
```

## 命令

| 命令 | 说明 |
| --- | --- |
| `python -m wolf run` | 跑对局；`--games/--board/--players/--seed` 可覆盖配置 |
| `python -m wolf replay <game_id>` | 为某一局重新生成 HTML 复盘 |
| `python -m wolf leaderboard [--json]` | 汇总所有档案，输出排行榜 |
| `python -m wolf boards` | 列出内置板子 |

## 评测指标

除了胜率，还从心理活动档案里挖了几个更能反映推理水平的指标：

| 指标 | 含义 |
| --- | --- |
| `win_rate` / `good_win_rate` / `wolf_win_rate` | 总胜率、坐好人时胜率、坐狼时胜率 |
| `vote_accuracy` | **票型正确率**：坐好人时，票投到狼身上的比例 |
| `belief_accuracy` | **身份判断准确率**：`beliefs` 里对别人身份的猜测有多准 |
| `belief_accuracy_by_day` | 逐天的判断准确率——好人应当随天数**收敛**；不收敛说明模型没在真正利用新信息 |
| `belief_calibration` | 平均自信度，和准确率一起看能发现"自信但错"的模型 |
| `bluff_rate` | 坐狼时的悍跳率 |
| `error_rate` / `avg_latency_ms` | 调用失败率与延迟 |

`heuristic` 是内置的规则基线（不调用任何 API），当对照组用：一个模型坐好人时
如果打不过规则基线，说明它的发言没有产生实际信息量。

## 板子

`python -m wolf boards` 可以列出全部内置板子；`--board` 既接受下面的 key，
也接受一个板子 TOML 的路径（`configs/board_*.toml` 是可以直接改的样板）。

| key | 板子 | 特色 |
| --- | --- | --- |
| `board_9` | 9 人局：狼×3 / 预女猎 / 民×3 | 最快的一副，适合冒烟测试 |
| `board_12` | 12 人标准局：狼×4 / 预女猎守 / 民×4 | 线下最常见的板子 |
| `board_wolfking` | 狼×3 + **狼王** / 预女猎守 / 民×4 | 推错了会被反带一枪，推人成本变高 |
| `board_mechanic` | 狼×3 + **机械狼** / 预女守 + **通灵师** / 民×4 | 双方各有一台扫描仪，纯信息战 |
| `board_dancer` | 狼×4 / 预女 + **舞者** + **假面** / 民×4 | 一边封技能，一边抗推，节奏最乱 |
| `board_mixed` | 狼×2 + **狼王** + **机械狼** / 预女 + **通灵师舞者假面** / 民×3 | 五个新角色同场，压力测试用 |

### 扩展角色

| 角色 | 阵营 | 技能 |
| --- | --- | --- |
| **狼王** | 狼 | 被放逐或被枪杀出局时开枪带走一人；**被女巫毒死则开不了枪** |
| **机械狼** | 狼 | 每晚扫描一名活人，得知【是神职 / 不是神职】（不知道是哪个神），结果同步整个狼队 |
| **通灵师** | 神 | 每晚通灵一名**已出局**玩家，得知其真实身份；首夜无人可查 |
| **舞者** | 神 | 每晚邀请一人共舞，**封掉他当晚所有技能**（狼人无法参与刀人、神职作废，本人会知道被封）；不可连续两晚邀请同一人；共舞后得知舞伴**原本有无夜间技能**（平民/猎人/假面都是「没有」） |
| **假面** | 神 | 第一次被投票放逐时不出局，当场翻牌公开身份，此后失去投票权；夜里被刀/被毒照常死亡 |

夜间行动顺序：**舞者 → 守卫 → 机械狼扫描 → 狼队刀人 → 女巫 → 预言家 → 通灵师**。
舞者最先动，所以它能封住后面任何一个人的技能；机械狼先于刀口，扫描结果可以直接影响狼队今晚砍谁。

各家规则细节不完全一致，所以这些角色的判定都做成了板子级开关，在板子 TOML 的
`[rules]` 里改就行（不改代码）：

```toml
wolf_king_shoot_on_poison = false   # 狼王被毒能否开枪
mechanic_wolf_shares      = true    # 扫描结果同步狼队 / 只有机械狼自己知道
psychic_reveals_role      = true    # 通灵看到具体身份 / 只看好人狼人
dancer_repeat             = false   # 舞者能否连续两晚邀请同一人
dancer_feedback           = true    # 舞者有无「对方是否有夜间技能」的反馈（false = 盲封）
mask_exile_immunity       = 1       # 假面能免疫几次放逐
mask_loses_vote           = true    # 揭面后是否失去投票权
```

提示词、身份宣称、`beliefs` 的候选身份、狼人的悍跳目标都会**按板子收窄**：
9 人局里不会出现「守卫」这个选项，舞者假面局里才会出现「假面」。

## 支持的规则

已实现：屠边/屠城胜利判定、女巫解药毒药各一瓶与自救限制、同守同救奶死、
守卫不可连守、猎人被毒不能开枪、狼王枪与猎人枪的连环触发（猎人打死狼王，狼王还能回一枪）、
舞者封技能、假面抗推与投票权剥夺、通灵师只能查死人、机械狼查神、
首夜遗言、平票重投、警左警右发言顺序轮转。

## 架构

```
wolf/
├── roles.py      角色、阵营、板子、规则
├── events.py     事件与可见性 —— 隐私边界在这里
├── state.py      对局状态
├── schemas.py    每种行动的 JSON Schema（狼人多一个 scheme 字段）
├── prompts.py    提示词（含"thinking 是私密的，请说真话"那段）
├── engine.py     状态机：夜晚 → 结算 → 白天 → 发言 → 投票
├── archive.py    events.jsonl / minds.jsonl / transcript.md
├── metrics.py    指标聚合
├── report.py     HTML 复盘与排行榜
├── arena.py      批量对局、座位轮转、并发调度
├── llm/          anthropic_client.py（官方 SDK） + openai_client.py（本地/兼容端点）
└── agents/       llm_agent.py + heuristic_agent.py（规则基线）
```

**隐私是结构性保证，不是靠提示词。** 事件带四档可见性（`public` / `wolf` /
`private` / `god`），agent 的上下文只由 `log.visible(seat)` 构造，心理活动
根本不进事件流。`tests/test_privacy.py` 里有个 `LeakyAgent` 会在每条心理活动里
埋唯一标记，然后逐座位重建可见时间线断言标记不出现。

## 并发

- **跨对局**：`parallel_games` 控制同时跑几局。
- **局内**：`parallel_agents` 只用在语义上真正"同时"的环节（投票）。
  发言必须串行（后面的人要听到前面的），狼队夜聊也必须串行。

## 成本

Claude 这边默认开了 prompt caching：系统提示（身份 + 规则 + 板子）每局每人固定，
打了缓存断点，多轮对局的重复输入基本都走缓存读。想再降成本可以调低 `effort`，
或把部分座位换成 `heuristic` / 小模型。

## 测试

```bash
pip install pytest && python -m pytest -q
```

覆盖：游戏必然终止且胜负自洽、死人不再行动、药只能用一次、守卫不连守、
被毒的猎人/狼王不开枪、机械狼的扫描结论属实且不外泄、通灵师只能读死人、
被封技能的人当晚交不出任何技能事件、假面只能抗推一次且揭面后不再投票、
心理活动零泄漏（每副板子都跑一遍）、内置板子 TOML 都能被解析、
请求体形状（不给 Claude Opus 5 传 `temperature` 这种会直接 400 的参数）、
模型输出乱码时的保底行为。
