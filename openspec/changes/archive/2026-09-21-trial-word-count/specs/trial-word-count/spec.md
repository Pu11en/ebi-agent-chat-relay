## Purpose

Provide the disposable workflow trial with a predictable word count that callers can use without normalizing text or interpreting punctuation.

## ADDED Requirements

### Requirement: Public counting interface

The trial counter SHALL expose `count_words(text: str) -> int` to callers and return the integer token count for a string input.

#### Scenario: Count one token through the public interface

- **WHEN** a caller invokes `count_words("hello")`
- **THEN** the result is the integer `1`

### Requirement: Split only on whitespace

The counter SHALL count nonempty tokens separated by whitespace, including spaces, tabs, line breaks, and Unicode whitespace. Repeated, leading, and trailing whitespace SHALL NOT create additional tokens.

#### Scenario: Mixed and repeated whitespace separates three tokens

- **WHEN** the input is `"  alpha\t\nbeta\u00a0gamma\r\n "`
- **THEN** the count is `3`

### Requirement: Punctuation remains within tokens

The counter SHALL treat punctuation as part of its whitespace-delimited token. It SHALL NOT split tokens on punctuation or discard punctuation-only tokens.

#### Scenario: Punctuation does not change token boundaries

- **WHEN** the input is `"hello,world can't state-of-the-art !!!"`
- **THEN** the count is `4`

### Requirement: Empty input returns zero

The counter SHALL return `0` for an empty string.

#### Scenario: Empty string has no tokens

- **WHEN** the input is `""`
- **THEN** the count is `0`

### Requirement: Whitespace-only input returns zero

The counter SHALL return `0` for a string containing only whitespace.

#### Scenario: Whitespace without a token

- **WHEN** the input is `" \t\r\n\u00a0 "`
- **THEN** the count is `0`
