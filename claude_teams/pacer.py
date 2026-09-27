"""Re-export of the shared pacer, which is not Teams-specific.

Discord meters edits per channel exactly as Teams meters them per conversation,
so the implementation moved to :mod:`claude_code_core.pacer` when the Discord
frontend needed it too. This module stays so existing imports keep working.
"""

from __future__ import annotations

from claude_code_core.pacer import UpdatePacer

__all__ = ["UpdatePacer"]
