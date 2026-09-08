## Purpose

Provide a predictable greeting for the disposable Lockin AI workflow trial, giving callers a stable name-formatting contract that can be verified independently.

## ADDED Requirements

Examples use `\t` for a tab and `\n` for a newline inside quoted strings.

### Requirement: Exact greeting format

The greeting capability SHALL expose `greet(name: str) -> str` and return exactly `Hello, <name>!`, substituting the name determined by the requirements below, with no added trailing newline.

#### Scenario: Greet a supplied name
- **WHEN** the caller invokes `greet("Drew")`
- **THEN** the returned string is exactly `"Hello, Drew!"`

### Requirement: Preserve name case

The greeting capability SHALL preserve the supplied name's letter case.

#### Scenario: Mixed-case name
- **WHEN** the supplied name is `"dReW"`
- **THEN** the returned string is exactly `"Hello, dReW!"`

### Requirement: Strip exterior whitespace

The greeting capability SHALL remove leading and trailing whitespace from the supplied name before formatting the greeting.

#### Scenario: Name surrounded by whitespace
- **WHEN** the supplied name is `" \tDrew\n "`
- **THEN** the returned string is exactly `"Hello, Drew!"`

### Requirement: Preserve interior whitespace

The greeting capability SHALL preserve whitespace between characters within the trimmed name, including repeated spaces and tabs.

#### Scenario: Name with repeated interior whitespace
- **WHEN** the supplied name is `"Mary  Jane\tWatson"`
- **THEN** the returned string is exactly `"Hello, Mary  Jane\tWatson!"`

### Requirement: Use friend for an empty trimmed name

The greeting capability SHALL use the lowercase name `friend` when removing exterior whitespace leaves an empty name.

#### Scenario: Empty input
- **WHEN** the supplied name is `""`
- **THEN** the returned string is exactly `"Hello, friend!"`

#### Scenario: Whitespace-only input
- **WHEN** the supplied name is `" \t\n "`
- **THEN** the returned string is exactly `"Hello, friend!"`
