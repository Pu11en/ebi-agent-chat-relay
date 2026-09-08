# WSL-first desktop boundary

- Keep real development projects under `/home/drewp/main-projects`. Do not create or relocate project repositories under `/mnt/c` unless Drew explicitly requests Windows-backed storage. Keep the shared desktop `CODEX_HOME` at `/mnt/c/Users/drewp/.codex`.
- When Codex receives a Windows drive path such as `C:\Users\...`, translate it to the corresponding WSL path such as `/mnt/c/Users/...` before using shell, image, or file tools. Translate WSL UNC paths such as `\\wsl.localhost\Ubuntu\home\...` to `/home/...`.
- Treat `codex-clipboard-*` files and other files under Windows `Temp` as volatile. Immediately copy them into a stable project input or asset directory, verify the copied file, and use only the stable copy afterward.
- Persist every generated image or media result to a stable project path and verify that file before handoff. Show the verified local file directly; do not rely solely on the generated-image card or a temporary asset pointer.
- If a desktop preview or attachment fails but the stable file verifies, report the Codex Desktop rendering failure separately from the project result. Do not move the project or regenerate valid media merely to work around the preview.

# Codex Desktop visualization boundary

- In a Windows Codex Desktop task whose agent environment is WSL, do not emit file-backed inline HTML visualization references. In the current cross-environment bridge, both WSL and Windows path spellings fail at the desktop read boundary with `Invalid visualization read request` before the HTML is opened.
- Preserve a verified HTML source instead of repeatedly renaming or rewriting it. Use Mermaid for a static in-task visual, or show the HTML through a browser/Site when interaction is required.
- For a true inline interactive visualization, use a supported normal ChatGPT chat and explicitly select `@Visualize` under Plugins. The `@Visualize` suggestion is the availability check; do not treat a local Codex WSL task as an acceptance test for that surface.

# Planning workflow

- Portable Planner is retired at Drew's request. Use the workflow Drew selects for the current feature; until its replacement is installed, plan directly through normal Codex conversation. Do not reactivate Portable Planner from old plan files or handoff prompts.
- Preserve existing plans as reference material, including confirmed requirements, decisions, and approved scope. Switching workflows does not authorize deleting or migrating those files.
- Treat a new feature conversation as a separate scope within its project. Identify the feature before resuming or updating a saved plan; do not assume the project's previous plan is the new feature's plan.
- Keep narrow facts, status updates, explanations, diagnosis-only work, and sufficiently specified build requests direct.
