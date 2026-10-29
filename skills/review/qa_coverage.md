# QA & Test Coverage Verification

## Overview
This skill guides the QA and test automation reviewer persona in verifying test integrity, assertion strength, edge-case coverage, and regression prevention.

---

## 1. Test Integrity & Meaningful Assertions
- **No Vacuous Assertions:** Detect and reject tests that assert tautologies (e.g. `assert True`, `assert result is not None` when checking a calculated value).
- **Exact Contract Verification:** Ensure assertions validate specific outputs, error codes, and mutated state against the specification.
- **Exception Verification:** Verify that negative tests check not only that an exception was raised, but also that the exception message or type matches expectations (`pytest.raises(CustomError, match="...")`).

## 2. No Mock Tricks
- **Real Execution over Artificial Mocks:** Scrutinize all mocks and stubs in tests. Ensure mocks are not replacing the very logic that needs to be validated.
- **No Over-Mocking:** Flag test suites where interactions are so heavily mocked that the tests test the mock configuration rather than real system behaviors.
- **No Suppression:** Disallow skipping tests (`@pytest.mark.skip`), suppressing assertion errors, or passing tests silently on error.

## 3. Boundary & Negative Test Coverage
- **Edge Cases:** Verify tests include boundary conditions: empty collections, zero values, boundary numbers, Unicode strings, and oversized inputs.
- **Error Recovery:** Check that failure scenarios (e.g. invalid configs, file not found, network timeouts, subprocess failures) are explicitly exercised.
- **State Invariants:** Ensure that state rollback or cleanup occurs when operations fail mid-flight.

## 4. Regression Prevention
- Every bug fix must be accompanied by an automated test that reproduces the bug before the fix and passes after it.
- Automated tests must run deterministically without depending on external network access or order of execution.
