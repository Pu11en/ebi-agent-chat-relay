# Goal elicitation: turning a vague wish into a clear, checkable goal

*Research notes, 14 Sep 2026. Primary sources only where possible; each claim links its source. Anything I could not verify is marked "not verified".*

## Plain summary for Drew

- The best tools don't ask "what do you want?" They ask a few small questions, each with lettered picks and a suggested answer, and write your answers down as they go.
- They cap the questions (about 3 to 5) and ask them one at a time, so the chat doesn't turn into homework.
- Showing you a draft to react to works better than asking you to describe it from nothing. Research found people liked being asked more than writing instructions, and it surfaced things they hadn't thought of.
- Every good goal ends with a "done test": one plain sentence the bot can prove true, like "the page loads and shows your recordings".
- For goal mode: the bot reads your notes first, guesses your goal, asks you up to 5 lettered questions, then shows you one short goal and one done test for a yes.

---

## 1. Coding agents: spec-first and plan-first flows

### Claude Code `/goal` (exists, verified)
- **How it works:** you type `/goal <condition>`. After every turn a small, fast model checks whether the condition holds. If not, Claude takes another turn. It stops when the condition is met, judged impossible, or an error you have to fix happens. ([docs](https://code.claude.com/docs/en/goal))
- The checker can't run commands or read files. It only sees what Claude has shown in the chat, so the condition has to be something Claude's own output can prove. ([docs](https://code.claude.com/docs/en/goal))
- A good condition has "one measurable end state", "a stated check" (e.g. "`npm test` exits 0"), and "constraints that matter" (what must not change). You can add "or stop after 20 turns". ([docs](https://code.claude.com/docs/en/goal))
- **Good for non-technical users:** the done test is written once, and then the work carries on by itself.
- **Failure modes:** `/goal` doesn't help you *find* the goal; it assumes you already have one. A vague condition ("make it nice") can't be checked, so it runs forever or ends early. The checker can be fooled by claims in the chat that were never actually run.

### Claude Code plan mode + AskUserQuestion / "interview me"
- In plan mode, Claude researches first and then uses the AskUserQuestion tool to ask multiple-choice questions, with a recommendation and a note under each option, before it writes the plan. ([tool description, extracted system prompt](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-askuserquestion.md); secondary: [ClaudeLog](https://claudelog.com/faqs/what-is-ask-user-question-tool-in-claude-code/))
- Thariq's (Claude Code team) "interview" command, verbatim: "Read this plan file $1 and interview me in detail using the AskUserQuestionTool about literally anything … make sure the questions are not obvious … continue interviewing me continually until it's complete, then write the spec to the file." ([gist](https://gist.github.com/robzolkos/40b70ed2dd045603149c6b3eed4649ad))
- **Good:** it reads what already exists before asking. Multiple choice is easier than open questions.
- **Failure modes:** "until it's complete" has no upper limit, so the interview can run to dozens of questions. The popup is a button UI, which Drew's Discord can't use; the options have to be typed as letters instead.

### OpenAI Codex plan mode
- Plan mode lets Codex "gather context, ask clarifying questions, and build a stronger plan before implementation". ([Codex CLI features](https://developers.openai.com/codex/cli/features))
- The option-picker question tool (`request_user_input`) only works in Plan mode by default, and users have filed requests to get it in normal mode as well. ([issue #11266](https://github.com/openai/codex/issues/11266), [issue #11536](https://github.com/openai/codex/issues/11536))
- The "up to 4 rounds" claim comes from a third-party knowledge base, **not verified** in OpenAI docs ([codex.danielvaughan.com](https://codex.danielvaughan.com/2026/03/27/planning-mode-in-practice/)).
- **Failure mode:** in the CLI, it isn't obvious that the agent is waiting for your answer. ([issue #13478](https://github.com/openai/codex/issues/13478))

### Devin: interactive planning + confidence
- Devin states its confidence (green/yellow/red) "at the start of the session, after creating a plan, whenever answering a question about the code." ([Cognition, Devin 2.1](https://cognition.com/blog/devin-2-1))
- "When Devin doesn't have 🟢 confidence, it will ask clarifying questions". It waits for approval only when it's unsure. Otherwise it goes ahead and takes feedback later. ([same](https://cognition.com/blog/devin-2-1))
- Green scores gave "twice the likelihood of a merged PR compared to 🔴." ([same](https://cognition.com/blog/devin-2-1))
- **Good idea to borrow:** ask *only when unsure*, and say how sure you are in plain words.
- **Failure mode:** the model rates its own confidence, and research shows models are often overconfident about ambiguity (see CLAMBER below).

### Kiro spec-driven development (EARS)
- Three phases: requirements (or bug analysis), then design, then tasks. It produces `requirements.md`, `design.md` and `tasks.md`. ([Kiro specs](https://kiro.dev/docs/specs/))
- Requirements use EARS: "WHEN [condition/event] THE SYSTEM SHALL [expected behavior]". Each one "can be directly converted into test cases". ([Feature specs](https://kiro.dev/docs/specs/feature-specs/))
- You review and approve between phases. "Quick Spec" skips those checks: you answer clarifying questions up front and go straight to tasks. ([same](https://kiro.dev/docs/specs/feature-specs/))
- **Good:** the WHEN/SHALL shape forces a testable sentence.
- **Failure modes:** EARS reads like legal text to a non-technical person. Three documents to approve is too much reading for one small goal.

### GitHub Spec Kit (`/speckit.specify`, `/speckit.clarify`)
- Flow: constitution, then specify, then (optionally) clarify, then plan, then tasks, then implement. Clarify is "recommended before `/speckit.plan`" and was formerly called `/quizme`. ([README](https://github.com/github/spec-kit))
- The `/clarify` template is the closest match to what Drew wants ([clarify.md](https://raw.githubusercontent.com/github/spec-kit/main/templates/commands/clarify.md)):
  - "Maximum of 5 total questions across the whole session."
  - Asked "EXACTLY ONE at a time".
  - Each question has a "why it matters" line and up to 5 options, **with a recommended option**. The user answers with a letter, "yes"/"recommended", or at most 5 words.
  - Questions are ranked by impact × uncertainty. Trivial style questions are skipped. Plain language only.
  - Each answer is written into the spec at once as `Q: … → A: …`.
- **Failure modes:** it assumes a spec already exists, so the "what is this even for" step happens in `/specify`, not in `/clarify`.

### OpenSpec
- Explore, then propose, then apply, then archive. `/opsx:explore` is a thinking-partner mode that comes before anything is written. `/opsx:propose` writes `proposal.md`, `specs/` (requirements with scenarios), `design.md` and `tasks.md`. ([README](https://github.com/Fission-AI/OpenSpec))
- Philosophy: "fluid not rigid … no rigid phase gates." ([same](https://github.com/Fission-AI/OpenSpec))
- **Good:** a separate "just think with me" step before any commitment. That fits a user who doesn't know the goal yet.
- **Failure mode:** there are no gates, so a fuzzy proposal can go straight to building.

### ralph (snarktank) PRD skill + loop
- The PRD skill asks "3-5 essential clarifying questions" with lettered options (A, B, C, D). The user replies like "1A 2C 3B". Topics: the problem/goal, core function, scope, and success criteria. ([SKILL.md](https://raw.githubusercontent.com/snarktank/ralph/main/skills/prd/SKILL.md))
- "Acceptance criteria must be verifiable, not vague. 'Works correctly' is bad." For UI changes it adds a browser check. ([same](https://raw.githubusercontent.com/snarktank/ralph/main/skills/prd/SKILL.md))
- The loop picks the highest-priority story still marked `passes: false`, builds it, runs checks, sets `passes: true`, and stops when every story passes. Each item must fit in one context window. ([README](https://github.com/snarktank/ralph))
- **Good:** batch-answering with letters is very fast, and the done test drives the build loop directly. This is the closest existing match to "goal, then repeated gowork builds".
- **Failure modes:** it asks all its questions at once (a batch, not one at a time), which is too much for Drew in one message. It also assumes the user can name the feature.

### BMAD method (analyst / PM / brainstorming)
- Skills in the repo include `bmad-agent-analyst` ("Mary … translating vague needs into actionable specs"), `bmad-brainstorming`, `bmad-forge-idea`, `bmad-product-brief`, `bmad-prfaq` and `bmad-prd`. ([repo skills/](https://github.com/bmad-code-org/BMAD-METHOD))
- Brainstorming offers three stances: **Facilitator** ("you never supply ideas"), **Creative Partner**, or **Ideate for me**. ([bmad-brainstorming SKILL.md](https://github.com/bmad-code-org/BMAD-METHOD/blob/main/skills/bmad-brainstorming/SKILL.md))
- Forge-idea: "Ask one question at a time, press on weak points, and do not let vague claims pass". Rejecting the idea counts as a complete outcome. ([bmad-forge-idea](https://github.com/bmad-code-org/BMAD-METHOD/blob/main/skills/bmad-forge-idea/SKILL.md))
- PRFAQ runs Amazon's Working Backwards as a "relentless but constructive" coach, but "when users are stuck, offer concrete suggestions". ([bmad-prfaq](https://github.com/bmad-code-org/BMAD-METHOD/blob/main/skills/bmad-prfaq/SKILL.md))
- **Good:** "when stuck, offer suggestions" fits someone who doesn't know what he wants.
- **Failure modes:** many roles and documents, which is heavy. "Hardcore" challenging can feel like an interrogation.

### Others
- **Cursor plan mode:** it researches the codebase, asks clarifying questions, then shows an editable to-do plan before building. ([Cursor docs](https://cursor.com/docs/agent/plan-mode), [blog](https://cursor.com/blog/plan-mode)). The "34% fewer errors" figure comes from a third-party blog and is **not verified**.
- **Aider:** "ask" mode discusses and "never make[s] changes". The suggested workflow is to agree in ask mode, then build in code mode. Architect mode splits the work into a planner model and an editor model. ([Aider modes](https://aider.chat/docs/usage/modes.html))

---

## 2. Research on LLMs asking clarifying questions

- **GATE: Eliciting Human Preferences with LMs** (Li, Tamkin, Goodman, Andreas). The model asks open questions and **makes up edge cases for the user to react to**. That drew out more information than user-written prompts or labels, took *less* effort, and surfaced considerations users hadn't thought of. ([arXiv 2310.11589](https://arxiv.org/abs/2310.11589))
- **STaR-GATE** (Andukuri, Fränken, Gerstenberg, Goodman). They trained a model to ask better questions using simulated users. After two rounds, its answers were preferred on 72% of tasks. ([arXiv 2403.19154](https://arxiv.org/abs/2403.19154))
- **Clarify When Necessary** (Zhang & Choi, Findings NAACL 2025). Splits the job into three parts: *when* to ask, *what* to ask, and how to use the answer. "Intent-sim" asks only when the model's guesses about what the user means disagree. ([arXiv 2311.09469](https://arxiv.org/abs/2311.09469))
- **CLAMBER** (Zhang et al., ACL 2024). About 12K examples. LLMs are weak at spotting ambiguity and asking good questions. Chain-of-thought and few-shot prompting can make them *overconfident* for only small gains. ([ACL Anthology](https://aclanthology.org/2024.acl-long.578/))
- **Ambig-SWE** (ICLR 2026). Coding agents struggle to tell clear tasks from underspecified ones, but when they do ask, results improve by up to 74%. ([arXiv 2502.13069](https://arxiv.org/abs/2502.13069))
- **ClariQ / ConvAI3** (Aliannejadi et al., 2020). A benchmark for ranking and choosing clarifying questions in conversation. It notes that clarifying is harder in "limited bandwidth" chat. ([arXiv 2009.11352](https://arxiv.org/abs/2009.11352), [repo](https://github.com/aliannejadi/ClariQ))
- **What this means:** don't trust the model to notice on its own that the goal is fuzzy. Make the interview a forced step. Show concrete examples to react to. Ask only questions whose answer would change the goal.

---

## 3. Coaching and product techniques

- **Jobs To Be Done switch interview** (Moesta & Spiek). Rebuild the timeline of a real decision. The four forces are push (frustration now), pull (the appeal of the new), anxiety (worry about the new), and habit (the pull of the current way). ([jobstobedone.org](https://jobstobedone.org/), [four forces](https://jobstobedone.org/the-four-forces/))
  - *For Drew:* "What annoyed you last time?" is easier to answer than "what do you want?" Failure mode: it's long, and built for customer research rather than a 5-question chat.
- **5 Whys** (Toyota). Ohno: "by repeating why five times the nature of the problem as well as its solution becomes clear." ([Lean Enterprise Institute](https://www.lean.org/the-lean-post/articles/five-whys-animation/)). Failure mode: asking "why?" over and over feels like an interrogation. Use one or two "what would that let you do?" questions instead.
- **GROW** (Whitmore, *Coaching for Performance*): Goal, Reality, Options, Will. ([Performance Consultants](https://www.performanceconsultants.com/resources/the-grow-model/)). This maps neatly onto: goal, what's true now (read the notes), options (lettered picks), and commitment (approve).
- **Socratic questioning** (Paul & Elder): questions that target clarity, assumptions, evidence and consequences. ([Thinker's Guide PDF](https://www.criticalthinking.org/files/SocraticQuestioning2006.pdf)). Failure mode: open-ended questions put the burden on the user.
- **Amazon Working Backwards** (Vogels, 2006): write the press release first, then the FAQ, then the customer experience, then the user manual. The press release "describes in a simple way what the product does and why it exists." ([All Things Distributed](https://www.allthingsdistributed.com/2006/11/working_backwards.html)). *For Drew:* a one-line "announcement" is an easy draft to react to.
- **SMART** (Doran, *Management Review* 1981): Specific, Measurable, Assignable, Realistic, Time-related. Doran said not every goal needs all five. ([citation](https://www.scirp.org/reference/ReferencesPapers?ReferenceID=1459599); original PDF not fetched)
- **Definition of Done** (Scrum Guide): "a formal description of the state of the Increment when it meets the quality measures". Work that doesn't meet it goes back to the backlog. ([Scrum Guide](https://scrumguides.org/scrum-guide.html))

---

## 4. UX patterns that work (and where they come from)

- **One question at a time, hard cap:** Spec Kit's clarify step (at most 5, one at a time). ([clarify.md](https://raw.githubusercontent.com/github/spec-kit/main/templates/commands/clarify.md))
- **Lettered multiple choice, typed answers:** ralph ("1A 2C"). Spec Kit accepts a letter or "yes". ([ralph](https://raw.githubusercontent.com/snarktank/ralph/main/skills/prd/SKILL.md), [Spec Kit](https://raw.githubusercontent.com/github/spec-kit/main/templates/commands/clarify.md))
- **Recommend a default, first:** Spec Kit's recommended option. AskUserQuestion puts its recommendation at the top.
- **Show a draft or an example to react to:** GATE's edge cases. Working Backwards' press release. Cursor's editable plan.
- **Always allow "something else":** Spec Kit allows short free-text answers. BMAD offers suggestions when the user is stuck.
- **Write answers down at once:** Spec Kit records each `Q → A` in the file as it goes, so an interrupted chat loses nothing.
- **Failure modes across all of these:** too many questions; jargon in the options; options that are too alike to choose between; asking what the files already answer; a "done" line that can't actually be checked.

---

## 5. Recommended flow: `/gowork` goal mode (Discord, typed replies)

**Step 0: read before asking (silent).** Read the project's plan files, `progress.md` / findings, and recent commits. Draft 2–3 guesses at what Drew probably wants next. Mark any question the files already answer as settled. *(Thariq interview, Devin, Clarify-When-Necessary)*

**Step 1: open with a guess, not a blank.** One message, at most 5 sentences: "Here's where the project is. I think the next goal is X." Then lettered picks: A = the recommended guess, B/C = the other guesses, D = "something bugged me recently" (a JTBD push), E = "none of these, tell me in a few words". *(Working Backwards draft, GATE, BMAD "offer suggestions when stuck")*

**Step 2: narrow it, one question per message, at most 5 in total.** Only ask what would change the goal, most important first: who or what it's for, what's in and out of scope, what "good enough" looks like, and what must not break. Each message has a one-line reason, 4–5 lettered options with the recommended one first, and accepts a letter, "yes", or a few words. Skip any question the files already answered. *(Spec Kit clarify, ralph, GROW)*

**Step 3: record each answer at once** into `goal.md` as `Q → A`, so an interrupted chat can pick up where it stopped. *(Spec Kit)*

**Step 4: show the draft goal for a yes.** Two lines, in plain English:
- **Goal:** one sentence (what, and for whom).
- **Done test:** one sentence Drew could check in 30 seconds, plus the machine check the bot will run (e.g. "page at localhost:8080 shows your last 3 recordings; `Check:` command exits 0"). The done test must be something the bot's own output can prove. *(Claude `/goal` guidance, ralph, Kiro WHEN/SHALL, Definition of Done)*

Then letters: A = approve, B = make the done test stricter, C = make the goal smaller, D = change the goal, E = start over.

**Step 5: lock it in and drive builds.** On approval, save the goal plus the done test as the plan's `Check:` line and a `## How to try it` section. Split the goal into small `- [ ]` tasks. Each gowork build runs the check, and the loop stops when it passes, when it's judged impossible, or after a turn limit. *(Claude `/goal`, ralph `passes` loop)*

**Guardrails.**
- If the bot is already confident (the files make the goal obvious), skip step 2 and go straight to step 4. *(Devin)*
- If Drew answers E twice, switch to "show me an example" mode and offer 3 short sample goals. *(GATE)*
- Never use jargon in the options. Never offer only two choices. Never put a done test in front of Drew that the bot can't check itself.
