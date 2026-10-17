---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled — the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

## How to put the questions

If the runtime provides an interactive picker tool (Claude Code's `AskUserQuestion`, or any equivalent), **use it instead of the text format below**. Follow these rules:

- **2–4 options per question.** Put your recommended option **first** and mark its label `（推薦）` / `(Recommended)`. Each option's description must let a non-engineer judge it: what this option does, what it affects, and how it differs from the others.
- **Open-ended questions still go through the picker.** A question with no discrete answers (a name, a number, a trade-off) must be forced into 2–4 concrete candidates. The user can always fall back to the auto-provided "Other" free-text option — that fallback is theirs to reach for, not your excuse to skip the work of proposing candidates.
- **The tool caps at 4 questions per call.** When the frontier is larger, send multiple batches in the same round — **at most 2 batches (8 questions) per round**; anything beyond that gets pushed to the next round.
- **Recompute between batches.** After batch 1 is answered, drop questions its answers made moot and rewrite the ones its answers changed, then send batch 2.
- **Leave a text record before each picker.** Write one topic sentence for the round, then one line per question: `Q1 <title> ─ 推薦：<answer>`. The picker is the input; this is what stays readable in the transcript.

If the runtime has no picker tool (e.g. Codex CLI), format each question like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Each round the user answers reshapes the tree — settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), dispatch a sub-agent to find it — don't ask the user for anything you could look up yourself. Don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report — ask the rest of the frontier now. The _decisions_ are the user's — put each to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.
