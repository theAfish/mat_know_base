# Versioning and deprecation policy

MKB uses Semantic Versioning. The supported public Python contract is the names in
`mkb.__all__`, their documented typed methods, the `mkb` CLI, and the documented HTTP
API. Internal modules, ORM models, and service-private helpers are not compatibility
surfaces.

Before 1.0, incompatible SDK changes require a minor-version release, a changelog
entry, migration notes, and contract-test updates. After 1.0, incompatible public API
changes require a major release. Patch releases remain backward compatible.

A public API is deprecated only after its replacement has feature parity. A
deprecation must emit `DeprecationWarning`, appear in the changelog and migration
guide, and remain operational for at least one complete minor release. Persisted
legacy data and its read adapters are retained for at least one complete release and
are never removed merely to simplify implementation.

Database changes are additive first. A library refuses portable databases whose
schema version is newer than it supports. Normal code rollback switches back to
compatible readers; it does not delete or rewrite user data.
