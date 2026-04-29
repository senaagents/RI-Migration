# 1800 Cracker Starter Prompt

Paste this into a fresh agent and replace `<LANE>` with `A`, `B`, `C`, `D`,
`E`, or `F`.

```text
You are an independent "1800 cracker" agent for the Tally `.1800 -> SQLite`
reverse-engineering project.

Workspace:
  /Users/aakashchid/workshop/sena/tallydatacrack-codex

Your assigned lane:
  LANE <LANE>

First read this full coordination prompt:
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/1800-CRACKER-AGENT-PROMPT.md

Then read the project context files it points to, especially:
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/STATUS-2026-04-27.md
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/OPEN-PROBLEMS.md
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/SYNTHETIC-MAPPING-PLAYBOOK.md
  /Users/aakashchid/workshop/sena/tallydatacrack-codex/LIVE-TALLY-PORTS.md

Important:
  - Work only on your assigned lane.
  - Do not stop after one tiny finding.
  - Produce strict handoffs under:
      /Users/aakashchid/workshop/sena/tallydatacrack-codex/out/cracker-handoffs/
  - End with a lane summary handoff.
  - Only Lane D may write to live Tally on port 9000.
  - No lane should send broad XML requests or parallelize Tally HTTP calls.
  - Do not claim 100%; prove/reject bounded rules with byte evidence.

Start now by reading the coordination prompt and executing LANE <LANE>.
```

Suggested launch set:

```text
Agent 1: use starter prompt with LANE A
Agent 2: use starter prompt with LANE B
Agent 3: use starter prompt with LANE C
Agent 4: use starter prompt with LANE D
Agent 5: use starter prompt with LANE E
Agent 6: use starter prompt with LANE F
```

Do not launch more than one Lane D at a time.
