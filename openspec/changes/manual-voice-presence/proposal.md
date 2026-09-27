## Why

Drew wants the old voice companion to stop joining automatically and to be easy to remove. This is the final bounded change before returning to the Discord-bot audit; the separate Jester Voice project is explicitly out of scope.

## What Changes

- **BREAKING**: Owner presence alone no longer starts or rejoins voice.
- Owner commands `!voice join` and `!voice leave` work only in the configured voice-transcripts text channel.
- Leave revokes the current join request; owner departure and process restart also require another explicit join.
- Keep existing local transcription, disclosure, owner checks and pause control; no voice feature expansion.

## Capabilities

### New Capabilities

- `manual-voice-presence`: Explicit owner control over joining and leaving the existing recording room.

### Modified Capabilities

None.

## Impact

Optional voice companion only, based on user-tested 50ff65f. No changes to framework chat commands, slash command registry, dependencies, service units, stored transcripts, or other bots. Do not include the unactivated Jester/Goku naming patches. Local verification before any authorized activation; no GitHub publication.
