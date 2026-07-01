# ACP 协议参考

> 来源: [agentclientprotocol/agent-client-protocol](https://github.com/agentclientprotocol/agent-client-protocol) v1
> 用途: 指导 `subagents` skill 的 ACP 后端实现，特别是进度追踪所需的事件类型

---

## 1. 协议概述

ACP (Agent Client Protocol) 是 **代码编辑器 (Client)** 与 **编码 agent (Agent)** 之间的标准化通信协议，基于 JSON-RPC 2.0。

- **Agent** = 使用 LLM 自主修改代码的程序（如 kimi-code、Claude Code）
- **Client** = 代理与用户之间的界面（如 IDE、终端）
- **传输** = stdio 子进程（Agent 作为 Client 的子进程运行）

与我们项目的对应关系：

| ACP 概念 | 我们的项目 |
|----------|-----------|
| Agent | kimi-code, claude, codex 等 |
| Client | `subagents` CLI（通过 `AcpTransport` 连接 Agent） |
| `session/update` 通知 | 进度追踪的事件源 |

---

## 2. 消息流

```
Client (subagents)                          Agent (kimi-code)
      │                                           │
      │──── initialize ─────────────────────────→│  版本协商 + 能力交换
      │←─── agentCapabilities ───────────────────│
      │                                           │
      │──── session/new ────────────────────────→│  创建会话
      │←─── sessionId ──────────────────────────│
      │                                           │
      │──── session/prompt ─────────────────────→│  发送用户消息
      │                                           │
      │←─── session/update (plan) ───────────────│  计划
      │←─── session/update (agent_message_chunk)─│  文本输出（流式）
      │←─── session/update (tool_call) ──────────│  工具调用
      │←─── session/update (tool_call_update) ───│  工具执行中/完成
      │←─── session/update (usage_update) ───────│  token 使用量
      │←─── session/update (agent_message_chunk)─│  更多文本...
      │←─── ...                                  │  循环直到结束
      │←─── session/prompt 响应 ─────────────────│  stopReason: "end_turn"
```

---

## 3. session/update 通知类型

`session/update` 是 Agent → Client 的**单向通知**，携带一个 `update` 对象，其 `sessionUpdate` 字段区分类型。这是进度追踪的**唯一数据源**。

### 3.1 稳定类型 (v1)

| `sessionUpdate` 值 | 说明 | 进度追踪用途 |
|---------------------|------|-------------|
| `agent_message_chunk` | Agent 的文本输出（流式） | 实时文字显示 |
| `user_message_chunk` | 用户消息回放（仅 `session/load`） | 无 |
| `tool_call` | 新的工具调用（`status: "pending"`） | **tick +1** |
| `tool_call_update` | 工具调用状态更新（`in_progress` / `completed` / `failed`） | 完成/失败检测 |
| `plan` | Agent 的计划条目 | 可选：计划进度 |
| `usage_update` | 当前 token 使用量 | 可选：成本估算 |
| `available_commands_update` | 可用的 slash 命令列表 | 无 |

### 3.2 消息格式

**agent_message_chunk:**
```json
{
  "sessionUpdate": "agent_message_chunk",
  "messageId": "msg_agent_c42b9",
  "content": {
    "type": "text",
    "text": "I'll analyze your code..."
  }
}
```
- `content.type` 可以是 `"text"` 或 `"thinking"`（思考过程）
- 相同 `messageId` 的 chunk 属于同一消息

**tool_call:**
```json
{
  "sessionUpdate": "tool_call",
  "toolCallId": "call_001",
  "title": "Reading configuration file",
  "kind": "read",
  "status": "pending"
}
```
- `toolCallId` 是 session 内唯一标识符
- `kind` 可选值: `read`, `edit`, `delete`, `move`, `search`, `execute`, `think`, `fetch`, `other`

**tool_call_update:**
```json
{
  "sessionUpdate": "tool_call_update",
  "toolCallId": "call_001",
  "status": "completed",
  "content": [{"type": "content", "content": {"type": "text", "text": "done"}}]
}
```
- `status`: `in_progress` → `completed` 或 `failed`

**usage_update:**
```json
{
  "sessionUpdate": "usage_update",
  "used": 53000,
  "size": 200000,
  "cost": {"amount": 0.045, "currency": "USD"}
}
```

---

## 4. 工具调用生命周期

```
tool_call (status: "pending")
  │   Agent 宣布要执行工具
  │   → ProgressEstimator.record_tool_call()
  ▼
[tool_call_update (status: "in_progress")]  ← 可选
  │   工具开始执行
  ▼
tool_call_update (status: "completed" | "failed")
  │   工具执行完毕
  │   → 仅计数，不影响进度
```

---

## 5. 当前实现 vs 协议能力

### 当前 `AcpBackend._on_update` 只处理了：

```python
def _on_update(self, params: dict) -> None:
    u = params.get("update", {})
    if u.get("sessionUpdate") == "agent_message_chunk":
        c = u.get("content", {})
        if c.get("type") == "text":
            self._emit_text(c["text"])  # 只消费文本
```

### 缺失的事件处理：

| 事件 | 当前状态 | 进度追踪需要 |
|------|---------|-------------|
| `agent_message_chunk` (type=`"thinking"`) | ❌ 忽略 | 可选：显示思考中 |
| `tool_call` | ❌ 忽略 | **必须：tick 计数** |
| `tool_call_update` | ❌ 忽略 | 可选：完成确认 |
| `plan` | ❌ 忽略 | 可选：计划进度 |
| `usage_update` | ❌ 忽略 | 可选：成本估算 |

### 建议扩展

```python
def _on_update(self, params: dict) -> None:
    u = params.get("update", {})
    kind = u.get("sessionUpdate", "")

    if kind == "agent_message_chunk":
        c = u.get("content", {})
        if c.get("type") == "text":
            self._emit("text", text=c["text"])
        elif c.get("type") == "thinking":
            self._emit("thinking", text=c["text"])

    elif kind == "tool_call":
        self._emit("tool_call_started",
                    tool_call_id=u.get("toolCallId", ""),
                    title=u.get("title", ""),
                    kind=u.get("kind", "other"))

    elif kind == "tool_call_update":
        self._emit("tool_call_finished",
                    tool_call_id=u.get("toolCallId", ""),
                    status=u.get("status", ""))

    elif kind == "usage_update":
        self._emit("usage", used=u.get("used", 0), size=u.get("size", 0))
```

---

## 6. 与 `subagents` skill 的集成点

### 6.1 进度追踪

`ProgressEstimator` 通过 `AcpBackend` 的回调消费事件：

```
ACP 事件                   ProgressEstimator 方法
──────────                 ────────────────────
session/prompt 开始        mark_started()
tool_call                  record_tool_call()
agent_message_chunk(text)  文本缓存（给渲染层）
tool_call_update(status)   不影响 tick（仅确认）
session/prompt 响应         mark_completed() / mark_failed()
```

### 6.2 Swarm 并发

ACP 协议允许多个 session 同时存在。swarm 模式下：
- 每个 item 创建一个独立 session
- 所有 session 的 `session/update` 通知并发到达
- 通过 `sessionId` 路由到对应的 `ProgressEstimator` member

### 6.3 取消

```json
{"method": "session/cancel", "params": {"sessionId": "..."}}
```

Agent 收到后中止所有操作，以 `"stopReason": "cancelled"` 响应 `session/prompt`。

---

## 7. kimi-code 特有行为

kimi-code 的 ACP 实现不完全遵循标准。已知差异：

- `session/update` 中的 `sessionUpdate` 值可能使用不同的命名（如不带下划线）
- `agent_message_chunk` 的 `content.type` 可能包含 `"tool_call"` 等非标准类型
- 认证方式: kimi-code 使用 `terminal` 类型的 `authMethod`（--login 流程）

**建议**: 在 `AcpBackend` 中保留 `_on_update` 的灵活性，允许后端特定的 `sessionUpdate` 值透传。

---

## 8. 参考链接

- 官方文档: https://agentclientprotocol.com/
- 协议规范: https://agentclientprotocol.com/protocol/v1/overview
- JSON Schema: https://github.com/agentclientprotocol/agent-client-protocol/tree/main/schema/v1
- Python SDK: https://github.com/agentclientprotocol/python-sdk