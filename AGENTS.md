# AGENTS.md

## Goal
Help me ship this trading platform safely with small, correct changes and clear explanations.

## Non-negotiables
- Don’t refactor unrelated code.
- Core/Ports/Adapters: **core must not import adapters**.
- Default to **paper trading**. Any live-trading capability must be behind an explicit config flag + risk limits + kill switch + documentation.
- Don’t guess silently: state assumptions and add TODOs if needed.
- Every change must include verification steps (tests/commands or manual steps).

## Required response format (do not use this if I am just having a discussion and it is obvious it is not needed)
1) What you (codex) did (plain English)
2) Why
3) How to verify (commands/steps)
4) Files changed/created
5) Confidence score (0.0–1.0) + biggest unknown

## Default problem-solving workflow
For non-trivial tasks use:
1. **DECOMPOSE** into sub-problems
2. **SOLVE** each with confidence (0.0–1.0)
3. **VERIFY** logic, facts, safety, edge cases
4. **SYNTHESIZE** into final plan using weighted confidence
5. **REFLECT**: if overall confidence < 0.8, identify weakness and retry

For simple questions, answer directly.

## “First principles + best practices” mode
When I ask: “I need to solve [TASK]. Research best practices and break it down from first principles.”
Return:
- First principles (goal, constraints, invariants)
- Best practices + common failure modes
- Minimal implementation plan (then optional upgrades)
- Verification plan
- Confidence (0.0–1.0)

## “Prompt engineer” mode
When I ask: “Generate a prompt based on the information you gave.”
Output a copy-paste prompt that includes:
- Context (repo + Core/Ports/Adapters + safety defaults)
- Task definition + constraints
- Required outputs (files, patch/pseudocode, verification)
- The workflow steps above

## Git shortcut
When I say `acp: <commit message>`, do:
1. `git add -A`
2. `git commit -m "<commit message>"`
3. `git push origin HEAD`
If any step fails, stop and report the error.
Never use `--amend` unless I explicitly ask.
