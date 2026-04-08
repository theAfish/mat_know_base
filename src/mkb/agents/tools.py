"""
Agent tool interfaces – functions that google-adk agents can call.

These are the building blocks for Phase 3.
Each function is designed to be registered as a Tool in the ADK framework.
"""

import uuid

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Asset, KnowledgeNode, ProcessingStatus
from mkb.storage.s3 import download_bytes


def list_unprocessed_assets(status: str = "STORED") -> list[dict]:
    """Return assets that have not yet been fully processed."""
    with SyncSessionLocal() as session:
        target = ProcessingStatus(status)
        assets = session.query(Asset).filter_by(status=target).all()
        return [
            {
                "asset_id": str(a.asset_id),
                "filename": a.filename,
                "mime_type": a.mime_type,
                "sha256": a.sha256,
            }
            for a in assets
        ]


def fetch_raw_binary(asset_id: str) -> bytes:
    """Fetch the raw bytes of an asset from object storage."""
    with SyncSessionLocal() as session:
        asset = session.query(Asset).filter_by(asset_id=uuid.UUID(asset_id)).one()
        return download_bytes(asset.s3_bucket, asset.s3_key)


def update_knowledge_node(
    entity_type: str,
    label: str,
    source_asset_id: str | None = None,
    properties: dict | None = None,
) -> str:
    """Create or update a knowledge node. Returns the node_id."""
    with SyncSessionLocal() as session:
        node = KnowledgeNode(
            node_id=uuid.uuid4(),
            entity_type=entity_type,
            label=label,
            source_asset_id=uuid.UUID(source_asset_id) if source_asset_id else None,
            properties=properties or {},
        )
        session.add(node)
        session.commit()
        return str(node.node_id)
