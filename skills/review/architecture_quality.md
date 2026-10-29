# Architecture & Code Quality Review

## Overview
This skill guides the architecture reviewer persona in evaluating system design, code cleanliness, maintainability, and structural coherence.

---

## 1. Code Smells & Anti-Patterns
- **Long Methods / Complex Functions:** Identify functions exceeding recommended length or cyclomatic complexity. Recommend decomposition into small, focused subroutines.
- **God Objects / Monoliths:** Flag classes or modules that handle disparate responsibilities (e.g. business logic mixed with HTTP rendering and file persistence).
- **Primitive Obsession:** Identify use of primitive types where rich value objects or domain models (e.g. Pydantic models, Enums) provide type safety and semantic clarity.
- **Dead Code & Speculative Generality:** Detect unused helper functions, commented-out blocks, or over-engineered generic layers.

## 2. Modularity, Coupling & Cohesion
- **High Cohesion:** Related functions and data structures should reside within the same module or package boundary.
- **Loose Coupling:** Components should communicate through defined public interfaces or protocols, minimizing direct internal dependencies.
- **Dependency Inversion:** High-level policy modules should not depend on low-level detail modules; both should depend on abstractions.

## 3. Maintainability & Error Handling
- **Defensive & Explicit Errors:** Catch specific exception types rather than bare `except:` or `except Exception: pass`. Ensure exceptions preserve context (`raise NewError(...) from exc`).
- **Idiomatic Conventions:** Verify conformity with the project's established style guides, naming conventions, and file organization.
- **Documentation & Invariants:** Verify that public classes and interfaces document their behavioral contract, parameter expectations, and return guarantees.
