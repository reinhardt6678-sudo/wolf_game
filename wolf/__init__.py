"""本地狼人杀 LLM 训练营。

用多个 LLM 互相对局，并把每个角色发言时的「心理活动」完整存档，
用于复盘与评测。
"""

__version__ = "0.1.0"

from .roles import BUILTIN_BOARDS, Board, Camp, Role, Rules  # noqa: F401
