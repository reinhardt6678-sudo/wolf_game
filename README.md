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
board = "board_9"
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

## 支持的规则

内置 9 人局（预女猎）和 12 人标准局（预女猎守），也可以写自己的板子 TOML。
已实现：屠边/屠城胜利判定、女巫解药毒药各一瓶与自救限制、同守同救奶死、
守卫不可连守、猎人被毒不能开枪、首夜遗言、平票重投、警左警右发言顺序轮转。

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
被毒的猎人不开枪、心理活动零泄漏、请求体形状（不给 Claude Opus 5 传 `temperature`
这种会直接 400 的参数）、模型输出乱码时的保底行为。
