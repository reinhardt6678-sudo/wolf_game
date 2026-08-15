"""生成 HTML 复盘页与排行榜。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .archive import load_game
from .metrics import leaderboard
from .prompts import PHASE_LABEL

CSS = """
:root{--bg:#faf9f7;--fg:#1c1b19;--muted:#6b6862;--card:#fff;--line:#e5e1d8;
--wolf:#b4462f;--good:#2f6fb4;--accent:#8a6d3b;--mind:#f4efe4;}
@media (prefers-color-scheme:dark){:root{--bg:#16151a;--fg:#eceae5;--muted:#9a968e;
--card:#1f1e24;--line:#33313a;--wolf:#e08065;--good:#7fb0e6;--accent:#d9b877;--mind:#26232c;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 -apple-system,"Segoe UI",
"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;}
.wrap{max-width:980px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:19px;margin:36px 0 12px;
border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--muted);margin-bottom:20px}
.bar{position:sticky;top:0;background:var(--bg);padding:12px 0;border-bottom:1px solid var(--line);
z-index:9;display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.bar label{display:flex;gap:6px;align-items:center;cursor:pointer;font-size:14px}
select,button{font:inherit;background:var(--card);color:var(--fg);border:1px solid var(--line);
border-radius:6px;padding:5px 10px;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:14px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;white-space:nowrap}
th{background:var(--card);font-weight:600}
.wolf{color:var(--wolf);font-weight:600}.good{color:var(--good);font-weight:600}
.day{margin:26px 0 8px;font-weight:700;color:var(--accent);letter-spacing:.04em}
.ev{padding:7px 12px;border-left:3px solid var(--line);margin:4px 0}
.ev.wolfchan{border-left-color:var(--wolf);background:color-mix(in srgb,var(--wolf) 7%,transparent)}
.ev.priv{border-left-color:var(--accent);background:color-mix(in srgb,var(--accent) 7%,transparent)}
.who{font-weight:600;margin-right:6px}
.mind{background:var(--mind);border:1px solid var(--line);border-radius:8px;
padding:10px 14px;margin:6px 0 12px 18px;font-size:14px}
.mind[hidden]{display:none}
.mind .h{font-weight:600;color:var(--accent);margin-bottom:4px}
.mind .txt{white-space:pre-wrap}
.scheme{margin-top:8px;padding:8px 10px;border-radius:6px;
background:color-mix(in srgb,var(--wolf) 12%,transparent)}
.beliefs{margin-top:8px;color:var(--muted);font-size:13px}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:12px;
border:1px solid var(--line);margin-right:4px}
.empty{color:var(--muted)}
a{color:var(--good)}
"""

JS = """
function applyFilters(){
  const show = document.getElementById('showMind').checked;
  const seat = document.getElementById('seatFilter').value;
  document.querySelectorAll('.mind').forEach(el=>{
    el.hidden = !show || (seat !== 'all' && el.dataset.seat !== seat);
  });
}
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('showMind').addEventListener('change',applyFilters);
  document.getElementById('seatFilter').addEventListener('change',applyFilters);
  applyFilters();
});
"""


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _page(title: str, body: str, extra_js: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>{CSS}</style></head>
<body><div class="wrap">{body}</div><script>{extra_js}</script></body></html>"""


def render_replay(game_dir: str | Path) -> Path:
    game = load_game(game_dir)
    meta, events, minds = game["meta"], game["events"], game["minds"]
    by_seat = {p["seat"]: p for p in meta["players"]}

    minds_by_event: dict[int, list[dict]] = {}
    for m in minds:
        minds_by_event.setdefault((m.get("meta") or {}).get("event_idx", -1), []).append(m)

    rows = []
    for p in meta["players"]:
        cls = "wolf" if p["camp"] == "狼人" else "good"
        fate = "存活" if p["alive"] else f"第{p['death_day']}天 · {p['death_cause']}"
        rows.append(
            f"<tr><td>{p['seat']}号</td><td class='{cls}'>{_esc(p['role'])}</td>"
            f"<td>{_esc(p['camp'])}</td><td><code>{_esc(p['model'])}</code></td><td>{_esc(fate)}</td></tr>"
        )

    seat_opts = "".join(
        f"<option value='{s}'>{s}号 · {_esc(by_seat[s]['role'])}</option>" for s in sorted(by_seat)
    )

    body_parts = [
        f"<h1>对局复盘 · {_esc(meta['game_id'])}</h1>",
        f"<div class='sub'>{_esc(meta['board'])}　|　"
        f"<b class=\"{'wolf' if meta['winner']=='狼人' else 'good'}\">{_esc(meta['winner'])}获胜</b>"
        f"（{_esc(meta['end_reason'])}）　|　共 {meta['days']} 天</div>",
        "<div class='bar'>"
        "<label><input type='checkbox' id='showMind' checked> 显示心理活动</label>"
        f"<label>只看：<select id='seatFilter'><option value='all'>全部玩家</option>{seat_opts}</select></label>"
        "</div>",
        "<h2>身份表</h2>",
        "<table><thead><tr><th>座位</th><th>身份</th><th>阵营</th><th>模型</th><th>结局</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>",
        "<h2>时间线</h2>",
    ]

    header = None
    for ev in events:
        if ev["visibility"] == "god":
            continue
        key = (ev["day"], ev["phase"])
        if key != header:
            header = key
            label = "游戏开始" if ev["phase"] == "setup" else (
                f"第 {ev['day']} 天 · {PHASE_LABEL.get(ev['phase'], ev['phase'])}"
            )
            body_parts.append(f"<div class='day'>{_esc(label)}</div>")
        cls = {"wolf": "ev wolfchan", "private": "ev priv"}.get(ev["visibility"], "ev")
        who = ""
        if ev["actor"] is not None and ev["type"] in (
            "speech", "last_words", "hunter_shot", "vote_declare", "wolf_talk"
        ):
            p = by_seat.get(ev["actor"], {})
            camp_cls = "wolf" if p.get("camp") == "狼人" else "good"
            who = f"<span class='who {camp_cls}'>{ev['actor']}号（{_esc(p.get('role',''))}）</span>"
        body_parts.append(f"<div class='{cls}'>{who}{_esc(ev['text'])}</div>")

        for m in minds_by_event.get(ev["idx"], []):
            p = by_seat.get(m["seat"], {})
            camp_cls = "wolf" if m["camp"] == "狼人" else "good"
            inner = [
                f"<div class='h'>💭 <span class='{camp_cls}'>{m['seat']}号 · {_esc(m['role'])}</span>"
                f" <span class='tag'>{_esc(m['model_key'])}</span>"
                f"<span class='tag'>{_esc(m['kind'])}</span></div>",
                f"<div class='txt'>{_esc(m['thinking']) or '<span class=empty>（未输出心理活动）</span>'}</div>",
            ]
            sc = m.get("scheme")
            if sc:
                inner.append(
                    "<div class='scheme'><b>狼人策略：</b>"
                    f"{_esc(sc.get('stance'))}　"
                    f"计划金水 → {_esc(sc.get('gold_water_target'))}号　"
                    f"计划查杀 → {_esc(sc.get('kill_check_target'))}号"
                    f"<div class='txt'>{_esc(sc.get('reason'))}</div></div>"
                )
            if m.get("beliefs"):
                bs = "　".join(
                    f"<span class='tag'>{_esc(b.get('seat'))}号={_esc(b.get('guess'))}"
                    f" {_esc(b.get('confidence'))}</span>"
                    for b in m["beliefs"][:8]
                )
                inner.append(f"<div class='beliefs'><b>身份判断：</b>{bs}</div>")
            if m.get("action"):
                inner.append(
                    f"<div class='beliefs'><b>本次动作：</b><code>{_esc(json.dumps(m['action'], ensure_ascii=False))}</code></div>"
                )
            err = (m.get("meta") or {}).get("error")
            if err:
                inner.append(f"<div class='beliefs wolf'><b>调用错误：</b>{_esc(err)}</div>")
            body_parts.append(
                f"<div class='mind' data-seat='{m['seat']}'>{''.join(inner)}</div>"
            )

    out = Path(game["dir"]) / "replay.html"
    out.write_text(_page(f"复盘 {meta['game_id']}", "".join(body_parts), JS), encoding="utf-8")
    return out


LB_COLS = [
    ("model", "模型"),
    ("games", "场次"),
    ("win_rate", "总胜率%"),
    ("good_win_rate", "好人胜率%"),
    ("wolf_win_rate", "狼人胜率%"),
    ("survival_rate", "存活率%"),
    ("avg_survival_days", "平均存活天"),
    ("vote_accuracy", "票型正确率%"),
    ("belief_accuracy", "身份判断准确率%"),
    ("belief_calibration", "平均自信度"),
    ("bluff_rate", "悍跳率%"),
    ("avg_latency_ms", "平均延迟ms"),
    ("error_rate", "调用失败率%"),
]


def render_leaderboard(root: str | Path, out_path: str | Path | None = None) -> Path:
    lb = leaderboard(root)
    s = lb["summary"]
    head = "".join(f"<th>{_esc(t)}</th>" for _, t in LB_COLS)
    rows = []
    for r in lb["models"]:
        tds = "".join(f"<td>{_esc(r.get(k) if r.get(k) is not None else '—')}</td>" for k, _ in LB_COLS)
        rows.append(f"<tr>{tds}</tr>")

    role_tbl = []
    for r in lb["models"]:
        cells = "、".join(
            f"{role} {v['win_rate'] if v['win_rate'] is not None else '—'}%({v['games']}场)"
            for role, v in r["by_role"].items()
        )
        role_tbl.append(f"<tr><td>{_esc(r['model'])}</td><td style='white-space:normal'>{_esc(cells)}</td></tr>")

    trend = []
    for r in lb["models"]:
        d = r["belief_accuracy_by_day"]
        if d:
            line = "　".join(f"第{k}天 {v}%" for k, v in d.items())
            trend.append(f"<tr><td>{_esc(r['model'])}</td><td style='white-space:normal'>{_esc(line)}</td></tr>")

    body = f"""
<h1>狼人杀训练营 · 模型排行榜</h1>
<div class='sub'>共 {s['games']} 局　|　好人胜率 {s['good_win_rate']}%　|　狼人胜率 {s['wolf_win_rate']}%
　|　平局 {s['draws']}　|　平均 {s['avg_days']} 天结束</div>
<h2>总览</h2>
<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>分角色胜率</h2>
<table><thead><tr><th>模型</th><th>各角色表现</th></tr></thead><tbody>{''.join(role_tbl)}</tbody></table>
<h2>身份判断准确率随天数变化</h2>
<div class='sub'>好人阵营的判断应当随天数推进而收敛；不收敛说明模型没有真正利用新信息。</div>
<table><thead><tr><th>模型</th><th>逐天准确率</th></tr></thead><tbody>{''.join(trend) or '<tr><td colspan=2>暂无数据</td></tr>'}</tbody></table>
"""
    out = Path(out_path or Path(root) / "leaderboard.html")
    out.write_text(_page("狼人杀训练营排行榜", body), encoding="utf-8")
    return out
