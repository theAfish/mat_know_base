# Roadmap: Scientific Knowledge System (Local MVP)

## Project Overview
Building a self-hosted, scalable infrastructure to transform heterogeneous scientific artifacts into a queryable Knowledge Graph.

* **Current Goal:** Local MVP (Phase 1 & 2).
* **Infrastructure:** Dockerized MinIO + PostgreSQL.
* **AI Strategy:** `google-adk` for agentic processing and KB reasoning.

---

## Phase 1: Core Infrastructure (The "Vault")
**Goal:** Establish the immutable storage layer for raw scientific data.

* [ ] **Containerized Environment:**
    * Set up `docker-compose.yaml` with **MinIO** (S3-compatible) and **PostgreSQL**.
    * Configure PostgreSQL with the `pgvector` extension (critical for future agentic search).
* [ ] **Storage Logic Implementation:**
    * Implement **Content-Addressable Storage (CAS)**: Files are stored by SHA256 hash to prevent duplicates at the byte level.
    * Define bucket structure: `raw/`, `archive/`, `temp/`.
* [ ] **Metadata Schema:**
    * Initialize `assets` table with fields for `asset_id`, `hash`, `mime_type`, and a `JSONB` column for domain-specific scientific metadata (e.g., lattice parameters, chemical formulas).

---

## Phase 2: Ingestion & Provenance (The "Pipeline")
**Goal:** Reliable data entry with full traceability.

* [ ] **Python Ingestion Worker:**
    * Build a script to scan a local directory or take an API upload.
    * Implement "Pre-flight" checks: file integrity and MIME-type sniffing.
* [ ] **Scientific Batching:**
    * Implement `ingestion_batch` logic to group related files (e.g., a paper PDF + its associated CSV datasets).
* [ ] **State Management:**
    * Implement a `processing_status` tracker: `[PENDING -> STORED -> EXTRACTED -> GRAPHED]`.

---

## Phase 3: Agent Integration (Preparing for `google-adk`)
**Goal:** Create the interfaces that your `google-adk` agents will use to "read" and "think."

* [ ] **Standardized Data Conversion:**
    * Develop a tool-calling interface for agents to trigger OCR or PDF-to-Markdown conversions.
* [ ] **Agent-Ready APIs:**
    * Expose internal endpoints for the `google-adk` agents to:
        1.  `list_unprocessed_assets()`
        2.  `fetch_raw_binary(asset_id)`
        3.  `update_knowledge_node(metadata_json)`
* [ ] **Vector Embedding Pipeline:**
    * Integrate a local embedding model to populate `pgvector` columns for semantic search.

---

## Phase 4: Knowledge Modeling (The "Brain")
**Goal:** Constructing the first iteration of the Knowledge Graph.

* [ ] **Entity Extraction:**
    * Deploy agents to identify scientific entities (Materials, Methods, Parameters, Authors, etc.).
* [ ] **Relationship Mapping AND Reification:**
    * Define basic edge types: `MEASURED`, `HAS_PROPERTY`, `SIMULATED_BY`, `CONTAINS_ELEMENT`, `HAS_STRUCTURE`, `STUDIED_IN`.
* [ ] **Attributes & Provenance:**
    * Ensure every node and edge has a link back to its source `asset_id` for full traceability.
    * Value nodes with attributes like `confidence_score`, `data`, `timestamp`, etc.
* [ ] **Graph Visualization:**
    * Implement a basic local UI (e.g., Streamlit or a Three.js-based graph) to verify connections.

---

## Local MVP Tech Stack Summary
| Component | Technology |
| :--- | :--- |
| **Object Storage** | MinIO (S3-API) |
| **Metadata / Vector DB** | PostgreSQL + `pgvector` |
| **Agent Framework** | `google-adk` (Planned) |
| **Language** | Python 3.10+ |
| **Orchestration** | Docker Compose |

---

## Success Criteria for MVP
1.  **Immutability:** Re-uploading the same paper results in 0 byte-growth in storage.
2.  **Traceability:** Every node in the Knowledge Graph can be traced back to its specific `asset_id` in MinIO.
3.  **Portability:** The entire environment can be moved to a cluster by simply changing the S3 endpoint and DB connection strings.

---

### A Note on `google-adk` Integration
As you move toward using `google-adk`, treat your **Raw Data Layer** as a "Tool" within the ADK environment. The agents should not bypass the Raw Data Layer; instead, they should act as the "interpreters" that take data from the **Conversion Layer** and write structured insights into the **Knowledge Modeling Layer**.
