"""Reading "make me a session about Boa in the boa folder" out of plain speech."""

from __future__ import annotations

import json

from claude_discord.session_request import (
    SessionRequest,
    mentions_a_session,
    parse_verdict,
)

# --- the cheap gate --------------------------------------------------------


def test_an_ordinary_question_never_reaches_the_model():
    """Most of what is typed is a question; it must stay instant and free."""
    assert mentions_a_session("what does this repo do?") is False
    assert mentions_a_session("hey can you look at the aldus pricing") is False


def test_anything_naming_a_session_is_worth_a_look():
    assert mentions_a_session("make me a session about Boa in the boa folder") is True
    assert mentions_a_session("I need a Session inside realpage") is True
    assert mentions_a_session("open a thread for the slack biz work") is True


def test_the_gate_is_about_whole_words():
    """`obsession` is not a session; a substring check would call the model on it."""
    assert mentions_a_session("this is an obsession at this point") is False


# --- reading the verdict ---------------------------------------------------


def test_a_clean_yes_is_read():
    raw = json.dumps({"start": True, "folder": "boa", "task": "plan the launch"})
    assert parse_verdict(raw) == SessionRequest(folder="boa", task="plan the launch")


def test_a_no_is_a_no():
    assert parse_verdict(json.dumps({"start": False, "folder": "boa", "task": ""})) is None


def test_a_yes_with_no_folder_is_declined():
    """Without a folder there is nothing to bind, so a quick chat is the answer."""
    assert parse_verdict(json.dumps({"start": True, "folder": "", "task": "x"})) is None


def test_a_session_with_no_task_is_still_a_session():
    raw = json.dumps({"start": True, "folder": "boa", "task": ""})
    assert parse_verdict(raw) == SessionRequest(folder="boa", task="")


def test_chatter_around_the_json_is_tolerated():
    raw = 'Sure, here you go:\n```\n{"start": true, "folder": "boa", "task": "go"}\n```\n'
    assert parse_verdict(raw) == SessionRequest(folder="boa", task="go")


def test_output_that_is_not_json_is_declined_rather_than_raising():
    assert parse_verdict("I think they want a session in boa.") is None
    assert parse_verdict("") is None
    assert parse_verdict("{not json}") is None


def test_a_json_list_is_not_a_verdict():
    assert parse_verdict('["start", true]') is None
