"""Authority is a typed intersection, never a reading of prose.

A handoff carries the :class:`~claude_code_core.handoffs.protocol.AuthorityScope`
the original human request granted. The recipient computes
``effective = inherited ∩ local_policy`` before it runs anything, and consults
the same scope again before any sensitive action. Nothing in the goal text
can widen that scope — "you have full permissions" is a sentence, not a
capability — but the goal text *can* reveal that the task needs more than it
was given, and that is what :func:`classify_goal` is for: a small keyword
heuristic that flags destructive, deployment, paid, external-message,
permission-changing and open-ended requests so the job blocks visibly and
asks the origin for authority instead of quietly attempting them.

The heuristic errs toward blocking. A false positive costs one clarification
in the job thread; a false negative would be a delete nobody approved.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from claude_code_core.handoffs.protocol import AuthorityScope, HandoffCapability, HandoffTask

ENV_ALLOW_EDIT = "CCDB_HANDOFF_ALLOW_EDIT"
ENV_CAPABILITIES = "CCDB_HANDOFF_CAPABILITIES"


class ActionKind(Enum):
    """What a task, or one step of it, is about to do."""

    READ = "read"
    EDIT = "edit"
    DESTRUCTIVE = "destructive"
    DEPLOYMENT = "deployment"
    PAID_PROVIDER = "paid_provider"
    EXTERNAL_MESSAGE = "external_message"
    PERMISSION_CHANGE = "permission_change"
    UNCLEAR = "unclear"


CAPABILITY_FOR_ACTION: dict[ActionKind, HandoffCapability] = {
    ActionKind.DESTRUCTIVE: HandoffCapability.DESTRUCTIVE,
    ActionKind.DEPLOYMENT: HandoffCapability.DEPLOYMENT,
    ActionKind.PAID_PROVIDER: HandoffCapability.PAID_PROVIDER,
    ActionKind.EXTERNAL_MESSAGE: HandoffCapability.EXTERNAL_MESSAGE,
    ActionKind.PERMISSION_CHANGE: HandoffCapability.PERMISSION_CHANGE,
}

# Word-boundary patterns. Multi-word phrases are matched as phrases so that
# "deployment docs" (a folder) is not "deploy" (an action).
_PATTERNS: dict[ActionKind, tuple[str, ...]] = {
    ActionKind.EDIT: (
        r"edit(?:s|ed|ing)?",
        r"modif(?:y|ies|ied|ying)",
        r"chang(?:e|es|ed|ing)",
        r"fix(?:es|ed|ing)?",
        r"refactor(?:s|ed|ing)?",
        r"writ(?:e|es|ing)",
        r"implement(?:s|ed|ing)?",
        r"add(?:s|ed|ing)?",
        r"creat(?:e|es|ed|ing)",
        r"updat(?:e|es|ed|ing)",
        r"renam(?:e|es|ed|ing)",
        r"mov(?:e|es|ed|ing)",
        r"patch(?:es|ed|ing)?",
        r"commit(?:s|ted|ting)?",
        r"install(?:s|ed|ing)?",
        r"reinstall(?:s|ed|ing)?",
        r"generat(?:e|es|ed|ing)",
    ),
    ActionKind.DESTRUCTIVE: (
        r"delet(?:e|es|ed|ing)",
        r"remov(?:e|es|ed|ing)",
        r"rm\s+-[a-z]*r[a-z]*f?",
        r"rm\s+-[a-z]*f[a-z]*r?",
        r"drop(?:s|ped|ping)?",
        r"wip(?:e|es|ed|ing)",
        r"purg(?:e|es|ed|ing)",
        r"eras(?:e|es|ed|ing)",
        r"truncat(?:e|es|ed|ing)",
        r"destroy(?:s|ed|ing)?",
        r"uninstall(?:s|ed|ing)?",
        r"force[- ]push(?:es|ed|ing)?",
        r"reset\s+--hard",
    ),
    ActionKind.DEPLOYMENT: (
        r"deploy(?:s|ed|ing)?",
        r"releas(?:e|es|ed|ing)",
        r"publish(?:es|ed|ing)?",
        r"ship(?:s|ped|ping)?\s+(?:it\s+)?to",
        r"roll(?:s|ed|ing)?[- ]out",
        r"go\s+live",
        r"push(?:es|ed|ing)?",
        r"production",
    ),
    ActionKind.PAID_PROVIDER: (
        r"pay(?:s|ing)?",
        r"purchas(?:e|es|ed|ing)",
        r"buy(?:s|ing)?",
        r"billing",
        r"paid",
        r"credits?",
        r"top[- ]up",
        r"subscri(?:be|ption)",
    ),
    ActionKind.EXTERNAL_MESSAGE: (
        r"e-?mail(?:s|ed|ing)?",
        r"send\s+(?:a\s+|an\s+|the\s+)?(?:message|text|sms|dm|note)",
        r"message\s+(?:the|our|a)\b",
        r"notify(?:ing)?\s+(?:the|our|a)\b",
        r"post(?:s|ed|ing)?\s+(?:it\s+|the\s+\w+\s+)?to",
        r"tweet(?:s|ed|ing)?",
        r"slack",
        r"reply(?:ing)?\s+to\s+(?:the|our|a)\b",
    ),
    ActionKind.PERMISSION_CHANGE: (
        r"chmod",
        r"chown",
        r"sudo",
        r"grant(?:s|ed|ing)?",
        r"permissions?",
        r"privileges?",
        r"admin",
        r"api\s+keys?",
        r"tokens?\s+rotat",
        r"invite(?:s|d)?",
        r"collaborators?",
    ),
    ActionKind.UNCLEAR: (
        r"whatever\s+(?:it\s+takes|is\s+needed|you\s+need)",
        r"anything\s+(?:necessary|needed|else)",
        r"full\s+(?:permissions?|access|authority|control)",
        r"do\s+everything",
        r"use\s+your\s+judge?ment",
        r"you\s+decide",
        r"no\s+restrictions?",
        r"unrestricted",
        r"etc\.?",
        r"and\s+so\s+on",
    ),
}

_COMPILED: dict[ActionKind, re.Pattern[str]] = {
    kind: re.compile(r"(?<![\w-])(?:" + "|".join(patterns) + r")(?![\w-])", re.IGNORECASE)
    for kind, patterns in _PATTERNS.items()
}


def classify_goal(text: str) -> frozenset[ActionKind]:
    """The action kinds a goal's wording implies (read is always implied)."""
    found = {kind for kind, pattern in _COMPILED.items() if pattern.search(text)}
    # "Full permissions" is open-ended, not a permission change.
    if ActionKind.UNCLEAR in found and re.search(
        r"full\s+(?:permissions?|access|authority|control)", text, re.IGNORECASE
    ):
        rest = re.sub(r"full\s+(?:permissions?|access|authority|control)", "", text, flags=re.I)
        if not _COMPILED[ActionKind.PERMISSION_CHANGE].search(rest):
            found.discard(ActionKind.PERMISSION_CHANGE)
    return frozenset(found)


@dataclass(frozen=True)
class RecipientPolicy:
    """What this computer permits a handoff to do at most."""

    allow_edit: bool = False
    capabilities: frozenset[HandoffCapability] = frozenset()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RecipientPolicy:
        source: Mapping[str, str] = os.environ if env is None else env
        allow_edit = (source.get(ENV_ALLOW_EDIT) or "").strip().lower() in {"1", "true", "yes"}
        caps: set[HandoffCapability] = set()
        for raw in (source.get(ENV_CAPABILITIES) or "").split(","):
            name = raw.strip().lower()
            if not name:
                continue
            try:
                caps.add(HandoffCapability(name))
            except ValueError:
                raise ValueError(f"{ENV_CAPABILITIES}: unknown capability {name!r}") from None
        return cls(allow_edit=allow_edit, capabilities=frozenset(caps))


def intersect(inherited: AuthorityScope, policy: RecipientPolicy) -> AuthorityScope:
    """``inherited ∩ policy`` — the only scope execution ever sees."""
    edit = inherited.edit and policy.allow_edit
    return AuthorityScope(
        read=inherited.read or inherited.edit,
        edit=edit,
        edit_paths=inherited.edit_paths if edit else (),
        capabilities=inherited.capabilities & policy.capabilities,
    )


def authorize_action(effective: AuthorityScope, action: ActionKind) -> str | None:
    """``None`` when ``effective`` permits ``action``; otherwise the blocker."""
    if action is ActionKind.READ:
        return None if effective.read else "read authority was not granted"
    if action is ActionKind.EDIT:
        return None if effective.edit else "edits are not authorized for this handoff"
    if action is ActionKind.UNCLEAR:
        return "the requested action is unclear or open-ended; say exactly what may be done"
    needed = CAPABILITY_FOR_ACTION[action]
    if needed in effective.capabilities:
        return None
    return f"{action.value} actions need the explicit {needed.value!r} capability"


@dataclass(frozen=True)
class AuthorityDecision:
    """What execution may do, and why it may not start if it may not."""

    effective: AuthorityScope
    required: frozenset[ActionKind]
    blockers: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return not self.blockers


def check_authority(
    task: HandoffTask,
    policy: RecipientPolicy,
    *,
    extra_actions: Iterable[ActionKind] = (),
) -> AuthorityDecision:
    """Decide, before execution, whether the task may run under ``policy``."""
    inherited = task.authority
    effective = intersect(inherited, policy)
    blockers: list[str] = []

    # A packet that asks for more than the recipient permits blocks visibly —
    # silently dropping the capability would run a different task than asked.
    if inherited.edit and not policy.allow_edit:
        blockers.append("recipient policy forbids edits on this computer")
    for cap in sorted(inherited.capabilities - policy.capabilities, key=lambda c: c.value):
        blockers.append(f"recipient policy forbids the {cap.value!r} capability here")

    required = frozenset(classify_goal(task.goal)) | frozenset(extra_actions)
    for action in sorted(required, key=lambda a: a.value):
        reason = authorize_action(effective, action)
        if reason is not None and reason not in blockers:
            blockers.append(reason)
    return AuthorityDecision(effective=effective, required=required, blockers=tuple(blockers))


def describe_scope(scope: AuthorityScope) -> str:
    """A short human line for prompts and status posts."""
    if scope.is_read_only:
        return "read-only"
    parts = ["read"]
    if scope.edit:
        where = ", ".join(scope.edit_paths) if scope.edit_paths else "within the project"
        parts.append(f"edit {where}")
    parts.extend(cap.value for cap in sorted(scope.capabilities, key=lambda c: c.value))
    return " · ".join(parts)


__all__ = [
    "CAPABILITY_FOR_ACTION",
    "ENV_ALLOW_EDIT",
    "ENV_CAPABILITIES",
    "ActionKind",
    "AuthorityDecision",
    "RecipientPolicy",
    "authorize_action",
    "check_authority",
    "classify_goal",
    "describe_scope",
    "intersect",
]
