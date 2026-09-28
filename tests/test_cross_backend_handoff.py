"""Cross-backend conversation history handoff tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_discord.cross_backend_handoff import (
    ConversationHistoryReader,
    build_handoff_prompt,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


class TestConversationHistoryReader:
    def test_reads_codex_response_messages_without_loading_context_or_tools(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "rollout.jsonl"
        records = [
            {"type": "session_meta", "payload": {"base_instructions": "native rules"}},
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "developer", "content": "secret rules"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": "# AGENTS.md instructions for /project\n<INSTRUCTIONS>rules",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": "<environment_context>injected cwd</environment_context>",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Please fix this"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Fixed and checked"},
                        {"type": "image", "text": "not conversation text"},
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "output": "tool result"},
            },
        ]
        _write_jsonl(path, records)

        assert ConversationHistoryReader()._read_codex(path) == [
            ("User", "Please fix this"),
            ("Assistant", "Fixed and checked"),
        ]

    @pytest.mark.parametrize("response_first", [False, True])
    def test_codex_mirrored_formats_are_not_duplicated_but_repeated_turns_survive(
        self, tmp_path: Path, response_first: bool
    ) -> None:
        records: list[dict] = []
        for role, message in [("user", "continue"), ("user", "continue"), ("assistant", "done")]:
            response = {
                "type": "response_item",
                "payload": {"type": "message", "role": role, "content": message},
            }
            event = {
                "type": "event_msg",
                "payload": {
                    "type": "user_message" if role == "user" else "agent_message",
                    "message": message,
                },
            }
            records.extend([response, event] if response_first else [event, response])
        path = tmp_path / "rollout.jsonl"
        _write_jsonl(path, records)

        assert ConversationHistoryReader()._read_codex(path) == [
            ("User", "continue"),
            ("User", "continue"),
            ("Assistant", "done"),
        ]

    def test_codex_mixed_formats_preserve_unmirrored_messages(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout.jsonl"
        _write_jsonl(
            path,
            [
                {"type": "event_msg", "payload": {"type": "user_message", "message": "old"}},
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "assistant", "content": "new"},
                },
            ],
        )
        assert ConversationHistoryReader()._read_codex(path) == [
            ("User", "old"),
            ("Assistant", "new"),
        ]

    def test_codex_malformed_records_do_not_break_a_valid_handoff(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout.jsonl"
        path.write_text(
            '\nnot JSON\nnull\n[]\n{"type":"event_msg","payload":[]}\n'
            '{"type":"response_item","payload":null}\n'
            '{"type":"event_msg","payload":{"type":"agent_message","message":"ok"}}\n'
        )
        assert ConversationHistoryReader()._read_codex(path) == [("Assistant", "ok")]

    def test_codex_invalid_field_shapes_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout.jsonl"
        _write_jsonl(
            path,
            [
                {"type": "event_msg", "payload": {"type": [], "message": "ignore"}},
                {"type": "response_item", "payload": {"type": "message", "role": []}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": [], "text": "ignore"},
                            {"type": "output_text", "text": "ok"},
                        ],
                    },
                },
            ],
        )
        assert ConversationHistoryReader()._read_codex(path) == [("Assistant", "ok")]

    def test_response_format_is_found_and_bounded_through_public_reader(
        self, tmp_path: Path
    ) -> None:
        session_id = "11111111-2222-3333-4444-555555555555"
        records: list[dict] = []
        for role, text in [
            ("user", "<div>keep this user's HTML</div>"),
            ("assistant", "checked"),
            ("user", "unanswered tail"),
        ]:
            records.append(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": role, "content": text},
                }
            )
        _write_jsonl(tmp_path / "sessions" / f"rollout-{session_id}.jsonl", records)
        reader = ConversationHistoryReader(codex_home=tmp_path, max_transcript_chars=100)
        transcript = reader.read("codex", session_id)
        assert transcript == "User:\n<div>keep this user's HTML</div>\n\nAssistant:\nchecked"
        assert len(transcript) <= 100

    def test_reads_claude_user_and_assistant_text_only(self, tmp_path: Path) -> None:
        session_id = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
        _write_jsonl(
            tmp_path / "project" / f"{session_id}.jsonl",
            [
                {"type": "user", "isMeta": True, "message": {"content": "internal"}},
                {"type": "user", "message": {"content": "最初の質問"}},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "secret reasoning"},
                            {"type": "text", "text": "最初の回答"},
                            {"type": "tool_use", "name": "Bash"},
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "content": "noise"}]},
                },
                # An unanswered tail is excluded: the current Discord message is
                # supplied separately and must not be duplicated.
                {"type": "user", "message": {"content": "未回答の末尾"}},
            ],
        )

        reader = ConversationHistoryReader(claude_sessions_root=tmp_path)

        transcript = reader.read("claude", session_id)

        assert transcript == "User:\n最初の質問\n\nAssistant:\n最初の回答"
        assert "internal" not in transcript
        assert "secret reasoning" not in transcript
        assert "noise" not in transcript
        assert "未回答の末尾" not in transcript

    def test_reads_codex_event_messages_only(self, tmp_path: Path) -> None:
        session_id = "cccccccc-1111-2222-3333-dddddddddddd"
        rollout = (
            tmp_path
            / "sessions"
            / "2026"
            / "08"
            / "05"
            / (f"rollout-2026-08-05T12-00-00-{session_id}.jsonl")
        )
        _write_jsonl(
            rollout,
            [
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "developer", "content": "ignore"},
                },
                {"type": "event_msg", "payload": {"type": "user_message", "message": "依頼"}},
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "対応したよ"},
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "token_count", "message": "ignore"},
                },
            ],
        )

        reader = ConversationHistoryReader(codex_home=tmp_path)

        assert reader.read("codex", session_id) == "User:\n依頼\n\nAssistant:\n対応したよ"

    def test_rejects_invalid_session_id_before_path_lookup(self, tmp_path: Path) -> None:
        reader = ConversationHistoryReader(
            claude_sessions_root=tmp_path,
            codex_home=tmp_path,
        )

        assert reader.read("claude", "../../etc/passwd") == ""
        assert reader.read("codex", "not valid") == ""

    def test_bounds_message_and_total_transcript_size(self, tmp_path: Path) -> None:
        session_id = "eeeeeeee-1111-2222-3333-ffffffffffff"
        records: list[dict] = []
        for number in range(20):
            role = "user" if number % 2 == 0 else "assistant"
            records.append(
                {"type": role, "message": {"content": f"message-{number}-" + ("x" * 200)}}
            )
        _write_jsonl(tmp_path / f"{session_id}.jsonl", records)
        reader = ConversationHistoryReader(
            claude_sessions_root=tmp_path,
            max_messages=4,
            max_message_chars=40,
            max_transcript_chars=140,
        )

        transcript = reader.read("claude", session_id)

        assert "message-16" in transcript
        assert "message-19" in transcript
        assert "message-15" not in transcript
        assert len(transcript) <= 140


def test_build_handoff_prompt_marks_history_and_current_message() -> None:
    prompt = build_handoff_prompt(
        source_backend="claude",
        target_backend="codex",
        transcript="User:\nold question\n\nAssistant:\nold answer",
        current_prompt="new question",
    )

    assert "Claude" in prompt
    assert "Codex" in prompt
    assert "old question" in prompt
    assert "old answer" in prompt
    assert prompt.endswith("Current user message:\nnew question")
    assert "do not repeat completed work" in prompt
