"""对局档案。

每局产出一个目录：

    archive/<game_id>/
        meta.json       身份表、模型分配、胜负
        events.jsonl    完整事件流（含可见性标记）
        minds.jsonl     每一次决策的心理活动档案  ← 本项目的核心产物
        transcript.md   人类可读的复盘（公开时间线 + 心理活动对照）

心理活动只写进 ``minds.jsonl``，绝不会进入 ``events.jsonl`` 的
public/wolf 事件，因此不可能被其他智能体读到。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .events import Event
from .prompts import PHASE_LABEL

KIND_LABEL = {
    "wolf_kill": "狼人刀人",
    "seer_check": "预言家查验",
    "guard_protect": "守卫守护",
    "witch_action": "女巫用药",
    "speech": "白天发言",
    "vote": "投票",
    "last_words": "遗言",
    "hunter_shot": "猎人开枪",
}


@dataclass
class MindRecord:
    game_id: str
    seq: int
    day: int
    phase: str
    kind: str
    seat: int
    role: str
    camp: str
    model_key: str
    model: str
    thinking: str
    notes: str
    beliefs: list[dict] = field(default_factory=list)
    scheme: dict | None = None
    action: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class Archive:
    def __init__(self, root: str | Path, game_id: str) -> None:
        self.dir = Path(root) / game_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.game_id = game_id
        self._events = (self.dir / "events.jsonl").open("w", encoding="utf-8")
        self._minds = (self.dir / "minds.jsonl").open("w", encoding="utf-8")
        self.mind_records: list[MindRecord] = []
        self.event_list: list[Event] = []
        self._seq = 0

    # ------------------------------------------------------------------
    def on_event(self, ev: Event) -> None:
        self.event_list.append(ev)
        self._events.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        self._events.flush()

    def record_mind(self, **kwargs: Any) -> MindRecord:
        self._seq += 1
        rec = MindRecord(game_id=self.game_id, seq=self._seq, **kwargs)
        self.mind_records.append(rec)
        self._minds.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        self._minds.flush()
        return rec

    def write_meta(self, meta: dict) -> None:
        (self.dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def close(self) -> None:
        self._events.close()
        self._minds.close()

    # ------------------------------------------------------------------
    def write_transcript(self, meta: dict) -> Path:
        """生成 Markdown 复盘：公开时间线，并在每次决策后附上心理活动。"""
        minds_by_idx: dict[int, list[MindRecord]] = {}
        for rec in self.mind_records:
            minds_by_idx.setdefault(rec.meta.get("event_idx", -1), []).append(rec)

        out: list[str] = [
            f"# 对局复盘 {self.game_id}",
            "",
            f"- 板子：{meta['board']}",
            f"- 结果：**{meta['winner']}获胜**（{meta['end_reason']}，共 {meta['days']} 天）",
            "",
            "## 身份表",
            "",
            "| 座位 | 身份 | 阵营 | 模型 | 结局 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for p in meta["players"]:
            fate = "存活" if p["alive"] else f"第{p['death_day']}天 {p['death_cause']}"
            out.append(
                f"| {p['seat']}号 | {p['role']} | {p['camp']} | `{p['model']}` | {fate} |"
            )
        out += ["", "## 时间线与心理活动", ""]

        header = None
        for ev in self.event_list:
            if ev.visibility.value == "god":
                continue
            key = (ev.day, ev.phase)
            if key != header:
                header = key
                label = "游戏开始" if ev.phase == "setup" else f"第 {ev.day} 天 · {PHASE_LABEL.get(ev.phase, ev.phase)}"
                out += ["", f"### {label}", ""]
            tag = {"public": "", "wolf": "🐺 ", "private": "🔒 "}.get(ev.visibility.value, "")
            speaker = f"**{ev.actor}号**：" if ev.actor is not None and ev.type in (
                "speech", "last_words", "hunter_shot", "wolf_talk"
            ) else ""
            out.append(f"- {tag}{speaker}{ev.text}")
            for rec in minds_by_idx.get(ev.idx, []):
                out.append("")
                out.append(
                    f"  <details><summary>💭 {rec.seat}号（{rec.role} / `{rec.model_key}`）的心理活动</summary>"
                )
                out.append("")
                out.append(f"  > {rec.thinking or '（空）'}".replace("\n", "\n  > "))
                if rec.scheme:
                    out.append("")
                    out.append(
                        f"  > **狼人策略**：{rec.scheme.get('stance', '')}"
                        f" ｜ 计划金水：{rec.scheme.get('gold_water_target', 0)}号"
                        f" ｜ 计划查杀：{rec.scheme.get('kill_check_target', 0)}号"
                    )
                    out.append(f"  > {rec.scheme.get('reason', '')}".replace("\n", "\n  > "))
                if rec.beliefs:
                    b = "；".join(
                        f"{x.get('seat')}号={x.get('guess')}({x.get('confidence')})"
                        for x in rec.beliefs[:6]
                    )
                    out.append("")
                    out.append(f"  > **身份判断**：{b}")
                out.append("")
                out.append("  </details>")
                out.append("")

        path = self.dir / "transcript.md"
        path.write_text("\n".join(out), encoding="utf-8")
        return path


def load_game(game_dir: str | Path) -> dict:
    """读回一局的档案。"""
    d = Path(game_dir)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    events = [json.loads(l) for l in (d / "events.jsonl").read_text(encoding="utf-8").splitlines() if l]
    minds = [json.loads(l) for l in (d / "minds.jsonl").read_text(encoding="utf-8").splitlines() if l]
    return {"meta": meta, "events": events, "minds": minds, "dir": d}


def iter_games(root: str | Path):
    root = Path(root)
    if not root.exists():
        return
    for d in sorted(root.iterdir()):
        if (d / "meta.json").exists():
            yield load_game(d)
