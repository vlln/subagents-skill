# Docs

```
docs/
  references/          # 外部参考文档（只读，不修改）
    claude-code/       # Claude Code 的 subagent/workflow 参考实现
    acp/               # [未来] ACP 协议规范
  design/              # 本项目的设计文档
    subagents/         # subagents skill 设计
    workflow/          # workflow skill 设计
  assets/              # README 和文档用到的图片等资源
    readme/            # README 多语言版本
```

## 约定

- `references/` 下的文件来自外部项目，**不修改**，仅作参考。
- `design/` 下的文件是本项目的架构设计、API 设计等，**随代码演进同步更新**。
- `assets/` 下的图片、SVG 等由 `demos/` 或外部工具生成。