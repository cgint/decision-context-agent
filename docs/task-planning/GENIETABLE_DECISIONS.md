# Genie Round-Table Design Decisions

> This file captures key design decisions and the reasoning behind them.
> Last updated: 2026-01-04 23:15

---

## Core Concept: The Deliberation Loop

The fundamental insight driving this design:

```
┌─────────────────────────────────────────────────────────────┐
│                    DELIBERATION LOOP                        │
│                                                             │
│   1. Agents DELIBERATE → read context, do work, propose     │
│   2. User CURATES → reviews, adds, removes, edits actions   │
│   3. User TRIGGERS → /step executes collected actions       │
│   4. Context ACCUMULATES → knowledge grows, loop repeats    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

This keeps the user in control while leveraging agent capabilities.

---

## Decision Record

### D1: Context Storage Format

**Decision:** Free-form markdown files in `genie_tasks/<task-name>/`

**Alternatives Considered:**
1. JSON structure in `genie_state.json`
2. Database (SQLite, ETS)
3. Single large context file

**Why Markdown Files:**
- Human-readable without tooling
- User can edit directly in any editor
- Git-friendly (diff, history, branching)
- Natural separation of concerns (one file per type)
- Easy to include in agent prompts
- No schema migrations needed

**Trade-offs Accepted:**
- Parsing is less precise than JSON
- No query capability
- File I/O for each access

---

### D2: Single Active Task (For Now)

**Decision:** Only one task active at a time, no explicit lifecycle

**Alternatives Considered:**
1. Multiple concurrent tasks
2. Explicit start/end commands
3. Session-based (reset on restart)

**Why Single Task:**
- Simpler mental model
- Matches typical workflow (focus on one thing)
- Avoids complexity of task switching
- Can add multi-task later if needed

**Future Evolution:**
- `/task list` to see all tasks
- `/task switch <name>` to change active task
- `/task archive <name>` to move completed tasks

---

### D3: Action Format

**Decision:** Simple markdown list with optional agent tag

```markdown
# Format
- action description                    # uses default agent
- [agent_type] action description       # uses specific agent

# Examples
- research Meilisearch Elixir client
- [repo_knowledge] find existing search-related code
- [doc_expert] check Phoenix LiveView patterns
```

**Alternatives Considered:**
1. Structured YAML/JSON
2. Checkbox format `- [ ] action`
3. Full action objects with metadata

**Why Simple List:**
- Easy to type manually
- Easy to parse with regex
- Agent tag is optional (progressive complexity)
- Familiar markdown format
- Can copy/paste from agent suggestions

**Parsing Logic:**
```elixir
# Regex: /^\s*-\s*(?:\[(\w+)\]\s*)?(.+)$/
# Group 1: agent_type (optional)
# Group 2: action description
```

---

### D4: Step Execution Mode

**Decision:** Parallel execution by default

**Alternatives Considered:**
1. Sequential (each agent sees previous results)
2. User chooses per-step
3. Dependency-based ordering

**Why Parallel:**
- Faster execution (agents run simultaneously)
- Agents are independent researchers
- Avoids blocking on slow agents
- Context updated atomically after all complete

**How Context Updates Work:**
1. Read current context files
2. Start all agents with same context snapshot
3. Wait for all to complete
4. Merge all outputs into context files
5. Clear `actions.md`

**Race Condition Mitigation:**
- All agents see same context (snapshot at step start)
- Outputs merged after ALL complete (no interleaving)
- Append-only to knowledge/uncertainties (no conflicts)

**Future Evolution:**
- `/step-seq` for sequential execution
- Dependency tags: `- [repo_knowledge, depends:research] find code`

---

### D5: Output Routing

**Decision:** Automatic routing based on M3 structured summary

**How It Works:**
```
Agent Output                    → Routes To
─────────────────────────────────────────────
## What I Learned               → knowledge.md
- fact 1                        
- fact 2                        

## What Remains Uncertain       → uncertainties.md
- question 1                    

## Suggested Next Steps         → actions.md
- [doc_expert] research X       
- investigate Y                 
```

**Alternatives Considered:**
1. User manually moves content
2. Agent writes directly to files
3. All output to single file

**Why Automatic Routing:**
- Leverages existing M3 infrastructure
- No extra user work for routine updates
- Consistent structure across agents
- User can still edit files manually

**Implementation:**
- Extend `SummaryParser` to route to task context files
- Call routing in `ShellAgent.finalize_agent/2`
- Only route if active task exists

---

### D6: Agent Assignment for Actions

**Decision:** User specifies agent type, with default fallback

**Format:**
```markdown
- [repo_knowledge] find search code    # explicit agent
- research best practices              # uses default agent
```

**Default Agent:** `gemini` (generic, can do anything)

**Alternatives Considered:**
1. Auto-detect from keywords (fragile, magic)
2. Always require agent type (verbose)
3. Planner assigns agents (extra step)

**Why User Specifies:**
- User knows what they want
- No magic/surprises
- Default handles simple cases
- Can add smart assignment later

---

### D7: Context Injection Method

**Decision:** Inject full context into prompt (not file references)

**Prompt Structure:**
```
=== TASK CONTEXT ===
[task.md content]

=== KNOWN FACTS ===  
[knowledge.md content]

=== OPEN QUESTIONS ===
[uncertainties.md content]

=== RELEVANT FILES ===
[relevant_files.md content]

=== YOUR ROLE ===
[agent system prompt]

=== USER REQUEST ===
[action description]

Provide a structured summary with What I Learned, What Remains Uncertain, and Suggested Next Steps.
```

**Alternatives Considered:**
1. Pass file paths, let agent read them
2. Selective injection (only relevant sections)
3. Summarize context before injection

**Why Full Injection:**
- Guaranteed agent sees context
- No tool-use required to read context
- Works with any agent/model
- Simpler implementation

**Trade-offs Accepted:**
- Prompt size grows with context
- May need truncation for large contexts

**Future Evolution:**
- Smart truncation (recent knowledge first)
- Context summarization for large tasks
- Selective injection based on agent type

---

## Principles Guiding Decisions

1. **User Control** — User triggers execution, user curates actions
2. **Transparency** — All context visible in plain files
3. **Simplicity** — Start simple, add complexity when needed
4. **Incremental** — Each step produces visible progress
5. **Recoverable** — Markdown files can be manually fixed if something breaks

---

## Post-RT4 Decisions

### D8: Context-First UI (vs Agent-First)

**Decision:** Redesign UI to emphasize *context* over *agents*

**Current State (Agent-Focused):**
- UI prominently shows "Agent Types" panel
- Agent badges on outputs
- Execution mechanics visible
- User thinks in terms of "which agent to run"

**New Direction (Context-Focused):**
- UI prominently shows current Goal, Knowledge, Uncertainties
- Agent type is an implementation detail (hidden or de-emphasized)
- User thinks in terms of "what do we know, what's next"
- Context files are the primary view, not chat history

**Why This Matters:**
- The human collaborator needs to quickly understand the *state* of the work
- "What agent ran" is less important than "what did we learn"
- Agents are tools; context is the conversation
- Reduces cognitive load — fewer concepts to track

**UI Implications:**
| Element | Current | New |
|---------|---------|-----|
| Left Sidebar | Agent Types list | Context summary (goal, knowledge, actions) |
| Main Panel | Chat + execution output | Context files + diff view |
| Agent Badge | Prominent | Subtle or hidden |
| Command Focus | `/agent <type>` | `/step` (context-driven) |

**Trade-offs Accepted:**
- Less visibility into execution mechanics
- Assumes user trusts the routing logic
- May need "advanced mode" for debugging

---

### D9: Trace Derivation (AI Auditor)

**Decision:** Add an AI pass that reviews context evolution and derives reasoning traces

**What It Does:**
1. Reads the history of changes to `knowledge.md`, `uncertainties.md`, etc.
2. Identifies patterns: What got confirmed? What was invalidated? What pivoted?
3. Produces a `trace.md` (or similar) with provenance and confidence signals

**Why:**
- Manual curation doesn't scale
- We want to learn *why* decisions were made
- Supports the "grounding score" concept (see `more_indeas_trace_and_reeval.md`)
- Enables cross-task ontology synthesis later

**When It Runs:**
- After each `/step` completes (optional, can be toggled)
- On-demand via `/trace` or similar command
- Periodically as an "auditor" background check

**Output Format:**
- Unstructured markdown initially (per `more_indeas_trace_and_reeval.md` decisions)
- Evolve schema as patterns emerge

---

### D10: Provenance Tracking

**Decision:** Every fact added to context should (eventually) carry provenance metadata

**Fields:**
| Field | Description |
|-------|-------------|
| Generated By | Which agent/step/user |
| Timestamp | When added |
| Input Context | What was in scope when this was derived |
| Basis | What evidence/reasoning supports this |

**Implementation:**
- Start simple: timestamp + source in the markdown itself
- Evolve to structured metadata (YAML frontmatter or JSON sidecar) as needed

**Why:**
- Enables precise invalidation cascades
- Supports grounding score calculation
- Makes debugging possible ("why do we believe X?")

---

### D11: Context File Interaction Mode (RT5)

**Decision:** Read-only display initially, inline editing later

**Question:** Should the Context-Centric UI allow editing context files directly, or just display them?

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Read-Only** | Display context files; edits happen in external editor or via commands | Simpler implementation, clear separation of concerns |
| **B: Inline Editing** | Edit `knowledge.md`, `actions.md`, etc. directly in the UI | More convenient, but adds complexity (save handling, conflicts) |
| **C: Hybrid** | Read-only by default, click-to-edit mode | Best UX, but most implementation effort |

**Choice:** **A (Read-Only)** for RT5 initial implementation.

**Rationale:**
- Keeps RT5 scope tight and shippable
- Users can still edit files in their preferred editor
- Commands (`/todo`, `/context`) handle common additions
- Inline editing can be added as a follow-up enhancement

---

## Open Questions (To Be Decided)

These questions are intentionally deferred—answers should emerge from experience rather than speculation. Each includes options and a preliminary preference.

---

### Q1: Grounding Score Weights

**Question:** How should we weight the different signals that contribute to a fact's confidence score?

**Signals Identified:**
- Number of independent sources supporting the fact
- Execution evidence (code ran, tests passed)
- Survival under previous re-evaluations
- User confirmation

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Equal Weights** | All signals count the same | Simple, but may over/underweight certain signals |
| **B: Execution-Heavy** | Execution evidence counts 2x | Biases toward "proven" facts, may undervalue research |
| **C: Source-Heavy** | Multiple sources count most | Rewards corroboration, may miss execution failures |
| **D: Learned Weights** | ML/heuristic tuning over time | Most accurate long-term, requires data collection first |

**Preference:** **A (Equal Weights)** initially, evolve to **D (Learned)** once we have usage data. Premature optimization here is risky.

---

### Q2: Re-Evaluation Aggressiveness

**Question:** How aggressively should the system flag facts for review when new information arrives?

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Conservative** | Only flag on explicit contradictions (keyword + semantic inversion) | Low noise, may miss subtle drift |
| **B: Moderate** | Flag on contradiction OR when a fact's basis is updated | More coverage, some false positives |
| **C: Aggressive** | Re-evaluate all related facts after every `/step` | Thorough, but high compute cost and noise |
| **D: User-Controlled** | Configurable threshold per task | Flexible, but adds complexity |

**Preference:** **A (Conservative)** to start. Trust the user to notice drift; avoid alert fatigue. Escalate to **B** if users report stale knowledge problems.

---

### Q3: Trace Entry Schema

**Question:** How structured should trace entries be?

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Free-form Markdown** | Prose paragraphs, no enforced structure | Flexible, but hard to query programmatically |
| **B: Semi-Structured** | Markdown with required headers (What/Why/Confidence/Source) | Balanced, human-readable + parseable |
| **C: Fully Structured** | JSON/YAML with strict schema | Machine-queryable, but less human-friendly |
| **D: Hybrid** | Markdown prose + JSON metadata sidecar | Best of both, but two files to maintain |

**Preference:** **B (Semi-Structured Markdown)** — enforces discipline without sacrificing readability. Example:
```markdown
### Trace: SearchHelper Module Location
- **What:** SearchHelper exists in `lib/search_helper.ex`
- **Why:** repo_knowledge agent found it via grep; confirmed by user
- **Confidence:** High (2 sources)
- **Source:** repo_knowledge @ 2026-01-05T14:30Z, user @ 2026-01-05T14:35Z
```

---

### Q4: Cross-Task Knowledge Transfer

**Question:** When and how should knowledge from one task inform another?

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: No Transfer** | Each task is isolated; start fresh | Simple, but loses accumulated wisdom |
| **B: Manual Import** | User explicitly imports facts from other tasks | Controlled, but requires user effort |
| **C: Shared Ontology** | High-confidence patterns auto-promote to global `learned_ontology.md` | Automatic learning, but risk of contamination |
| **D: Task Inheritance** | New tasks can "extend" a parent task's context | Structured reuse, but adds complexity |

**Preference:** **B (Manual Import)** initially, evolve to **C (Shared Ontology)** once RT6 (Trace Derivation) is mature. Premature sharing risks propagating bad assumptions.

---

### Q5: Ontology Synthesis Frequency

**Question:** How often should the system synthesize cross-task patterns into a learned ontology?

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: On-Demand** | User triggers via `/synthesize` command | Full control, but may be forgotten |
| **B: After N Tasks** | Auto-trigger after every 3-5 completed tasks | Regular cadence, but arbitrary threshold |
| **C: Continuous** | Background process updates ontology incrementally | Always fresh, but compute-intensive |
| **D: Milestone-Based** | Trigger on task completion (explicit `/task complete`) | Natural checkpoint, requires discipline |

**Preference:** **D (Milestone-Based)** — synthesis happens at a meaningful moment (task completion), not arbitrary intervals. Add `/task complete` command to mark task done and trigger synthesis.

---

### Q6: Auditor Execution Model

**Question:** Where and when does the Auditor agent run?

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Post-Step Hook** | Automatically runs after every `/step` completes | Seamless, but adds latency to every step |
| **B: Separate Command** | User triggers via `/trace` or `/audit` | Explicit control, but may be forgotten |
| **C: Background Process** | Runs periodically (every N minutes or events) | Unobtrusive, but timing may be suboptimal |
| **D: Opt-In Per Step** | `/step --audit` flag to include auditor | Flexible, but verbose |

**Preference:** **B (Separate Command)** initially. Users should consciously decide when to reflect. Once trust is built, consider **A (Post-Step Hook)** as opt-in default.

---

### Q7: Contradiction Detection Method

**Question:** How should the system detect when new facts contradict existing knowledge?

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Keyword Heuristics** | Simple pattern matching (e.g., "X is Y" vs "X is not Y") | Fast, but misses semantic contradictions |
| **B: Embedding Similarity** | Vector comparison of fact statements | Catches semantic similarity, but not logical contradiction |
| **C: LLM Comparison** | Ask LLM "Does A contradict B?" | Most accurate, but expensive per fact pair |
| **D: Tiered Approach** | Heuristics first, escalate to LLM for ambiguous cases | Balanced cost/accuracy |

**Preference:** **D (Tiered Approach)** — use cheap heuristics to filter obvious non-contradictions, only invoke LLM for borderline cases. Keeps costs manageable while maintaining accuracy.

---

### Q8: Dependency Tracking Granularity

**Question:** At what level should we track what depends on what?

**Options:**
| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: No Tracking** | Append-only, no dependency graph | Simplest, but invalidation cascade is blind |
| **B: Per-Agent-Run** | Track which agent run produced which facts | Coarse, but captures provenance |
| **C: Per-Fact** | Each fact explicitly lists its dependencies | Precise cascades, but high annotation burden |
| **D: Inferred** | LLM infers dependencies when auditing | No upfront cost, but inference may be wrong |

**Preference:** **B (Per-Agent-Run)** to start — low overhead, sufficient for "this whole batch may need review" cascades. Evolve to **C (Per-Fact)** if users need finer granularity.

---

## Decision Status Summary

| Question | Status | Current Choice |
|----------|--------|----------------|
| Q1: Grounding Score Weights | Deferred | Equal weights initially |
| Q2: Re-Evaluation Aggressiveness | Deferred | Conservative |
| Q3: Trace Entry Schema | Deferred | Semi-structured markdown |
| Q4: Cross-Task Knowledge Transfer | Deferred | Manual import |
| Q5: Ontology Synthesis Frequency | Deferred | Milestone-based |
| Q6: Auditor Execution Model | Deferred | Separate command |
| Q7: Contradiction Detection Method | Deferred | Tiered approach |
| Q8: Dependency Tracking Granularity | Deferred | Per-agent-run |

**Philosophy:** Ship RT5 first (visibility), observe usage, then revisit these decisions with real data.
