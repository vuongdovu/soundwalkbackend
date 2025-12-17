---
allowed-tools: mcp__sequential-thinking__sequentialthinking
---

# Push Command

This command handles git operations to add, commit, and push changes to the remote repository.

## Arguments

- `d` (optional): **Docs mode** - Run documentation agents to update API and architecture docs before committing

## Instructions

1. Run `git pull` to get latest changes from remote
2. If there are merge conflicts, stop and tell the user "There are merge conflicts. Please resolve them manually and run the command again."
3. **If "d" argument IS provided**: Launch two documentation agents **in parallel in the background**:
   - **API docs agent** (subagent_type: `api-docs`) - Reviews and updates API documentation
   - **Architecture docs agent** (subagent_type: `architecture-docs`) - Reviews and updates architecture documentation with Mermaid diagrams
4. **If "d" argument IS provided**: **Wait for both documentation agents to complete** using TaskOutput before proceeding
5. Run `ruff check --fix .` to auto-fix linting issues
6. Run `ruff format .` to format code
7. If ruff reports any remaining errors that couldn't be auto-fixed, fix them manually before continuing
8. Run `git status` to see what files have changed (including any doc changes from agents)
9. Run `git diff` to see the actual changes since the last commit
10. Analyze the changes and generate a descriptive commit message based on what was modified
11. `git add .` (add all files)
12. `git commit -m "[generated descriptive message]"` (make a commit with the generated message)
13. `git push` (push to remote repository)

Always show the user what commands you're running and their output.

## Important

- Never include "Co-Authored-By" or any AI attribution in commit messages
- Keep commit messages clean and professional without signatures or generated-by tags
- When running in docs mode (with "d"), must wait for documentation agents to complete before linting/formatting to include any code changes they made
- Use `/p` for quick commits (default fast mode)
- Use `/p d` when documentation updates are needed
