# Genie Round-Table Testing Guide

> Quick guide to test the Context-Centric UI (RT5)
> Last updated: 2026-01-08

---

## Quick Start: Testing RT5

### Prerequisites
```bash
cd /Users/cgint/dev/agent-coding-gui
mix phx.server
```

Navigate to: http://localhost:4000

---

## Test Workflow: Context-Centric Experience

### Step 1: Create a Task
```
Command: /task search-feature implement full-text search for notes
```

**Expected Result:**
- System message: "Created and activated new task: search-feature"
- Badge appears in header: "Task: search-feature"
- **Left sidebar updates** with:
  - Task Goal: "implement full-text search for notes"
  - Context Status showing: 0 facts, 0 uncertainties, 0 actions
- **Main panel shows** three empty context cards:
  - Knowledge (with 0 badge)
  - Uncertainties (with 0 badge)
  - Pending Actions (with 0 badge)

---

### Step 2: Run Discovery (Let Agents Analyze)
```
Command: /step
```

**What Happens:**
- System spawns 4 agents automatically (repo_knowledge, web_search, doc_expert, planner)
- Right sidebar shows agents running with spinner icons
- Wait 30-60 seconds for agents to complete

**Expected Result After Completion:**
- **Main panel updates in real-time** (no page refresh needed!)
- **Knowledge card** fills with discovered facts:
  ```markdown
  - Phoenix LiveView is already installed
  - SearchHelper module exists in lib/
  - Meilisearch client library available
  ```
- **Uncertainties card** shows open questions:
  ```markdown
  - How to handle large result sets?
  - What pagination strategy to use?
  ```
- **Actions card** shows proposed next steps:
  ```markdown
  - [doc_expert] research Meilisearch integration patterns
  - [repo_knowledge] check existing search implementation
  ```
- **Left sidebar updates** with new counts (e.g., "5 facts, 2 uncertainties, 4 actions")

---

### Step 3: Add Custom Action
```
Command: /todo [web_search] find LiveView search UI examples
```

**Expected Result:**
- System message: "Added action: [web_search] find LiveView search UI examples"
- **Actions card updates immediately** with new item
- **Left sidebar count increases** (e.g., 4 → 5 actions)

---

### Step 4: Execute Pending Actions
```
Command: /step
```

**What Happens:**
- All pending actions execute in parallel
- Actions card clears after execution starts
- Agents run and route output back to context files

**Expected Result After Completion:**
- **Knowledge card grows** with new findings from agents
- **Uncertainties may update** with new questions or resolved items
- **Actions card refills** with newly proposed next steps
- **Real-time updates** - you see changes appear without refreshing

---

## Visual Reference: UI Layout

```
┌────────────────────────────────────────────────────────────────┐
│  GENIE  │  Task: search-feature                      [header]   │
├──────────┬─────────────────────────────────────┬────────────────┤
│ GOAL     │  KNOWLEDGE (5)                      │  Agent Types   │
│ Full-text│  ─────────────────────────────────  │  (collapsed)   │
│ search   │  • Phoenix LiveView installed       │                │
│          │  • SearchHelper exists in lib/      │  AGENTS        │
│ STATUS   │  • Meilisearch available            │  ✅ planner    │
│ 💡 5     │                                     │  ✅ doc_expert │
│ ❓ 2     │  UNCERTAINTIES (2)                  │                │
│ ➡️ 4     │  ─────────────────────────────────  │  OUTPUT        │
│          │  • Large result set handling?       │  [selected     │
│ TODOS    │  • Pagination strategy?             │   agent        │
│ ☐ Task 1 │                                     │   output]      │
│ ☑ Task 2 │  PENDING ACTIONS (4)                │                │
│          │  ─────────────────────────────────  │  SUMMARY       │
│          │  • [doc_expert] research patterns   │  [What I       │
│          │  • Check existing code              │   Learned]     │
└──────────┴─────────────────────────────────────┴────────────────┘
                        Command: /step
```

---

## What to Observe: Key Features

### ✅ Context Visibility
- **Before RT5**: Had to open `genie_tasks/search-feature/*.md` files manually
- **After RT5**: Context visible directly in main panel, formatted as HTML

### ✅ Real-Time Updates
- **Before RT5**: Needed to refresh or reopen files to see agent output
- **After RT5**: Context cards update automatically when agents complete

### ✅ Context Summary
- **Before RT5**: No quick overview of task progress
- **After RT5**: Left sidebar shows counts (5 facts, 2 questions, 4 actions)

### ✅ Markdown Rendering
- **Before RT5**: Raw markdown text in UI
- **After RT5**: Rendered HTML with proper formatting (headings, lists, code blocks)

### ✅ Focus Shift
- **Before RT5**: UI emphasized "which agents exist"
- **After RT5**: UI emphasizes "what we know about the task"

---

## Testing Checklist

| Test | Command | Expected Behavior | Status |
|------|---------|-------------------|--------|
| **Create task** | `/task my-task my description` | Left sidebar shows goal, main panel shows empty cards | ☐ |
| **Discovery mode** | `/step` (no actions) | All 4 agents spawn, context fills after completion | ☐ |
| **Add custom action** | `/todo check config files` | Action appears in actions card immediately | ☐ |
| **Execute actions** | `/step` (with actions) | Actions execute, context updates in real-time | ☐ |
| **Switch tasks** | `/task other-task switch to this` | Context reloads for new task | ☐ |
| **List tasks** | `/task` | Shows all tasks with active indicator | ☐ |
| **No active task** | (start fresh, no /task) | Main panel shows info alert + chat | ☐ |
| **Real-time updates** | Watch during `/step` | Cards update without page refresh | ☐ |
| **Context counts** | After agents complete | Left sidebar badges update with correct counts | ☐ |
| **Markdown rendering** | View knowledge card | Lists, headings, code formatted properly | ☐ |

---

## Expected File Structure

After running the test workflow, check:

```bash
ls -la genie_tasks/search-feature/
```

**Expected files:**
```
genie_tasks/search-feature/
├── task.md              # Goal: "implement full-text search..."
├── knowledge.md         # Populated with discovered facts
├── uncertainties.md     # Populated with open questions
├── actions.md           # Cleared after /step, refilled by agents
└── relevant_files.md    # Empty unless you used /context command
```

**Verify content:**
```bash
cat genie_tasks/search-feature/knowledge.md
```

Should contain markdown-formatted bullet points of discovered facts.

---

## Troubleshooting

### Issue: Context cards not updating
**Solution:** 
- Refresh browser (ensures WebSocket connection)
- Check that task is active (badge shows in header)
- Verify agents completed (check right sidebar)

### Issue: Markdown not rendering
**Solution:**
- Check browser console for errors
- Verify Earmark dependency installed: `mix deps | grep earmark`
- Re-compile: `mix compile --force`

### Issue: Counts don't match
**Solution:**
- Context files count items starting with `- ` (bullet points)
- Empty files or files with only headers show count of 0
- Use `/todo` to add actions if needed

---

## Next Steps After RT5

Once RT5 is working, the foundation is ready for:

- **RT6: Trace Derivation** - Add `/trace` command to derive reasoning traces
- **Provenance Tracking** - Metadata about where facts came from
- **Re-Evaluation** - Detect contradictions and flag outdated knowledge

See `GENIETABLE_PLAN.md` for full roadmap.

---

## Quick Debug Commands

```bash
# View current active task
cat genie_state.json | jq '.active_task'

# View task context files
cat genie_tasks/*/knowledge.md

# Check compilation
mix compile

# Run precommit checks
./precommit.sh

# Start server with debug logging
iex -S mix phx.server
```

---

## Success Criteria

✅ **You should be able to:**
1. Create a task and see goal displayed immediately
2. Run `/step` and watch context fill in real-time
3. See formatted markdown (not raw text) in context cards
4. See accurate counts in left sidebar badges
5. Navigate between tasks and see context switch
6. Use `/todo` to add actions and see them appear instantly
7. Never need to open external files to see task progress

**If all above work → RT5 is functioning correctly!** 🎉
