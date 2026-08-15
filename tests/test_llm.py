"""LLM 通路测试（不发真实网络请求）。

验证请求体的形状是对的 —— 这是最容易出错、又最难在跑真实对局时
才发现的部分（比如给 Claude Opus 5 传了 temperature 会直接 400）。
"""

import json
from types import SimpleNamespace

import pytest

from wolf.agents.base import ActionRequest
from wolf.agents.llm_agent import LLMAgent
from wolf.llm.anthropic_client import AnthropicClient
from wolf.llm.base import LLMResult, extract_json
from wolf.roles import BUILTIN_BOARDS, Role
from wolf.schemas import schema_for


class _FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def _fake_message(text: str, stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        model="claude-opus-5",
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=0,
        ),
    )


@pytest.fixture
def captured():
    return {}


def _install_fake_sdk(client: AnthropicClient, captured: dict, payload: dict):
    def stream(**kwargs):
        captured.update(kwargs)
        return _FakeStream(_fake_message(json.dumps(payload, ensure_ascii=False)))

    client.client = SimpleNamespace(
        messages=SimpleNamespace(stream=stream),
        beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)),
    )


def test_opus5_request_shape(captured):
    c = AnthropicClient(key="opus", model="claude-opus-5", effort="high", api_key="sk-test")
    _install_fake_sdk(c, captured, {"thinking": "x", "beliefs": [], "notes": "", "target": 3})
    schema = schema_for("seer_check", is_wolf=False)
    c.generate_json(system="SYS", user="USR", schema=schema)

    # Claude Opus 5 不接受采样参数，传了会 400
    assert "temperature" not in captured
    assert "top_p" not in captured
    assert "top_k" not in captured
    # 自适应思考 + 结构化输出 + effort
    assert captured["thinking"] == {"type": "adaptive"}
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["output_config"]["effort"] == "high"
    # 系统提示带缓存断点（每局每人固定，能省下大量重复输入）
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["messages"] == [{"role": "user", "content": "USR"}]


def test_disabled_thinking_caps_effort_at_high():
    # Claude Opus 5 上 thinking=disabled 与 xhigh/max 同时出现会返回 400
    c = AnthropicClient(key="k", model="claude-opus-5", effort="max", thinking=False, api_key="sk-test")
    assert c.effort == "high"


def test_refusal_is_surfaced(captured):
    from wolf.llm.base import LLMError

    c = AnthropicClient(key="k", model="claude-opus-5", api_key="sk-test")

    def stream(**kwargs):
        return _FakeStream(_fake_message("", stop_reason="refusal"))

    c.client = SimpleNamespace(
        messages=SimpleNamespace(stream=stream),
        beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)),
    )
    with pytest.raises(LLMError, match="拒绝"):
        c.generate_json(system="s", user="u", schema=schema_for("vote", False))


def test_usage_is_reported(captured):
    c = AnthropicClient(key="k", model="claude-opus-5", api_key="sk-test")
    _install_fake_sdk(c, captured, {"thinking": "t", "beliefs": [], "notes": "", "target": 1, "one_liner": ""})
    res = c.generate_json(system="s", user="u", schema=schema_for("vote", False))
    assert res.usage["input_tokens"] == 100
    assert res.usage["cache_read"] == 80


# ----------------------------------------------------------------------
class FakeClient:
    """按脚本返回内容的假客户端，用来测 LLMAgent 的解析与纠错。"""

    model = "fake"

    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def generate_json(self, *, system, user, schema, max_tokens=8000):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return LLMResult(text=self.text, model="fake", usage={}, latency_ms=1)


def _agent(text: str, role=Role.WEREWOLF):
    a = LLMAgent(FakeClient(text), model_key="fake")
    a.on_game_start(
        seat=1, role=role, board=BUILTIN_BOARDS["board_9"], teammates=[2, 3], all_seats=list(range(1, 10))
    )
    return a


def _req(kind="vote", options=(2, 3, 4)):
    return ActionRequest(
        kind=kind, day=1, phase="day", observation="(略)", alive=[1, 2, 3, 4],
        dead=[], options=list(options), allow_zero=True,
    )


def test_agent_parses_full_payload():
    payload = {
        "thinking": "我是狼，准备悍跳",
        "beliefs": [{"seat": 4, "guess": "预言家", "confidence": 0.7, "reason": "发言像"}],
        "notes": "记住4号",
        "scheme": {
            "stance": "悍跳预言家",
            "gold_water_target": 2,
            "kill_check_target": 4,
            "reason": "2号是狼队友，给他金水能让他站住；4号像真预言家，先查杀他抢身份",
        },
        "target": 4,
        "one_liner": "投4号",
    }
    a = _agent(json.dumps(payload, ensure_ascii=False))
    d = a.act(_req())
    assert d.thinking.startswith("我是狼")
    assert d.scheme["stance"] == "悍跳预言家"
    assert d.scheme["gold_water_target"] == 2
    assert d.data["target"] == 4
    assert a.notes == "记住4号"  # 备忘会带到下一轮


def test_agent_coerces_illegal_target():
    a = _agent(json.dumps({"thinking": "t", "beliefs": [], "notes": "", "target": 99, "one_liner": ""}))
    d = a.act(_req(options=(2, 3)))
    assert d.data["target"] in (2, 3)
    assert "target" in d.meta["coerced_fields"]


def test_agent_survives_garbage_output():
    a = _agent("对不起，我不能扮演这个角色。")
    d = a.act(_req())
    assert d.meta["fallback_decision"] is True
    assert d.data["target"] == 0  # 保底为弃票，不会打断对局


def test_wolf_schema_has_scheme_but_good_does_not():
    assert "scheme" in schema_for("speech", is_wolf=True)["properties"]
    assert "scheme" not in schema_for("speech", is_wolf=False)["properties"]
    wolf_schema = schema_for("wolf_kill", is_wolf=True)
    assert wolf_schema["additionalProperties"] is False
    assert set(wolf_schema["required"]) >= {"thinking", "beliefs", "notes", "scheme", "target"}


def test_extract_json_handles_code_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('好的，这是我的决定：\n{"a": 2}\n希望有帮助') == {"a": 2}
    assert extract_json('{"s": "花括号 } 在字符串里", "n": 3}')["n"] == 3


def test_openai_payload_shape():
    from wolf.llm.openai_client import OpenAICompatClient

    c = OpenAICompatClient(key="local", model="qwen3:32b", base_url="http://x/v1")
    body = c._payload("SYS", "USR", schema_for("vote", False), 1024, "json_schema")
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][0]["role"] == "system"
    # 降级模式会把 schema 塞进 system prompt
    body2 = c._payload("SYS", "USR", schema_for("vote", False), 1024, "json_object")
    assert body2["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in body2["messages"][0]["content"]
