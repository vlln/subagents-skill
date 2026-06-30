#!/bin/bash
# Subagents demo — real commands with prompt
cd /home/vlln/Project/skill_project/subagent-skills

GREEN="\033[1;32m"
RESET="\033[0m"

# Setup
rm -f .agents/subagents/agents.json
mkdir -p .agents/subagent
cat > .agents/subagent/reviewer.md << 'EOF'
---
name: reviewer
description: Expert code reviewer
---
EOF
python3 -c "
import sys; sys.path.insert(0, 'skills/subagents/scripts/lib')
from registry import register, enqueue_task, set_current_task, add_task
register('reviewer', 's1', 'sid-demo', cwd='/tmp/.claude/worktrees/refactor', background=True)
enqueue_task('reviewer', 's1', 'Refactor auth module')
enqueue_task('reviewer', 's1', 'Update unit tests')
set_current_task('reviewer', 's1', {'prompt': 'Analyze codebase', 'status': 'running'})
add_task('reviewer', 's1', 'Setup project', 'done')
add_task('reviewer', 's1', 'Configure linting', 'done')
"

sleep 1

# Scene 1
echo -e "${GREEN}\$ ${RESET}subagents status reviewer s1"
echo ""
python3 skills/subagents/scripts/subagents status reviewer s1
sleep 1.5

# Scene 2
echo -e "${GREEN}\$ ${RESET}subagents send s1 \"Fix auth service\""
echo ""
python3 skills/subagents/scripts/subagents send s1 "Fix auth service"
sleep 1
echo -e "${GREEN}\$ ${RESET}subagents send s1 \"Write integration tests\""
echo ""
python3 skills/subagents/scripts/subagents send s1 "Write integration tests"
sleep 1.5

# Scene 3
echo -e "${GREEN}\$ ${RESET}subagents status reviewer s1"
echo ""
python3 skills/subagents/scripts/subagents status reviewer s1
sleep 1.5

# Scene 4
echo -e "${GREEN}\$ ${RESET}subagents cancel s1 --task 2"
echo ""
python3 skills/subagents/scripts/subagents cancel s1 --task 2
sleep 1
echo -e "${GREEN}\$ ${RESET}subagents cancel s1 --all"
echo ""
python3 skills/subagents/scripts/subagents cancel s1 --all
sleep 1.5

# Scene 5
echo -e "${GREEN}\$ ${RESET}subagents list"
echo ""
python3 skills/subagents/scripts/subagents list