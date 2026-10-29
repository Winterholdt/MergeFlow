# Application Security & OWASP Top 10 Audit

## Overview
This skill provides audit rules for identifying security vulnerabilities, command injection risks, credential leaks, and permission violations in code changes.

---

## 1. Injection Prevention
- **Command Injection:** Never pass raw, formatted, or concatenated user strings into a shell (`shell=True`, `os.system`, `subprocess.Popen(..., shell=True)`). Always execute commands as list vectors via `asyncio.create_subprocess_exec(*cmd_args)` or `subprocess.run([...])`.
- **SQL / Query Injection:** Always use parameterized queries or trusted ORM interfaces; never construct SQL or database commands using string formatting.
- **Path Traversal:** Validate that all file paths resolve within the designated workspace boundary using `.resolve()` and verify `path.is_relative_to(repo_root)`. Block any input containing `../` attempts to escape the root.

## 2. Secrets & Credential Management
- **No Hardcoded Secrets:** Flag any hardcoded API keys, tokens, passwords, private keys, or certificates immediately as a `BLOCKER`.
- **Log Leakage Prevention:** Verify that authorization headers, bearer tokens, passwords, and sensitive environment variables are masked and never written to logs or error messages.
- **Environment Boundaries:** Secrets must be loaded exclusively through secure configuration, environment variables, or secret managers.

## 3. Principle of Least Privilege & Access Control
- Restrict filesystem read/write operations to the minimal necessary directory scope.
- Ensure authentication checks and authorization policies cannot be bypassed or short-circuited.
- Verify security gatekeepers or access guards are evaluated before executing privileged actions.

## 4. Deserialization & Parser Safety
- Forbid unsafe deserialization libraries (`pickle.loads`, `yaml.load` without `SafeLoader`, `eval`, `exec`).
- Use `yaml.safe_load`, `json.loads`, or typed Pydantic models for parsing untrusted external inputs.
- Guard XML or HTML parsers against entity expansion (XXE) and billion laughs attacks.
