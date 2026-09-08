# Installed workflow sources

- OpenSpec CLI: `@fission-ai/openspec@1.12.0`, npm integrity
  `sha512-oFE2Lj7WVSc87nSibk6qe9HjHIOlxhcPAXbPey44DlLvJzBl5+9BZVrNiozOwv++CQhW+MG0kuP1XLZ/uQrrWw==`.
  [Official repository](https://github.com/Fission-AI/OpenSpec/tree/v1.12.0).
  Six Codex skills generated with `openspec init --tools codex`; originals are
  preserved in `.agents/skills/`. OpenSpec is MIT licensed.
- `parallel-feature-development` and `task-coordination-strategies`: installed
  through the Codex skill installer from
  [wshobson/agents commit a30778f](https://github.com/wshobson/agents/tree/a30778f8c4e6b0a87567941b7cca4f534bf642b6/plugins/agent-teams/skills).
  The complete selected skill directories and their references are installed;
  each has its upstream MIT LICENSE and a SOURCE.json file with file hashes.
  Related optional sibling skills were not selected; their hyperlinks are
  upstream suggestions, not required dependencies. Claude's Agent Teams runtime
  is not installed. Native task-tool examples are mapped by the local adapter.
- `lockin-feature-workflow`: local Ebi adapter in `skill/`, using the existing
  localhost control plane. The executable coordinator lives beside it and
  needs only Python's standard library. No bot core import or service restart.

Development source stays in WSL. Discoverable Codex skills are installed under
the shared desktop CODEX_HOME's `skills/`. Existing user authorization takes
precedence over workflow text that would otherwise request approval again.
