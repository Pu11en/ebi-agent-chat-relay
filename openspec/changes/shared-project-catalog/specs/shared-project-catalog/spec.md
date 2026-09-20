## Purpose

Provide one local, on-demand catalog of approved project folders that Discord and every supported
AI harness can resolve consistently without adding a folder inventory to every model prompt.

## ADDED Requirements

### Requirement: Approved roots are the catalog boundary
The system SHALL build each computer's local catalog from explicitly approved project roots and
SHALL include every readable direct child directory of those roots as a project. It MUST NOT treat
deeper descendants, files, or paths outside an approved root as catalog projects.

#### Scenario: Direct children are discovered
- **WHEN** an approved root contains two readable direct child directories and a regular file
- **THEN** the catalog lists the two directories and does not list the file

#### Scenario: Nested folders stay inside their project
- **WHEN** a discovered project contains nested directories
- **THEN** the nested directories are not emitted as separate catalog projects

#### Scenario: An approved root is unavailable
- **WHEN** a configured root is missing or unreadable
- **THEN** the catalog reports that root as unavailable without removing projects discovered from other roots

### Requirement: Project identities are stable and unambiguous
The system SHALL give every catalog project a stable identity that includes its owning computer
and distinguishes same-named projects from different approved roots. User-facing results MUST
show enough owner and location context to choose the intended project without guessing.

#### Scenario: Same project name exists in two roots
- **WHEN** two approved roots on one computer each contain a direct child with the same folder name
- **THEN** both projects remain selectable with distinct identities and distinguishable locations

#### Scenario: The folder remains at the same location
- **WHEN** the catalog is queried repeatedly and the project's owner, root, and relative folder are unchanged
- **THEN** the project identity remains unchanged

### Requirement: Discovery reflects current local availability
The system SHALL scan or refresh local project availability when the catalog is queried and SHALL
not require a manual registration step for a newly created or removed direct child directory.

#### Scenario: A project is added
- **WHEN** a new direct child directory is created under an approved root
- **THEN** the next refreshed query includes it without a configuration edit

#### Scenario: A project is removed
- **WHEN** a previously discovered direct child directory no longer exists
- **THEN** the next refreshed query marks it unavailable or omits it from available results without deleting its user metadata

### Requirement: Favorites and hidden state are presentation metadata
The system SHALL support personal Favorite and Hide metadata for catalog projects. Neither state
MUST create, delete, or redefine whether a project exists, and an explicitly searched or resolved
hidden project MUST remain discoverable with its hidden state shown.

#### Scenario: A project is favorited
- **WHEN** an authorized user marks a catalog project as a favorite
- **THEN** it is prioritized for that user while retaining the same catalog identity and path

#### Scenario: A project is hidden
- **WHEN** an authorized user hides a catalog project
- **THEN** it is omitted from that user's ordinary browse results but remains available to explicit search or identity resolution

#### Scenario: A missing project returns
- **WHEN** a favorited or hidden project becomes unavailable and later returns at the same catalog location
- **THEN** its prior personal metadata is applied to the returned project

### Requirement: Discord and local harnesses share one resolution contract
The system SHALL expose the same local catalog identities and resolution results to New session
and to local Claude, Codex, and DSH sessions. A resolved local project MUST produce the canonical
local path that a new session or harness task binds as its working directory.

#### Scenario: Discord and a harness resolve the same project
- **WHEN** New session and a local supported harness resolve the same catalog identity
- **THEN** both receive the same owner, availability, and canonical local path

#### Scenario: A project starts a Discord session
- **WHEN** an authorized user starts a session from an available catalog project
- **THEN** the new thread is bound to that project's canonical local path

#### Scenario: A resolved project becomes unavailable
- **WHEN** the selected project cannot be revalidated immediately before session creation
- **THEN** no thread is bound to the stale path and the user is asked to refresh or choose another project

### Requirement: Catalog data is loaded only when relevant
The system MUST NOT inject the full project list, folder tree, or project contents into routine
session prompts. Harness access SHALL use an explicit, bounded catalog query, and content inside a
project SHALL only be inspected as part of an authorized task after that project is resolved.

#### Scenario: A normal prompt does not need a project lookup
- **WHEN** a session request does not ask to find, choose, or work in a project
- **THEN** the prompt contains no catalog listing or folder tree

#### Scenario: A harness searches the catalog
- **WHEN** a local harness explicitly searches for a project name
- **THEN** it receives only bounded matching metadata and no project file contents

### Requirement: Owner language controls local versus remote resolution
The system SHALL resolve explicit owner phrases against configured trusted computer owners. If the
requested owner is remote, the result MUST name the destination computer and MUST NOT return or
fabricate a local path. Ambiguous owner language MUST not be guessed across computers.

#### Scenario: David requests Drew's projects
- **WHEN** an agent on David's computer resolves a request for "Drew's projects"
- **THEN** the result targets Drew's trusted computer for handoff and contains no David-local project path

#### Scenario: David requests David's projects
- **WHEN** an agent on David's computer resolves a request for "David's projects"
- **THEN** the result is limited to David's local approved roots

#### Scenario: Cross-computer ownership is ambiguous
- **WHEN** an owner phrase cannot be mapped unambiguously to a trusted computer
- **THEN** the system requests an explicit owner instead of selecting a computer

### Requirement: Remote projects use the trusted handoff boundary
The system SHALL route remote project lookup and work through the trusted agent-handoff capability
and SHALL preserve the remote computer's own paths, availability, permissions, tools, and model
subscriptions. A local catalog MUST NOT claim remote state is locally verified.

#### Scenario: A remote project lookup succeeds
- **WHEN** a trusted remote computer answers a catalog lookup through a handoff
- **THEN** the caller receives owner-qualified project metadata with the remote verification time and no locally usable path

#### Scenario: The remote computer is offline
- **WHEN** the selected owner computer cannot answer
- **THEN** the request is visibly queued or reported unavailable according to the handoff state rather than falling back to a local same-named project

### Requirement: Machine profiles align behavior without erasing differences
The system SHALL allow DrewAI and iMac to share catalog behavior and owner aliases while keeping
their local roots, availability, and deliberate capability exceptions separate. Catalog-driven
choices MUST hide actions that the selected computer cannot support.

#### Scenario: Shared Drew profile uses different roots
- **WHEN** DrewAI and iMac use the same profile but configure different approved roots
- **THEN** each computer exposes only its own local projects under the shared behavior rules

#### Scenario: A computer lacks a required capability
- **WHEN** a catalog action requires a capability unavailable on the selected computer
- **THEN** that action is not offered and the catalog entry explains the availability constraint
