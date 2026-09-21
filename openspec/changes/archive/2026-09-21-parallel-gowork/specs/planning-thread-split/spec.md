## Purpose

Keeps large Discord planning conversations clear by moving clearly independent builds into linked
child planning threads while the original thread coordinates decisions, status, and integration.

## ADDED Requirements

### Requirement: Automatically separate clear independent builds
During planning, the system SHALL identify a proposed build as independent when it has a distinct
goal and can be planned and delivered without changing the active build's acceptance criteria or
requiring the same writable files. It SHALL create a child planning thread automatically for a
clear boundary and SHALL ask one focused question when independence is uncertain.

#### Scenario: Distinct build is clear
- **WHEN** the conversation introduces a separate goal with independent acceptance criteria and no required shared writable scope
- **THEN** a linked child planning thread is created without interrupting the active build plan

#### Scenario: Boundary is uncertain
- **WHEN** the proposed work may be either a required part of the active build or a separate deliverable
- **THEN** the system asks the user one focused split-or-keep question before creating a child

### Requirement: Honor explicit separation requests
The system SHALL treat an explicit user instruction such as “make this separate” as a request to
create a linked child planning thread, unless a hard technical dependency makes independent
planning unsafe; in that case it SHALL explain the dependency in the parent.

#### Scenario: User requests a separate build
- **WHEN** the user says to make an identified idea a separate build
- **THEN** the system creates a child planning thread for that idea and records its relationship to the parent

### Requirement: Send compact structured child context
A child planning thread SHALL receive a compact handoff containing the goal, project and computer,
locked decisions, dependencies, restrictions and authority, expected planning output, and parent
thread link. It MUST NOT copy the entire parent transcript merely to provide context.

#### Scenario: Child planner starts
- **WHEN** a child planning thread is created
- **THEN** its first briefing contains every required structured field and only the relevant decisions for that build

#### Scenario: Relevant decision changes
- **WHEN** the parent changes a locked decision that affects an unfinished child
- **THEN** the child receives a bounded update identifying the changed decision and its source

### Requirement: Keep parent and child ownership explicit
The parent thread SHALL remain the coordinator for overall status and cross-build integration. Each
child thread SHALL own exactly one build plan and SHALL link its current state and decisions back to
the parent without editing another child's plan.

#### Scenario: Several children are active
- **WHEN** two or more child planning threads are open
- **THEN** the parent shows each child link, build goal, planning or execution state, dependencies, and blocker

#### Scenario: Child completes planning
- **WHEN** a child finishes an implementation-ready plan
- **THEN** it reports its approved revision and next executable boundary to the parent once

### Requirement: Coordinate same-project work safely
Multiple child plans MAY target the same repository at the same time, but planning SHALL remain
read-only and execution SHALL not share a writable checkout. Before execution, the parent SHALL
represent dependencies and owned paths so overlapping work cannot run concurrently.

#### Scenario: Same project, independent files
- **WHEN** two children plan changes in the same repository with non-overlapping owned paths
- **THEN** both may finish planning and their builds may later run in separate worktrees

#### Scenario: Same project, overlapping files
- **WHEN** child plans require overlapping writable paths
- **THEN** the parent records an ordering dependency or a single file owner before either build is dispatched

### Requirement: Recover linked planning state
Parent-child relationships, compact handoffs, current state, and last acknowledged update SHALL be
durable across bot restarts. Recovery MUST NOT create a duplicate child for the same split request.

#### Scenario: Restart after child creation
- **WHEN** the bot restarts after recording child creation
- **THEN** the parent restores the existing child link and does not spawn a replacement thread
