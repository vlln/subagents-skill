# Swarm 设计文档

## 目录

1. [设计目标](#设计目标)
2. [架构分层](#架构分层)
3. [进度数据层](#进度数据层)
4. [事件采集层](#事件采集层)
5. [Swarm 编排层](#swarm-编排层)
6. [渲染层](#渲染层)
7. [CLI 后端退化策略](#cli-后端退化策略)
8. [与 Workflow 的集成](#与-workflow-的集成)

---

## 设计目标

`subagents swarm` 提供与 kimi-code `AgentSwarm` 等价的 CLI 能力：一个命令批量并发启动多个 subagent，模板化 prompt，实时追踪进度，汇总结果。

核心原则：

- **进度计算与渲染解耦** — 数据层（`ProgressEstimator`）纯数学，渲染层只消费 `ProgressEstimate`。
- **后端无关** — ACP 后端享受完整进度，CLI 后端自动退化。
- **可复用** — 进度数据层可被 `workflow`、未来 Web UI 等任何上层消费。

---

## 架构分层

```
┌──────────────────────────────────────────────┐
│  渲染层                                       │
│  lib/swarm/renderer_tui.py                   │
│  - braille 条、网格布局、实时文字              │
│  - 只消费 ProgressEstimate，不自己算          │
├──────────────────────────────────────────────┤
│  编排层                                       │
│  lib/swarm/orchestrator.py                   │
│  - 模板展开、并发调度、结果汇总                │
│  - 调用采集层喂事件给数据层                    │
├──────────────────────────────────────────────┤
│  进度数据层 (纯数学)                           │
│  lib/progress/estimator.py                   │
│  lib/progress/types.py                       │
│  - 指数衰减速率估计                            │
│  - 先验构建 + 置信度加权 boost                 │
│  - 指数衰减动画 catch-up                      │
│  - 零外部依赖，纯 Python                      │
├──────────────────────────────────────────────┤
│  事件采集层                                   │
│  lib/backends/acp_backend.py (扩展)           │
│  - 监听 ACP notification: tool_call,          │
│    agent_message_chunk, turn_complete 等      │
│  - 回调通知上层                                │
│  lib/backends/cli_backend.py (无扩展)          │
│  - 只有 start/done，无中间事件                 │
└──────────────────────────────────────────────┘
```

---

## 进度数据层

### 类型定义 (`lib/progress/types.py`)

```python
from dataclasses import dataclass, field
from enum import Enum

class Phase(Enum):
    PENDING   = "pending"
    QUEUED    = "queued"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"

@dataclass
class ProgressEstimate:
    """每次 estimate() 调用的输出，渲染层只消费这个"""
    raw_ticks: int              # 实际 tool call 次数
    display_ticks: float        # 动画后的显示值 (用于 braille 条)
    estimated_total: int | None # 预估总 tool call 数 (None = 无先验)
    progress: float             # 0.0 ~ 1.0
    confidence: float           # 0.0 ~ 1.0 (先验可信度)
    phase: Phase
    boosted: bool               # 是否被 boost 过
    duration_ms: float          # 从 started 到现在的活跃耗时
```

### Estimator 接口 (`lib/progress/estimator.py`)

```python
class ProgressEstimator:
    """
    纯数据层。输入事件，输出估计值。不涉及任何 I/O 或渲染。

    算法：
      1. 从已完成的 session 构建先验 (典型耗时、典型 tool call 数、典型速率)
      2. 对运行中的 session，用指数衰减加权计算本地速率
      3. 本地速率和先验速率做几何插值，估计总 tool call 数
      4. 置信度加权：样本越多、观测越多 → boost 越大
      5. 指数衰减动画：display_ticks 向 target 平滑追赶
    """

    # 默认参数
    RATE_WINDOW_MS = 45_000        # 速率衰减窗口
    CATCHUP_TIME_MS = 1_500        # 动画追赶时间常数
    WORKLOAD_SPREAD_FACTOR = 1.5   # 总 tool call 软边界系数
    UNFINISHED_PROGRESS_CAP = 0.85 # 进度上限（保留余量）
    MAX_BOOST_GAIN = 0.75          # 最大 boost 幅度

    # ── 事件输入 ──

    def ensure_member(self, session_id: str, now_ms: float) -> None: ...
    def mark_started(self, session_id: str, now_ms: float) -> None: ...
    def mark_queued(self, session_id: str, now_ms: float) -> None: ...
    def record_tool_call(self, session_id: str, tool_call_id: str, now_ms: float) -> bool: ...
    def mark_completed(self, session_id: str, now_ms: float) -> None: ...
    def mark_failed(self, session_id: str, now_ms: float) -> None: ...
    def mark_cancelled(self, session_id: str, now_ms: float) -> None: ...

    # ── 查询输出 ──

    def estimate(self, session_id: str, capacity: int, now_ms: float) -> ProgressEstimate: ...
    def estimate_all(self, capacity: int, now_ms: float) -> dict[str, ProgressEstimate]: ...
    def has_pending_catchup(self) -> bool: ...
```

### 关键设计决策

| 决策 | 理由 |
|------|------|
| `now_ms` 由调用方传入 | 避免内部取 `time.time()`，方便测试和帧同步 |
| `capacity` 由调用方传入 | braille 条列数由渲染层决定，数据层不关心 |
| `tool_call_id` 去重 | 同一个 tool call 可能被多次通知，只计一次 |
| 暂停时间不计入速率 | 被 rate limit 暂停时，速率不应被拖慢 |
| 几何中位数而非算术平均 | tool call 次数是偏态分布，避免异常值干扰 |

---

## 事件采集层

### ACP 后端扩展 (`lib/backends/acp_backend.py`)

当前 `_on_update` 只处理 `agent_message_chunk`。需扩展为：

```python
def _on_update(self, params: dict) -> None:
    u = params.get("update", {})
    kind = u.get("sessionUpdate", "")

    if kind == "agent_message_chunk":
        c = u.get("content", {})
        if c.get("type") == "text":
            self._emit("text_delta", text=c["text"])
        elif c.get("type") == "thinking":
            self._emit("thinking_delta", text=c["text"])

    elif kind == "tool_call":
        self._emit("tool_call_started",
                    tool_call_id=u.get("toolCallId", ""),
                    tool_name=u.get("toolName", ""))

    elif kind == "tool_result":
        self._emit("tool_call_finished",
                    tool_call_id=u.get("toolCallId", ""))

    elif kind == "agent_status":
        self._emit("status", status=u.get("status", ""),
                    usage=u.get("usage"))

    elif kind == "turn_complete":
        self._emit("turn_complete")

    elif kind == "error":
        self._emit("error", message=u.get("message", ""))
```

通过回调机制通知上层，不耦合到具体消费者：

```python
class AcpBackend(BaseBackend):
    def on_event(self, event_type: str, handler: Callable[..., None]) -> None:
        """注册事件回调。swarm 编排层用此接口订阅进度事件。"""
        self._event_handlers[event_type] = handler

    def _emit(self, event_type: str, **kwargs) -> None:
        handler = self._event_handlers.get(event_type)
        if handler:
            handler(**kwargs)
```

### CLI 后端

CLI 后端只有 `start` / `done(failed)` 两个时间点，无中间事件。不需要扩展。

---

## Swarm 编排层

### 命令接口

```bash
subagents swarm <agent> <session_prefix> \
  --template "Review {{item}} for security issues" \
  --items "src/auth.ts" "src/db.ts" "src/api.ts"

# 从文件读取 items
subagents swarm <agent> <session_prefix> \
  --template "Analyze {{item}}" \
  --items-file targets.txt

# 控制并发数
subagents swarm <agent> <session_prefix> \
  --template "..." --items ... --concurrency 5

# 恢复未完成的 swarm
subagents swarm --resume <prefix>
```

### 编排逻辑 (`lib/swarm/orchestrator.py`)

```python
class SwarmOrchestrator:
    def __init__(self, agent: str, prefix: str, template: str, items: list[str],
                 concurrency: int = 0, backend: str | None = None):
        ...

    def run(self) -> dict[str, str]:
        """
        1. 展开模板，为每个 item 生成独立 prompt
        2. 以 <prefix>-001, <prefix>-002, ... 命名 session
        3. 并发启动 (受 concurrency 限制)
        4. 实时订阅 ACP 事件，喂给 ProgressEstimator
        5. 全部完成后汇总结果
        """
        ...

    def estimate(self) -> dict[str, ProgressEstimate]:
        """供渲染层或外部消费者查询当前进度"""
        return self._estimator.estimate_all(...)
```

### 结果汇总

```bash
subagents swarm status --prefix rev
#  rev-001  ✅ done       "Review src/auth.ts"      12 calls  45.2s
#  rev-002  🔄 running    "Review src/db.ts"         8 calls  32.1s
#  rev-003  ❌ failed     "Review src/api.ts"         3 calls  18.7s
#  rev-004  ⏳ queued     "Review src/utils.ts"

subagents swarm result --prefix rev
# 输出 JSON 格式的汇总结果，供 workflow 或脚本消费
```

---

## 渲染层

### 职责

`AgentProgressRenderer` 是一个纯函数渲染器：输入 `estimates`，输出 ANSI 行。不持有任何 session 状态，不调用 `time.time()`，不计算进度。

### 三种布局

```
AgentProgressRenderer
  ├── render_single()   → 单 agent，全宽 braille 条 + 详细文字
  ├── render_grid()     → N 列网格（swarm）
  └── render_list()     → 垂直列表（一行一个 agent）
```

**使用场景：**

| 命令 | 布局 | 说明 |
|------|------|------|
| `subagents run` (ACP, TTY) | `single` | 前台跑一个 agent，替代纯文本流 |
| `subagents wait <session>` | `single` | 等待一个后台 session |
| `subagents swarm` | `grid` | 并发多个 agent |
| `subagents status --watch` | `list` | 实时监控所有 session |

**TTY 检测：**

```python
if sys.stderr.isatty():
    renderer.render_single(estimate, width)   # TUI 进度条
else:
    # 管道/重定向 → 保持现有流式文本输出
```

### 布局效果

```
# single: 全宽，一条 braille 条 + 最后一行模型输出
  ⣿⣿⣿⣿⣿⣶⣀⣀  working...  Reading src/auth.ts (8/12 calls, 32s)

# grid: N 列，紧凑
  001 ⣿⣿⣶⣀  002 ⣿⣿⣿⣿  003 ⣀⣀⣀⣀
      working     ✓ done      ⏳ queued

# list: 垂直，每行有完整信息
  001 ⣿⣿⣿⣿⣿⣶⣀⣀  working    12 calls  45s   reviewer/session-1
  002 ⣿⣿⣿⣿⣿⣿⣿⣿  ✓ completed 15 calls  52s   reviewer/session-2
```

### 接口

```python
class AgentProgressRenderer:
    def __init__(self, *, max_columns: int | None = None):
        """
        max_columns: 网格/列表模式的最大列数。None = 自动按终端宽度计算。
        """

    # ── 三种布局 ──

    def render_single(self, estimate: ProgressEstimate, width: int,
                      label: str = "") -> list[str]:
        """单 agent 全宽渲染。返回 1-2 行 ANSI 字符串。"""

    def render_grid(self, estimates: dict[str, ProgressEstimate],
                    width: int, description: str = "") -> list[str]:
        """网格布局：header + grid + status bar。"""

    def render_list(self, estimates: dict[str, ProgressEstimate],
                    width: int) -> list[str]:
        """垂直列表，每行一个 agent 的完整信息。"""

    # ── 内部 ──

    def _render_cell(self, estimate: ProgressEstimate, cell_width: int,
                     show_text: bool) -> str:
        """渲染一个 cell：ID + braille 条 + 状态标签/文字。"""

    def _braille_bar(self, display_ticks: float, phase: Phase,
                     bar_cells: int) -> str:
        """将 display_ticks 映射为 braille 字符序列。"""

    def _status_icon(self, phase: Phase) -> str:
        """completed → ✓, failed → ✗, running → 无, queued → ⏳"""
```

### 列数策略

`max_columns` 控制网格布局的最大列数：

- `None`（默认）：`columns = floor(width / min_cell_width)`，完全自动
- 指定值：`columns = min(max_columns, floor(width / min_cell_width))`
- `min_cell_width = 15`（compact 模式阈值）

### Braille 条宽度

```python
bar_cells = min(8, max(3, cell_width // 5))
```

cell 宽 15 字符 → 3 字符 braille；25 → 5；40 → 8（上限）。

### Cell 模式切换

```python
show_text = cell_width >= 25
```

`--compact` 标志可强制 `show_text = False`。

### 颜色方案

256 色 ANSI escape：

| 角色 | 色号 | 用途 |
|------|------|------|
| primary | 51 (cyan) | ID、bar 前景 |
| accent | 33 (blue) | 渐变高亮 |
| success | 46 (green) | ✓ 图标 |
| failed | 196 (red) | ✗ 图标 |
| warning | 220 (yellow) | ⏳ 图标 |
| dim | 240 (gray) | 背景 braille、次要文字 |

非 ANSI 终端 fallback：用 `[====>  ]` 代替 braille，纯 ASCII 状态图标。

### 动画驱动

80ms 帧率轮询（约 12.5fps）：

```python
while not all_done:
    estimates = estimator.estimate_all(capacity, time_ms())
    lines = renderer.render_grid(estimates, width, description)
    sys.stderr.write("\r" + "\r\n".join(lines) + ANSI_CURSOR_UP * len(lines))
    sys.stderr.flush()
    time.sleep(0.08)
```

---

## CLI 后端退化策略

CLI 后端无中间事件，`ProgressEstimator` 只能收到 `mark_started` 和 `mark_completed/failed`。

| 能力 | ACP 后端 | CLI 后端 |
|------|---------|---------|
| braille 进度条 | ✅ tick 驱动 | ❌ 只显示状态 + 耗时 |
| 实时文字 | ✅ agent_message_chunk | ❌ |
| 进度预测 | ✅ 先验+boost | ❌ 无 tick 数据 |
| running/done/failed | ✅ | ✅ |
| elapsed time | ✅ 精确到活跃时间 | ✅ 粗略 wall time |
| 结果汇总 | ✅ | ✅ |

CLI 后端渲染自动检测 `Phase` 只经历 `QUEUED → RUNNING → COMPLETED/FAILED`，跳过 braille 条，只渲染状态图标 + 耗时：

```
# single (CLI)
  ⏳ running 12s

# grid (CLI)
  001 ⏳ 12s    002 ✓ done   003 ⏳ 8s
```

---

## 与 Workflow 的集成

### workflow 消费进度数据

```python
# workflow 脚本中
def run(agent, parallel, pipeline, phase, log, args, workflow):
    phase("Review")
    swarm = workflow.swarm("reviewer", "rev", template="...", items=files)

    while not swarm.done():
        estimates = swarm.estimate()
        progress_pct = sum(e.progress for e in estimates.values()) / len(estimates)
        log(f"Progress: {progress_pct:.1%}")
        time.sleep(1)

    return swarm.results()
```

### 数据流

```
                   ┌─────────────────────┐
                   │  ProgressEstimator  │
                   │  (纯数据，无 I/O)    │
                   └────────┬────────────┘
                            │ estimate_all()
                            ▼
    ┌──────────────────────────────────────────┐
    │         AgentProgressRenderer            │
    │  (纯函数：estimates → ANSI lines)         │
    │                                          │
    │  render_single / render_grid / render_list│
    └──────────────┬───────────────────────────┘
                   │
         ┌─────────┼─────────┐
         ▼         ▼         ▼
    subagents   subagents  subagents
      run        swarm      status --watch
         │
         └── workflow.parallel() 进度查询
               └── workflow display panel
```