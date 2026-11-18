"""Local body lookup (FIX-PLAN-S6 C): LGD CSV -> local_body -> town name or GPS -> the body."""
import sys
from pathlib import Path

import pytest

from app.rag import local_body

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import load_local_bodies

# Shaped like the lgdirectory.gov.in "All Urban Local Bodies of India" CSV: a title line, the
# header, rows, a footer. Codes 99000xx are made up so tests never touch real rows.
LGD_CSV = """All Urban Local Bodies of India,,,,,,,
S.No.,State Code,State Name,Local Body Code,Local Body Name (In English),Local Body Name (In Local),Local Body Type Name,District Name
1,6,Haryana,9900001,Badhra,बाढड़ा,Municipal Committee,Charkhi Dadri
2,6,Haryana,9900002,Charkhi Dadri,चरखी दादरी,Municipal Council,Charkhi Dadri
3,6,Haryana,9900003,Badli,बादली,Municipal Committee,Jhajjar
4,8,Rajasthan,9900004,Jaipur Greater,जयपुर ग्रेटर,Municipal Corporation,Jaipur
5,8,Rajasthan,9900005,Badhra Test Town,,Nagar Palika,Jaipur
Report generated on 15-11-2026,,,,,,,
"""


def test_reads_the_lgd_csv_shape():
    rows = load_local_bodies.read_rows(LGD_CSV)
    assert len(rows) == 5
    badhra = rows[0]
    assert badhra == {"lgd_code": 9900001, "name": "Badhra", "name_local": "बाढड़ा",
                      "body_type": "Municipal Committee", "district": "Charkhi Dadri",
                      "state_name": "Haryana", "state_code": "HR", "name_norm": "badhra"}
    assert rows[3]["state_code"] == "RJ"


def test_name_normalising_drops_body_type_words():
    assert local_body.norm_name("Municipal Committee Badhra") == "badhra"
    assert local_body.norm_name("  BADHRA (M.C.) ") == "badhra"
    assert local_body.state_code("Andaman And Nicobar Islands") == "AN"


@pytest.fixture
def bodies():
    from test_health import db_is_up

    from app.db import connect

    if not db_is_up():
        pytest.skip("needs the local DB")
    with connect() as conn:
        load_local_bodies.load(conn, load_local_bodies.read_rows(LGD_CSV))
    yield
    with connect() as conn:
        conn.execute("DELETE FROM local_body WHERE lgd_code BETWEEN 9900000 AND 9900099")


def test_badhra_haryana_is_its_municipal_committee(bodies):
    from app.db import connect

    with connect() as conn:
        hit = local_body.find(conn, "Badhra", "HR")
        assert hit["label"] == "Municipal Committee Badhra, Charkhi Dadri, Haryana"
        assert local_body.find(conn, "badhra town", "HR")["lgd_code"] == 9900001  # fuzzy
        assert local_body.find(conn, "Badhraa", "HR")["lgd_code"] == 9900001      # typo
        assert local_body.find(conn, "Gurugram", "HR") is None  # never a wrong guess


def test_gps_uses_our_state_outline_and_the_osm_town_name(bodies):
    from app.db import connect

    with connect() as conn:
        if not conn.execute("SELECT 1 FROM state_boundary WHERE code = 'HR'").fetchone():
            pytest.skip("state outlines not loaded")
        # A pin in Charkhi Dadri district; the reverse geocoder is a stub (no network in tests).
        hit = local_body.find(conn, lat=28.59, lng=76.27, reverse=lambda lat, lng: "Badhra")
        assert hit["state_code"] == "HR" and hit["body_type"] == "Municipal Committee"
