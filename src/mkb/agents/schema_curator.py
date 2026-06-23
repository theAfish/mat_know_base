"""Deprecated compatibility facade for the Ontology Induction Agent."""

from mkb.agents.ontology_induction import (
    build_ontology_induction_agent,
    run_ontology_induction,
)

build_schema_curator = build_ontology_induction_agent
run_schema_curator = run_ontology_induction

__all__ = ["build_schema_curator", "run_schema_curator"]
