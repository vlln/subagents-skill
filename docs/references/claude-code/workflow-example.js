export const meta = {
  name: 'code-review-workflow',
  description: 'Multi-perspective code review with security, performance, and style checks',
  whenToUse: 'When you need comprehensive code review across multiple dimensions',
  phases: [
    { title: 'Scan', detail: 'Identify changed files and categorize by risk' },
    { title: 'Review', detail: 'Parallel reviews across security, performance, and style dimensions' },
    { title: 'Synthesis', detail: 'Consolidate findings and prioritize fixes' },
  ],
}

// Phase 1: Scan the changes
phase('Scan')
log('Analyzing git diff to identify changed files...')

const CHANGED_FILES_SCHEMA = {
  type: 'object',
  properties: {
    files: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          path: { type: 'string' },
          changeType: { type: 'string', enum: ['added', 'modified', 'deleted'] },
          riskLevel: { type: 'string', enum: ['low', 'medium', 'high'] },
          category: { type: 'string', enum: ['auth', 'data', 'api', 'ui', 'config', 'test', 'other'] },
        },
        required: ['path', 'changeType', 'riskLevel', 'category'],
      },
    },
  },
  required: ['files'],
}

const changedFiles = await agent(
  'Run `git diff --name-status HEAD` and categorize each file by risk level and category. High risk: auth, payment, data deletion. Medium: API endpoints, database queries. Low: UI, tests, docs.',
  { schema: CHANGED_FILES_SCHEMA, effort: 'low' }
)

if (!changedFiles || changedFiles.files.length === 0) {
  log('No changes detected')
  return { status: 'no-changes', findings: [] }
}

log(`Found ${changedFiles.files.length} changed files`)

// Phase 2: Parallel review across dimensions
phase('Review')

const reviewDimensions = [
  {
    name: 'Security',
    prompt: `Review these files for security issues:
${changedFiles.files.map(f => f.path).join('\n')}

Focus on:
- Authentication/authorization bypasses
- SQL injection, XSS, CSRF vulnerabilities
- Secrets in code
- Unsafe deserialization
- Path traversal

For each issue found, provide: file, line number, severity (critical/high/medium/low), description, and fix recommendation.`,
    schema: {
      type: 'object',
      properties: {
        findings: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              file: { type: 'string' },
              line: { type: 'number' },
              severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
              category: { type: 'string' },
              description: { type: 'string' },
              recommendation: { type: 'string' },
            },
            required: ['file', 'severity', 'category', 'description', 'recommendation'],
          },
        },
      },
      required: ['findings'],
    },
  },
  {
    name: 'Performance',
    prompt: `Review these files for performance issues:
${changedFiles.files.map(f => f.path).join('\n')}

Focus on:
- N+1 queries
- Unnecessary loops or duplicated work
- Missing indexes or inefficient queries
- Memory leaks or unbounded growth
- Blocking I/O in hot paths

For each issue found, provide: file, line number, severity, description, and fix recommendation.`,
    schema: {
      type: 'object',
      properties: {
        findings: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              file: { type: 'string' },
              line: { type: 'number' },
              severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
              category: { type: 'string' },
              description: { type: 'string' },
              recommendation: { type: 'string' },
            },
            required: ['file', 'severity', 'category', 'description', 'recommendation'],
          },
        },
      },
      required: ['findings'],
    },
  },
  {
    name: 'Code Quality',
    prompt: `Review these files for code quality issues:
${changedFiles.files.map(f => f.path).join('\n')}

Focus on:
- Code duplication
- Overly complex functions
- Poor naming
- Missing error handling
- Inconsistent style with codebase

For each issue found, provide: file, line number, severity, description, and fix recommendation.`,
    schema: {
      type: 'object',
      properties: {
        findings: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              file: { type: 'string' },
              line: { type: 'number' },
              severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
              category: { type: 'string' },
              description: { type: 'string' },
              recommendation: { type: 'string' },
            },
            required: ['file', 'severity', 'category', 'description', 'recommendation'],
          },
        },
      },
      required: ['findings'],
    },
  },
]

// Run all review dimensions in parallel
const reviews = await parallel(
  reviewDimensions.map(dimension => async () => {
    log(`Starting ${dimension.name} review...`)
    return await agent(dimension.prompt, {
      label: `${dimension.name} Review`,
      schema: dimension.schema,
      effort: 'medium',
    })
  })
)

// Phase 3: Synthesize findings
phase('Synthesis')
log('Consolidating findings across all dimensions...')

const allFindings = reviews
  .filter(Boolean)
  .flatMap((review, idx) => {
    const dimension = reviewDimensions[idx].name
    return (review.findings || []).map(finding => ({
      ...finding,
      dimension,
    }))
  })

const SYNTHESIS_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    criticalCount: { type: 'number' },
    highCount: { type: 'number' },
    mediumCount: { type: 'number' },
    lowCount: { type: 'number' },
    topPriorities: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          severity: { type: 'string' },
          dimension: { type: 'string' },
          issue: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['file', 'severity', 'dimension', 'issue', 'fix'],
      },
    },
    recommendation: { type: 'string', enum: ['approve', 'request-changes', 'comment'] },
  },
  required: ['summary', 'criticalCount', 'highCount', 'mediumCount', 'lowCount', 'topPriorities', 'recommendation'],
}

const synthesis = await agent(
  `Synthesize these review findings into a coherent report:

${JSON.stringify(allFindings, null, 2)}

Provide:
1. Executive summary
2. Count by severity
3. Top 5 priorities to address (must-fix before merge)
4. Overall recommendation: approve / request-changes / comment`,
  { schema: SYNTHESIS_SCHEMA, effort: 'low' }
)

log(`Review complete: ${synthesis.criticalCount} critical, ${synthesis.highCount} high, ${synthesis.mediumCount} medium, ${synthesis.lowCount} low`)

return {
  status: 'complete',
  changedFiles: changedFiles.files.length,
  findings: allFindings,
  synthesis,
}
