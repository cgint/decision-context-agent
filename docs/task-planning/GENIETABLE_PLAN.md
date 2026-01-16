# Genie Round-Table Orchestrator Plan

> This file tracks the implementation plan for the Task-Based Context system.
> Last updated: 2026-01-04 23:15

---

## Vision

Transform Genie from "run single agents" into a **deliberation system** where:
1. Multiple agents contribute perspectives to a shared task context
2. Agents propose actions, user curates and triggers execution
3. Knowledge accumulates across agent runs
4. User maintains control at every step

### Core Principles (from project_implementation_more_ideas.md)
1. **Small step-by-step tasks** — Short-lived agent calls, not long-running monoliths
2. **Maintain information necessary to know before acting** — Gather context before execution
3. **Right agents, right time, right information** — Orchestration discipline
4. **Keep user informed, let user stop anytime** — Transparency + control

---

## Architecture

### Task Directory Structure
```
genie_tasks/
└── <task-name>/
    ├── task.md              # Goal, constraints, what we're trying to do
    ├── knowledge.md         # Accumulated facts from agents
    ├── uncertainties.md     # Open questions, gaps
    ├── relevant_files.md    # Code/files flagged as relevant
    └── actions.md           # Queued actions for next /step
```

### Commands
| Command | Action |
|---------|--------|
| `/task <name> <description>` | Create/switch to task, sets goal in `task.md` |
| `/todo <action>` | Add an action to `actions.md` |
| `/todo [agent_type] <action>` | Add action with specific agent type |
| `/step` | Execute all actions in `actions.md` (parallel), then clear |
| `/context <file-path>` | Add file to `relevant_files.md` |

### Agent Prompt Injection
When an agent runs, it receives full task context:
```
=== TASK CONTEXT ===
[contents of task.md]

=== KNOWN FACTS ===
[contents of knowledge.md]

=== OPEN QUESTIONS ===
[contents of uncertainties.md]

=== RELEVANT FILES ===
[contents of relevant_files.md]

=== YOUR ROLE ===
[agent system prompt]

=== USER REQUEST ===
[user's prompt]

After completing your work, provide a structured summary and propose next actions.
```

### Agent Output Routing
When agent completes, its structured summary is parsed and routed:
- "What I Learned" → appends to `knowledge.md`
- "What Remains Uncertain" → appends to `uncertainties.md`
- "Suggested Next Steps" → appends to `actions.md` (as proposed todos)

---

## Implementation Milestones

### RT1: Task Context Foundation
**Status:** Not Started

**Deliverables:**
- [ ] Create `genie_tasks/` directory structure
- [ ] `Genie.TaskContext` module for reading/writing context files
- [ ] `/task <name> <description>` command to create/switch tasks
- [ ] Store active task name in `genie_state.json`
- [ ] UI shows current task name and status

**Files to create/modify:**
- `lib/genie/task_context.ex` (new)
- `lib/genie_web/live/dashboard_live.ex` (add /task command)
- `genie_state.json` schema (add `active_task`)

---

### RT2: Context Injection
**Status:** Not Started

**Deliverables:**
- [ ] Update `ShellAgent` to inject task context into prompts
- [ ] `/context <file-path>` command to add relevant files
- [ ] Context files created with sensible defaults/templates

**Files to create/modify:**
- `lib/genie/shell_agent.ex` (inject context)
- `lib/genie/task_context.ex` (add file management)
- `lib/genie_web/live/dashboard_live.ex` (add /context command)

---

### RT3: Action Queue & Output Routing
**Status:** Not Started

**Deliverables:**
- [ ] `/todo <action>` command to add actions
- [ ] `/todo [agent_type] <action>` with optional agent specification
- [ ] Route agent output to appropriate context files
- [ ] UI shows pending actions from `actions.md`

**Files to create/modify:**
- `lib/genie/task_context.ex` (action management)
- `lib/genie/shell_agent.ex` (output routing on completion)
- `lib/genie_web/live/dashboard_live.ex` (add /todo command, show actions)

---

### RT4: Step Execution
**Status:** Not Started

**Deliverables:**
- [ ] `/step` command to execute all pending actions
- [ ] Parse action format: `- [agent_type] action description` or `- action description`
- [ ] Default agent type if not specified (configurable)
- [ ] Parallel agent execution for all actions
- [ ] Clear `actions.md` after execution (or mark done)
- [ ] UI shows step execution progress

**Files to create/modify:**
- `lib/genie/step_executor.ex` (new - orchestrates /step)
- `lib/genie_web/live/dashboard_live.ex` (add /step command)

---

## Design Decisions

### Decision 1: Context Storage
**Choice:** Markdown files in `genie_tasks/<task-name>/`
**Rationale:** 
- Human-readable and editable
- Version controllable (git)
- No database dependency
- Easy to inspect and debug

### Decision 2: Action Format
**Choice:** Simple markdown list with optional agent tag
```markdown
- research Meilisearch Elixir client
- [repo_knowledge] find existing search-related code
- [doc_expert] check Phoenix LiveView patterns for search UI
```
**Rationale:**
- Easy to write manually
- Easy to parse
- Optional complexity (agent tag)

### Decision 3: Step Execution Mode
**Choice:** Parallel by default
**Rationale:**
- Faster execution
- Agents are independent researchers
- Context files updated after ALL agents complete (avoids race conditions)
**Future:** Add `/step-seq` for sequential if needed

### Decision 4: Output Routing
**Choice:** Automatic routing based on structured summary sections
**Rationale:**
- Agents already produce structured output (M3)
- No user intervention needed for routine updates
- User can always edit files manually

---

## Example Workflow

```
# User starts a task
User: /task search-feature implement full-text search for notes

# User asks for initial research
User: /agent repo_knowledge what search-related code exists?

# Agent runs, finds some code, updates context:
# - knowledge.md += "Found SearchHelper module in lib/search_helper.ex"
# - uncertainties.md += "Unclear if Meilisearch is already configured"
# - actions.md += "- [doc_expert] research Meilisearch Elixir integration"

# User adds their own action
User: /todo [repo_knowledge] check config files for search settings

# User reviews actions.md (visible in UI), then triggers
User: /step

# Both actions run in parallel:
# - doc_expert researches Meilisearch
# - repo_knowledge checks config files

# Context files updated with new findings
# actions.md cleared, new proposed actions added by agents

# Cycle continues until user is satisfied
```

---

## Open Questions

1. **Default agent for untagged actions** - Use `gemini` (generic) or `planner` (structured)?
2. **Action deduplication** - What if multiple agents propose same action?
3. **Context size limits** - How to handle when context files get too large for prompt?
4. **Task switching** - How to handle multiple tasks? Archive? Delete?

---

## Phase 2: Context-Centric Evolution (RT5-RT6)

### RT5: Context-Centric UI
**Status:** Not Started

**Vision:** The UI should answer "What's the state of our work?" not "What agents exist?"

**Deliverables:**
- [ ] Redesign left sidebar: show Goal, Knowledge summary, Pending Actions
- [ ] De-emphasize or hide Agent Types panel (move to settings/advanced)
- [ ] Main panel: Context file viewer with live updates
- [ ] Quick status: "3 facts known, 2 uncertainties, 4 pending actions"
- [ ] Reduce chat focus, increase context focus

**UI Sketch:**
```
┌──────────────────────────────────────────────────────────────┐
│  GENIE  │  Task: search-feature                    [/step]   │
├─────────┴────────────────────────────────────────────────────┤
│ ┌─────────────┐  ┌─────────────────────────────────────────┐ │
│ │ GOAL        │  │ knowledge.md                       [edit]│ │
│ │ Implement   │  │ ─────────────────────────────────────── │ │
│ │ full-text   │  │ - SearchHelper exists in lib/          │ │
│ │ search      │  │ - Meilisearch client available         │ │
│ │             │  │ - Config in runtime.exs                │ │
│ ├─────────────┤  │                                         │ │
│ │ KNOWLEDGE   │  │ uncertainties.md                        │ │
│ │ 3 facts     │  │ ─────────────────────────────────────── │ │
│ │             │  │ - How to handle large result sets?      │ │
│ ├─────────────┤  │                                         │ │
│ │ UNCERTAIN   │  │ actions.md                              │ │
│ │ 2 questions │  │ ─────────────────────────────────────── │ │
│ ├─────────────┤  │ - research pagination patterns          │ │
│ │ NEXT        │  │ - check existing LiveView components    │ │
│ │ 4 actions   │  │                                         │ │
│ └─────────────┘  └─────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│ > /step                                                      │
└──────────────────────────────────────────────────────────────┘
```

**Files to create/modify:**
- `lib/genie_web/live/dashboard_live.ex` (major UI restructure)
- `lib/genie_web/live/dashboard_live.html.heex` (new layout)
- `assets/css/app.css` (context-focused styling)

---

### RT6: Trace Derivation
**Status:** Not Started

**Vision:** AI observes context evolution and derives reasoning traces automatically.

**Deliverables:**
- [ ] `/trace` command to trigger trace derivation for current task
- [ ] Trace derivation agent prompt (analyzes knowledge.md history, actions.md, etc.)
- [ ] Output to `genie_tasks/<task>/trace.md` (unstructured initially)
- [ ] Optional: auto-run after each `/step` completes
- [ ] Track provenance: what came from where, what depended on what

**Trace Agent Prompt (Draft):**
```
You are analyzing the evolution of a task's context.

Given:
- The current state of knowledge.md, uncertainties.md, actions.md
- The history of changes (if available)
- The original goal

Identify:
1. What facts were confirmed? By what evidence?
2. What assumptions were invalidated or changed?
3. What pivots occurred (direction changes)?
4. What remains uncertain?

Output a reasoning trace that captures the "why" behind the current state.
```

**Files to create/modify:**
- `lib/genie/trace_deriver.ex` (new - trace derivation logic)
- `lib/genie_web/live/dashboard_live.ex` (add /trace command)
- `genie_agents.json` (add "auditor" or "tracer" agent type)

---

## Milestone Summary (Updated)

| Phase | Milestone | Description | Status |
|-------|-----------|-------------|--------|
| 1 | RT1 | Task Context Foundation | ✅ Complete |
| 1 | RT2 | Context Injection | ✅ Complete |
| 1 | RT3 | Action Queue & Output Routing | ✅ Complete |
| 1 | RT4 | Step Execution | ✅ Complete |
| 2 | RT5 | Context-Centric UI | Not Started |
| 2 | RT6 | Trace Derivation | Not Started |

---

## References

- `GENIE_PLAN.md` — Original M1-M3 implementation plan
- `GENIETABLE_DECISIONS.md` — Design decisions and rationale
- `more_indeas_trace_and_reeval.md` — Trace & Re-Evaluation concepts
- `more_indeas_opus_delta_learn_insight_illusion.md` — DDL/Insight applied to context
- `paper_takeaways_raw.md` — Academic foundations
