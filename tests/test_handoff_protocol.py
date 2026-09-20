"""Tests for the trusted-agent handoff wire protocol (OpenSpec task 1.1).

The protocol module is surface-neutral: it may not import Discord, the database,
or anything else that ties a task packet to one transport. Everything here is a
value-object/validation test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from claude_code_core.handoffs import protocol as p

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"


def _origin() -> p.ConversationCoordinate:
    return p.ConversationCoordinate(
        guild_id=111, channel_id=222, thread_id=333, message_id=444
    )


def _task(**overrides: object) -> p.HandoffTask:
    fields: dict[str, object] = {
        "task_id": TASK_ID,
        "sender": "drewai",
        "recipient": "david",
        "origin": _origin(),
        "origin_human_id": "drew",
        "project": p.ProjectLocator(owner="drew", folder="main-projects/realpage"),
        "goal": "Report how many rows the leads table has.",
        "authority": p.AuthorityScope(read=True),
        "findings": ("The table was last refreshed on 2026-09-18.",),
        "expected_result": "One number plus the file it came from.",
        "reply_to": _origin(),
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=6),
    }
    fields.update(overrides)
    return p.HandoffTask(**fields)  # type: ignore[arg-type]


def _event(**overrides: object) -> p.HandoffEvent:
    fields: dict[str, object] = {
        "event_id": EVENT_ID,
        "kind": p.HandoffEventKind.TASK,
        "task_id": TASK_ID,
        "sender": "drewai",
        "recipient": "david",
        "sequence": 0,
        "created_at": NOW,
        "task": _task(),
    }
    fields.update(overrides)
    return p.HandoffEvent(**fields)  # type: ignore[arg-type]


class TestSurfaceNeutrality:
    def test_module_does_not_import_discord_or_database(self) -> None:
        source = p.__file__
        text = open(source, encoding="utf-8").read()
        assert "import discord" not in text
        assert "aiosqlite" not in text
        assert "claude_discord" not in text


class TestUuidValidation:
    def test_canonical_uuid_is_accepted_and_lowercased(self) -> None:
        assert p.validate_uuid(TASK_ID.upper()) == TASK_ID

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "not-a-uuid",
            "6d9f6ad03c6e4a1e9f4a2f2a1c0b7e11",
            "{6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11}",
            "urn:uuid:6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11",
            123,
        ],
    )
    def test_malformed_ids_are_rejected(self, bad: object) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.validate_uuid(bad)


class TestAgentIds:
    def test_agent_id_is_normalised(self) -> None:
        assert p.validate_agent_id("DrewAI") == "drewai"

    @pytest.mark.parametrize("bad", ["", "  ", "a b", "drew/ai", "x" * 65, "../david", None])
    def test_bad_agent_ids_are_rejected(self, bad: object) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.validate_agent_id(bad)


class TestProjectLocator:
    def test_owner_and_folder_round_trip(self) -> None:
        locator = p.ProjectLocator(owner="Drew", folder="main-projects/realpage")
        assert locator.owner == "drew"
        assert locator.folder == "main-projects/realpage"
        assert p.ProjectLocator.from_dict(locator.to_dict()) == locator

    @pytest.mark.parametrize(
        "owner",
        ["my", "mine", "our projects", "his", "her", "their", "your", "the user", "me", "someone"],
    )
    def test_ambiguous_owner_language_is_rejected(self, owner: str) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.ProjectLocator(owner=owner, folder="realpage")

    @pytest.mark.parametrize(
        "folder",
        [
            "/home/drew/main-projects/realpage",
            "C:\\Users\\drew\\realpage",
            "\\\\server\\share\\realpage",
            "~/main-projects/realpage",
            "file:///home/drew/realpage",
        ],
    )
    def test_absolute_remote_paths_are_rejected(self, folder: str) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.ProjectLocator(owner="drew", folder=folder)

    @pytest.mark.parametrize(
        "folder",
        [
            "../secrets",
            "main-projects/../../etc",
            "main-projects/./realpage/..",
            "main-projects/%2e%2e/etc",
            "main-projects\\realpage",
            "",
            "   ",
            "realpage\x00",
            "a" * 300,
        ],
    )
    def test_path_traversal_and_junk_is_rejected(self, folder: str) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.ProjectLocator(owner="drew", folder=folder)


class TestAuthorityScope:
    def test_default_scope_is_read_only(self) -> None:
        scope = p.AuthorityScope(read=True)
        assert scope.is_read_only is True
        assert scope.capabilities == frozenset()

    def test_edit_paths_require_edit_authority(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.AuthorityScope(read=True, edit=False, edit_paths=("src",))

    def test_edit_paths_follow_locator_path_rules(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.AuthorityScope(read=True, edit=True, edit_paths=("../other-project",))

    def test_scope_without_read_or_edit_is_rejected(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.AuthorityScope(read=False, edit=False)

    def test_exceptional_capabilities_round_trip(self) -> None:
        scope = p.AuthorityScope(
            read=True,
            edit=True,
            edit_paths=("scripts",),
            capabilities=frozenset({p.HandoffCapability.DEPLOYMENT}),
        )
        assert p.AuthorityScope.from_dict(scope.to_dict()) == scope
        assert scope.is_read_only is False

    @pytest.mark.parametrize("prose", ["full permissions", "whatever is needed", "destructive?"])
    def test_prose_capabilities_are_rejected(self, prose: str) -> None:
        raw = p.AuthorityScope(read=True).to_dict()
        raw["capabilities"] = [prose]
        with pytest.raises(p.HandoffValidationError):
            p.AuthorityScope.from_dict(raw)


class TestConversationCoordinate:
    def test_round_trip(self) -> None:
        coord = _origin()
        assert p.ConversationCoordinate.from_dict(coord.to_dict()) == coord

    def test_thread_and_message_are_optional(self) -> None:
        coord = p.ConversationCoordinate(guild_id=1, channel_id=2)
        assert coord.thread_id is None
        assert p.ConversationCoordinate.from_dict(coord.to_dict()) == coord

    @pytest.mark.parametrize("bad", [0, -5, "222", 2**63])
    def test_invalid_ids_are_rejected(self, bad: object) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.ConversationCoordinate(guild_id=111, channel_id=bad)  # type: ignore[arg-type]


class TestHandoffTask:
    def test_valid_task_round_trips(self) -> None:
        task = _task()
        assert p.HandoffTask.from_dict(task.to_dict()) == task
        assert task.version == p.PROTOCOL_VERSION

    def test_sender_and_recipient_must_differ(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _task(recipient="DrewAI")

    def test_expiry_must_follow_creation(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _task(expires_at=NOW - timedelta(minutes=1))

    def test_naive_timestamps_are_rejected(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _task(created_at=datetime(2026, 9, 20, 12, 0, 0))  # noqa: DTZ001

    def test_expiry_is_reported(self) -> None:
        task = _task()
        assert task.is_expired(NOW + timedelta(hours=1)) is False
        assert task.is_expired(NOW + timedelta(hours=7)) is True

    def test_goal_and_expected_result_are_required(self) -> None:
        for field_name in ("goal", "expected_result"):
            with pytest.raises(p.HandoffValidationError):
                _task(**{field_name: "   "})

    def test_oversized_goal_is_rejected(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _task(goal="x" * (p.MAX_GOAL_CHARS + 1))

    def test_findings_are_bounded_in_count_and_size(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _task(findings=tuple(f"note {i}" for i in range(p.MAX_FINDINGS + 1)))
        with pytest.raises(p.HandoffValidationError):
            _task(findings=("x" * (p.MAX_FINDING_CHARS + 1),))

    def test_findings_must_not_be_a_transcript_blob(self) -> None:
        task = _task(findings=())
        assert task.findings == ()

    def test_missing_field_in_dict_is_rejected(self) -> None:
        raw = _task().to_dict()
        del raw["goal"]
        with pytest.raises(p.HandoffValidationError):
            p.HandoffTask.from_dict(raw)

    def test_unknown_field_in_dict_is_rejected(self) -> None:
        raw = _task().to_dict()
        raw["transcript"] = "the entire conversation"
        with pytest.raises(p.HandoffValidationError):
            p.HandoffTask.from_dict(raw)

    def test_unsupported_version_is_rejected(self) -> None:
        raw = _task().to_dict()
        raw["version"] = "v99"
        with pytest.raises(p.HandoffVersionError):
            p.HandoffTask.from_dict(raw)


class TestEventKinds:
    def test_all_kinds_are_known(self) -> None:
        assert {k.value for k in p.HandoffEventKind} == {
            "task",
            "ack",
            "state",
            "question",
            "answer",
            "result",
        }

    def test_only_a_task_event_creates_work(self) -> None:
        assert p.HandoffEventKind.TASK.creates_work is True
        for kind in p.HandoffEventKind:
            if kind is not p.HandoffEventKind.TASK:
                assert kind.creates_work is False

    def test_terminal_result_is_recognised(self) -> None:
        assert p.HandoffEventKind.RESULT.is_terminal is True
        assert p.HandoffEventKind.ACK.is_terminal is False


class TestSequenceRules:
    def test_task_event_starts_the_chain_at_zero(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(sequence=1)

    def test_other_events_must_follow_the_task(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(kind=p.HandoffEventKind.ACK, sequence=0, task=None)

    def test_monotonic_sequence_is_enforced(self) -> None:
        assert p.validate_monotonic_sequence(None, 0) == 0
        assert p.validate_monotonic_sequence(3, 4) == 4
        for previous, candidate in ((3, 3), (3, 2), (None, 1)):
            with pytest.raises(p.HandoffValidationError):
                p.validate_monotonic_sequence(previous, candidate)

    def test_sequence_is_bounded(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.validate_monotonic_sequence(None, p.MAX_SEQUENCE + 1)
        with pytest.raises(p.HandoffValidationError):
            p.validate_monotonic_sequence(None, True)

    def test_next_sequence(self) -> None:
        assert p.next_sequence(None) == 0
        assert p.next_sequence(7) == 8


class TestEventPayloadRules:
    def test_task_event_requires_a_task(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(task=None)

    def test_non_task_event_must_not_carry_a_task(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(kind=p.HandoffEventKind.ACK, sequence=1)

    def test_task_event_ids_must_agree(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(task=_task(sender="imac"))

    def test_state_event_requires_a_state_value(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(kind=p.HandoffEventKind.STATE, sequence=1, task=None)
        event = _event(
            kind=p.HandoffEventKind.STATE,
            sequence=1,
            task=None,
            payload={"state": "running"},
        )
        assert event.payload["state"] == "running"

    def test_result_event_requires_outcome_and_summary(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(
                kind=p.HandoffEventKind.RESULT,
                sequence=2,
                task=None,
                payload={"outcome": "completed"},
            )
        with pytest.raises(p.HandoffValidationError):
            _event(
                kind=p.HandoffEventKind.RESULT,
                sequence=2,
                task=None,
                payload={"outcome": "sideways", "summary": "done"},
            )

    def test_question_and_answer_require_their_text(self) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(kind=p.HandoffEventKind.QUESTION, sequence=1, task=None, payload={})
        with pytest.raises(p.HandoffValidationError):
            _event(kind=p.HandoffEventKind.ANSWER, sequence=2, task=None, payload={"answer": ""})

    @pytest.mark.parametrize(
        "payload",
        [
            {"state": {"nested": "object"}},
            {"state": ["running"]},
            {"state": 1.5},
            {"Bad Key": "running"},
            {"state": "run\x00ning"},
            {"state": "x" * 4000},
        ],
    )
    def test_unsafe_payload_values_are_rejected(self, payload: dict[str, object]) -> None:
        with pytest.raises(p.HandoffValidationError):
            _event(kind=p.HandoffEventKind.STATE, sequence=1, task=None, payload=payload)

    def test_payload_key_count_is_bounded(self) -> None:
        payload = {"state": "running"} | {f"k{i}": "v" for i in range(p.MAX_PAYLOAD_KEYS)}
        with pytest.raises(p.HandoffValidationError):
            _event(kind=p.HandoffEventKind.STATE, sequence=1, task=None, payload=payload)


class TestSerialization:
    def test_task_event_round_trips_through_json(self) -> None:
        event = _event()
        decoded = p.decode_event(p.encode_event(event))
        assert decoded == event
        assert decoded.task is not None
        assert decoded.task.project.folder == "main-projects/realpage"

    def test_state_event_round_trips_through_json(self) -> None:
        event = _event(
            kind=p.HandoffEventKind.STATE,
            sequence=4,
            task=None,
            payload={"state": "blocked", "attempt": 2, "final": False},
        )
        assert p.decode_event(p.encode_event(event)) == event

    def test_encoded_event_is_a_json_object(self) -> None:
        raw = json.loads(p.encode_event(_event()))
        assert raw["kind"] == "task"
        assert raw["version"] == p.PROTOCOL_VERSION

    def test_oversized_packet_is_rejected_on_decode(self) -> None:
        text = p.encode_event(_event())
        padded = text[:-1] + ',"pad":"' + "x" * p.MAX_PACKET_BYTES + '"}'
        with pytest.raises(p.HandoffSizeError):
            p.decode_event(padded)

    def test_oversized_packet_is_rejected_on_encode(self) -> None:
        big = _task(findings=tuple("y" * p.MAX_FINDING_CHARS for _ in range(p.MAX_FINDINGS)))
        with pytest.raises(p.HandoffSizeError):
            p.encode_event(_event(task=big), max_bytes=256)

    @pytest.mark.parametrize(
        "text",
        ["", "not json", "[]", '"task"', "null", "{}"],
    )
    def test_malformed_json_is_rejected(self, text: str) -> None:
        with pytest.raises(p.HandoffValidationError):
            p.decode_event(text)

    def test_unsupported_version_is_rejected(self) -> None:
        raw = json.loads(p.encode_event(_event()))
        raw["version"] = "v2"
        with pytest.raises(p.HandoffVersionError):
            p.decode_event(json.dumps(raw))

    def test_unknown_kind_is_rejected(self) -> None:
        raw = json.loads(p.encode_event(_event(kind=p.HandoffEventKind.ACK, sequence=1, task=None)))
        raw["kind"] = "shutdown"
        with pytest.raises(p.HandoffValidationError):
            p.decode_event(json.dumps(raw))

    def test_unknown_top_level_key_is_rejected(self) -> None:
        raw = json.loads(p.encode_event(_event()))
        raw["exec"] = "rm -rf /"
        with pytest.raises(p.HandoffValidationError):
            p.decode_event(json.dumps(raw))

    def test_missing_required_key_is_rejected(self) -> None:
        raw = json.loads(p.encode_event(_event()))
        del raw["event_id"]
        with pytest.raises(p.HandoffValidationError):
            p.decode_event(json.dumps(raw))

    def test_event_and_task_versions_must_agree(self) -> None:
        raw = json.loads(p.encode_event(_event()))
        raw["task"]["version"] = "v0"
        with pytest.raises(p.HandoffVersionError):
            p.decode_event(json.dumps(raw))

    def test_decoded_absolute_path_is_still_rejected(self) -> None:
        raw = json.loads(p.encode_event(_event()))
        raw["task"]["project"]["folder"] = "/etc/passwd"
        with pytest.raises(p.HandoffValidationError):
            p.decode_event(json.dumps(raw))

    def test_package_exports_public_names(self) -> None:
        from claude_code_core import handoffs

        for name in (
            "HandoffTask",
            "HandoffEvent",
            "HandoffEventKind",
            "HandoffCapability",
            "AuthorityScope",
            "ProjectLocator",
            "ConversationCoordinate",
            "HandoffProtocolError",
            "encode_event",
            "decode_event",
        ):
            assert hasattr(handoffs, name), name
