## Purpose

Keep the existing Discord voice companion out of the room unless its owner explicitly requests it, and provide a quick way to make it leave.

## ADDED Requirements

### Requirement: Joining requires an owner command
The companion SHALL join the configured recording room only after its owner sends `!voice join` in the configured guild's voice-transcripts channel while present in the recording room. Presence updates, periodic checks, startup, and old Resume buttons MUST NOT initiate a join.

#### Scenario: Owner enters without a command
- **WHEN** the owner enters the room or the companion starts while the owner is present
- **THEN** the companion stays out of voice

#### Scenario: Explicit join
- **WHEN** the owner sends `!voice join` in the transcript channel while in the recording room
- **THEN** the companion posts the existing recording disclosure, joins once, and confirms in the transcript channel

#### Scenario: Unauthorized or misplaced command
- **WHEN** another user, bot, webhook, different guild, or different channel supplies the command
- **THEN** it does not start or stop voice

### Requirement: Leaving revokes the join request
The owner SHALL be able to send `!voice leave` from the transcript channel even when outside voice. It MUST disconnect promptly, cancel an in-progress join, and remain disconnected through later health checks. Owner departure, removal of the bot, or process restart SHALL require a fresh join command. Saved transcripts MUST remain intact.

#### Scenario: Leave while owner remains
- **WHEN** the owner sends `!voice leave` and remains in the room
- **THEN** the companion disconnects and does not automatically return

#### Scenario: Leave during a slow join
- **WHEN** a join is waiting on Discord and a leave command arrives
- **THEN** the pending connection is canceled rather than waiting for its full timeout or joining afterward

#### Scenario: Restart with a recorded active session
- **WHEN** a prior recording remains marked active after restart
- **THEN** it is marked stopped without reconnecting and existing transcript content is preserved

### Requirement: Existing recording protections remain
Recording SHALL still require owner presence and successful disclosure. Participants SHALL retain the ability to pause. A paused or disconnected companion MUST require a new owner join command to reconnect; this change MUST NOT enable unapproved spoken-command features.

#### Scenario: Disclosure fails
- **WHEN** a join request cannot post the recording notice
- **THEN** the companion does not connect or automatically retry joining
