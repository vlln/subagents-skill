<h1 align="center">Subagents</h1>

<p align="center">
  <strong>一个接口，八个后端。</strong><br/>
  将任务分发到 kimi、claude、codex、pi、opencode、qwen、kiro、gemini 等 AI 编程 Agent ——<br/>
  全部使用同一条 <code>subagents run</code> 命令。
</p>

<p align="center">
  <a href="https://github.com/vlln/subagents-skill/stargazers"><img src="https://badgen.net/github/stars/vlln/subagents-skill?label=%E2%98%85" alt="GitHub stars" /></a>
  <img src="https://badgen.net/github/license/vlln/subagents-skill" alt="License" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/dependencies-zero-44CC11?style=flat-square" alt="零依赖" />
  <img src="https://img.shields.io/badge/spec-Agent%20Skills-8257D0?style=flat-square" alt="Agent Skills 规范" />
</p>

<p align="center">
  <sub><a href="../../README.md">English</a> · <a href="README.zh-CN.md">中文</a></sub>
</p>

---

## 支持的后端

<p align="center">
  <a href="https://www.kimi.com/code"><kbd><img src="https://www.google.com/s2/favicons?domain=kimi.com&sz=64" alt="Kimi" width="16" valign="middle" /> Kimi</kbd></a> &nbsp;
  <a href="https://claude.com/product/claude-code"><kbd><img src="https://www.google.com/s2/favicons?domain=claude.com&sz=64" alt="Claude Code" width="16" valign="middle" /> Claude Code</kbd></a> &nbsp;
  <a href="https://openai.com/codex/"><kbd><img src="https://www.google.com/s2/favicons?domain=openai.com&sz=64" alt="Codex" width="16" valign="middle" /> Codex</kbd></a> &nbsp;
  <a href="https://pi.dev/"><kbd><img src="https://pi.dev/favicon.svg" alt="Pi" width="16" valign="middle" /> Pi</kbd></a> &nbsp;
  <a href="https://opencode.ai/"><kbd><img src="https://www.google.com/s2/favicons?domain=opencode.ai&sz=64" alt="OpenCode" width="16" valign="middle" /> OpenCode</kbd></a> &nbsp;
  <a href="https://qwen.ai/qwencode"><kbd><img src="https://www.google.com/s2/favicons?domain=qwen.ai&sz=64" alt="Qwen Code" width="16" valign="middle" /> Qwen Code</kbd></a> &nbsp;
  <a href="https://kiro.dev/"><kbd><img src="https://www.google.com/s2/favicons?domain=kiro.dev&sz=64" alt="Kiro" width="16" valign="middle" /> Kiro</kbd></a> &nbsp;
  <a href="https://geminicli.com/"><kbd><img src="https://www.google.com/s2/favicons?domain=geminicli.com&sz=64" alt="Gemini CLI" width="16" valign="middle" /> Gemini CLI</kbd></a>
</p>

<p align="center">
  <sub>后端和传输协议自动检测。可通过 <code>--backend &lt;name&gt;</code> 和 <code>--transport cli|acp</code> 手动指定。</sub>
</p>

| 后端 | 命令 | 传输协议 | 系统提示 |
|---------|---------|-----------|---------------|
| kimi | `kimi` | CLI, ACP | inline |
| claude | `claude` | CLI | native (append + overwrite) |
| codex | `codex` | CLI | inline |
| pi | `pi` | CLI | native (append + overwrite) |
| opencode | `opencode` | CLI, ACP | inline |
| qwen | `qwen` | CLI, ACP | native (append + overwrite) |
| kiro | `kiro-cli` | CLI, ACP | inline |
| gemini | `gemini` | CLI, ACP | inline |

遵循 [Agent Skills 规范](https://agentskills.io/specification) —— 兼容 Claude Code、Codex、Open Code 等任何支持 Skills 的 Agent。

## 特性

<table>
<tr>
<td width="50%" valign="top">

### 🧩 一个接口，八个后端

kimi、claude、codex、pi、opencode、qwen、kiro、gemini —— 全部通过同一条 `subagents run` 命令调用。自动检测选择合适的后端，也可通过 `--backend` 手动指定。

</td>
<td width="50%" valign="top">

### 🔁 持久化会话

每次运行创建命名会话。后续可恢复，Agent 保留所有历史上下文。轻松构建跨越多天的多轮对话。

</td>
</tr>
<tr>
<td width="50%" valign="top">

### ⚡ 并行集群

通过 `--bg` 将任务分发到多个 Agent，用 `subagents wait` 收集结果。同时对 10 个文件运行 10 个 reviewer。

</td>
<td width="50%" valign="top">

### 🔗 工作流编排

通过 `workflow.py` 实现流水线、并行和嵌套工作流。串联 Agent、分叉合并、恢复失败阶段。

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 📡 JSONL 输出

`--output json` 提供结构化、可流式传输、版本化的 JSONL 输出。每个事件都有类型和时间戳，可直接接入其他工具。

</td>
<td width="50%" valign="top">

### 🪶 零依赖

仅需 Python 3.10+ 标准库。无需 `pip install`，无需 `virtualenv`。丢进目录就能跑。

</td>
</tr>
</table>

## 安装

### [skit](https://github.com/vlln/skit)（推荐）

```bash
skit install ./subagents-skills --all
```

### 手动安装

<table>
<tr>
<td width="33%">

**Claude Code**

```bash
cp -r skills/subagents .claude/skills/
```

</td>
<td width="33%">

**Codex**

```bash
cp -r skills/subagents ~/.codex/skills/
```

</td>
<td width="33%">

**OpenCode**

```bash
git clone https://github.com/vlln/subagents-skill.git \
  ~/.opencode/skills/subagents-skills
```

</td>
</tr>
</table>

## Skills

| Skill | 描述 |
|-------|-------------|
| [subagents](skills/subagents/SKILL.md) | 将任务分发到多个后端的命名 Agent 会话。支持会话恢复、并行集群、JSONL 输出。 |
| [workflow](skills/workflow/SKILL.md) | 多 Agent 编排 —— 流水线、并行、分阶段工作流。嵌套子工作流、恢复、结构化输出。 |

## 快速开始

```bash
# 定义 Agent（可选）
mkdir -p .agents/subagents
cat > .agents/subagents/reviewer.md << 'EOF'
---
name: reviewer
description: 代码审查专家
---
你是一名代码审查专家。从正确性、安全性和最佳实践的角度分析代码。
EOF

# 运行任务
scripts/subagents run reviewer review-auth "审查 src/auth.ts 的安全性"

# 恢复会话，追加更多上下文
scripts/subagents run reviewer review-auth "现在检查错误处理逻辑"

# 并行运行 3 个 reviewer
scripts/subagents run --bg reviewer r1 "审查 src/auth.ts"
scripts/subagents run --bg reviewer r2 "审查 src/db.ts"
scripts/subagents run --bg reviewer r3 "审查 src/api.ts"
scripts/subagents wait r1 && scripts/subagents wait r2 && scripts/subagents wait r3

# 列出所有 Agent 和会话
scripts/subagents list
```

## 开发

```bash
# 单元测试（无需后端，CI 安全）
python3 -m pytest tests/ --ignore=tests/test_integration.py

# 集成测试（需要 kimi CLI）
SKIP_INTEGRATION=0 python3 -m pytest tests/test_integration.py -v

# 全部测试
SKIP_INTEGRATION=0 python3 -m pytest tests/ -v
```

| 层级 | 文件 | 用例数 | 触发方式 | 耗时 |
|-------|------|-------|---------|------|
| 单元 | `tests/test_subagents.py` | 79 | `pytest` 自动 | <1s |
| 单元 | `tests/test_workflow.py` | 26 | `pytest` 自动 | <1s |
| 集成 | `tests/test_integration.py` | 8 | `SKIP_INTEGRATION=0` | ~3min |

## License

MIT