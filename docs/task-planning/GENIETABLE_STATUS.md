# Genie Round-Table Implementation Status

> This file tracks current progress on the Task-Based Context system.
> Last updated: 2026-01-08 11:50

---

## Current Phase: RT5 Complete! 🎉 Context-Centric UI Deployed

### Overall Progress
| Milestone | Status | Progress |
|-----------|--------|----------|
| RT1: Task Context Foundation | ✅ Complete | 100% |
| RT2: Context Injection | ✅ Complete | 100% |
| RT3: Action Queue & Output Routing | ✅ Complete | 100% |
| RT4: Step Execution | ✅ Complete | 100% |
| RT5: Context-Centric UI | ✅ Complete | 100% |
| RT6: Trace Derivation | 📋 Not Started | 0% |

---

## Foundation Complete (M1-M3)

The following infrastructure exists and will be extended:

- **M1: Shell Agent Supervisor** — Agents run via `ShellAgent`, managed by `AgentSupervisor`
- **M2: Agent Type Definitions** — 4 agent types in `genie_agents.json`, `/agent` command works
- **M3: Structured Output** — `SummaryParser` extracts learned/uncertain/next_steps, displayed in UI

---

## RT1: Task Context Foundation ✅

**Completed:** 2026-01-05

**Files Created:**
- `lib/genie/task_context.ex` — Module for task directory/file management

**Files Modified:**
- `lib/genie_web/live/dashboard_live.ex` — Added `/task` command handling, UI task badge

**Features:**
- `/task <name> <description>` — Create new task and activate it
- `/task <name>` — Switch to existing task
- `/task` — List all tasks with active indicator
- UI header shows "Task: <name>" badge when task is active
- Task directories created in `genie_tasks/<name>/` with 5 template files:
  - `task.md`, `knowledge.md`, `uncertainties.md`, `relevant_files.md`, `actions.md`

---

## RT2: Context Injection ✅

**Completed:** 2026-01-05

**Files Modified:**
- `lib/genie/task_context.ex` — Added `build_context_for_prompt/0` function
- `lib/genie/shell_agent.ex` — Updated `build_prompt_with_system/2` to inject task context
- `lib/genie_web/live/dashboard_live.ex` — Added `/context` command

**Features:**
- When running `/agent <type> <prompt>` with an active task, agent receives full task context
- Context includes: task description, knowledge, uncertainties, relevant files
- `/context <file-path>` — Adds file path to task's `relevant_files.md`

---

## RT3: Action Queue & Output Routing ✅

**Completed:** 2026-01-05

**Files Modified:**
- `lib/genie/task_context.ex` — Added `parse_actions/1` function
- `lib/genie/shell_agent.ex` — Added `route_output_to_context/1` in `finalize_agent/2`
- `lib/genie_web/live/dashboard_live.ex` — Added `/todo` command, pending actions UI

**Features:**
- `/todo <action>` — Adds action to active task's `actions.md`
- `/todo [agent_type] <action>` — Adds action with specific agent type tag
- Agent output automatically routes to context files:
  - "What I Learned" → `knowledge.md`
  - "What Remains Uncertain" → `uncertainties.md`
  - "Suggested Next Steps" → `actions.md`
- UI shows pending actions in left sidebar (with agent badges when tagged)

---

## RT4: Step Execution ✅

**Completed:** 2026-01-05

**Files Modified:**
- `lib/genie/task_context.ex` — Added `clear_actions/1` function
- `lib/genie_web/live/dashboard_live.ex` — Added `/step` command, `start_step_agents/1`, `start_discovery_agents/2`

**Features:**
- `/step` with actions → Executes pending actions in parallel
- `/step` without actions → **Spawns ALL agent types** to analyze task and propose actions
  - Each agent gets a discovery prompt tailored to their expertise
  - repo_knowledge: What code/files to examine?
  - web_search: What external info to find?
  - doc_expert: What APIs/docs to consult?
  - planner: What's the overall approach?
- Actions with `[agent_type]` tags use that agent; others default to `"planner"`
- `actions.md` is cleared after execution starts (header preserved)
- Agent outputs route to context files (from RT3)

---

## Key Design Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Context storage | Markdown files in `genie_tasks/<task>/` | Human-readable, git-friendly, no DB |
| Action format | `- [agent_type] action` or `- action` | Simple, optional complexity |
| Step execution | Parallel by default | Faster, agents are independent |
| Output routing | Automatic to context files | Leverages existing M3 summary parser |

---

## Recent Changes

| Date | Change | Files |
|------|--------|-------|
| 2026-01-08 | RT5 complete (context-centric UI) | mix.exs, task_context.ex, dashboard_live.ex |
| 2026-01-05 | RT4 complete (step execution) | task_context.ex, dashboard_live.ex |
| 2026-01-05 | RT3 complete (action queue & routing) | task_context.ex, shell_agent.ex, dashboard_live.ex |
| 2026-01-05 | RT2 complete (context injection) | task_context.ex, shell_agent.ex, dashboard_live.ex |
| 2026-01-05 | RT1 complete (task context) | task_context.ex, dashboard_live.ex |
| 2026-01-04 | Round-table planning complete | GENIETABLE_PLAN.md, GENIETABLE_STATUS.md, GENIETABLE_DECISIONS.md |
| 2026-01-04 | M3 complete (structured output) | summary_parser.ex, shell_agent.ex, dashboard_live.ex |
| 2026-01-04 | M2 complete (agent types) | genie_agents.json, agent_types.ex |
| 2026-01-04 | M1 complete (shell agent) | shell_agent.ex, agent_supervisor.ex |

---

## Next Actions

**Round-Table system is now feature-complete with discovery mode!**

Suggested testing workflow:
1. `/task search-feature implement full-text search for notes`
2. `/step` — **All 4 agents spawn automatically**, each analyzing from their expertise
3. Wait for agents to complete, check knowledge.md and actions.md populated
4. `/step` — Now executes the proposed actions
5. Repeat until task is understood/planned

Manual additions still work:
- `/todo [agent_type] action` — Add specific action
- `/context lib/some/file.ex` — Flag relevant files
- `/agent <type> <prompt>` — Run specific agent with custom prompt

Future enhancements:
- Autonomous loop mode (`/auto`)
- Planner synthesis pass before execution
- Action deduplication
- Context size limits / summarization
- Task archiving / switching

---

---

## RT5: Context-Centric UI ✅

**Completed:** 2026-01-08

**Files Created:**
- None (all modifications to existing files)

**Files Modified:**
- `mix.exs` — Added Earmark dependency for markdown rendering
- `lib/genie/task_context.ex` — Added `render_markdown/1`, `subscribe_to_context/1`, `broadcast_context_change/2`
- `lib/genie_web/live/dashboard_live.ex` — Major UI restructure and context loading

**Features:**
- **Markdown Rendering** — Context files (knowledge.md, uncertainties.md, actions.md) rendered as formatted HTML
- **Real-time Updates** — PubSub topic `"genie:context:<task_name>"` broadcasts context changes
- **Context-First UI** — Main panel displays context files, not chat history
- **Left Sidebar** — Shows task goal and context status summary (counts)
- **Context Summary** — Quick visibility: "X facts known, Y uncertainties, Z actions pending"
- **Collapsed Agent Types** — Moved to collapsible panel in right sidebar
- **Automatic Context Loading** — When switching tasks via `/task`, context loads immediately
- **Live Context Updates** — When agents complete and route output, UI updates in real-time

**UI Transformation:**
| Element | Before (RT4) | After (RT5) |
|---------|--------------|-------------|
| Left Sidebar | Todos + Pending Actions | Task Goal + Context Status + Todos |
| Main Panel | Chat Messages (chat-first) | Context Viewer (knowledge, uncertainties, actions) |
| Right Sidebar | Agent Types (prominent) | Agent Types (collapsed) + Agent execution |
| Primary Focus | "What agents are running?" | "What do we know?" |

**Technical Implementation:**
1. **Phase 1 (Infrastructure)**:
   - Added Earmark library for markdown→HTML conversion
   - Created PubSub broadcasting in `TaskContext.append_to_file/3`
   - Added `render_markdown/1` helper function
2. **Phase 2 (State Management)**:
   - Extended `mount/3` to subscribe to context changes
   - Added `handle_info({:context_updated, data}, socket)` handler
   - Created `load_task_context/2` helper for async context loading
   - Added context-related assigns: `task_goal`, `knowledge_content`, `uncertainties_content`, `actions_content`, plus counts
3. **Phase 3 (UI Restructure)**:
   - Left sidebar: Added task goal display + context status badges
   - Main panel: Replaced chat-first with context cards (knowledge, uncertainties, actions)
   - Right sidebar: Collapsed agent types panel into accordion
   - Chat remains functional but secondary (shows when no active task)

**Verification:**
- ✅ All precommit checks passed
- ✅ Compilation successful with no warnings
- ✅ All 6 tests passing
- ✅ Assets compiled successfully
- ✅ No production-unsafe runtime calls

---

## Next Phase: Trace Derivation (RT6)

**Direction:** Add AI auditor agent to derive reasoning traces from context evolution.

### The Insight

RT5 provides visibility into *what we know*. RT6 will add visibility into *why we believe it* — capturing the reasoning trace behind decisions.

**What RT6 Will Add:**
1. **Auditor Agent** — Reviews context history and derives reasoning traces
2. **Trace Derivation** — `/trace` command to trigger trace generation
3. **Provenance Tracking** — Metadata about fact origins and confidence scores
4. **Reasoning Traces** — `trace.md` file capturing "why" behind decisions

### Related Documentation

- `more_ideas_not_implemented.md` — RT6 conceptual implementation guidance
- `GENIETABLE_DECISIONS.md` — Open questions Q1-Q8 about provenance and re-evaluation
- `GENIETABLE_PLAN.md` — RT6 milestone specification
- `paper_takeaways_raw.md` — Academic foundations
