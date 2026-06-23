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
    "dft-relaxation": {"label": "Structure relaxation", "parameters": {"method": "dft"}, "aliases": ["DFT relaxation", "geometry optimization", "structure relaxation"]},
    "dft-single-point": {"label": "Single-point calculation", "parameters": {"method": "dft"}, "aliases": ["DFT single-point calculation", "single point calculation", "SCF calculation"]},
    "dft-band-structure": {"label": "Band structure calculation", "parameters": {"method": "dft"}, "aliases": ["DFT band structure calculation", "band structure calculation"]},
    "molecular-dynamics": {"label": "Molecular dynamics", "aliases": ["MD simulation"]},
    "powder-xrd": {"label": "Powder XRD", "aliases": ["PXRD", "powder X-ray diffraction"]},
    "sem": {"label": "SEM", "aliases": ["scanning electron microscopy"]},
    "annealing": {"label": "Annealing", "aliases": ["annealed", "heat treatment"]},
    "synthesis": {"label": "Synthesis", "aliases": ["prepared", "fabrication"]},
    "data-analysis": {"label": "Data analysis", "aliases": ["analysis", "data processing"]},
}


def get_schema_library_payload(schema_version: str | None = None) -> dict:
    seed = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "object_schemas": OBJECT_SCHEMAS,
        "operation_templates": {
            f"operation-template:{CANONICAL_SCHEMA_VERSION}:{slug}": value
            for slug, value in OPERATION_TEMPLATES.items()
        },
    }
    # Database-backed versions are optional so contracts and unit tests remain
    # usable without a running database.
    try:
        from mkb.db.engine import SyncSessionLocal
        from mkb.db.models import WorkflowSchemaVersion
        with SyncSessionLocal() as session:
            query = session.query(WorkflowSchemaVersion)
            row = (
                query.filter_by(name=schema_version).first()
                if schema_version
                else query.filter_by(status="active").order_by(WorkflowSchemaVersion.version.desc()).first()
            )
            if row:
                return row.payload
    except Exception:
        pass
    return seed
