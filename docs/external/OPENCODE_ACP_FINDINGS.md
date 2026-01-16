# OpenCode ACP & Agent Client Protocol (ACP) Findings

This document summarizes findings regarding the **Agent Client Protocol (ACP)** and its implementation in **OpenCode**.

## 1. What is ACP (Agent Client Protocol)?
The **Agent Client Protocol (ACP)** is an open, JSON-RPC 2.0 based protocol designed to standardize communication between **Clients** (typically code editors like Zed, Neovim, or JetBrains) and **Agents** (AI coding assistants like OpenCode, Claude Code, etc.).

### Key Characteristics:
*   **Standardized Interface**: Acts as the "LSP for AI Agents".
*   **Transport**: Primarily uses **stdio** (stdin/stdout) for local communication.
*   **Bidirectional**: Both Client and Agent can send requests and notifications.
*   **Stateful**: Built around the concept of **Sessions**.
*   **Governance**: Originally developed by IBM (as part of `i-am-bee`) and Zed Industries, now part of the **Linux Foundation's Agent2Agent (A2A)** protocol suite.

---

## 2. OpenCode's Implementation (`opencode acp`)
OpenCode implements the ACP server through the `opencode acp` command. This starts a headless server that can be integrated into any ACP-compatible client.

### Features:
*   **Tool Bridging**: Maps OpenCode's internal tools (Bash, Read, Edit) to ACP tool kinds.
*   **Streaming Support**: Streams text, thought/reasoning chunks, and tool call status updates.
*   **Session Persistence**: Allows creating new sessions or loading existing ones via `session/new` and `session/load`.
*   **Permissions**: Integrates with OpenCode's permission system for sensitive file/terminal operations.

---

## 3. Protocol Architecture & Methods
ACP uses **JSON-RPC 2.0**. Below are the primary methods used in the lifecycle:

### Handshake & Session
1.  **`initialize`**: Exchange capabilities between Client and Agent.
2.  **`session/new`**: Request the agent to start a new stateful session.
3.  **`session/load`**: Resume a previous session.

### Interaction
*   **`session/prompt_turn`**: The client sends a user prompt to the agent.
*   **`session/update` (Notification)**: The agent sends streaming updates.
    *   `message_part_updated`: Used for streaming text or tool call output.
    *   `status_updated`: Updates on the agent's current activity (e.g., "thinking", "calling tool").

### Client-Side Callbacks (Agent-to-Client)
The agent can request the client to perform operations on its behalf:
*   **`fs/read_text_file`**: Read a file from the user's project.
*   **`fs/write_text_file`**: Apply changes to a file.
*   **`terminal/create`**: Execute a command in a terminal managed by the client.

---

## 4. Elixir Integration via `acpex`
For Elixir-based applications (like the one we are building), the **`acpex`** library provides a robust implementation of the protocol.

### Library Details:
*   **Name**: `acpex` (Hex.pm)
*   **Author**: `lostbean`
*   **GitHub**: `https://github.com/lostbean/acpex`

### Example Client Usage:
```elixir
# Start an ACP client that connects to OpenCode
{:ok, client_pid} = ACPex.start_client(MyClientModule, [],
  agent_path: "opencode",
  agent_args: ["acp"]
)

# Interaction flow (managed via ACPex callbacks)
# 1. handle_session_update/2 for streaming text
# 2. handle_fs_read_text_file/2 to allow agent to read project files
```

---

## 5. Comparison: ACP vs. MCP
While similar in name, they serve different roles in the AI ecosystem:

| Feature | **MCP (Model Context Protocol)** | **ACP (Agent Client Protocol)** |
|---------|---------------------------------|---------------------------------|
| **Focus** | Connecting Models to Tools/Data | Connecting Agents to Clients/Editors |
| **OpenCode Role** | **Client** (consumes tools) | **Server** (provides agent service) |
| **State** | Mostly Stateless | Stateful (Sessions) |
| **Primary Goal** | Tool Interoperability | Editor-Agent Integration |

---

## 7. Gemini CLI ACP Capabilities
The **Gemini CLI** (google-gemini) was the reference implementation for ACP and offers several advanced capabilities.

### Starting Gemini in ACP Mode
*   **Command**: `gemini --experimental-acp`
*   When run with this flag, it switches from TUI mode to a headless JSON-RPC server over stdio.

### Key Capabilities:
*   **Multi-modal Support**: Declares support for `image`, `audio`, and `embeddedContext` during the handshake.
*   **MCP Client Bridge**: Can connect to external MCP servers (HTTP/SSE) and utilize their tools within the ACP session.
*   **Hierarchical Context**: Automatically loads instructions from `GEMINI.md` files in the project structure.
*   **Authentication**: Supports OAuth (Google Login), API Keys, and Vertex AI.

## 8. Summary Comparison: OpenCode vs. Gemini CLI

| Feature | OpenCode | Gemini CLI |
| :--- | :--- | :--- |
| **ACP Support** | `opencode acp` | `gemini --experimental-acp` |
| **Multi-modal** | Primarily Text | Text, Image, Audio |
| **MCP Role** | Server (Internal Tool Bridge) | Client Bridge (Connects to external MCP) |
| **Language** | Elixir / Rust | TypeScript / Node.js |
| **Context** | Session-based / `.opencode` | Hierarchical `GEMINI.md` |

## 9. Next Steps for Implementation
1.  **Integrate `acpex`**: Add `acpex` to `mix.exs`.
2.  **Define Client Behaviour**: Create a module implementing `@behaviour ACPex.Client`.
3.  **Command Orchestration**: Update `Genie.CommandOrchestrator` to support ACP-based communication instead of raw shell execution.
4.  **UI Updates**: Modify the Dashboard to handle streaming `agent_message_chunk` and `agent_thought_chunk` notifications.
