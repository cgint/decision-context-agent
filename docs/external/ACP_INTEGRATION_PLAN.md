# ACP Integration Plan: Genie AI Orchestrator

This plan outlines the steps to transition the Genie AI Orchestrator from raw shell command execution to a standardized **Agent Client Protocol (ACP)** integration.

## 1. Goal
Replace the current sequential command execution in `Genie.CommandOrchestrator` with a stateful, streaming ACP client that can orchestrate multiple agents (OpenCode, Gemini CLI) using a standardized protocol.

## 2. Proposed Architecture

### Current Flow:
`UI -> Orchestrator -> Port.open("gem.sh ...") -> Output to State -> UI`

### New ACP Flow:
`UI -> Orchestrator -> ACPex Connection -> OpenCode/Gemini (stdio)`
`OpenCode/Gemini -> ACPex Callbacks -> Orchestrator/State -> UI`

---

## 3. Implementation Phases

### Phase 1: Foundation & Dependencies
*   **Step 1.1**: Add `acpex` to `mix.exs`.
    ```elixir
    {:acpex, "~> 0.1.0"}
    ```
*   **Step 1.2**: Create `Genie.ACP.Client` module.
    *   Implement `@behaviour ACPex.Client`.
    *   Handle `handle_session_update/2` to capture `agent_message_chunk` and `agent_thought_chunk`.
    *   Implement `handle_fs_read_text_file/2` and `handle_terminal_create/2` to bridge agent requests to our system.

### Phase 2: Orchestrator Refactor
*   **Step 2.1**: Update `Genie.CommandOrchestrator` to manage ACP connections.
    *   Maintain a mapping of `agent_name -> conn_pid`.
    *   Implement an `initialize_agent(agent_name)` function.
*   **Step 2.2**: Implement Session Management.
    *   Call `session/new` on agent startup and store the `sessionId`.
    *   Update `genie_state.json` to track active ACP sessions.

### Phase 3: Streaming & UI Updates
*   **Step 3.1**: Enhance `Genie.State` to support granular message updates.
    *   Instead of replacing the whole message, support appending chunks to the last message.
*   **Step 3.2**: Update `DashboardLive`.
    *   Differentiate between "Thought" blocks (reasoning) and "Message" blocks (final output) in the chat UI.
    *   Show active tool calls with status (pending/running/done).

### Phase 4: Tool Approval Workflow
*   **Step 4.1**: Implement a permission gate in `Genie.ACP.Client`.
    *   When an agent calls `fs/write_text_file` or `terminal/create`, pause and broadcast a "Permission Requested" event.
*   **Step 4.2**: Add an "Approve/Deny" UI component to the Dashboard.
    *   Allow the user to see the proposed file change (diff) before the agent applies it.

### Phase 5: Multi-Agent Support
*   **Step 5.1**: Add configuration for multiple agents in `runtime.exs`.
    ```elixir
    config :genie, :agents,
      opencode: [path: "opencode", args: ["acp"]],
      gemini: [path: "gemini", args: ["--experimental-acp"]]
    ```
*   **Step 5.2**: Implement a dropdown in the UI to switch between active agents.

---

## 4. Technical Challenges & Considerations
1.  **Standardization vs. Variation**: While ACP is a standard, some agents (like Gemini) might send custom notification types. The `Genie.ACP.Client` must be resilient to unknown notification methods.
2.  **State Persistence**: Sessions should survive LiveView crashes. We should store the `sessionId` and `conn_pid` in a registry or a persistent GenServer.
3.  **Timeout Management**: Long-running prompts (especially those involving complex coding tasks) need generous timeouts in `ACPex.Protocol.Connection.send_request`.

## 5. Success Criteria
*   [ ] `acpex` is successfully integrated and connecting to `opencode acp`.
*   [ ] User messages are sent via `session/prompt` and responses stream in real-time.
*   [ ] The UI displays the agent's "thoughts" separately from the final response.
*   [ ] Agent tool calls (e.g., reading a file) are successfully fulfilled by our client implementation.
