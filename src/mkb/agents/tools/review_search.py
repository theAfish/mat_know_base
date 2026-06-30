"""Opt-in external search tools for projection review."""

from __future__ import annotations

import re
from typing import Any

import requests

DEFAULT_TIMEOUT = 20
UNIPROT_SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"
UNIPROT_ENTRY_URL = "https://rest.uniprot.org/uniprotkb/{accession}.json"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
VALID_REVIEW_SEARCH_TOOL_NAMES = {"web", "uniprot", "crossref", "ncbi"}


def _truncate(value: str | None, max_chars: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def web_search(query: str, limit: int = 5) -> dict:
    """Search the public web for review evidence.

    Use this only when local source files and the knowledge frame are
    insufficient, and cite the returned URL/title in review notes for any
    correction based on external evidence.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return {"error": "query must not be empty"}
    limit = max(1, min(int(limit or 5), 10))

    try:
        from ddgs import DDGS
    except ImportError:
        return {"error": "ddgs is not installed.", "query": cleaned}

    try:
        with DDGS() as ddgs:
            items = list(ddgs.text(cleaned, max_results=limit))
    except Exception as exc:
        return {"error": f"Web search failed: {exc}", "query": cleaned}

    results = []
    for item in items[:limit]:
        results.append({
            "title": _truncate(item.get("title"), 300),
            "url": item.get("href") or item.get("url"),
            "snippet": _truncate(item.get("body") or item.get("snippet"), 700),
        })
    return {
        "query": cleaned,
        "result_count": len(results),
        "results": results,
    }


def _uniprot_entry_summary(entry: dict[str, Any], *, include_sequence: bool) -> dict:
    accession = entry.get("primaryAccession")
    protein = entry.get("proteinDescription") or {}
    recommended = protein.get("recommendedName") or {}
    protein_name = ((recommended.get("fullName") or {}).get("value"))
    organism = (entry.get("organism") or {}).get("scientificName")
    genes = [
        (name.get("value") or "")
        for gene in entry.get("genes") or []
        for name in [gene.get("geneName") or {}]
        if name.get("value")
    ]
    sequence_obj = entry.get("sequence") or {}
    summary = {
        "accession": accession,
        "entry_type": entry.get("entryType"),
        "protein_name": protein_name,
        "organism": organism,
        "genes": genes,
        "sequence_length": sequence_obj.get("length"),
        "uniProtkb_id": entry.get("uniProtkbId"),
        "source": f"https://rest.uniprot.org/uniprotkb/{accession}" if accession else None,
    }
    if include_sequence:
        summary["sequence"] = sequence_obj.get("value")
    return summary


def search_uniprot(query: str, limit: int = 5, include_sequence: bool = False) -> dict:
    """Search UniProtKB for proteins/sequences matching a free-text query.

    Use this when a projection field, source column, reference, species name,
    gene/protein name, or approximate identifier suggests an external protein
    sequence source. If a sequence field is empty, search with the most specific
    combined query available from the row (for example accession + organism +
    protein name), then fetch or copy the sequence only when the match is clear.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return {"error": "query must not be empty"}
    limit = max(1, min(int(limit or 5), 10))

    try:
        resp = requests.get(
            UNIPROT_SEARCH_URL,
            params={
                "query": cleaned,
                "format": "json",
                "size": limit,
                "fields": "accession,id,protein_name,organism_name,gene_names,length,sequence",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"error": f"UniProt search failed: {exc}", "query": cleaned}

    payload = resp.json()
    results = [
        _uniprot_entry_summary(entry, include_sequence=include_sequence)
        for entry in payload.get("results", [])
    ]
    return {
        "query": cleaned,
        "result_count": len(results),
        "results": results,
    }


def fetch_uniprot_sequence(accession: str) -> dict:
    """Fetch the canonical UniProtKB amino-acid sequence for an accession.

    Use this after an accession or search result has been verified against
    the projection row's other context. Do not use the returned sequence if
    the organism/protein/source context conflicts with the projection.
    """
    cleaned = (accession or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", cleaned):
        return {"error": "accession must be a simple UniProt accession/id"}

    try:
        resp = requests.get(
            UNIPROT_ENTRY_URL.format(accession=cleaned),
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"error": f"UniProt fetch failed: {exc}", "accession": cleaned}

    return _uniprot_entry_summary(resp.json(), include_sequence=True)


def _first_genbank_qualifier(record: str, qualifier: str) -> str | None:
    match = re.search(rf'/{re.escape(qualifier)}="([^"]*)"', record, flags=re.DOTALL)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _parse_genbank_translations(record: str) -> list[dict[str, Any]]:
    translations: list[dict[str, Any]] = []
    feature_matches = list(re.finditer(r"^     CDS\s+(.+?)(?=^     \S|\Z)", record, flags=re.MULTILINE | re.DOTALL))
    for idx, feature in enumerate(feature_matches, start=1):
        text = feature.group(0)
        translation = _first_genbank_qualifier(text, "translation")
        if not translation:
            continue
        sequence = re.sub(r"[^A-Za-z*]", "", translation)
        location = (text.splitlines()[0].split("CDS", 1)[1] or "").strip()
        translations.append({
            "index": idx,
            "location": location,
            "protein_id": _first_genbank_qualifier(text, "protein_id"),
            "product": _first_genbank_qualifier(text, "product"),
            "gene": _first_genbank_qualifier(text, "gene"),
            "translation_length": len(sequence.replace("*", "")),
            "translation": sequence,
        })
    return translations


def fetch_ncbi_nucleotide_record(accession: str) -> dict:
    """Fetch a GenBank/NCBI nuccore record and return CDS translations.

    Use this for nucleotide accessions such as GenBank IDs in source fields
    (for example MF496231). Prefer this direct database lookup over web_search
    when the user asks to recover protein sequences from a nucleotide record.
    If multiple CDS translations are returned, pick one only when product/gene
    and source-row context make the match clear.
    """
    cleaned = (accession or "").strip()
    if not re.fullmatch(r"[A-Za-z]{1,4}_?[A-Za-z0-9]+(?:\.\d+)?", cleaned):
        return {"error": "accession must look like a nucleotide accession/id"}

    try:
        resp = requests.get(
            NCBI_EFETCH_URL,
            params={
                "db": "nuccore",
                "id": cleaned,
                "rettype": "gb",
                "retmode": "text",
            },
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "mat-know-base/0.1 (mailto:unknown@example.com)"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"error": f"NCBI fetch failed: {exc}", "accession": cleaned}

    record = resp.text or ""
    if not record.strip() or "Error" in record[:200]:
        return {"error": "NCBI returned an empty or error record", "accession": cleaned}

    locus = None
    definition = None
    organism = None
    locus_match = re.search(r"^LOCUS\s+(.+)$", record, flags=re.MULTILINE)
    if locus_match:
        locus = re.sub(r"\s+", " ", locus_match.group(1)).strip()
    definition_match = re.search(
        r"^DEFINITION\s+(.+?)(?=^ACCESSION\s)",
        record,
        flags=re.MULTILINE | re.DOTALL,
    )
    if definition_match:
        definition = re.sub(r"\s+", " ", definition_match.group(1)).strip()
    organism_match = re.search(
        r"^\s+ORGANISM\s+(.+)$",
        record,
        flags=re.MULTILINE,
    )
    if organism_match:
        organism = organism_match.group(1).strip()

    translations = _parse_genbank_translations(record)
    return {
        "accession": cleaned,
        "source": f"https://www.ncbi.nlm.nih.gov/nuccore/{cleaned}",
        "locus": locus,
        "definition": definition,
        "organism": organism,
        "translation_count": len(translations),
        "translations": translations,
        "record_preview": _truncate(record, 1200),
    }


def search_crossref_works(query: str, limit: int = 5) -> dict:
    """Search Crossref works for a likely publication/reference match.

    Use this when a projection source/reference field is incomplete or slightly
    garbled and you need bibliographic clues (title, DOI, year, authors) to
    disambiguate an external source before searching sequence databases.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return {"error": "query must not be empty"}
    limit = max(1, min(int(limit or 5), 10))

    try:
        resp = requests.get(
            CROSSREF_WORKS_URL,
            params={"query.bibliographic": cleaned, "rows": limit},
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "mat-know-base/0.1 (mailto:unknown@example.com)"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"error": f"Crossref search failed: {exc}", "query": cleaned}

    items = (resp.json().get("message") or {}).get("items") or []
    results = []
    for item in items:
        authors = []
        for author in item.get("author") or []:
            name = " ".join(
                part
                for part in [author.get("given"), author.get("family")]
                if part
            ).strip()
            if name:
                authors.append(name)
        published = (
            item.get("published-print")
            or item.get("published-online")
            or item.get("issued")
            or {}
        )
        year_parts = published.get("date-parts") or []
        year = year_parts[0][0] if year_parts and year_parts[0] else None
        results.append({
            "title": _truncate((item.get("title") or [None])[0], 300),
            "doi": item.get("DOI"),
            "year": year,
            "authors": authors[:8],
            "container_title": _truncate((item.get("container-title") or [None])[0], 200),
            "url": item.get("URL"),
            "score": item.get("score"),
        })

    return {
        "query": cleaned,
        "result_count": len(results),
        "results": results,
    }


REVIEW_SEARCH_TOOL_GROUPS = {
    "web": [web_search],
    "uniprot": [search_uniprot, fetch_uniprot_sequence],
    "ncbi": [fetch_ncbi_nucleotide_record],
    "crossref": [search_crossref_works],
}


def normalize_review_search_tool_names(tool_names) -> list[str]:
    if isinstance(tool_names, str):
        candidates = [tool_names]
    elif isinstance(tool_names, list):
        candidates = tool_names
    else:
        candidates = ["web"]

    normalized: list[str] = []
    for name in candidates:
        key = str(name).strip().lower()
        if key in VALID_REVIEW_SEARCH_TOOL_NAMES and key not in normalized:
            normalized.append(key)
    return normalized or ["web"]


def get_projection_review_search_tools(tool_names) -> list:
    tools = []
    for name in normalize_review_search_tool_names(tool_names):
        tools.extend(REVIEW_SEARCH_TOOL_GROUPS[name])
    return tools
