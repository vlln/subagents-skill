# Workflow 设计完整指南

## 目录
1. [什么是 Workflow](#什么是-workflow)
2. [核心概念](#核心概念)
3. [元数据定义](#元数据定义)
4. [核心 API](#核心-api)
5. [设计模式](#设计模式)
6. [高级特性](#高级特性)
7. [性能优化](#性能优化)
8. [调试与故障排查](#调试与故障排查)
9. [最佳实践](#最佳实践)

---

## 什么是 Workflow

Workflow 是一个**多 agent 编排框架**，用于将复杂任务分解为多个独立的子任务，并通过多个 AI agent 并行或流水线式地执行。

### 适用场景

✅ **应该使用 Workflow 的场景：**
- 需要从多个角度分析同一问题（如代码审查：安全、性能、风格）
- 需要处理大量相似任务（如批量迁移、批量测试）
- 需要多步骤流水线处理（如理解 → 设计 → 实现 → 验证）
- 单个 context 无法容纳的大规模任务（如全代码库审计）
- 用户明确要求使用多 agent 或 workflow

❌ **不应该使用 Workflow 的场景：**
- 简单的单文件修改
- 用户没有明确授权多 agent 执行（会消耗大量 token）
- 任务可以通过单个 Agent 工具完成

### Ultracode 模式

当系统提示中出现 `ultracode` 关键词或模式开启时，应该默认为每个实质性任务编写和运行 workflow，不受 token 成本限制。

---

## 核心概念

### 1. Meta 对象（必需）

每个 workflow 脚本**必须**以 `export const meta` 开头：

```javascript
export const meta = {
  name: 'workflow-name',              // 必需：kebab-case 命名
  description: '一行描述',             // 必需：用于权限对话框
  whenToUse: '何时使用此 workflow',    // 可选：在列表中显示
  phases: [                            // 可选但推荐：定义阶段
    { 
      title: 'Scan',                   // 阶段标题
      detail: '扫描变更文件',           // 阶段详情
      model: 'haiku'                   // 可选：此阶段使用的模型
    },
    { title: 'Review', detail: '并行审查' },
    { title: 'Synthesis', detail: '综合结果' },
  ],
}
```

⚠️ **重要约束：**
- `meta` 必须是**纯字面量**（不能使用变量、函数调用、展开运算符、模板字符串）
- `phases` 中的 `title` 必须与 `phase()` 调用中的标题**完全匹配**

### 2. Phase（阶段）

Phase 用于将 workflow 分组为逻辑阶段，提供更好的进度显示：

```javascript
phase('Scan')  // 开始新阶段
// 后续的 agent() 调用会被分组到这个阶段下

phase('Review')
// 新阶段开始
```

### 3. Agent 调用

`agent()` 是 workflow 的核心执行单元，生成一个子 agent：

```javascript
const result = await agent(prompt, options)
```

**返回值：**
- 无 `schema`：返回 agent 的最终文本输出（字符串）
- 有 `schema`：返回验证后的结构化对象
- agent 失败或用户跳过：返回 `null`

---

## 核心 API

### agent(prompt, opts)

生成一个子 agent 执行任务。

```javascript
const result = await agent(
  'Your detailed prompt here',
  {
    label: 'Display Label',        // 可选：显示标签
    phase: 'Review',               // 可选：明确指定阶段（用于 pipeline/parallel 内）
    schema: JSON_SCHEMA,           // 可选：强制结构化输出
    model: 'opus',                 // 可选：模型覆盖（慎用）
    effort: 'medium',              // 可选：推理努力度 low/medium/high/xhigh/max
    isolation: 'worktree',         // 可选：git worktree 隔离（昂贵！仅并行写文件时）
    agentType: 'code-reviewer',    // 可选：使用自定义 agent 类型
  }
)
```

**参数说明：**

- **prompt**: 任务描述，要具体明确
- **schema**: JSON Schema 对象，指定后 agent 必须调用 StructuredOutput 工具，返回验证后的对象
- **model**: 模型覆盖。**默认省略**（继承会话模型）。仅在确信需要不同层级时设置
- **effort**: 推理努力度。机械任务用 `'low'`，最难的验证/判断阶段用更高层级
- **isolation**: `'worktree'` 在独立 git worktree 中运行（~200-500ms 开销），**仅在多个 agent 并行修改文件会冲突时使用**
- **agentType**: 使用自定义 agent 类型（如 'Explore'、'code-reviewer'），可与 schema 组合

**返回值：**
- 无 schema：字符串（agent 的最终输出）
- 有 schema：验证后的对象
- 失败/跳过：`null`（使用 `.filter(Boolean)` 过滤）

### pipeline(items, ...stages)

**流水线模式**：每个 item 独立经过所有 stage，**stage 之间没有屏障**。

```javascript
const results = await pipeline(
  ['file1.js', 'file2.js', 'file3.js'],
  
  // Stage 1: 分析
  async (item, idx) => {
    return await agent(`Analyze ${item}`, { schema: ANALYSIS_SCHEMA })
  },
  
  // Stage 2: 修复（使用前一阶段结果）
  async (prevResult, originalItem, idx) => {
    if (!prevResult) return null  // 前一阶段失败
    return await agent(`Fix issues in ${originalItem}: ${JSON.stringify(prevResult)}`)
  },
  
  // Stage 3: 验证
  async (prevResult, originalItem, idx) => {
    return await agent(`Verify ${originalItem} is fixed`)
  }
)
```

**关键特性：**
- **无屏障**：file1 可以在 stage 3，同时 file2 在 stage 1
- **墙钟时间** = 最慢单个 item 的链条时间（不是各阶段最慢之和）
- 每个 stage 回调接收：`(prevResult, originalItem, index)`
- 抛出异常的 item 变为 `null` 并跳过剩余 stage

**何时使用：**
- 批量处理：迁移多个文件、测试多个组件
- 多步骤转换：扫描 → 分析 → 修复 → 验证

### parallel(thunks)

**并行屏障**：同时运行多个任务，**等待全部完成**后返回。

```javascript
const results = await parallel([
  async () => agent('Security review'),
  async () => agent('Performance review'),
  async () => agent('Style review'),
])

// results: [securityResult, perfResult, styleResult]
// 失败的 thunk 返回 null
const validResults = results.filter(Boolean)
```

**关键特性：**
- **屏障**：必须等所有 thunk 完成
- 失败的 thunk 解析为 `null`（不会抛出异常）
- 返回数组，顺序与输入顺序一致

**何时使用：**
- 多角度分析需要汇总（如示例中的三维度审查）
- 独立任务需要同步点

⚠️ **注意：** `parallel()` 是屏障，会等待最慢的任务。如果任务是独立的多步骤流程，优先使用 `pipeline()`。

### log(message)

向用户输出进度消息（显示为 narrator 行）：

```javascript
log('Starting security scan...')
log(`Found ${count} vulnerabilities`)
```

### phase(title)

开始新阶段，后续 agent 调用会分组显示：

```javascript
phase('Scan')
const files = await agent('List changed files')

phase('Review')  // 新阶段开始
const issues = await agent('Review files')
```

⚠️ **title 必须与 `meta.phases` 中的 `title` 完全匹配**

### args

访问 Workflow 调用时传入的参数：

```javascript
// 调用 workflow 时：
// Workflow({ script: '...', args: { targetDir: '/src', depth: 3 } })

// 脚本中：
const targetDir = args.targetDir || '/default'
const depth = args.depth || 1

log(`Scanning ${targetDir} with depth ${depth}`)
```

⚠️ **重要：** 将数组/对象作为实际 JSON 值传递，**不要**作为 JSON 字符串：
```javascript
// ✅ 正确
args: ["a.ts", "b.ts"]

// ❌ 错误（会导致 args.filter/args.map 失败）
args: "[\"a.ts\", \"b.ts\"]"
```

### budget

访问 token 预算（当用户设置 `+500k` 等指令时）：

```javascript
budget.total      // number | null（未设置时为 null）
budget.spent()    // 本次会话已消耗的输出 token
budget.remaining() // max(0, total - spent()) 或 Infinity

// 动态循环
while (budget.total && budget.remaining() > 50_000) {
  await agent('Process next batch')
}

// 静态扩展
const FLEET_SIZE = budget.total ? Math.floor(budget.total / 100_000) : 5
```

⚠️ **预算是硬上限**：`spent()` 达到 `total` 后，`agent()` 调用会抛出异常。

### workflow(nameOrRef, args)

内联运行另一个 workflow 作为子步骤：

```javascript
// 通过名称调用已保存的 workflow
const result = await workflow('saved-workflow-name', { param: 'value' })

// 通过路径调用脚本文件
const result = await workflow(
  { scriptPath: '/path/to/script.js' },
  { param: 'value' }
)
```

**特性：**
- 子 workflow 共享：并发上限、agent 计数器、中止信号、token 预算
- 子 workflow 的 agent 显示在 `/workflows` 的组中
- 嵌套限制：仅一层（workflow 内不能再调用 workflow）

---

## 设计模式

### 模式 1: 扇出 - 汇总（Fan-out Gather）

多角度分析 → 综合结果。

```javascript
phase('Analysis')

const dimensions = ['security', 'performance', 'maintainability']

const analyses = await parallel(
  dimensions.map(dim => async () => 
    await agent(`Analyze ${dim}`, { schema: ANALYSIS_SCHEMA })
  )
)

phase('Synthesis')
const summary = await agent(
  `Synthesize these analyses: ${JSON.stringify(analyses)}`,
  { schema: SUMMARY_SCHEMA }
)

return summary
```

**适用：** 代码审查、多维度评估、对比分析

### 模式 2: 流水线（Pipeline）

批量处理，每个 item 经过多阶段。

```javascript
const results = await pipeline(
  fileList,
  
  async (file) => {
    return await agent(`Parse ${file}`, { schema: AST_SCHEMA })
  },
  
  async (ast, file) => {
    return await agent(`Transform ${file}`, { schema: TRANSFORM_SCHEMA })
  },
  
  async (transformed, file) => {
    return await agent(`Validate ${file}`)
  }
)
```

**适用：** 迁移、批量转换、ETL 流程

### 模式 3: 对抗验证（Adversarial Verify）

独立生成 → 对抗验证 → 重试。

```javascript
phase('Generate')
let solution = await agent('Generate solution', { schema: SOLUTION_SCHEMA })

phase('Verify')
const verification = await agent(
  `Adversarially verify this solution: ${JSON.stringify(solution)}. Find flaws.`,
  { schema: VERIFICATION_SCHEMA, effort: 'high' }
)

if (verification.flaws.length > 0) {
  phase('Refine')
  solution = await agent(
    `Fix these flaws: ${JSON.stringify(verification.flaws)}`,
    { schema: SOLUTION_SCHEMA }
  )
}

return solution
```

**适用：** 高质量要求、Ultracode 模式、安全关键代码

### 模式 4: 探索 - 深入（Discover then Deep-Dive）

先发现工作列表，再并行处理。

```javascript
phase('Discover')
const targets = await agent('Find all test files', { schema: FILE_LIST_SCHEMA })

log(`Found ${targets.files.length} test files`)

phase('Process')
const results = await pipeline(
  targets.files,
  async (file) => await agent(`Analyze ${file}`, { isolation: 'worktree' })
)

return results
```

**适用：** 动态目标、需要先扫描再处理的场景

### 模式 5: 循环至完成（Loop Until Dry）

处理直到队列为空或预算耗尽。

```javascript
let queue = initialTasks
let processed = []

while (queue.length > 0 && budget.remaining() > 10_000) {
  phase(`Iteration ${processed.length + 1}`)
  
  const batch = queue.splice(0, 10)  // 每次处理 10 个
  
  const batchResults = await pipeline(
    batch,
    async (task) => await agent(`Process ${task}`)
  )
  
  processed.push(...batchResults.filter(Boolean))
  
  // 可能生成新任务
  const newTasks = await agent('Check for more work')
  if (newTasks) queue.push(...newTasks)
}

return { processed: processed.length, remaining: queue.length }
```

**适用：** 递归任务发现、依赖图遍历

---

## 高级特性

### 结构化输出（Schema）

使用 JSON Schema 强制 agent 返回结构化数据：

```javascript
const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          severity: { 
            type: 'string', 
            enum: ['critical', 'high', 'medium', 'low'] 
          },
          description: { type: 'string' },
        },
        required: ['file', 'severity', 'description'],
      },
    },
    summary: { type: 'string' },
  },
  required: ['findings', 'summary'],
}

const result = await agent('Find security issues', { 
  schema: FINDINGS_SCHEMA 
})

// result 已验证，可直接使用
result.findings.forEach(f => {
  console.log(`${f.severity}: ${f.description}`)
})
```

### Worktree 隔离

当多个 agent 并行修改文件时使用：

```javascript
const results = await pipeline(
  componentFiles,
  async (file) => {
    return await agent(`Refactor ${file}`, {
      isolation: 'worktree',  // 独立 git worktree
      schema: REFACTOR_SCHEMA
    })
  }
)
```

⚠️ **开销：** 每个 worktree ~200-500ms 设置时间 + 磁盘空间。仅在真正需要时使用。

### 自定义 Agent 类型

使用预定义的专用 agent：

```javascript
// 使用只读探索 agent（更快的文件扫描）
const codeMap = await agent('Map the authentication system', {
  agentType: 'Explore',
  // Explore agent 有特殊工具集，适合广泛搜索
})

// 可以与 schema 组合
const findings = await agent('Review code', {
  agentType: 'code-reviewer',
  schema: REVIEW_SCHEMA
})
```

**可用类型：**
- `Explore`: 只读搜索，适合广泛扫描
- `code-reviewer`: 代码审查专用
- `Plan`: 架构设计和规划
- 其他：参考主会话可用的 agent 类型列表

### 模型选择策略

**默认：省略 `model` 参数**（继承会话模型，通常是正确的）

**何时覆盖：**
```javascript
// 机械的廉价任务：使用 haiku
const fileList = await agent('List all .ts files', {
  model: 'haiku',
  effort: 'low'
})

// 最难的判断任务：使用 opus
const criticalDecision = await agent('Is this a security vulnerability?', {
  model: 'opus',
  effort: 'high'
})
```

⚠️ **警告：** 仅在高度确信不同层级适合任务时设置。不确定时省略。

### Effort 层级

控制推理努力度（适用于支持的模型）：

- `'low'`: 机械任务（文件列表、简单转换）
- `'medium'`: 默认
- `'high'` / `'xhigh'` / `'max'`: 最难的验证/判断阶段

```javascript
// 快速扫描
const files = await agent('Find test files', { effort: 'low' })

// 深度验证
const isVulnerable = await agent('Analyze security', { 
  effort: 'high' 
})
```

---

## 性能优化

### 1. 使用 Pipeline 而非嵌套 Parallel

❌ **慢（有屏障）：**
```javascript
const analyzed = await parallel(items.map(i => () => agent(`Analyze ${i}`)))
const fixed = await parallel(analyzed.map(a => () => agent(`Fix ${a}`)))
const verified = await parallel(fixed.map(f => () => agent(`Verify ${f}`)))
```

✅ **快（无屏障）：**
```javascript
const results = await pipeline(
  items,
  async (item) => agent(`Analyze ${item}`),
  async (prev, item) => agent(`Fix ${item}`),
  async (prev, item) => agent(`Verify ${item}`)
)
```

墙钟时间从 3×最慢任务 降至 1×最长链。

### 2. 避免过度使用 Worktree

❌ **不必要的开销：**
```javascript
await parallel([
  () => agent('Read file A', { isolation: 'worktree' }),  // 只读不需要
  () => agent('Read file B', { isolation: 'worktree' }),
])
```

✅ **仅在并行写入时：**
```javascript
await pipeline(
  files,
  async (file) => agent(`Modify ${file}`, {
    isolation: 'worktree'  // 多个文件并行修改
  })
)
```

### 3. 混合 Effort 层级

不要所有任务都用高 effort：

```javascript
// 扫描：low
const targets = await agent('Find targets', { effort: 'low' })

// 分析：medium（默认）
const analysis = await agent('Analyze')

// 验证：high（最关键）
const verified = await agent('Verify security', { effort: 'high' })
```

### 4. 早期过滤

在 workflow 开始时过滤掉不相关项：

```javascript
phase('Filter')
const relevant = await agent('Filter to high-risk files only', {
  schema: FILE_LIST_SCHEMA,
  effort: 'low'
})

if (relevant.files.length === 0) {
  return { status: 'nothing-to-review' }
}

phase('Deep Review')
// 仅处理相关文件
```

### 5. 渐进式详细

先粗粒度扫描，只对问题区域深入：

```javascript
phase('Quick Scan')
const suspicious = await pipeline(
  allFiles,
  async (file) => agent(`Quick scan ${file} for issues`, { effort: 'low' })
)

const problematic = suspicious.filter(s => s && s.hasIssues)

phase('Deep Analysis')
const detailed = await pipeline(
  problematic,
  async (file) => agent(`Deep analyze ${file}`, { effort: 'high' })
)
```

---

## 调试与故障排查

### 查看运行中的 Workflow

```bash
/workflows
```

显示所有 agent 的实时进度。

### 停止 Workflow

```javascript
// 在主会话中
TaskStop({ task_id: 'wf_xxxxx' })
```

### Resume 失败的 Workflow

```javascript
// 重新运行，已完成的 agent 会使用缓存
Workflow({
  scriptPath: '/path/to/script.js',
  resumeFromRunId: 'wf_xxxxx'
})
```

只有 prompt 和 opts 未改变的 agent() 调用会返回缓存结果。

### 常见错误

#### 1. Meta 不是纯字面量

❌ **错误：**
```javascript
const phases = [{ title: 'Scan', detail: 'desc' }]
export const meta = {
  name: 'test',
  description: 'Test',
  phases: phases  // 变量引用
}
```

✅ **正确：**
```javascript
export const meta = {
  name: 'test',
  description: 'Test',
  phases: [{ title: 'Scan', detail: 'desc' }]  // 字面量
}
```

#### 2. Phase 标题不匹配

❌ **错误：**
```javascript
export const meta = {
  phases: [{ title: 'Scan', detail: '...' }]
}

phase('Scanning')  // 不匹配
```

✅ **正确：**
```javascript
export const meta = {
  phases: [{ title: 'Scan', detail: '...' }]
}

phase('Scan')  // 匹配
```

#### 3. 忘记处理 null 返回

❌ **错误：**
```javascript
const results = await parallel([...])
results.forEach(r => r.data.forEach(...))  // r 可能是 null
```

✅ **正确：**
```javascript
const results = await parallel([...])
results.filter(Boolean).forEach(r => r.data.forEach(...))
```

#### 4. Args 作为字符串传递

❌ **错误：**
```javascript
Workflow({
  script: '...',
  args: '["file1.js", "file2.js"]'  // 字符串
})

// 脚本中：
args.map(...)  // 错误：args 是字符串，没有 map
```

✅ **正确：**
```javascript
Workflow({
  script: '...',
  args: ["file1.js", "file2.js"]  // 数组
})
```

---

## 最佳实践

### 1. 先本地探查，再编排

不要盲目启动 workflow。先在主会话中探查：

```javascript
// 主会话中：先列出文件
Bash({ command: 'find . -name "*.test.js"' })

// 然后基于实际文件列表创建 workflow
Workflow({
  script: `
    export const meta = { ... }
    const files = ${JSON.stringify(fileList)}
    const results = await pipeline(files, ...)
  `
})
```

### 2. 使用有意义的 Label

```javascript
await agent('Review security', {
  label: `Security Review: ${filename}`,  // 清晰的标签
  phase: 'Review'
})
```

### 3. 渐进式质量

根据需求选择质量策略：

**快速迭代：**
```javascript
const result = await agent('Quick analysis', { effort: 'low' })
```

**Ultracode 模式：**
```javascript
phase('Generate')
const solution = await agent('Generate solution')

phase('Adversarial Verify')
const flaws = await agent('Find flaws', { effort: 'high' })

if (flaws.length > 0) {
  phase('Refine')
  const refined = await agent('Fix flaws')
}
```

### 4. 清晰的返回值

Workflow 的返回值会返回给调用者：

```javascript
// 结构化返回
return {
  status: 'complete',
  filesProcessed: files.length,
  findings: allFindings,
  summary: synthesis,
  recommendation: 'approve'
}
```

### 5. 早期验证

在开始昂贵操作前验证输入：

```javascript
phase('Validate')
if (!args || !args.targetDir) {
  log('Error: targetDir is required')
  return { status: 'error', message: 'Missing targetDir' }
}

const exists = await agent(`Check if ${args.targetDir} exists`)
if (!exists) {
  return { status: 'error', message: 'Directory not found' }
}

phase('Process')
// 继续处理...
```

### 6. 文档化复杂 Schema

```javascript
// 定义清晰的 schema
const SECURITY_FINDING_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string', description: 'Relative path from repo root' },
          line: { type: 'number', description: 'Line number (1-indexed)' },
          severity: { 
            type: 'string', 
            enum: ['critical', 'high', 'medium', 'low'],
            description: 'CRITICAL: exploitable, HIGH: likely exploitable, MEDIUM: needs review, LOW: best practice'
          },
          cwe: { type: 'string', description: 'CWE identifier if applicable' },
          description: { type: 'string' },
          recommendation: { type: 'string' },
        },
        required: ['file', 'severity', 'description', 'recommendation'],
      },
    },
  },
  required: ['findings'],
}
```

### 7. 使用 Log 提供进度反馈

```javascript
log('Starting workflow...')
log(`Processing ${files.length} files`)

phase('Review')
log('Running parallel security, performance, and style reviews')

const reviews = await parallel([...])

log(`Found ${totalIssues} issues across ${reviews.length} dimensions`)
```

### 8. 处理预算限制

```javascript
if (budget.total && budget.remaining() < 20_000) {
  log(`Low budget: ${budget.remaining()} tokens remaining`)
  return { 
    status: 'partial', 
    message: 'Stopped due to budget limit',
    processed: resultsCounter 
  }
}
```

### 9. 避免过度并行

不要启动数百个并发 agent：

```javascript
// ❌ 可能启动 1000 个并发 agent
await parallel(allFiles.map(f => () => agent(`Process ${f}`)))

// ✅ 分批处理
const BATCH_SIZE = 20
for (let i = 0; i < allFiles.length; i += BATCH_SIZE) {
  const batch = allFiles.slice(i, i + BATCH_SIZE)
  await parallel(batch.map(f => () => agent(`Process ${f}`)))
}

// ✅ 或使用 pipeline（自动管理并发）
await pipeline(allFiles, async (f) => agent(`Process ${f}`))
```

### 10. 命名约定

- **Workflow 名称**: kebab-case (`security-review`, `bulk-migration`)
- **Phase 标题**: 简短的动词或名词 (`Scan`, `Review`, `Synthesis`)
- **变量**: camelCase (`changedFiles`, `securityFindings`)

---

## 完整示例库

### 示例 1: 代码审查 Workflow

参见 `example-code-review.js` - 演示：
- 三阶段流程：扫描 → 审查 → 综合
- 多维度并行审查（安全、性能、质量）
- 结构化输出 schema
- 清晰的返回值

### 示例 2: 批量文件迁移

```javascript
export const meta = {
  name: 'bulk-migration',
  description: 'Migrate files from old pattern to new pattern',
  phases: [
    { title: 'Discover', detail: 'Find files to migrate' },
    { title: 'Migrate', detail: 'Transform each file' },
    { title: 'Verify', detail: 'Validate migrations' },
  ],
}

phase('Discover')
const targets = await agent(
  `Find all files matching pattern: ${args.pattern}`,
  { schema: { type: 'object', properties: { files: { type: 'array', items: { type: 'string' } } }, required: ['files'] } }
)

if (targets.files.length === 0) {
  return { status: 'no-files', message: 'No files match pattern' }
}

log(`Found ${targets.files.length} files to migrate`)

phase('Migrate')
const results = await pipeline(
  targets.files,
  
  // Stage 1: Backup
  async (file) => {
    return await agent(`Create backup of ${file}`, { 
      effort: 'low',
      isolation: 'worktree' 
    })
  },
  
  // Stage 2: Transform
  async (backup, file) => {
    return await agent(`Migrate ${file} from ${args.oldPattern} to ${args.newPattern}`, {
      schema: { 
        type: 'object', 
        properties: { 
          success: { type: 'boolean' }, 
          changes: { type: 'number' } 
        } 
      },
      isolation: 'worktree'
    })
  },
  
  // Stage 3: Verify
  async (migration, file) => {
    if (!migration || !migration.success) return null
    return await agent(`Verify ${file} compiles and tests pass`, {
      effort: 'low'
    })
  }
)

const successful = results.filter(Boolean)

phase('Summary')
log(`Successfully migrated ${successful.length}/${targets.files.length} files`)

return {
  status: 'complete',
  total: targets.files.length,
  successful: successful.length,
  failed: targets.files.length - successful.length,
}
```

### 示例 3: 研究 Workflow

```javascript
export const meta = {
  name: 'deep-research',
  description: 'Multi-source research with adversarial verification',
  phases: [
    { title: 'Search', detail: 'Gather sources from multiple channels' },
    { title: 'Extract', detail: 'Deep-read each source' },
    { title: 'Verify', detail: 'Cross-check claims' },
    { title: 'Synthesize', detail: 'Create cited report' },
  ],
}

const question = args.question || 'Research topic'

phase('Search')
const sources = await parallel([
  async () => agent(`Web search: ${question}`, { schema: SOURCE_LIST_SCHEMA }),
  async () => agent(`Academic search: ${question}`, { schema: SOURCE_LIST_SCHEMA }),
  async () => agent(`Documentation search: ${question}`, { schema: SOURCE_LIST_SCHEMA }),
])

const allSources = sources
  .filter(Boolean)
  .flatMap(s => s.sources)
  .slice(0, 10)  // Limit to top 10

log(`Gathered ${allSources.length} sources`)

phase('Extract')
const extracts = await pipeline(
  allSources,
  async (source) => {
    return await agent(`Extract key information from ${source.url}`, {
      schema: EXTRACT_SCHEMA,
      effort: 'medium'
    })
  }
)

phase('Verify')
const claims = extracts
  .filter(Boolean)
  .flatMap(e => e.claims)

const verified = await parallel(
  claims.slice(0, 5).map(claim => async () => {
    return await agent(`Verify this claim across sources: ${claim}`, {
      schema: VERIFICATION_SCHEMA,
      effort: 'high'
    })
  })
)

phase('Synthesize')
const report = await agent(
  `Create a comprehensive report on: ${question}
  
  Sources: ${JSON.stringify(allSources)}
  Extracts: ${JSON.stringify(extracts)}
  Verified: ${JSON.stringify(verified)}
  
  Include citations and confidence levels.`,
  {
    schema: REPORT_SCHEMA,
    effort: 'high'
  }
)

return {
  status: 'complete',
  question,
  sourcesCount: allSources.length,
  claimsVerified: verified.filter(Boolean).length,
  report,
}
```

---

## 性能基准参考

基于典型任务的墙钟时间估算：

| 模式 | Agent 数量 | 预计时间 | Token 消耗 |
|------|-----------|---------|-----------|
| 单个分析 | 1 | 10-30s | 5-20k |
| 三维度并行审查 | 3 | 30-60s | 20-60k |
| 10 文件 pipeline (3 stage) | 30 | 2-5min | 100-300k |
| 50 文件 pipeline (3 stage) | 150 | 10-20min | 500k-1.5M |
| 深度研究 (10 源 + 验证) | 15-20 | 3-8min | 150-400k |

⚠️ **实际时间取决于：**
- 模型响应速度
- 任务复杂度
- 并发限制
- 网络延迟

---

## 安全注意事项

### 1. 避免在 Prompt 中泄露敏感信息

```javascript
// ❌ 不要
await agent(`Review file: ${fileContent}`)  // fileContent 可能包含密钥

// ✅ 应该
await agent(`Review file: ${filePath}`)  // 让 agent 自己读取
```

### 2. 验证 Args 输入

```javascript
// 验证路径
if (args.targetDir && !args.targetDir.startsWith('/home/user/project')) {
  return { status: 'error', message: 'Invalid directory' }
}

// 验证类型
if (typeof args.depth !== 'number' || args.depth < 0 || args.depth > 10) {
  return { status: 'error', message: 'Invalid depth' }
}
```

### 3. 限制破坏性操作

```javascript
// 对于删除等破坏性操作，要求明确确认
if (args.operation === 'delete' && !args.confirmed) {
  return { 
    status: 'confirmation-required',
    message: 'Please set args.confirmed = true to proceed with deletion'
  }
}
```

### 4. 日志中不要记录敏感数据

```javascript
// ❌ 不要
log(`API key: ${apiKey}`)

// ✅ 应该
log('API key configured')
```

---

## 总结

Workflow 是强大的多 agent 编排工具，关键点：

✅ **核心原则：**
1. 用户必须明确授权（ultracode 或显式请求）
2. 先本地探查，再编排
3. Pipeline 优于嵌套 Parallel（除非需要同步点）
4. 使用 Schema 强制结构化输出
5. 渐进式质量：快速迭代用 low effort，关键决策用 high

✅ **常用模式：**
- **扇出-汇总**: 多维度分析 → 综合
- **流水线**: 批量处理，无屏障
- **对抗验证**: 生成 → 验证 → 修复
- **探索-深入**: 发现 → 并行处理

✅ **性能优化：**
- 使用 pipeline 而非多个 parallel 屏障
- 避免不必要的 worktree 隔离
- 早期过滤，渐进式详细
- 混合 effort 层级

✅ **调试：**
- 使用 `/workflows` 查看进度
- 使用 `resumeFromRunId` 恢复失败的运行
- 处理 `null` 返回值（`.filter(Boolean)`）

参考 `example-code-review.js` 了解完整实现。