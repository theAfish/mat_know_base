# Sequence Normalizer

Normalizes amino-acid sequence strings in biomineralization projection rows.

Run `sequence_normalizer.py` as a post-processor script. It accepts the standard
post-processor JSON payload on stdin and prints one JSON result. It processes the
selected live projection, writes `normalized_sequence` and
`sequence_normalization` fields for successful normalizations, and asks the
reviewer agent to resolve uncertain sequences.