# Requirements Engineering & Scope Definition

## Overview
This skill guides the discovery agent in formulating precise, unambiguous, and testable requirements specifications (`SPEC.md`). It prevents architectural ambiguity, untested assumptions, and scope creep.

---

## 1. Edge-Case & Boundary Analysis
- **Boundary Values:** Identify minimum, maximum, empty, zero, and off-by-one boundary conditions for all data inputs.
- **Incomplete / Malformed Data:** Specify explicit handling for missing fields, invalid formats, wrong types, and null/None values.
- **Concurrency & State:** Define behavior under concurrent operations, race conditions, and interrupted operations.
- **Failure Modes:** Explicitly specify what happens when external dependencies, databases, filesystem access, or network calls fail or time out.

## 2. Validation & Invariant Checks
- **Schema Validation:** Define strict input and output schemas (e.g. Pydantic models, JSON Schema) for all interfaces and functions.
- **Fail Fast:** Validate preconditions immediately at public boundaries before initiating side effects.
- **Explicit Invariants:** Document state invariants that must remain true before and after state transitions.
- **Deterministic Error Messages:** Ensure all validation errors provide actionable, deterministic error messages.

## 3. Scope Boundary & Prevention of Scope Creep
- **Clear Scope Definition:** Clearly separate what is strictly included in the feature from what is out of scope.
- **Out of Scope Section:** Every specification MUST include a dedicated `## Out of Scope` section explicitly listing deferred features, non-goals, and unneeded abstractions.
- **YAGNI (You Aren't Gonna Need It):** Reject speculative abstractions, generic wrappers, and premature optimizations not required by the core requirement.
- **Minimal Viable Changes:** Focus the specification on the smallest complete set of changes that satisfy the business need.

## 4. Testable Acceptance Criteria
- Every requirement must translate directly into a verifiable check item (`- [ ] ...`).
- Acceptance criteria must state the concrete trigger, the expected result, and observable evidence (e.g. return value, emitted event, status code).
- Avoid subjective words like "fast", "clean", or "user-friendly" without quantitative thresholds.
