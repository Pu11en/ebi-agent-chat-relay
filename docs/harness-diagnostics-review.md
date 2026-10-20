# Harness diagnostic corrections

## Behavior

The health endpoint previously returned `status: ok` and zero overdue notifications
when reading the notification database raised an exception. It now reports
`status: degraded` and `overdue_notifications: null` for that unknown state. HTTP 200
is retained for process liveness, and the normal healthy/backlogged cases are unchanged.
Clients must allow a null backlog count when the store cannot be inspected.

The configuration audit rejected ordinary skill names containing `sk-` inside a word,
such as `task-coordination-strategies`. Credential-shape matching now requires a word
boundary before that API-key prefix. Actual standalone credentials remain detected,
and the fail-closed serialization gate remains enabled.

Audit discovery now resolves symlinks in parent directories, not only a symlink at
the final file. This prevents shared skill directories from being classified as
independent copies and rejects directory aliases that resolve outside approved roots
before reading the target. Cleanup decisions must verify canonical file identity;
equal content at two paths alone does not establish two independently stored files.

## macOS verification

Several tests assumed Linux filesystem and service behavior. Temporary vault paths
are now compared canonically; autocomplete uses paths within Discord's value limit
and checks that a project-name match ranks before any fuzzy parent-path match. The
transcript privacy assertion checks the actual private prompt marker instead of
rejecting macOS's `/private` directory name. Linux deployment-script and real systemd
executable checks are explicitly Linux-only and still run there.

The existing `make live-check` script targets a Linux/systemd installation. It is not
a macOS deployment acceptance test. Read-only API, process and database checks can
establish current health, but a source verification result does not prove that an
installed relay is running the new code or that model response latency improved.

## Test evidence

Regression tests first reproduced false health, harmless-name rejection, incorrect
directory-alias identity and inspection through an outside-root directory alias.
The corrected tests retain credential rejection and approved-root enforcement.

Final `make verify` passed: 6,133 tests passed, 12 Linux-only tests skipped, and
19 warnings; format, lint and pyright passed. Targeted security lint and public
imports also passed. Warnings include asynchronous mock cleanup, duplicate ZIP
fixture data and a discord.py deprecation. No release, deployment or running-service
restart is implied by this change.
