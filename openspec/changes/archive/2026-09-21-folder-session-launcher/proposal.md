# Folder session launcher

## Why
Several bots expose identical slash commands. Drew selected favorite folders, new sessions, and resume as the primary shortcuts for himself and David.

## What Changes
A persistent panel labels the current bot/computer and opens personal favorite folders, creates folder-bound threads, or reopens existing threads. No model runs until someone sends a task. Existing slash commands remain compatible.

## Impact
Reusable framework Cog automatically wired by setup_bridge. Existing settings/session repositories store favorites and thread folders; no personal IDs or credentials in framework code.
