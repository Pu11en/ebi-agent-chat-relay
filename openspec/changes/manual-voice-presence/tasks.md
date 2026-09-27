Check: npm --prefix extensions/voice_transcripts test
Try: node --test extensions/voice_transcripts/test/manual-presence.test.mjs

## 1. Manual voice presence

- [x] 1.1 Replace automatic joining with scoped owner join/leave commands, cancellation-safe lifecycle and truthful notices; verify RED then GREEN with offline lifecycle/command tests and the full voice suite, then make verify and a focused security review before a local commit.

## How to try it

After authorized activation, in Discord (no website):
1. Enter the recording room: DrewAI stays out.
2. Send `!voice join` in voice-transcripts: it joins and confirms.
3. Send `!voice leave`: it leaves and stays out while you remain in voice.

Next work is the Discord-bot audit, not more voice features. Drew chose a normal session here, requested grilling before deciding the project direction, then explicitly asked to push/save and finish voice first. Activation and feature-branch publication are completed; no merge, further voice work, or paid-agent test is implied. See activation.md.
