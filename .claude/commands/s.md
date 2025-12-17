---
allowed-tools: mcp__sequential-thinking__sequentialthinking
argument-hint: [task-description]
description: Perform deep sequential thinking and analysis before implementation
---

Use the sequential thinking tool to analyze this task: $ARGUMENTS

## Sequential Thinking Requirements:

1. **Understand the problem** - Break down the problem into its fundamental components. Identify what is actually needed vs. what might be over-engineering.

2. **Consider approaches with KISS in mind** - Evaluate solutions, but prefer the simplest one that solves the problem. Avoid premature optimization and YAGNI violations.

3. **Think through edge cases** - Identify potential failure modes, boundary conditions, and security vulnerabilities relevant to the actual requirements.

4. **Follow Clean Code principles** - Keep functions small and focused. One responsibility per class. Meaningful names. No unnecessary abstractions.

5. **Optimize for clarity and correctness** - Prioritize readability and maintainability. Don't add features, refactor code, or make "improvements" beyond what was asked.

## Process:

- Start simple and only add complexity when justified
- Question whether each addition is truly necessary
- Prefer explicit over clever
- Leave code cleaner than you found it (Boy Scout Rule)

Analyze thoroughly but implement simply.
