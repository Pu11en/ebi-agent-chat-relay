# Voice routing evidence

## What was checked

This is a targeted investigation of the supplied example, not an audit of every session or all voice behavior. No provider call, microphone recording, restart or production edit was performed during planning.

## The missing final sentence

Source: read-only query of the voice companion's local transcript database, limited to the two quoted segments, plus the service log for the matching seconds.

- First segment: `8623`, captured at `2026-09-27T16:35:49.802Z`, duration `30000 ms`, stored after transcription at `16:36:33.363Z`.
- Next segment: `8624`, captured at `2026-09-27T16:36:21.590Z`, duration `2740 ms`, stored at `16:36:37.046Z`.
- Recorded gap: `16:36:21.590 - (16:35:49.802 + 30s) = 1.788s`; these are capture-derived timings, not proof about every physical microphone pause.
- The final text was present: “They can go to our website and look at that section.”
- At `16:36:34Z`, the live log reports both a send and `Cannot read properties of null (reading 'parts')` from the transcript listener.
- At `16:36:37Z`, the final segment is successfully transcribed after the earlier send.

An offline replay of the deployed controller, with a fake clock, fake timers and fake delivery, produced a zero-millisecond send timer on the first transcript; the next sentence returned `ignored` and was absent from the single fake send. The first transcript's nominal silence deadline had already passed, but the controller had no knowledge of its pending continuation.

## Source-level observations

- `control/controller.mjs` uses `capturedAt + durationMs + silenceMs - now()` to arm a send. It learns about activity only through completed transcripts, not current capture or queued transcription work.
- `voice/job-queue.mjs` invokes the controller after each transcription is saved; pending jobs are not supplied to the routing decision.
- The controller clears its active request before awaiting delivery; an asynchronously posted listening message can then try to access the cleared request. The matching live error confirms this second race occurred in the example.
- The current silence tests model speech duration, but their helper advances the clock to speech end and supplies text immediately; their transcription-delay comments do not exercise this incident's real completion lag.
- The current UI posts and edits in the transcript channel, not the addressed thread; growing per-segment preview text and Edit/Cancel/Retry are proposed additions.
- The controller's send failure path reports an error after its request state has already been cleared; a normal transcript survives, but there is no retryable delivery draft.
- The relay's spoken endpoint returns `202` after scheduling an asynchronous delivery; that is not evidence that the agent has started or that work completed.
- The current spoken endpoint has a `4000`-character limit; long requests need an explicit retained-error path or compatible full-request support, never silent clipping.
- Existing bot-authored messages are ignored by normal chat handling except for the separate handoff path; the new preview format must be tested against both, not merely assumed safe.
- The README contains both the current ten-second rule and an obsolete ninety-second continuation paragraph.

## Deployment boundary

The voice worker runs from `drew-ai-voice-runtime`, separately from the Python bot. The inspected controller matched this repository at investigation time; a future try-out must recheck all changed voice files, not assume a bot restart updates the companion.

## Limits

- No historical audit beyond this incident was performed.
- No benchmark of recognition accuracy or guaranteed word-for-word live latency was established.
- An unfamiliar word without recognizable addressing intent cannot safely be treated as a command; detection boundaries need review before enabling unclear-name alerts.
- The source material remains local; no raw transcript collection is attached to this plan.

## Planning checks

- `npm --prefix extensions/voice_transcripts test`: 137 existing tests passed in about 11 seconds, offline.
- Running the raw Node test glob from the repository root initially failed the worker-contract test because that test resolves its worker relative to the package directory; the package command above supplies the correct working directory without changing code.
- `openspec validate voice-session-reliability --strict --json`: passed for the written proposal/specification.
- At the original planning check, only proposal/specs existed. The subsequent local-repair approval added a bounded design and task checklist; later control designs and implementation remain gated. Save-until-Send remains a preference, and restart recovery is excluded.

## Local repair verification — 2026-09-27

- Authorization: Drew selected A, fix the missing-words bug locally and test without restarting the live bot.
- RED: the incident test sent before the second transcript; active-capture and real-gap separation tests also failed. The slow-acknowledgment regression reproduced `Cannot read properties of null (reading 'parts')` before source edits. New store/transport contract assertions failed until their methods existed.
- GREEN: `npm --prefix extensions/voice_transcripts test` passed all 145 tests (original baseline 137), approximately 11 seconds. Seven tests in delayed-speech.test.mjs cover the original incident, live capture, real-gap separation, unaddressed later speech, unrelated future capture, acknowledgment timing and real SQLite/serial-queue integration. Store/transport tests verify speaker and recording isolation.
- The integration test uses the actual queue and temporary SQLite store with a manually delayed fake transcriber and fake relay. It confirms both transcript segments are saved and delivered in one request. It never contacts a provider or Discord.
- `make verify` passed: formatting, lint, zero pyright errors and 6,067 Python tests on Python 3.13.12; 19 existing-style mock/deprecation warnings were reported. The test phase took about 113 seconds.
- `node --check` passed for all four changed source modules; public Python imports and `git diff --check` passed.
- Focused security review: owner authorization remains before routing; pending metadata is scoped to speaker plus recording; SQL parameters are bound; no new secrets, subprocesses, permissions, dependencies, database migrations or API endpoints.
- Only four voice-source modules changed: controller, transport, store metadata lookup and runtime wiring. Existing shutdown flushing, recognition retry policy and delivery policy remain unchanged. This is not a guarantee of word recovery after permanent recognition failure, process restart or out-of-order recognition retries; those behaviors were not redesigned.
- No live service switch, restart, model call or publish was performed. The companion's separate runtime still needs a deliberately approved try-out before this is live.
