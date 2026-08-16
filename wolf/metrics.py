"""从档案里算指标。

除了最直观的胜率，这里还从「心理活动档案」里挖两类更能反映
推理水平的指标：

- **身份判断准确率**：agent 在 beliefs 里对别人身份的猜测有多准，
  以及随天数推进是否在收敛（好人应该越来越准）。
- **票型正确率**：好人把票投到狼身上的比例。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .archive import iter_games

#: 「狼人」既是身份名也是阵营名，按阵营判定；狼王/机械狼按具体身份判定。
WOLF_GUESSES = {"狼人"}
WOLF_ROLE_GUESSES = {"狼王", "机械狼"}
GOOD_GUESSES = {"好人", "平民", "预言家", "女巫", "猎人", "守卫", "通灵师", "舞者", "假面"}


@dataclass
class ModelStats:
    model: str
    games: int = 0
    wins: int = 0
    games_as_wolf: int = 0
    wins_as_wolf: int = 0
    games_as_good: int = 0
    wins_as_good: int = 0
    survived: int = 0
    survival_days: list[int] = field(default_factory=list)
    role_games: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    role_wins: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    votes_cast: int = 0
    votes_on_wolf: int = 0  # 仅统计好人身份时的投票
    good_votes: int = 0
    belief_total: int = 0
    belief_correct: int = 0
    belief_conf_sum: float = 0.0
    belief_by_day: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    wolf_bluffs: int = 0  # 悍跳次数（scheme.stance 以「悍跳」开头）
    wolf_bluff_games: set = field(default_factory=set)
    llm_errors: int = 0
    calls: int = 0
    latency_ms: list[int] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        def pct(a: int, b: int) -> float | None:
            return round(100.0 * a / b, 1) if b else None

        return {
            "model": self.model,
            "games": self.games,
            "win_rate": pct(self.wins, self.games),
            "wolf_win_rate": pct(self.wins_as_wolf, self.games_as_wolf),
            "good_win_rate": pct(self.wins_as_good, self.games_as_good),
            "survival_rate": pct(self.survived, self.games),
            "avg_survival_days": round(sum(self.survival_days) / len(self.survival_days), 2)
            if self.survival_days
            else None,
            "vote_accuracy": pct(self.votes_on_wolf, self.good_votes),
            "belief_accuracy": pct(self.belief_correct, self.belief_total),
            "belief_calibration": round(self.belief_conf_sum / self.belief_total, 2)
            if self.belief_total
            else None,
            "bluff_rate": pct(len(self.wolf_bluff_games), self.games_as_wolf),
            "avg_latency_ms": int(sum(self.latency_ms) / len(self.latency_ms))
            if self.latency_ms
            else None,
            "error_rate": pct(self.llm_errors, self.calls),
            "tokens_in": self.input_tokens,
            "tokens_out": self.output_tokens,
            "by_role": {
                r: {
                    "games": n,
                    "win_rate": pct(self.role_wins.get(r, 0), n),
                }
                for r, n in sorted(self.role_games.items())
            },
            "belief_accuracy_by_day": {
                str(d): round(100.0 * sum(v) / len(v), 1)
                for d, v in sorted(self.belief_by_day.items())
                if v
            },
        }


def _belief_is_correct(guess: str, true_role: str, true_camp: str) -> bool | None:
    if guess in ("不确定", "", None):
        return None
    if guess in WOLF_GUESSES:
        return true_camp == "狼人"
    if guess in WOLF_ROLE_GUESSES:
        return guess == true_role
    if guess == "好人":
        return true_camp == "好人"
    if guess in GOOD_GUESSES:
        return guess == true_role
    return None


def aggregate(root: str | Path) -> tuple[dict[str, ModelStats], dict[str, Any]]:
    stats: dict[str, ModelStats] = {}
    totals = {"games": 0, "good_wins": 0, "wolf_wins": 0, "draws": 0, "days": []}

    for game in iter_games(root):
        meta = game["meta"]
        totals["games"] += 1
        totals["days"].append(meta["days"])
        if meta["winner"] == "好人":
            totals["good_wins"] += 1
        elif meta["winner"] == "狼人":
            totals["wolf_wins"] += 1
        else:
            totals["draws"] += 1

        by_seat = {p["seat"]: p for p in meta["players"]}
        for p in meta["players"]:
            st = stats.setdefault(p["model"], ModelStats(model=p["model"]))
            st.games += 1
            won = meta["winner"] == p["camp"]
            st.wins += int(won)
            st.role_games[p["role"]] += 1
            st.role_wins[p["role"]] += int(won)
            if p["camp"] == "狼人":
                st.games_as_wolf += 1
                st.wins_as_wolf += int(won)
            else:
                st.games_as_good += 1
                st.wins_as_good += int(won)
            st.survived += int(p["alive"])
            st.survival_days.append(p["death_day"] or meta["days"])

        for rec in game["minds"]:
            model = rec["model_key"]
            st = stats.setdefault(model, ModelStats(model=model))
            st.calls += 1
            m = rec.get("meta") or {}
            if m.get("error"):
                st.llm_errors += 1
            if m.get("latency_ms"):
                st.latency_ms.append(int(m["latency_ms"]))
            usage = m.get("usage") or {}
            st.input_tokens += int(usage.get("input_tokens") or 0)
            st.output_tokens += int(usage.get("output_tokens") or 0)

            if rec["kind"] == "vote" and rec["camp"] == "好人":
                target = (rec.get("action") or {}).get("target") or 0
                if target and target in by_seat:
                    st.good_votes += 1
                    st.votes_cast += 1
                    if by_seat[target]["camp"] == "狼人":
                        st.votes_on_wolf += 1

            scheme = rec.get("scheme") or {}
            if rec["camp"] == "狼人" and str(scheme.get("stance", "")).startswith("悍跳"):
                st.wolf_bluffs += 1
                st.wolf_bluff_games.add((rec["game_id"], rec["seat"]))

            for b in rec.get("beliefs") or []:
                seat = b.get("seat")
                if seat not in by_seat or seat == rec["seat"]:
                    continue
                ok = _belief_is_correct(
                    str(b.get("guess", "")), by_seat[seat]["role"], by_seat[seat]["camp"]
                )
                if ok is None:
                    continue
                st.belief_total += 1
                st.belief_correct += int(ok)
                try:
                    st.belief_conf_sum += float(b.get("confidence") or 0)
                except (TypeError, ValueError):
                    pass
                st.belief_by_day[int(rec["day"])].append(int(ok))

    summary = {
        "games": totals["games"],
        "good_win_rate": round(100.0 * totals["good_wins"] / totals["games"], 1)
        if totals["games"]
        else None,
        "wolf_win_rate": round(100.0 * totals["wolf_wins"] / totals["games"], 1)
        if totals["games"]
        else None,
        "draws": totals["draws"],
        "avg_days": round(sum(totals["days"]) / len(totals["days"]), 2) if totals["days"] else None,
    }
    return stats, summary


def leaderboard(root: str | Path) -> dict[str, Any]:
    stats, summary = aggregate(root)
    rows = sorted((s.as_dict() for s in stats.values()), key=lambda r: -(r["win_rate"] or 0))
    return {"summary": summary, "models": rows}
