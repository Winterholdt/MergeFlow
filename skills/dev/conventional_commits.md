# Conventional Commits & Atomic History

## Overview
This skill defines standards for writing atomic Git commits adhering strictly to the Conventional Commits specification. Clean commit history enables automated changelog generation, predictable versioning, and effortless bisecting.

---

## 1. Commit Message Structure
Every commit message must follow this structure:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### Allowed Types:
- `feat`: A new user-facing or public capability/feature.
- `fix`: A bug fix or remediation.
- `test`: Adding missing tests or correcting existing tests without altering production code.
- `refactor`: A code change that neither fixes a bug nor adds a feature.
- `docs`: Documentation changes only (e.g. README, docstrings, architectural docs).
- `chore`: Maintenance tasks, tool updates, build configurations, or dependency bumps.
- `ci`: CI/CD pipeline or runner script adjustments (e.g. `fix(ci): <reason>`).
- `perf`: A code change that improves performance without changing functionality.

## 2. Formatting Rules & Tone
- **Imperative Mood:** Use the imperative mood in the subject line (e.g., `feat(auth): add JWT validation`, not `added JWT validation` or `adds JWT validation`).
- **No Capitalization & No Trailing Period:** Keep the description in lowercase and do not end with a period.
- **Concise Subject:** Limit the header line to 72 characters or fewer.
- **Breaking Changes:** Indicate breaking changes by appending `!` before the colon (e.g., `feat(api)!: drop deprecated v1 endpoints`) or adding a `BREAKING CHANGE:` footer.

## 3. Atomic Commits
- **One Logical Change per Commit:** Do not combine refactoring, formatting changes, and new features into a single commit.
- **Green at Every Commit:** Every commit must leave the repository in a passing, buildable, and testable state.
- **Revertible:** Each atomic commit should be safe to cherry-pick or revert independently without breaking unrelated features.
