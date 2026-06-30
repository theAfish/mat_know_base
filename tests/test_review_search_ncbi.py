from mkb.agents.tools.review_search import _parse_genbank_translations


def test_parse_genbank_translations_extracts_cds_translation():
    record = """
FEATURES             Location/Qualifiers
     source          1..1000
                     /organism="Hyriopsis cumingii"
     CDS             10..366
                     /gene="HcPif"
                     /product="HcPif80"
                     /protein_id="ABC12345.1"
                     /translation="MAVTAA
                     GGLK"
     CDS             complement(500..800)
                     /product="other protein"
ORIGIN
//
"""

    translations = _parse_genbank_translations(record)

    assert translations == [
        {
            "index": 1,
            "location": "10..366",
            "protein_id": "ABC12345.1",
            "product": "HcPif80",
            "gene": "HcPif",
            "translation_length": 10,
            "translation": "MAVTAAGGLK",
        }
    ]
