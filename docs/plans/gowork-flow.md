# /gowork: how it works, start to finish

The decisions made so far are marked ✅. The part still open is marked ❓.

```mermaid
flowchart TD
    A[You plan with the planner<br/>Opus / Fable] --> B{Plan ready}
    B -->|planner asks| C[🟢 Start the build? — Yes ✅]
    B -->|or you type| C2["/gowork → pick a plan button ✅"]
    C --> D[Pick harness → pick model ✅]
    C2 --> D
    D --> E[Worker thread opens next to the planner<br/>own copy of the project ✅]
    E --> F[Round: fresh session does ONE small task]
    F --> G{Bot checks: saved in git<br/>and box ticked?}
    G -->|yes| H[✅ posted quietly — no ping ✅]
    G -->|no, first time| F
    G -->|no, twice| S[🛑 Stuck — pings you ✅]
    F -->|needs a decision| Q[❓ yes/no buttons — pings you ✅]
    Q -->|you tap| F
    H --> I{More tasks?}
    I -->|yes| F
    I -->|no| Z[❓ THE ENDING — what you see when you come back]
    R[Bot restarts mid-build] -.->|resumes by itself ✅| F
```

## ❓ The ending, as your journey after the build

What you need at the end is to **see it working and test it without reading much**. A "merge?" question
doesn't give you that. The idea is a **Try-it card** at the top of the planner thread:

> **🧪 realpage QA fixes are ready to try** (12 of 12 tasks done)
> **Open it:** http://localhost:8765 ← a local copy is already running with the new work
> **Check these 3 things (30 seconds):**
> 1. Shrink the window to phone size: the table fits, with no sideways scrolling.
> 2. Click "Junction 15": the website link opens.
> 3. Ask the chatbot "which buildings use Yardi?": you get an answer with sources.
>
> [ ✅ Looks good, keep it ] [ ❌ Something's off ] [ 📋 What changed (short) ]

- **✅ Looks good:** the work is combined into the project. Going live (a push or deploy) is still its own yes/no.
- **❌ Something's off:** you type or say what's wrong in one line. That becomes a new small task, and the worker fixes it and gives you a fresh card.
- **Where the check list comes from:** the planner writes a "How to try it" section into every plan (the command that starts the local copy, the address, and the 3 things to check), so the card is written before the work even starts.
- **Projects with no website to open** (for example this bot): "Open it" becomes "try `/gowork` in #test-channel", with the same 3-check format.
