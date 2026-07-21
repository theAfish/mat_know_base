# Supported versions

MKB is currently pre-1.0. Security and correctness fixes target the latest commit on
the default branch and the latest published release, if one exists. Older commits,
development branches, modified deployments, and legacy canonical-workflow surfaces
receive best-effort compatibility support only.

Upgrade to the latest supported release before reporting a defect when practical. A
deployment that cannot upgrade should include its exact commit, migration revision,
Python/Node/Docker versions, and sanitized `make doctor` output in a private report.
