# Push Command

This command handles git operations to add, commit, and push changes to the remote repository.

## Instructions

1. Run `git pull` to get latest changes from remote
2. If there are merge conflicts, stop and tell the user "There are merge conflicts. Please resolve them manually and run the command again."
3. Run `ruff check --fix .` to auto-fix linting issues
4. Run `ruff format .` to format code
5. If ruff reports any remaining errors that couldn't be auto-fixed, fix them manually before continuing (pre-commit will fail otherwise)
6. Run `git status` to see what files have changed
7. Run `git diff` to see the actual changes since the last commit
8. Analyze the changes and generate a descriptive commit message based on what was modified
9. `git add .` (add all files)
10. `git commit -m "[generated descriptive message]"` (make a commit with the generated message)
11. `git push` (push to remote repository)

Always show the user what commands you're running and their output.

## Important

- Never include "Co-Authored-By" or any AI attribution in commit messages
- Keep commit messages clean and professional without signatures or generated-by tags