Check: `npm --prefix extensions/voice_transcripts test`

Try: `npm --prefix extensions/voice_transcripts test`

## 1. Approved local missing-sentence repair

- [x] 1.1 Write failing offline tests for the reported delayed final sentence, active capture and separate real-pause requests; confirm failures before source edits.
- [x] 1.2 Expose scoped unfinished-job and active-capture metadata and use it to gate automatic sending; verify the regression and existing voice tests pass, including unrelated-speaker/session isolation.
- [x] 1.3 Verify the runtime wiring offline, run the full project quality gate, and save the fix locally without switching or restarting either service; record commands and results.

## 2. Later preferences — not authorized in this turn

- [ ] 2.1 Finalize and implement live in-session drafts with accurate queue receipts only after separate approval; check previews never invoke an agent and busy work is not interrupted.
- [ ] 2.2 Finalize and implement owner-only Edit, Save-until-Send and Cancel only after separate approval; check timers and late words never bypass owner actions.
- [ ] 2.3 Finalize and implement manual Retry and unclear-destination choice only after separate approval; check no duplicate admission, guessing or automatic resend.
- [ ] 2.4 Address long-request limits and update the complete workflow documentation when those controls exist; verify no silent clipping and documentation matches the implementation.
- [ ] 2.5 Prepare a separately approved Discord try-out; verify the exact runtime copy and active sessions before any owner-approved switch, with no automatic deployment.

## How to try it

For a later approved live try-out: say a character name and two sentences with a short pause; confirm both arrive in one request; after a genuine ten-second pause, address a different character and confirm the requests stay separate. These are not claims that the live service has changed.
