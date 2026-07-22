"""Script-first amino-acid sequence normalization post-processor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
import sys


CANONICAL = set("ACDEFGHIKLMNPQRSTVWY")
AA3 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}
AA_FULL = {
    "ALANINE": "A", "ARGININE": "R", "ASPARAGINE": "N", "ASPARTATE": "D",
    "ASPARTICACID": "D", "CYSTEINE": "C", "GLUTAMINE": "Q", "GLUTAMATE": "E",
    "GLUTAMICACID": "E", "GLYCINE": "G", "HISTIDINE": "H", "ISOLEUCINE": "I",
    "LEUCINE": "L", "LYSINE": "K", "METHIONINE": "M", "PHENYLALANINE": "F",
    "PROLINE": "P", "SERINE": "S", "THREONINE": "T", "TRYPTOPHAN": "W",
    "TYROSINE": "Y", "VALINE": "V",
}
PTMS = {
    "S": r"\[pS\]|\(pS\)|pSer|phosphoserine|pS",
    "T": r"\[pT\]|\(pT\)|pThr|phosphothreonine|pT",
    "Y": r"\[pY\]|\(pY\)|pTyr|phosphotyrosine|pY",
}
N_TERMINI = [("Ace-", "Acetylation"), ("Ac-", "Acetylation"), ("Acetyl-", "Acetylation"), ("NH2-", "Free amine")]
C_TERMINI = [("-CONH2", "Amidation"), ("-NH2", "Amidation"), ("-Amide", "Amidation")]


@dataclass
class NormalizeResult:
    normalized_sequence: str
    need_normalization: bool
    success: bool
    modifications: list = field(default_factory=list)
    terminal_modifications: dict = field(default_factory=lambda: {"n_terminal": [], "c_terminal": []})
    removed_groups: list = field(default_factory=list)
    removed_noncanonical_residues: list = field(default_factory=list)
    unknown_tokens: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    confidence: float = 1.0


def _expand_repeats(text: str, result: NormalizeResult) -> str:
    stack = [("", None)]
    last_closed = ""
    index = 0
    while index < len(text):
        char = text[index]
        if char in "([":
            stack.append(("", char))
            index += 1
            continue
        if char in ")]":
            close = ")" if char == ")" else "]"
            opener = "(" if close == ")" else "["
            end = index + 1
            while end < len(text) and text[end].isdigit():
                end += 1
            count_text = text[index + 1:end]
            count = int(count_text) if count_text else 1
            if len(stack) > 1 and stack[-1][1] == opener:
                content, _ = stack.pop()
                last_closed = content * count
                stack[-1] = (stack[-1][0] + last_closed, stack[-1][1])
            elif count_text and last_closed:
                stack[-1] = (stack[-1][0] + last_closed * count, stack[-1][1])
                result.warnings.append("Unmatched closing repeat bracket interpreted as implicit outer repeat.")
            else:
                stack[-1] = (stack[-1][0] + text[index:end], stack[-1][1])
                result.warnings.append("Unmatched closing bracket could not be expanded.")
            index = end
            continue
        stack[-1] = (stack[-1][0] + char, stack[-1][1])
        index += 1
    if len(stack) > 1:
        result.warnings.append("Unclosed repeat brackets detected; content was kept literally.")
    return "".join(buffer for buffer, _ in stack)


def normalize_sequence(sequence: str) -> NormalizeResult:
    raw = str(sequence or "").strip()
    if re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", raw):
        return NormalizeResult(raw, False, True)
    result = NormalizeResult("", True, True)
    text = raw
    for token, label in N_TERMINI:
        if text.lower().startswith(token.lower()):
            result.terminal_modifications["n_terminal"].append(label)
            text = text[len(token):]
            break
    for token, label in C_TERMINI:
        if text.lower().endswith(token.lower()):
            result.terminal_modifications["c_terminal"].append(label)
            text = text[:-len(token)]
            break
    text = text.replace("-", " ")
    for match in reversed(list(re.finditer(r"\(([^()]*)\)", text))):
        words = re.findall(r"[A-Za-z]+", match.group(1))
        if len(words) >= 3 and sum(any(char.islower() for char in word) for word in words) >= 3:
            result.removed_groups.append({"type": "annotation", "text": match.group(1)})
            text = text[:match.start()] + text[match.end():]
    text = _expand_repeats(text, result)
    ptm_pattern = "|".join(f"(?:{pattern})" for pattern in PTMS.values())
    pieces = []
    for fragment in re.split(f"({ptm_pattern})", text, flags=re.I):
        if not fragment:
            continue
        if re.fullmatch(ptm_pattern, fragment, flags=re.I):
            pieces.append(fragment)
        else:
            pieces.extend(re.findall(r"[A-Za-z0-9]+|[\[\]\(\)]", fragment))
    output = []
    for token in pieces:
        for residue, pattern in PTMS.items():
            if re.fullmatch(pattern, token, flags=re.I):
                output.append(residue)
                result.modifications.append({"position": len(output), "residue": residue, "type": "phosphorylation", "original": token})
                break
        else:
            upper = token.upper()
            if upper in AA3:
                output.append(AA3[upper])
            elif upper in AA_FULL:
                output.append(AA_FULL[upper])
            elif token == upper and re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", token):
                output.extend(token)
            elif re.fullmatch(r"[A-Za-z]+", token):
                result.removed_groups.append({"type": "annotation", "token": token})
            else:
                result.unknown_tokens.append(token)
    result.normalized_sequence = "".join(output)
    if result.unknown_tokens:
        result.success, result.confidence = False, 0.5
    elif result.removed_groups:
        result.success, result.confidence = False, 0.6
        result.warnings.append("Removed annotation text may contain sequence-relevant information; manual review required.")
    return result


def main() -> None:
    payload = json.load(sys.stdin)
    projections = payload.get("projections") or []
    if not projections:
        print(json.dumps({"run_agent": False, "context": {"message": "No projections to normalize."}}))
        return
    projection = projections[0]
    data = projection.get("data") or {}
    updates, reports, failures = [], [], []
    for collection, source_field in (("templates", "sequence"), ("functional_modules", "amino_acid_sequence")):
        for index, row in enumerate(data.get(collection) or []):
            raw = row.get(source_field) if isinstance(row, dict) else None
            if not isinstance(raw, str) or not raw.strip():
                continue
            result = normalize_sequence(raw)
            report = asdict(result)
            base = f"{collection}[{index}]"
            updates.append({"path": f"{base}.sequence_normalization", "value": report})
            if result.success:
                updates.append({"path": f"{base}.normalized_sequence", "value": result.normalized_sequence})
            entry = {"path": base, "source_field": source_field, "original_sequence": raw, "result": report}
            reports.append(entry)
            if not result.success:
                failures.append(entry)
    patch = None
    if updates:
        patch = {
            "winning_projection_id": projection["projection_id"],
            "updates": updates,
            "review_notes": f"Sequence normalizer processed {len(reports)} sequence field(s); {len(failures)} require review.",
        }
    print(json.dumps({
        "run_agent": bool(failures),
        "patch": patch,
        "context": {"kind": "sequence_normalization", "processed": reports, "failed": failures},
    }))


if __name__ == "__main__":
    main()