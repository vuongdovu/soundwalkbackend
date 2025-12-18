---
allowed-tools: mcp__sequential-thinking__sequentialthinking, Task, TaskOutput, Glob, Grep, Read, mcp__plugin_serena_serena__get_symbols_overview, mcp__plugin_serena_serena__find_symbol, mcp__plugin_serena_serena__find_referencing_symbols, mcp__plugin_serena_serena__search_for_pattern, mcp__plugin_serena_serena__list_dir, mcp__plugin_serena_serena__find_file, mcp__plugin_serena_serena__read_file
argument-hint: [app-name]
description: Explore a Django app with three parallel Serena Explorer agents
---

# Explore Django App Command

Explore the `$ARGUMENTS` Django app using three parallel Serena Explorer agents with sequential thinking.

## Instructions

1. **Use sequential thinking** to plan what each agent should focus on within the `app/$ARGUMENTS/` directory

2. **Launch three serena-explorer agents in parallel in the background** (all in a single message with three Task tool calls, each with `run_in_background: true`):

   **Agent 1 - Models & Data Layer**:
   - subagent_type: `serena-explorer`
   - Focus: Explore models, migrations, database schema, relationships, and mixins in `app/$ARGUMENTS/`
   - Prompt should instruct agent to analyze models.py, migrations/, model relationships, custom managers

   **Agent 2 - API & Serialization Layer**:
   - subagent_type: `serena-explorer`
   - Focus: Explore views, viewsets, serializers, URL routing, and permissions in `app/$ARGUMENTS/`
   - Prompt should instruct agent to analyze views.py, serializers.py, urls.py, permissions

   **Agent 3 - Services & Business Logic**:
   - subagent_type: `serena-explorer`
   - Focus: Explore service classes, Celery tasks, signals, and utilities in `app/$ARGUMENTS/`
   - Prompt should instruct agent to analyze services.py, tasks.py, signals.py, utils.py

3. **Wait for ALL three agents to complete using TaskOutput before proceeding** - Do NOT synthesize or continue until every agent has finished. Use TaskOutput with `block: true` for each agent to ensure completion.

4. **Synthesize findings** into 4-8 paragraphs in plain English (ONLY after all agents complete):
   - Combine all agent results into a coherent narrative
   - Each paragraph should cover a distinct architectural aspect
   - Include file paths and symbol names as references
   - Use code exploration tools (Glob, Grep, Read, Serena tools) to fill gaps or verify details
   - Write in clear, accessible prose suitable for developer onboarding

## Important

- All three Task tool calls MUST be in a single message for parallel execution
- Each agent uses Serena MCP tools (get_symbols_overview, find_symbol, find_referencing_symbols)
- Run agents in background with `run_in_background: true`
- **CRITICAL: You MUST use TaskOutput with `block: true` for ALL three agents and wait for them to complete BEFORE synthesizing the response. Do NOT start writing the synthesis until every agent has returned results.**
- You have access to code exploration tools to supplement agent findings during synthesis
