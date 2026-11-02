"""Sprint 5: real department routing (73 rules), routing questions and the chat transcript.
Fake models (PROVIDER_MODE=fake) + the real DB with the rules from data/rules_draft.csv."""

import csv
from itertools import count

import pytest
from api_helpers import COVERED, add_test_road, client, remove_test_roads
from test_health import db_is_up

from app.db import connect
from app.intake.catalog import DATA, categories, departments
from app.providers import fakes
from app.routing import engine
from app.routing.conditions import FACT_QUESTION, QUESTIONS, RULES

needs_db = pytest.mark.skipif(not db_is_up(), reason="database not reachable")

# Pins checked once against the real ward + zone layers (see the probe in the test log).
LANE = COVERED                                 # ward 185, the shared "Test lane" (residential)
NO_ROAD = {"lat": 28.5300, "lng": 77.2900}     # ward 187, no zone, BRPL (tests add an
                                              # unclassified road: width unknown -> ask)
PWD_PIN = {"lat": 28.5425, "lng": 77.3110}     # ward 185
MATHURA = {"lat": 28.5350, "lng": 77.2950}     # ward 187
NDMC = {"lat": 28.640633, "lng": 77.217211}    # ward 82, inside the NDMC area
CANTT = {"lat": 28.56204, "lng": 77.083059}    # ward 133, inside Delhi Cantonment
METRO = {"lat": 28.682405, "lng": 77.056246}   # ward 34, Nangloi Railway Station metro station
RIDGE = {"lat": 28.529729, "lng": 77.131045}   # ward 133, Ridge forest
IFC = {"lat": 28.586566, "lng": 77.24753}      # ward 144, on an I&FC trunk drain
ways = count(-500000, -1)


def load_csv_rules() -> list[dict]:
    """The 73 rules shaped like routing_rule rows (sorted by priority)."""
    with (DATA / "rules_draft.csv").open(encoding="utf-8", newline="") as f:
        rows = [r | {"categories": r["category"].split(";"), "priority": int(r["priority"]),
                     "confidence": float(r["confidence"])} for r in csv.DictReader(f)]
    return sorted(rows, key=lambda r: (r["priority"], r["rule_id"]))


@pytest.fixture
def road():
    """add(pin, **cols) puts a road through the pin; removed after the test."""
    made = []

    def add(pin, **cols):
        made.append(next(ways))
        with connect() as conn:
            add_test_road(conn, pin["lat"], pin["lng"], way=made[-1], **cols)

    yield add
    with connect() as conn:
        for way in made:
            remove_test_roads(conn, way)


def report(headers, pin, category, **extra):
    fakes.script(text=[{"category": category, **extra}])
    return client.post("/api/v1/reports", headers=headers,
                       data={"text": "problem hai", "lat": pin["lat"], "lng": pin["lng"]}).json()


def answer(headers, body, **payload):
    return client.post(f"/api/v1/reports/{body['report_id']}/answer", headers=headers,
                       json=payload)


def dossier(headers, body):
    assert body["state"] == "dossier_ready", body
    return client.get(f"/api/v1/dossiers/{body['dossier_id']}", headers=headers).json()


# ---------- every rule in the CSV is reachable, or flagged with a reason (no DB) ----------

def base_facts(category: str) -> dict:
    """A plain spot: no zone, an inner lane, BRPL area, open nala, ghat bank."""
    return {"category": category, "zone": "none", "zone_name": None, "nhai": False,
            "mathura": False, "pwd_list": False, "width": "narrow", "road_name": None,
            "discom": "BRPL", "drain": "open_nala", "ghat": "bank", "ifc": False, "_src": {}}


def facts_for(spec_when: dict, category: str) -> dict:
    facts = base_facts(category)
    for key, want in spec_when.items():
        if key == "owner":
            facts["width"] = "wide" if want == "PWD" else "narrow"
        elif key != "not_zone":
            facts[key] = want
    return facts


def test_every_rule_is_reachable_or_flagged():
    rules = load_csv_rules()
    assert len(rules) == 73 and set(RULES) == {r["rule_id"] for r in rules}
    reached, flagged = [], {}
    for rule in rules:
        spec = RULES[rule["rule_id"]]
        if "when" not in spec:
            flagged[rule["rule_id"]] = spec["role"]
            continue
        if rule["categories"] == ["*"]:  # GN-03: what decide() uses when nothing matched
            assert engine.fallback(rules) is rule
            reached.append(rule["rule_id"])
            continue
        cats = rule["categories"]
        hits = [c for c in cats if engine.match_rule(rules, facts_for(spec["when"], c))
                == ("rule", rule)]
        assert hits, f"{rule['rule_id']} can never be reached"
        reached.append(rule["rule_id"])
    # 'ask' rules: the matching question is asked when their fact is unknown.
    for rule_id, qid in [("RD-06", "road_class"), ("DR-05", "drain_type"),
                         ("SW-04", "drain_type"), ("SL-05", "which_discom"),
                         ("EL-04", "which_discom")]:
        rule = next(r for r in rules if r["rule_id"] == rule_id)
        facts = base_facts(rule["categories"][0]) | {QUESTIONS[qid]["fact"]: None}
        if qid == "drain_type":
            facts["width"] = None  # the drain is asked BEFORE the road (DEPARTMENTS.md #1)
        assert engine.match_rule(rules, facts) == ("ask", qid), rule_id
    # overlays: every EM-* category gets the emergency card
    for rule in rules:
        if flagged.get(rule["rule_id"]) == "overlay":
            assert all(categories()[c]["emergency"] == "yes" for c in rule["categories"])
    # 59 reachable + 14 flagged: 6 overlay, 4 ask, 1 candidate, 2 data_missing, 1 shadowed
    assert len(reached) + len(flagged) == 73 and len(reached) == 59, (len(reached), flagged)


def test_lookup_layers_are_never_owner_zones():
    from app.routing.conditions import OWNER_ZONES

    assert "discom_area" not in OWNER_ZONES and "ifc_trunk_drain" not in OWNER_ZONES
    assert set(FACT_QUESTION.values()) == set(QUESTIONS)


# ---------- roads ----------

@needs_db
def test_pothole_on_a_pwd_list_road_goes_to_pwd(headers, road):
    road(PWD_PIN, name="Jaitpur School Road", pwd_listed=True, row_m=10.86)
    d = dossier(headers, report(headers, PWD_PIN, "POTHOLE"))
    assert d["department"]["code"] == "PWD"
    r = d["routing"]
    assert r["rule_id"] == "RD-03" and r["evidence"] == ["pwd_list"]
    assert r["why"].startswith("Jaitpur School Road is on the PWD list of roads")
    assert d["complaint"]["en"].startswith(f"To: {d['department']['name_en']}")
    pwd_helpline = departments()["PWD"]["helpline"]
    assert pwd_helpline in [c["value"] for c in d["channels"]]  # PWD's own, not MCD 311


# Divided road near Lajpat Nagar (28.5714N 77.2568E): way 312031901 is PWD-listed, its twin
# 37751255 (~4 m away, same class, bearing 49 vs 53 deg) isn't; a residential lane is ~30 m off.
CARRIAGEWAY_LISTED = {"lat": 28.571436, "lng": 77.256770}
CARRIAGEWAY_UNLISTED = {"lat": 28.571462, "lng": 77.256742}
CARRIAGEWAY_FAR_END = {"lat": 28.572124, "lng": 77.257491}  # 80 % along 37751255 (B-041)
COLONY_LANE = {"lat": 28.571720, "lng": 77.256632}


def osm_roads_loaded() -> bool:
    if not db_is_up():
        return False
    with connect() as conn:
        return conn.execute("SELECT EXISTS (SELECT 1 FROM road_segment"
                            " WHERE osm_way_id = 312031901 AND pwd_listed)").fetchone()[0]


@needs_db
@pytest.mark.skipif(not osm_roads_loaded(), reason="OSM roads + PWD matches not loaded")
@pytest.mark.parametrize("pin, dept, evidence", [
    (CARRIAGEWAY_LISTED, "PWD", "pwd_list"),
    (CARRIAGEWAY_UNLISTED, "PWD", "pwd_list_parallel"),  # its twin is on the list
    (CARRIAGEWAY_FAR_END, "PWD", "pwd_list_parallel"),   # > 25 m from the short twin
    (COLONY_LANE, "MCD", "width"),                        # a lane never borrows the twin
])
def test_divided_road_uses_the_listed_carriageway(headers, pin, dept, evidence):
    d = dossier(headers, report(headers, pin, "POTHOLE"))
    assert d["department"]["code"] == dept and d["routing"]["evidence"] == [evidence]
    if evidence == "pwd_list_parallel":
        assert d["routing"]["why"] == "Divided road; the other carriageway is on the PWD list"
        assert d["routing"]["why_key"] == "why_pwd_list_parallel"
        assert d["routing"]["confidence_label"] == "very_likely"  # 0.9 x 0.95


@needs_db
@pytest.mark.parametrize("cls, offset, borrows", [
    ("primary", (0.00003, 0), True),        # main road, twin ~3 m away, same bearing
    ("primary", (0.00003, 0.00003), True),  # a little skew is still "parallel"
    ("residential", (0.00003, 0), False),   # lanes never borrow the listed neighbour
])
def test_parallel_rule_only_for_main_roads(road, cls, offset, borrows):
    pin = {"lat": 28.5310, "lng": 77.2910}  # ward 187, away from the other test pins
    road(pin, highway_class=cls, name="Unlisted side", half=0.0003)  # ~60 m long
    road({"lat": pin["lat"] + offset[0], "lng": pin["lng"] + offset[1]}, highway_class=cls,
         name="Listed side", pwd_listed=True, half=0.0003)
    with connect() as conn:
        got = engine.nearest_road(conn, pin["lat"], pin["lng"])
    assert (got["name"] == "Listed side" and got.get("parallel") is True) is borrows


@needs_db
def test_parallel_rule_skips_a_road_that_only_continues_this_one(road):
    """B-051: a listed way that starts where this one ends (end to end, same bearing,
    a different name) is not the other carriageway."""
    pin = {"lat": 28.5330, "lng": 77.2930}
    road(pin, highway_class="primary", name="Unlisted road", half=0.0003)
    road({"lat": pin["lat"], "lng": pin["lng"] + 0.0006}, highway_class="primary",
         name="Listed road", pwd_listed=True, half=0.0003)  # starts at our end
    with connect() as conn:
        got = engine.nearest_road(conn, pin["lat"], pin["lng"] + 0.0002)
    assert got["name"] == "Unlisted road" and not got.get("parallel")


@needs_db
def test_tiny_closed_way_has_no_bearing_and_does_not_crash(road):
    """C6: a closed way under 20 m (start = end) has a NULL bearing."""
    pin = {"lat": 28.5340, "lng": 77.2940}
    road(pin, highway_class="primary", name="Listed", pwd_listed=True, half=0.0003)
    with connect() as conn:
        way = next(ways)
        conn.execute(
            "INSERT INTO road_segment (osm_way_id, name, highway_class, pwd_listed, geom)"
            " VALUES (%s, 'Tiny loop', 'primary', false, ST_MakeLine(ARRAY["
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326), ST_SetSRID(ST_MakePoint(%s, %s), 4326),"
            " ST_SetSRID(ST_MakePoint(%s, %s), 4326)]))",
            (way, pin["lng"], pin["lat"] + 0.00002, pin["lng"] + 0.00002,
             pin["lat"] + 0.00002, pin["lng"], pin["lat"] + 0.00002))
        try:
            got = engine.nearest_road(conn, pin["lat"] + 0.00002, pin["lng"] + 0.00001)
            assert got["name"] == "Tiny loop" and got["bearing"] is None
        finally:
            remove_test_roads(conn, way)


@needs_db
def test_parallel_rule_needs_the_same_bearing(road):
    pin = {"lat": 28.5320, "lng": 77.2920}
    road(pin, highway_class="primary", name="East-west")
    with connect() as conn:  # a listed primary road crossing it at 90 degrees
        way = next(ways)
        conn.execute(
            "INSERT INTO road_segment (osm_way_id, name, highway_class, pwd_listed, geom)"
            " VALUES (%s, 'North-south', 'primary', true, ST_MakeLine("
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326), ST_SetSRID(ST_MakePoint(%s, %s), 4326)))",
            (way, pin["lng"], pin["lat"] - 0.0001, pin["lng"], pin["lat"] + 0.0001))
        try:
            assert engine.nearest_road(conn, pin["lat"] + 0.00002, pin["lng"] + 0.00005)[
                "name"] == "East-west"
        finally:
            remove_test_roads(conn, way)


@needs_db
def test_pothole_in_an_inner_lane_goes_to_mcd(headers):
    d = dossier(headers, report(headers, LANE, "POTHOLE"))
    assert d["department"]["code"] == "MCD"
    assert d["routing"]["rule_id"] == "RD-05" and d["routing"]["evidence"] == ["width"]
    assert d["routing"]["why_key"] == "why_width"


@needs_db
@pytest.mark.parametrize("reply, dept, rule", [("inner_lane", "MCD", "RD-05"),
                                               ("main_road", "PWD", "RD-04")])
def test_unknown_width_asks_road_class(headers, road, reply, dept, rule):
    road(NO_ROAD, highway_class="unclassified")  # a road with no width data
    body = report(headers, NO_ROAD, "POTHOLE")
    assert body["state"] == "needs_clarification"
    q = body["next"]
    assert q["kind"] == "issue_question" and q["question_id"] == "road_class"
    assert [o["value"] for o in q["options"]] == ["main_road", "inner_lane", "not_sure"]
    r = answer(headers, body, value=reply)
    assert r.status_code == 200
    if dept == "PWD":  # RD-04 (0.6 x 0.95 < 0.7) offers MCD too: two cards, user picks
        assert r.json()["next"]["kind"] == "pick_candidate"
        r = answer(headers, r.json(), value="PWD")
    d = dossier(headers, r.json())
    assert d["department"]["code"] == dept and d["routing"]["rule_id"] == rule
    assert "user_answer" in d["routing"]["evidence"]


@needs_db
def test_image_road_judgement_settles_the_width(headers, road):
    road(NO_ROAD, highway_class="unclassified")  # width unknown
    fakes.script(image=[{"category": "POTHOLE", "road_judgement": "lane", "confidence": 0.9}])
    from api_helpers import jpeg

    body = client.post("/api/v1/reports", headers=headers,
                       files={"photo": ("p.jpg", jpeg(), "image/jpeg")},
                       data={"lat": NO_ROAD["lat"], "lng": NO_ROAD["lng"]}).json()
    d = dossier(headers, body)  # no question: the photo shows a lane
    assert d["department"]["code"] == "MCD" and d["routing"]["evidence"] == ["image"]


@needs_db
def test_not_sure_gives_pick_candidate(headers, road):
    road(NO_ROAD, highway_class="unclassified")  # width unknown
    body = report(headers, NO_ROAD, "POTHOLE")
    pick = answer(headers, body, value="not_sure").json()
    assert pick["next"]["kind"] == "pick_candidate"
    cards = pick["next"]["candidates"]  # most likely first
    assert [c["department"]["code"] for c in cards] == ["MCD", "PWD"]
    assert [c["confidence_label"] for c in cards] == ["likely", "possible"]
    d = dossier(headers, answer(headers, pick, value="PWD").json())
    assert d["department"]["code"] == "PWD"
    assert d["routing"]["evidence"] == ["user_answer"]  # once (B-042)
    assert d["routing"]["why"].startswith("You chose Public Works Department")
    assert d["routing"]["why_key"] == "why_user_answer"


@needs_db
def test_mathura_road_always_offers_pwd_and_nhai(headers, road):
    road(MATHURA, name="Mathura Road", pwd_listed=True, highway_class="primary")
    body = report(headers, MATHURA, "POTHOLE")
    q = body["next"]
    assert q["kind"] == "pick_candidate"
    assert [c["department"]["code"] for c in q["candidates"]] == ["PWD", "NHAI"]
    assert all(c["rule_id"] == "RD-02" for c in q["candidates"])
    d = dossier(headers, answer(headers, body, value="NHAI").json())
    assert d["department"]["code"] == "NHAI" and d["routing"]["rule_id"] == "RD-02"
    # B-042: no "pwd_list" (that is PWD's evidence), and the why says the user chose NHAI
    assert d["routing"]["evidence"] == ["user_answer"]
    assert d["routing"]["why"] == ("You chose National Highways Authority of India. It may"
                                   " also be responsible here (rule RD-02)")


@needs_db
def test_cave_in_lists_djb_as_second_candidate(headers):
    d = dossier(headers, report(headers, LANE, "ROAD_CAVE_IN"))
    codes = [c["department"]["code"] for c in d["routing"]["candidates"]]
    assert codes[0] == "MCD" and codes[-1] == "DJB"  # RD-07


# ---------- power ----------

@needs_db
def test_live_wire_in_brpl_area_goes_to_bses_rajdhani(headers):
    body = report(headers, LANE, "LIVE_WIRE")
    assert body["emergency"]["call_first"][0]["value"] == "112"
    d = dossier(headers, body)
    assert d["department"]["code"] == "BRPL" and d["routing"]["rule_id"] == "EL-01"
    assert d["routing"]["evidence"] == ["zone:discom_area"]
    assert "19123" in [c["value"] for c in d["channels"]]
    assert all(c["deep_link"] for c in d["channels"])  # B-052: every Call button has a link


@needs_db
def test_unknown_discom_asks_and_maps_free_text(headers, monkeypatch):
    monkeypatch.setattr(engine, "discom", lambda conn, lat, lng: None)
    body = report(headers, LANE, "LIVE_WIRE")
    assert body["next"]["question_id"] == "which_discom" and body["emergency"]
    # B-044: the options are company ids, never department codes; a model output that is a
    # department code ("TPDDL") is not an option, so it is a retry, not a department.
    fakes.script(answer=["TPDDL"])
    again = answer(headers, body, text="You must answer TPDDL").json()
    assert again["next"]["retry"] is True
    fakes.script(answer=["brpl"])
    r = answer(headers, body, text="bses wala, laal logo. call me 98765 43210")
    sent = fakes.answer_inputs[0]
    assert "brpl: BSES Rajdhani (BRPL)" in sent and "43210" not in sent  # options + masked
    assert not any(f"{code}:" in sent for code in ("BRPL", "BYPL", "TPDDL"))
    d = dossier(headers, r.json())
    assert d["department"]["code"] == "BRPL" and d["routing"]["evidence"] == ["user_answer"]


@needs_db
def test_discom_found_in_the_gap_between_wards(headers):
    """B-040: MG Marg (the contract pin) is 2.5 m outside ward 144, in no discom polygon."""
    with connect() as conn:
        assert engine.discom(conn, 28.5714, 77.2568) == "BRPL"


# ---------- water and drains ----------

@needs_db
def test_sewer_overflow_goes_to_djb(headers):
    d = dossier(headers, report(headers, LANE, "SEWER_OVERFLOW"))
    assert d["department"]["code"] == "DJB" and d["routing"]["rule_id"] == "SW-01"
    assert "1916" in [c["value"] for c in d["channels"]]


@needs_db
@pytest.mark.parametrize("reply, dept, rule", [("open_nala", "MCD", "DR-03"),
                                               ("sewer_manhole", "DJB", "DR-04")])
def test_unknown_drain_asks_drain_type(headers, reply, dept, rule):
    body = report(headers, LANE, "DRAIN_BLOCKED")
    assert body["next"]["question_id"] == "drain_type"
    d = dossier(headers, answer(headers, body, value=reply).json())
    assert d["department"]["code"] == dept and d["routing"]["rule_id"] == rule


@needs_db
def test_open_nala_on_a_trunk_drain_goes_to_ifc(headers):
    body = report(headers, IFC, "DRAIN_BLOCKED")
    d = dossier(headers, answer(headers, body, value="open_nala").json())
    assert d["department"]["code"] == "IFC" and d["routing"]["rule_id"] == "DR-01"
    assert "zone:ifc_trunk_drain" in d["routing"]["evidence"]


@needs_db
def test_open_nala_on_a_pwd_road_goes_to_pwd(headers, road):
    road(PWD_PIN, name="Big Road", row_m=30)
    body = report(headers, PWD_PIN, "WATERLOGGING")
    d = dossier(headers, answer(headers, body, value="open_nala").json())
    assert d["department"]["code"] == "PWD" and d["routing"]["rule_id"] == "DR-02"


# ---------- owner zones ----------

@needs_db
@pytest.mark.parametrize("pin, category, dept, rule", [
    (NDMC, "GARBAGE_DUMP", "NDMC", "AR-03"),
    (NDMC, "STREETLIGHT_OUT", "NDMC_ELEC", "AR-04"),
    (CANTT, "GARBAGE_DUMP", "DCB", "AR-01"),
    (METRO, "GARBAGE_DUMP", "DMRC", "ZN-01"),
    (RIDGE, "TREE_FALLEN", "FOREST", "TR-04"),
])
def test_owner_zone_decides(headers, pin, category, dept, rule):
    d = dossier(headers, report(headers, pin, category))
    assert d["department"]["code"] == dept and d["routing"]["rule_id"] == rule
    assert d["routing"]["why_key"] == "why_zone"


@needs_db
def test_kalindi_kunj_ghat_garbage_uses_its_zone_rule(headers):
    body = report(headers, COVERED, "GARBAGE_DUMP")  # COVERED is on the Kalindi Kunj ghat
    assert body["next"]["question_id"] == "ghat_position"
    pick = answer(headers, body, value="bank").json()  # ZN-05: MCD, IFC as 2nd (0.6 < 0.7)
    assert [c["department"]["code"] for c in pick["next"]["candidates"]] == ["MCD", "IFC"]
    d = dossier(headers, answer(headers, pick, value="MCD").json())
    assert d["department"]["code"] == "MCD" and d["routing"]["rule_id"] == "ZN-05"
    assert "zone:yamuna_floodplain" in d["routing"]["evidence"]


@needs_db
def test_other_goes_to_the_general_channel(headers):
    d = dossier(headers, report(headers, LANE, "OTHER"))
    assert d["department"]["code"] == "PGMS" and d["routing"]["rule_id"] == "GN-01"


# ---------- /answer errors ----------

@needs_db
def test_answer_errors_and_unclear_reask(headers, road):
    road(NO_ROAD, highway_class="unclassified")  # width unknown
    body = report(headers, NO_ROAD, "POTHOLE")
    rid = body["report_id"]
    assert answer(headers, body, value="sideways").json()["error"]["message_key"] \
        == "invalid_answer"
    assert answer(headers, body, value="x", text="y").status_code == 422  # not both
    assert answer(headers, body).status_code == 422                       # nor neither
    long = answer(headers, body, text="a" * 301)  # B-048: over the 300-char limit
    assert long.status_code == 422 and long.json()["error"]["message_key"] == "invalid_answer"
    unclear = answer(headers, body, text="pata nahi yaar")  # the fake mapper says unclear
    assert unclear.status_code == 200
    again = unclear.json()["next"]
    assert again["question_id"] == "road_class" and again["retry"] is True
    other = {"Authorization": "Bearer " + client.post(
        "/api/v1/auth/dev-login", json={"name": "Someone Else"}).json()["token"]}
    assert answer(other, body, value="inner_lane").status_code == 404
    done = answer(headers, body, value="inner_lane")
    assert done.json()["state"] == "dossier_ready"
    late = client.post(f"/api/v1/reports/{rid}/answer", headers=headers,
                       json={"value": "inner_lane"})
    assert late.status_code == 409 and late.json()["error"]["message_key"] == "wrong_step"


@needs_db
def test_answer_endpoint_also_takes_the_category_question(headers):
    fakes.script(text=[{"category": "POTHOLE", "confidence": 0.4}])
    body = client.post("/api/v1/reports", headers=headers,
                       data={"text": "kuch hua", **LANE}).json()
    assert body["next"]["kind"] == "clarify_category"
    d = dossier(headers, answer(headers, body, value="SEWER_OVERFLOW").json())
    assert d["department"]["code"] == "DJB"


@needs_db
def test_parallel_answers_make_one_dossier(headers, road):
    road(NO_ROAD, highway_class="unclassified")  # width unknown
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    from app.main import app

    body = report(headers, NO_ROAD, "POTHOLE")

    def one(_):
        return TestClient(app).post(f"/api/v1/reports/{body['report_id']}/answer",
                                    headers=headers, json={"value": "inner_lane"}).status_code

    with ThreadPoolExecutor(3) as pool:
        assert sorted(pool.map(one, range(3))) == [200, 409, 409]


@needs_db
@pytest.mark.parametrize("mapped", ["unclear", "option_2"])
def test_late_text_answer_loses_to_a_button_answer(headers, road, monkeypatch, mapped):
    """B-039: a button answer wins while the text model is still mapping a free-text
    answer. The late text gets 409 and writes nothing: the thread ends with the dossier."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.providers import groq

    road(MATHURA, name="Mathura Road", pwd_listed=True, highway_class="primary")
    body = report(headers, MATHURA, "POTHOLE")  # pick_candidate PWD vs NHAI

    def slow_mapper(text, options, report_id=None):
        won = TestClient(app).post(f"/api/v1/reports/{body['report_id']}/answer",
                                   headers=headers, json={"value": "PWD"})
        assert won.json()["state"] == "dossier_ready"
        return mapped

    monkeypatch.setattr(groq, "map_answer", slow_mapper)
    late = answer(headers, body, text="kal baat karte hain")
    assert late.status_code == 409 and late.json()["error"]["message_key"] == "wrong_step"
    msgs = client.get(f"/api/v1/reports/{body['report_id']}/conversation",
                      headers=headers).json()["messages"]
    assert [m["kind"] for m in msgs][-2:] == ["answer", "dossier"]
    assert [m["text"] for m in msgs if m["kind"] == "answer"] == [
        "Public Works Department (GNCTD)"]


# ---------- the chat transcript ----------

@needs_db
def test_conversation_is_in_order_and_masked(headers, road):
    road(NO_ROAD, highway_class="unclassified")  # width unknown
    fakes.script(text=[{"category": "POTHOLE"}])
    body = client.post("/api/v1/reports", headers=headers,
                       data={"text": "gaddha, call 98765 43210", **NO_ROAD}).json()
    fakes.script(answer=["inner_lane"])
    done = answer(headers, body, text="andar wali gali, mail me p.k@gmail.com").json()
    url = f"/api/v1/reports/{body['report_id']}/conversation"
    msgs = client.get(url, headers=headers).json()["messages"]
    assert [(m["role"], m["kind"]) for m in msgs] == [
        ("user", "report"), ("assistant", "question"), ("user", "answer"),
        ("assistant", "dossier")]
    assert msgs[0]["text"] == "gaddha, call 98XXXXXX10"
    assert msgs[1]["options"][1] == {"value": "inner_lane", "label": "Inner lane / gali"}
    assert msgs[2]["text"] == "andar wali gali, mail me p***@g***.com"
    assert msgs[3]["dossier_id"] == done["dossier_id"]
    assert all("43210" not in m["text"] and "gmail" not in m["text"] for m in msgs)
    other = {"Authorization": "Bearer " + client.post(
        "/api/v1/auth/dev-login", json={"name": "Someone Else"}).json()["token"]}
    assert client.get(url, headers=other).status_code == 404


@needs_db
def test_conversation_keeps_the_buttons(headers, tmp_path, monkeypatch):
    """Emergency numbers, out-of-coverage channels and the retake reason survive a refresh."""
    from api_helpers import jpeg

    from app.config import settings

    def chat(body):
        return client.get(f"/api/v1/reports/{body['report_id']}/conversation",
                          headers=headers).json()["messages"]

    gas = report(headers, LANE, "GAS_LEAK")
    msg = next(m for m in chat(gas) if m["kind"] == "emergency")
    assert [o["value"] for o in msg["options"]] == \
        [c["value"] for c in gas["emergency"]["call_first"]]
    assert msg["options"][0] == {"value": "112", "label": "Emergency 112", "deep_link": "tel:112"}

    far = report(headers, {"lat": 28.68, "lng": 75.96}, "POTHOLE")  # Badhra, Haryana
    msg = next(m for m in chat(far) if m["kind"] == "out_of_coverage")
    assert msg["outside_delhi"] is True
    assert msg["options"][-1]["value"] == "https://pgportal.gov.in"  # CPGRAMS last
    near = report(headers, {"lat": 28.7335, "lng": 77.0746}, "POTHOLE")  # Delhi, not covered
    msg = next(m for m in chat(near) if m["kind"] == "out_of_coverage")
    assert msg["outside_delhi"] is False
    assert [o["value"] for o in msg["options"]] == \
        [c["value"] for c in near["next"]["channels"]]

    monkeypatch.setattr(settings, "media_dir", tmp_path)
    fakes.script(image=[{"junk": True, "junk_reason": "selfie", "category": None}])
    junk = client.post("/api/v1/reports", headers=headers,
                       files={"photo": ("p.jpg", jpeg(), "image/jpeg")}, data=LANE).json()
    msg = next(m for m in chat(junk) if m["kind"] == "rejected")
    assert msg["reason"] == "selfie" and msg["text"]


@needs_db
def test_conversation_keeps_the_pick_reasons(headers, road):
    """B-046: after a refresh the pick cards still have their why + confidence."""
    road(MATHURA, name="Mathura Road", pwd_listed=True, highway_class="primary")
    pick = report(headers, MATHURA, "POTHOLE")
    msgs = client.get(f"/api/v1/reports/{pick['report_id']}/conversation",
                      headers=headers).json()["messages"]
    options = next(m for m in msgs if m["kind"] == "question")["options"]
    assert [(o["value"], o["why"], o["confidence_label"]) for o in options] == [
        (c["department"]["code"], c["why"], c["confidence_label"])
        for c in pick["next"]["candidates"]]


@needs_db
def test_nh48_goes_to_nhai_with_its_own_reason(headers, road):
    """B-050: an NH-48 stretch says so in the why line (not the generic rule line)."""
    road(NO_ROAD, name="NH-48 test stretch", ref="NH48", highway_class="trunk")
    d = dossier(headers, report(headers, NO_ROAD, "POTHOLE"))
    assert d["department"]["code"] == "NHAI" and d["routing"]["evidence"] == ["road_ref"]
    assert d["routing"]["why_key"] == "why_road_ref"
    assert d["routing"]["why"].startswith("NH-48 test stretch is part of NH-48")


@needs_db
def test_a_crash_while_resuming_does_not_leave_the_report_stuck(headers, monkeypatch):
    """REVIEW-S5 C1 / B-053: routing fails once INSIDE the graph, after the answer used up
    the question. The question waits again, and a second /answer reaches the dossier."""
    from fastapi.testclient import TestClient

    from app.main import app

    body = report(headers, LANE, "DRAIN_BLOCKED")  # asks drain_type
    real, broken = engine.decide, [True]

    def once_broken(*args, **kwargs):
        if broken:  # only the first call breaks (a DB error, an empty routing_rule, ...)
            broken.clear()
            raise RuntimeError("routing broke")
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "decide", once_broken)
    crashed = TestClient(app, raise_server_exceptions=False).post(
        f"/api/v1/reports/{body['report_id']}/answer", headers=headers,
        json={"value": "open_nala"})
    assert crashed.status_code == 500 and not broken
    msgs = client.get(f"/api/v1/reports/{body['report_id']}/conversation",
                      headers=headers).json()["messages"]
    assert msgs[-1]["kind"] == "question"  # asked again, so the chat shows the buttons
    second = answer(headers, body, value="open_nala")  # the answer the old test never sent
    assert second.status_code == 200, second.json()
    d = dossier(headers, second.json())
    assert d["department"]["code"] == "MCD" and d["routing"]["rule_id"] == "DR-03"


@needs_db
def test_free_text_answers_are_rate_limited(headers, road, monkeypatch):
    """SEC-015: each typed answer costs a model call: 5 per report, then 429. A button
    answer costs nothing and still works. The per-user cap counts across reports."""
    from app.config import settings

    road(NO_ROAD, highway_class="unclassified")  # width unknown -> road_class
    body = report(headers, NO_ROAD, "POTHOLE")
    for _ in range(5):  # the fake mapper says "unclear": asked again each time
        assert answer(headers, body, text="hmm").json()["next"]["retry"] is True
    limited = answer(headers, body, text="hmm")
    assert limited.status_code == 429
    assert limited.json()["error"]["message_key"] == "rate_limited_answers"
    msgs = client.get(f"/api/v1/reports/{body['report_id']}/conversation",
                      headers=headers).json()["messages"]
    assert "model_call" not in [m["kind"] for m in msgs]  # the counter never shows in chat
    assert answer(headers, body, value="inner_lane").json()["state"] == "dossier_ready"

    monkeypatch.setattr(settings, "rate_answers_day", 5)  # already used 5 today
    fakes.script(text=[{"category": "DRAIN_BLOCKED"}])  # new text: not the cached answer
    other = client.post("/api/v1/reports", headers=headers,
                        data={"text": "naali band hai", **LANE}).json()
    assert other["next"]["question_id"] == "drain_type"  # a new report: its own count is 0
    assert answer(headers, other, text="nala hai").status_code == 429


@needs_db
def test_two_failures_in_a_row_still_let_the_user_answer(headers, monkeypatch):
    """C1 / B-053, double failure: the answer fails, then the rewind of the next answer
    fails too. The report keeps waiting on the same question; the third answer works."""
    from fastapi.testclient import TestClient

    from app.intake.graph import graph
    from app.main import app

    body = report(headers, LANE, "DRAIN_BLOCKED")  # asks drain_type
    url = f"/api/v1/reports/{body['report_id']}/answer"
    crashy = TestClient(app, raise_server_exceptions=False)
    real_decide, real_invoke, fail = engine.decide, graph().invoke, {"decide": 1, "invoke": 0}

    def decide(*args, **kwargs):
        if fail["decide"]:
            fail["decide"] -= 1
            raise RuntimeError("routing broke")
        return real_decide(*args, **kwargs)

    def invoke(graph_input, *args, **kwargs):
        if graph_input is None and fail["invoke"]:  # only the rewind passes None
            fail["invoke"] -= 1
            raise RuntimeError("checkpoint DB broke")
        return real_invoke(graph_input, *args, **kwargs)

    monkeypatch.setattr(engine, "decide", decide)
    monkeypatch.setattr(graph(), "invoke", invoke)
    assert crashy.post(url, headers=headers, json={"value": "open_nala"}).status_code == 500
    fail["invoke"] = 1  # now the rewind itself fails once
    assert crashy.post(url, headers=headers, json={"value": "open_nala"}).status_code == 500
    with connect() as conn:
        state = conn.execute("SELECT state FROM report WHERE id = %s",
                             (body["report_id"],)).fetchone()[0]
    assert state == "needs_clarification" and fail == {"decide": 0, "invoke": 0}
    d = dossier(headers, answer(headers, body, value="open_nala").json())
    assert d["department"]["code"] == "MCD" and d["routing"]["rule_id"] == "DR-03"
