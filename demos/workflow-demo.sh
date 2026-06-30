#!/bin/bash
# Workflow demo — show command + real TTY tree output
cd "$(dirname "$0")/.."
echo -e "\033[1;32m\$ \033[0mworkflow run test_workflow.py"
echo ""
python3 skills/workflow/scripts/workflow run demos/test_workflow.py