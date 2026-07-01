# Subagent 使用完全指南

## 目录
1. [什么是 Subagent](#什么是-subagent)
2. [何时使用 Subagent](#何时使用-subagent)
3. [任务委派粒度](#任务委派粒度)
4. [提示词编写技巧](#提示词编写技巧)
5. [Agent 类型选择](#agent-类型选择)
6. [高级用法](#高级用法)
7. [与 Workflow 的对比](#与-workflow-的对比)
8. [常见问题](#常见问题)

---

## 什么是 Subagent

Subagent 是独立的 AI agent 实例，用于执行特定的子任务。主 agent（你正在交互的 Claude）可以启动 subagent 来：

- 保持主会话的 context 清洁（避免被大量文件内容填满）
- 并行执行独立任务
- 使用专门的 agent 类型（如只读搜索、代码审查等）
- 隔离复杂的多步骤任务

**关键特性：**
- Subagent 有自己的 context window
- Subagent 可以使用所有工具（除非是受限的专用类型）
- Subagent 的**最终消息**会返回给主 agent（中间过程不可见）
- Subagent 可以在后台运行，完成后通知主 agent

---

## 何时使用 Subagent

### 应该使用 Subagent

#### 1. 广泛的代码库搜索
当你需要在大量文件中搜索信息，但不知道确切位置时。

**为什么：** Explore agent 专门用于广泛搜索，读取文件摘录而非完整内容，只返回结论。

#### 2. 复杂的多步骤任务
需要多个工具调用和决策的任务：

**为什么：** 主 agent 只需要最终结论，不需要看到所有中间搜索和读取的过程。

#### 3. 并行的独立任务
多个不相关的任务可以同时执行：

**为什么：** 三个任务完全独立，可以并行执行以节省时间。

#### 4. 深度研究任务
需要阅读大量文档或外部资源的任务：

**为什么：** 研究过程会产生大量上下文，委派给 subagent 可以保持主会话清洁。

## 任务委派粒度

### 粒度原则

**太粗（不好）：**
问题：任务太大，subagent 可能需要多次交互或超出 context 限制。

**太细（不好）：**
问题：启动多个 subagent 的开销大于直接执行。

**刚好（好）：**
```javascript
Agent({
  prompt: `Analyze the authentication system:
  1. Find all authentication-related files
  2. Read and understand the current implementation
  3. Identify security issues or improvement opportunities
  4. Provide a summary with specific recommendations
  
  Return: {
    files: [...],
    currentApproach: "...",
    issues: [...],
    recommendations: [...]
  }`,
  description: 'Analyze auth system'
})
```
这是一个完整的、可独立完成的分析任务。

### 粒度判断标准

| 标准 | 说明 |
|------|------|
| **自包含** | 任务有明确的输入和输出，不需要主 agent 中途干预 |
| **复杂度适中** | 3-10 个步骤，不是一个简单操作，也不是整个项目 |
| **边界清晰** | 明确知道什么时候任务完成了 |
| **独立性** | 不依赖主 agent 的 context 或状态 |

### 好的委派示例

```javascript
// 好：明确的分析任务
Agent({
  prompt: `Analyze the performance of the /api/users endpoint:
  - Find the endpoint implementation
  - Trace database queries
  - Check for N+1 queries or missing indexes
  - Measure estimated response time with current data
  
  Return: bottlenecks found and optimization suggestions.`,
  description: 'Performance analysis'
})

// 好：结构化的搜索任务
Agent({
  prompt: `Find all usages of the deprecated 'getUserData' function:
  - Search across all TypeScript files
  - For each usage, note: file, line number, context
  - Categorize by usage pattern (direct call, prop passing, etc.)
  
  Return: structured list of usages grouped by pattern.`,
  description: 'Find deprecated usage'
})

// 好：完整的验证任务
Agent({
  prompt: `Verify that all API endpoints have authentication:
  - Find all route definitions
  - Check if each has auth middleware
  - List any unprotected endpoints
  
  Return: security audit report with unprotected endpoints.`,
  description: 'Auth audit'
})
```

---

## 提示词编写技巧

### 核心原则

Subagent 的提示词应该：
1. **明确任务目标**
2. **列出具体步骤**（可选但推荐）
3. **指定返回格式**
4. **提供必要的上下文**

### 模板结构

```javascript
Agent({
  prompt: `
[任务描述 - 一句话说明目标]

[背景上下文 - 如果需要]
- 当前状态/问题
- 相关约束条件

[具体步骤 - 引导 subagent 的执行路径]
1. 第一步
2. 第二步
3. 第三步

[输出要求 - 明确返回什么]
Return: [具体的数据结构或格式]
`,
  description: '[简短的任务标签，5-10 词]'
})
```

### 示例对比

#### 糟糕的提示词

```javascript
Agent({
  prompt: 'Check the code',
  description: 'Check code'
})
```

**问题：**
- 太模糊：检查什么？
- 没有范围：哪些文件？
- 没有输出要求：返回什么格式？

#### 优秀的提示词

```javascript
Agent({
  prompt: `Review the authentication code for security vulnerabilities.

Context: We recently added OAuth support and want to ensure no security issues were introduced.

Steps:
1. Find all files in src/auth/
2. Check for common vulnerabilities:
   - SQL injection in login queries
   - XSS in user input handling
   - Insecure token storage
   - Missing rate limiting
3. Review the OAuth implementation against OWASP guidelines

Return a structured report:
{
  filesReviewed: [...],
  vulnerabilities: [
    {
      file: "...",
      line: 123,
      severity: "high|medium|low",
      issue: "...",
      recommendation: "..."
    }
  ],
  summary: "..."
}`,
  description: 'Security review of auth code'
})
```

### 提示词技巧

#### 1. 使用结构化输出

当需要计算机读取和处理时应该明确要求 JSON 或结构化格式：

#### 2. 提供示例

当输出格式复杂时，给出示例：

#### 3. 设定约束条件

明确限制和要求：

#### 4. 分解复杂任务

对于复杂任务，明确列出步骤：

#### 5. 指定搜索范围

缩小搜索范围以提高效率：

---

## Agent 类型选择

不同的 agent 类型有不同的工具集和专长。

### Agent 类型示例

#### 1. **通用 Agent** (默认)

**工具：** 所有工具（Read, Write, Edit, Bash, Agent, 等）  
**适用：** 需要读写文件、执行命令的通用任务

- 实现新功能
- 修复 bug
- 重构代码

#### 2. **Explore Agent** (只读搜索)

**工具：** 只读工具（Read, Bash, WebSearch 等），**不能** Edit/Write  
**特性：** 读取文件摘录而非完整内容，专为广泛搜索优化  
**适用：** 代码库探索、查找文件、理解架构

**何时使用：**
- "找到所有使用 X 的地方"
- "理解 Y 系统的架构"
- "搜索包含 Z 的文件"
- 不要用于需要修改文件的任务

#### 3. **Plan Agent** (架构设计)

**工具：** 只读工具（不能 Edit/Write）  
**适用：** 设计实现方案、架构决策、计划制定

**何时使用：**
- 设计新功能的实现方案
- 重大重构的规划
- 技术决策分析
- 不要用于实际实现代码

---

## 高级用法

### 1. 后台运行

对于长时间运行的任务，可以让 subagent 在后台执行：

**特点：**
- Subagent 在后台运行，主 agent 可以继续其他工作
- 完成时会收到通知：`<task-notification>`
- 适用于：测试运行、构建、部署等耗时任务

### 2. 继续之前的 Subagent

如果需要与已完成的 subagent 继续对话：

### 3. 使用 Worktree 隔离

当 subagent 需要修改文件但不想影响主分支, 可以创建 worktree 再委派 Agent 到 worktree 工作：

**特点：**
- 在独立的 git worktree 中工作
- 不影响主工作目录
- 完成后可以查看 diff 再决定是否合并

### 4. 指定模型

某些任务可能需要特定模型：

**注意：** 大多数情况下应该省略 `model`，让 subagent 继承会话模型。

---

## 与 Workflow 的对比

### Subagent vs Workflow

| 特性 | Subagent | Workflow |
|------|----------|----------|
| **用途** | 单个任务委派 | 多 agent 编排 |
| **复杂度** | 简单 | 复杂 |
| **并发** | 手动启动多个 | 内置 pipeline/parallel |
| **结构化输出** | 需要在 prompt 中要求 | 内置 schema 支持 |
| **用户授权** | 不需要特殊授权 | 需要明确授权（ultracode） |
| **Token 成本** | 单个 agent 成本 | 可能是几十到上百个 agent |
| **适用场景** | 单个分析/搜索任务 | 大规模批处理、多维度分析 |


### 何时用 Subagent
- 单个搜索任务
- 单个分析任务

### 何时用 Workflow
- 多维度审查
- 批量处理

### 混合使用

主 agent 可以先用 subagent 探索，再用 workflow 处理：

---

## 常见问题

### Q1: Subagent 会看到主会话的 context 吗？

**A:** 不会。Subagent 有独立的 context window，只能看到：
- 它自己的 prompt
- 项目文件（通过 Read 工具读取）
- 命令输出（通过 Bash 工具执行）

它看不到主会话的对话历史。

**解决方案：** 在 prompt 中提供必要的上下文。

### Q2: 如何获取 Subagent 的详细执行日志？

**A:** Subagent 的中间过程对主 agent 不可见，只能看到最终输出。

如果需要详细日志：
1. 在 prompt 中要求 subagent 返回执行步骤
2. 或者主 agent 自己执行任务（不用 subagent）

### Q3: Subagent 可以启动其他 Subagent 吗？

**A:** 可以。Subagent 可以使用 Agent 工具启动其他 subagent，但要注意：
- 嵌套会增加复杂度
- Token 成本会倍增
- 通常不推荐超过 2 层嵌套

### Q4: 如何限制 Subagent 的执行时间？

**A:** 目前没有直接的超时参数。可以：
1. 在 prompt 中设定明确的范围限制
2. 使用 `run_in_background: true` 并手动停止
3. 在 prompt 中要求 "快速分析" 或 "初步扫描"

### Q5: Subagent 失败了怎么办？

**A:** Subagent 可能因为以下原因失败：
- Context window 超限
- 任务太复杂
- 权限被拒绝
- API 错误

**处理方法：**
1. 检查返回的 error 信息
2. 简化任务或缩小范围
3. 分解为多个小任务
4. 或者主 agent 自己执行

### Q6: 如何优化 Token 成本？

**建议：**
1. **使用 Explore agent** 进行搜索（读取摘录而非完整文件）
2. **明确范围** - 不要让 subagent 搜索整个代码库
3. **结构化输出** - 只返回需要的信息
4. **批量处理** - 一个 subagent 处理多个相似任务，而不是启动多个
5. **选择合适的模型** - 简单任务用 haiku

### Q7: Subagent 和普通函数调用有什么区别？

**A:** Subagent 是完整的 AI agent，有自己的推理能力和工具访问权限。它不是简单的函数调用。

| 特性 | Subagent | 函数调用 |
|------|----------|----------|
| 智能 | 有推理能力 | 机械执行 |
| 灵活性 | 可以调整策略 | 固定逻辑 |
| 工具使用 | 可用各种工具 | 有限的功能 |
| 成本 | 消耗 tokens | 免费 |
| 速度 | 较慢（需推理） | 快速 |

**何时用 Subagent：**
- 需要搜索和探索
- 需要理解和分析
- 需要做决策

**何时用工具/函数：**
- 确切知道要做什么
- 机械性操作
- 简单的读写操作
