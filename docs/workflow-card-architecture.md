# Workflow card architecture

The workflow subsystem has two agents:

1. The **Workflow Extraction Agent** reads one paper package and writes an
   immutable, evidence-grounded graph of instantiated cards.
2. The **Ontology Induction Agent** analyzes many graphs, proposes versioned
   ontology changes, and derives reviewable mappings/refinements. It never
   mutates the source extraction.

The former extraction → canonicalization sequence is retired. Legacy raw and
canonical records remain readable during migration.

## Cards and instances

An ontology card describes a reusable concept:

```json
{
  "card_id": "card:ontology/3:operation:xrd-measurement",
  "kind": "operation",
  "canonical_name": "XRD Measurement",
  "semantic_type": "characterization.measurement",
  "aliases": ["X-ray diffraction measurement"],
  "parameter_slots": {
    "temperature": {"value_type": "quantity", "recommended_unit": "K"},
    "radiation_source": {"value_type": "string"},
    "scan_range": {"value_type": "quantity_range"}
  },
  "status": "active",
  "provenance": {"introduced_in": "ontology/3", "supporting_workflows": []}
}
```

Object and Operation instances share the same open structure: `canonical_name`,
`semantic_type`, `parameters`, `identity`, `state`, `role`, `context`, source
terminology/evidence, and an optional `card_id`. Object cards emphasize stable
identity and changing state. Operation cards emphasize input/output roles and
run parameters. Slots are hints, not a closed-world validator: novel explicit
keys are retained and become signals for ontology induction.

## Graph

Instances are connected with typed, evidenced edges. `input_to` is Object →
Operation and `produces` is Operation → Object. Edge roles distinguish multiple
inputs/outputs. Explicit coarse/fine relations are retained. Each graph records
the ontology version used, unresolved information, and a reproducibility
assessment with missing details and assumptions.

## Reproducibility and abstraction

Canonical names stay short and reusable; the lossless evidence layer stays
paper-specific. Exact settings belong in structured fields, including units and
uncertainty when given. Unknown but important details remain in
`unparsed_modifiers` or `unresolved_information`; absent critical settings are
listed rather than guessed. Thus ontology remapping can change without erasing
what the authors reported.

## Evolution

Ontology induction clusters observed names, aliases, parameters, I/O
signatures, and recurring subgraphs. Proposals require corpus evidence and human
review. Releases are immutable and additive where possible. Merges/deprecations
carry replacement mappings. Refinement produces a new derived overlay with its
source graph ID, old/new ontology versions, mapping confidence, rationale, and
agent/model provenance; rollback simply selects the previous overlay.

## Example: experiment

```json
{
  "schema_version": "workflow-cards/2.0",
  "ontology_version": "ontology/3",
  "nodes": [
    {"node_id": "n1", "node_kind": "object", "canonical_name": "Material",
     "identity": {"sample_id": "Material A"}},
    {"node_id": "n2", "node_kind": "operation", "canonical_name": "XRD Measurement",
     "parameters": {"temperature": {"value": 300, "unit": "K"},
                    "radiation_source": "Cu Kα"}},
    {"node_id": "n3", "node_kind": "object", "canonical_name": "XRD Spectrum"}
  ],
  "edges": [
    {"source_node": "n1", "relation_type": "input_to", "target_node": "n2"},
    {"source_node": "n2", "relation_type": "produces", "target_node": "n3"}
  ]
}
```

## Example: computation

`MAPbI3` → **Band Structure Calculation** → `Band Structure`, where the
operation parameters contain `method: DFT`, software/version, functional,
k-point mesh, cutoff, convergence criteria, and other reported settings. None
of those values are embedded in the operation name.
