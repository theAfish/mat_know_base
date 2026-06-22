"""Small, versioned seed library for Phase-2 canonicalization."""

CANONICAL_SCHEMA_VERSION = "workflow-schema/1.0"
CANONICALIZER_VERSION = "workflow-canonicalizer/1.0"

OBJECT_SCHEMAS = {
    "MaterialObject": "A material, sample, precursor, product, or material system.",
    "MoleculeObject": "A molecule, molecular species, ligand, or molecular composition.",
    "StructureObject": "A crystal, atomic, molecular, or derived structural representation.",
    "PropertyObject": "A measured or calculated material/molecular property.",
    "DataObject": "Raw or processed numerical, image, spectrum, table, or model data.",
}

OPERATION_TEMPLATES = {
    "dft-relaxation": {"label": "DFT relaxation", "aliases": ["geometry optimization", "structure relaxation"]},
    "dft-single-point": {"label": "DFT single-point calculation", "aliases": ["single point calculation", "SCF calculation"]},
    "dft-band-structure": {"label": "DFT band structure calculation", "aliases": ["band structure calculation"]},
    "molecular-dynamics": {"label": "Molecular dynamics", "aliases": ["MD simulation"]},
    "powder-xrd": {"label": "Powder XRD", "aliases": ["PXRD", "powder X-ray diffraction"]},
    "sem": {"label": "SEM", "aliases": ["scanning electron microscopy"]},
    "annealing": {"label": "Annealing", "aliases": ["annealed", "heat treatment"]},
    "synthesis": {"label": "Synthesis", "aliases": ["prepared", "fabrication"]},
    "data-analysis": {"label": "Data analysis", "aliases": ["analysis", "data processing"]},
}


def get_schema_library_payload() -> dict:
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "object_schemas": OBJECT_SCHEMAS,
        "operation_templates": {
            f"operation-template:{CANONICAL_SCHEMA_VERSION}:{slug}": value
            for slug, value in OPERATION_TEMPLATES.items()
        },
    }
