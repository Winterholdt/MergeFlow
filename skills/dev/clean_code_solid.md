# Clean Code & SOLID Principles

## Overview
This skill guides the developer agent in writing maintainable, idiomatic, and robust software adhering to Clean Code standards and SOLID engineering principles.

---

## 1. Single Responsibility Principle (SRP)
- Every class, module, and function must have exactly one reason to change and one focused responsibility.
- Break down monolithic functions into small, cohesive, single-purpose functions (aim for < 30 lines).
- Separate business logic from I/O, persistence, presentation, and framework orchestration.

## 2. Encapsulation & Information Hiding
- Keep internal implementation details, mutable state, and helper structures private or protected.
- Expose minimal, stable, and explicit public interfaces.
- Protect domain invariants by encapsulating state transitions behind validated methods rather than direct field mutations.

## 3. Open/Closed & Dependency Inversion
- Design components to be open for extension but closed for modification.
- Depend upon abstractions (interfaces, abstract base classes, Protocols) rather than concrete implementations.
- Inject dependencies (e.g. config, storage, providers) rather than hardcoding singleton instantiation or global state.

## 4. DRY (Don't Repeat Yourself) & KISS (Keep It Simple, Stupid)
- Eliminate duplicate business logic, validation rules, and magic values by centralizing them into reusable functions or constants.
- Resist premature abstraction: two similar lines of code do not necessarily justify a complex meta-programming or generic hierarchy.
- Prefer boring, readable, and direct solutions over clever or convoluted constructs.

## 5. Type Safety & Self-Documenting Code
- Use complete and strict type hints on all function parameters, return types, and class attributes.
- Choose intention-revealing, descriptive names for functions, variables, and classes; avoid obscure abbreviations.
- Write docstrings explaining *why* decisions were made, while letting clean code explain *how*.
