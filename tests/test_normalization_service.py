from mkb.services.normalization import (
    canonical_label,
    merge_aliases,
    preserve_evidence,
    relation_identity,
    set_patch_value,
)


def test_same_paper_duplicates_and_repeated_reviews_are_idempotent():
    evidence = {"project_id": "paper-1", "field_path": "results[0]", "snippet": "value"}
    assert preserve_evidence([evidence], [evidence]) == [evidence]


def test_cross_paper_aliases_keep_canonical_name_out_of_aliases():
    assert merge_aliases("Lithium Iron Phosphate", ["LFP"], ["lithium iron phosphate", "LiFePO4"]) == ["LFP", "LiFePO4"]


def test_relation_identity_normalizes_spacing_and_case():
    assert relation_identity("  LFP", "HAS   PHASE", " Olivine ") == ("lfp", "has phase", "olivine")
    assert canonical_label("A   B") == "a b"


def test_conflicting_patch_changes_only_target_and_preserves_source():
    data = {"rows": [{"value": 1, "source_evidence": [{"project_id": "paper-1"}]}]}
    set_patch_value(data, "rows[0].value", 2)
    assert data == {"rows": [{"value": 2, "source_evidence": [{"project_id": "paper-1"}]}]}


def test_source_preservation_keeps_distinct_papers():
    refs = preserve_evidence(
        [{"project_id": "paper-1", "snippet": "a"}],
        [{"project_id": "paper-2", "snippet": "b"}],
    )
    assert [ref["project_id"] for ref in refs] == ["paper-1", "paper-2"]
