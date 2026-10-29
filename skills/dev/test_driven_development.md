# Test-Driven Development & Robust Testing

## Overview
This skill guides the developer agent in applying Test-Driven Development (TDD) and writing resilient, reliable, and deterministic automated test suites.

---

## 1. Red-Green-Refactor Cycle
1. **Red:** Write a small, focused test covering a specific acceptance criterion or edge case. Run it to confirm it fails for the expected reason.
2. **Green:** Write the minimal production code necessary to make the test pass.
3. **Refactor:** Clean up the implementation, eliminate duplication, and enhance readability while keeping all tests passing.

## 2. Test Architecture & Structure (AAA Pattern)
Structure each test cleanly using the Arrange-Act-Assert pattern:
- **Arrange:** Set up preconditions, test fixtures, and inputs cleanly.
- **Act:** Execute the single operation or method under test.
- **Assert:** Verify the exact expected outputs, state changes, or raised exceptions.

## 3. Robust Assertions & No Mock Tricks
- **Assert Behaviors, Not Implementations:** Test public contract outputs and observable side effects, rather than internal private variables.
- **Mock at System Boundaries Only:** Use mocks, stubs, or fakes exclusively for external non-deterministic boundaries (e.g. LLM APIs, network I/O, subprocesses, current time).
- **No Mock Tricks:** Never mock the class or method under test. Never introduce artificial mocks that make tests pass while real execution would fail.
- **Deterministic & Isolated:** Tests must never depend on execution order, network availability, or shared mutable global state. Always clean up temporary files using temporary directory fixtures (e.g. `tmp_path`).

## 4. Coverage of Edge Cases & Failure Modes
- Verify boundary values, zero/empty states, and out-of-range inputs.
- Test that expected domain errors and exceptions are raised with appropriate error messages (`pytest.raises`).
- Ensure every acceptance criterion in the specification is backed by at least one dedicated automated test.
