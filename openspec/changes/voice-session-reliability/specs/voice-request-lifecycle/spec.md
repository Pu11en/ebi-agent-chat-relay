## Purpose

Let Drew address a session by its spoken character name, see the complete request being collected, and safely control its submission without losing delayed sentences or starting duplicate agent jobs.

## Scope boundary

Restart recovery is explicitly outside this change: do not add a recovery screen, startup replay or automatic resubmission. Existing restart behavior is left alone; these draft requirements do not promise restoration across a process restart. The immediate repair concerns lost words during normal running. The previously selected controls remain later work, not prerequisites for that repair.

## ADDED Requirements

### Requirement: A spoken request includes all of its captured speech

The system SHALL submit a named request only after at least ten seconds of actual silence from its owner and after all captured speech belonging to that request has been processed. A recording chunk boundary or a delay in transcription MUST NOT be interpreted as a new request or as proof of silence. The complete recognized text SHALL be preserved in capture order; it MUST NOT be silently truncated or split into multiple agent turns merely to fit a display limit.

#### Scenario: The reported missing final sentence

- **WHEN** a first chunk starts at 16:35:49.802, lasts 30 seconds and finishes transcription at 16:36:33.363, and a following chunk starts at 16:36:21.590, lasts 2.740 seconds and finishes transcription at 16:36:37.046
- **THEN** the chunks belong to one request because their recorded gap is 1.788 seconds
- **AND** the system submits neither an incomplete request when the first transcript arrives nor a second separate request for the last sentence
- **AND** after both the real silence threshold and transcription completion, the submitted request contains the final sentence exactly once

#### Scenario: Speech is still being captured or processed

- **WHEN** the owner has continued speaking but the later words have not yet reached the draft
- **THEN** the earlier text remains an unsubmitted draft
- **AND** a pending or failed transcription is visible rather than silently treated as completed speech

#### Scenario: A later request follows a genuine pause

- **WHEN** the owner has completed one named request, remained silent for at least ten seconds, then starts another named request while earlier transcription is delayed
- **THEN** the two requests remain separate even if their transcripts arrive close together

#### Scenario: A long request exceeds a surface or submission limit

- **WHEN** a complete request exceeds what one display message or the receiving endpoint can currently accept
- **THEN** all of its text remains available
- **AND** it is either submitted as one complete request through supported handling or retained with an explicit limit error and correction controls
- **AND** a clipped preview is never passed off as the complete submitted instruction

### Requirement: A known destination has one quiet live draft in its session

The system SHALL show a visible listening confirmation and a growing transcript inside the addressed session, without spoken acknowledgment. Listening, Processing audio, Queued, accepted and delivery-failure states SHALL reflect observed progress rather than assumed success. Preview posts and edits MUST NOT invoke an agent. If a preview cannot be posted or updated, the request SHALL remain saved and the failure SHALL be reported through an available authorized fallback.

#### Scenario: Zoro is recognized

- **WHEN** the owner addresses a known Zoro session
- **THEN** a draft inside that session identifies Zoro and shows the recognized words as they become available
- **AND** it shows that audio is still being processed while relevant speech remains pending
- **AND** the preview itself starts no agent turn

#### Scenario: Feedback races with request completion

- **WHEN** posting the listening message is slow and the request becomes ready in the meantime
- **THEN** completion cannot erase the state needed to finish posting or updating the draft
- **AND** the result is neither a false failure notice nor a duplicate submission

### Requirement: Submitted requests wait visibly behind current work

The system SHALL admit completed voice requests to the destination session without interrupting its current job. Accepted queued requests SHALL start in their admission order, at most one active request per session. A request shown as Queued MUST actually be accepted for that queue; network submission alone MUST NOT be reported as completed agent work.

#### Scenario: Zoro is already working

- **WHEN** a complete voice request is accepted while Zoro's existing job is running
- **THEN** the draft shows Queued and the existing job is not interrupted
- **AND** the new request starts only after the earlier work finishes

#### Scenario: Several accepted requests are waiting

- **WHEN** two voice requests are accepted for the same busy session
- **THEN** they run once each in the order they were accepted
- **AND** a later request cannot overtake an earlier accepted request

### Requirement: The owner can edit or cancel an unsubmitted draft

The system SHALL provide Edit and Cancel controls on an unsubmitted draft, usable only by the configured owner. Opening Edit SHALL pause automatic submission. Save SHALL preserve the edited text as an unsent draft and SHALL NOT resume automatic submission; the owner MUST explicitly press Send before that saved draft can be submitted. Cancel SHALL prevent that draft from being submitted, including when a previously scheduled send or a late transcript completes afterward. Controls for unsubmitted drafts MUST NOT imply that an already accepted or running job can be edited or cancelled through the same operation.

#### Scenario: Editing crosses the silence deadline

- **WHEN** the owner opens Edit before submission and the ten-second silence threshold passes while the editor is open
- **THEN** neither the original nor a half-edited instruction is automatically submitted

#### Scenario: Saving is not sending

- **WHEN** the owner changes the text and presses Save
- **THEN** the edited text remains visible as an unsent draft with a separate Send action
- **AND** passage of the silence deadline, a scheduled callback or returning connectivity does not submit it
- **AND** only an explicit authorized Send action can submit the saved revision

#### Scenario: Cancellation races with late words

- **WHEN** the owner cancels an unsubmitted request and another transcript belonging to it arrives afterward
- **THEN** the cancelled request is not revived or submitted
- **AND** its late words are not attached to an unrelated session

#### Scenario: Another room participant clicks a control

- **WHEN** someone other than the configured owner clicks Edit or Cancel
- **THEN** the draft and its submission state remain unchanged

### Requirement: An unclear destination is retained without guessing

When a request has been identified as addressed to a session but its destination cannot be resolved safely, the system SHALL keep an unsent draft in the existing voice-transcript channel with its text and a Choose Session control. It MUST NOT choose the previous, most recent or similar-sounding session automatically. Choosing a destination SHALL place the draft in that session without that routing choice itself starting an agent. Ordinary unaddressed room speech MUST NOT become an instruction merely because it was transcribed.

#### Scenario: A request needs a destination

- **WHEN** an addressed request cannot be assigned safely to a live session
- **THEN** its words remain in the transcript channel as an unsent draft with Choose Session
- **AND** no session receives it as an instruction

#### Scenario: The owner chooses Zoro

- **WHEN** the owner chooses an allowed Zoro session for that draft
- **THEN** the draft becomes visible in Zoro's thread
- **AND** choosing Zoro alone does not start an agent

#### Scenario: Ordinary room conversation

- **WHEN** room speech contains no recognized addressing intent
- **THEN** it stays in the normal transcript and does not create agent work or a stream of unclear-name alerts

### Requirement: Failed delivery is retried only by the owner

On an instruction-delivery failure, the system SHALL preserve the complete request and present Retry, Edit and Cancel controls where that draft is displayed. It MUST NOT resend because time passed or connectivity returned. Retry SHALL be a deliberate owner action and SHALL reuse the identity of the same unchanged request. A lost response MUST be distinguished from proven rejection; uncertain acceptance SHALL be reconciled before an edit or resend could create another job. Speech-recognition retry policy is outside this instruction-delivery requirement.

#### Scenario: The network returns

- **WHEN** delivery failed and connectivity later returns without the owner pressing Retry
- **THEN** the request remains saved without another delivery attempt or agent invocation

#### Scenario: The owner retries

- **WHEN** the owner presses Retry for a saved failed request
- **THEN** the system attempts or reconciles that same request without requiring its speech to be repeated
- **AND** repeated clicks cannot produce multiple admitted jobs

#### Scenario: The server accepted the request but its response was lost

- **WHEN** Retry is pressed after a response was lost for an already accepted request
- **THEN** the existing receipt and queue state are recovered without admitting another job
- **AND** the display does not falsely claim that no instruction was received

### Requirement: Voice controls retain their authorization and routing boundaries

Only the configured owner SHALL cause session work through voice drafts or their controls. Existing known-name aliases SHALL continue to work. A later character name inside an already addressed request SHALL remain part of its text rather than silently redirecting it, and a completed request SHALL not leave an invisible forwarding window open for later unaddressed speech. Runtime/model-change and session-management commands outside normal dictated requests SHALL not gain new behavior through this change.

#### Scenario: A character is mentioned inside a request

- **WHEN** the owner addresses Zoro and later mentions Nami while continuing that same request
- **THEN** the request remains addressed to Zoro

#### Scenario: Speech after a completed request is not addressed

- **WHEN** a previous named request has been completed and later speech names no destination
- **THEN** the previous destination is not reused automatically

#### Scenario: Another speaker is transcribed

- **WHEN** someone other than the configured owner says a character name and an instruction
- **THEN** the ordinary transcript may record their speech but no agent request is created
