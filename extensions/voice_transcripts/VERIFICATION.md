# Verification — 2026-09-07

- Test-first cycles confirmed missing automatic/runtime/transport modules failed,
  then the corresponding implementations passed. A silence regression first failed
  on an unwanted blank segment, then passed after discarding empty recognition.
- `npm test`: 25 tests passed.
- Fresh `npm ci --omit=dev` with patched `tar` 7.5.22: passed.
- `npm audit --omit=dev`: zero vulnerabilities.
- Native Opus decoder and DAVE dependency discovery: passed.
- Local faster-whisper 1.2.1 with cached `base.en`: loaded offline; one-second
  silence yielded no transcript; a locally synthesized sentence yielded recognizable
  speech (with expected recognition errors in the name “Drew AI”).
- Python worker: Ruff check/format and Pyright with its actual `/usr/bin/python3`
  environment passed.
- Framework Ruff, format check, and Pyright: passed. Framework files are unchanged.
- Full framework suite: 2,854 passed, one failure in the unchanged Teams relay test
  `TestTheReceiverVerifiesBeforeEnqueueing.test_an_oversized_activity_is_dropped_not_retried`.
  It expects HTTP 200 and receives HTTP 400 with aiohttp 3.14.3. The failure reproduces
  independently. No Teams code, tests, or framework dependency files were changed.
- The optional broad Bandit-rule Ruff scan reports 21 existing framework findings
  (assertions, subprocess checks, and similar); these are outside this extension.
  Manual audit of the extension found no shell interpolation, hardcoded credentials,
  credentials passed to the recognition worker, or unscoped interaction controls.
- Systemd unit verification passed. The dedicated voice companion started with the
  existing bot identity and zero restarts; the existing ccdb chat PID stayed the same.
- Voice and text channels were read back from Discord. The output denies Everyone
  View Channel, with explicit owner and bot access. A room disclosure was posted.
- Live microphone acceptance is pending a person joining the designated room,
  speaking, and leaving. No actual conversation has been claimed as verified.

Deployment values and IDs are in the ignored `data/setup.json` receipt and
`data/voice.env`. The supervised unit is in ignored `data/` with a user-systemd
symlink. Private runtime transcripts stay in ignored `data/runtime/`.
