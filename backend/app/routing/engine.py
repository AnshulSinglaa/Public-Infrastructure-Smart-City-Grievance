"""Routing (contract sprint5-api.md §1): plain SQL lookups + the 73 rules. Never the AI.
pin -> covered ward -> owner zone -> nearest road -> lookups -> first matching rule."""

import json
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.intake.catalog import categories, departments, states
from app.routing.conditions import FACT_QUESTION, NOT_SURE, OWNER_ZONES, QUESTIONS, RULES

POINT = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"  # ST_MakePoint takes (x, y) = (lng, lat)
# Delhi's outer edge. Source: OpenCity MCD wards 2022 (public domain), union of 250 wards,
# plus the NDMC area and Delhi Cantonment (not MCD wards; DataMeet, CC BY) so that CP and
# India Gate count as Delhi.
# ponytail: simplified to ~20 m, so a pin within ~20 m of the state border may get the other
# out-of-coverage text; both texts give the same helplines-only answer.
DELHI_OUTLINE = json.dumps(json.loads(
    (Path(__file__).parent / "delhi_outline.geojson").read_text(encoding="utf-8"))["geometry"])


def find_ward(conn: psycopg.Connection, lat: float, lng: float) -> dict | None:
    """The covered ward for a pin. ST_Covers (not ST_Contains) counts a pin exactly on a ward
    edge or corner as inside. A pin that is in no covered ward but within 15 m of one (GPS
    noise at a boundary) gets the nearest; ::geography makes the distance metres."""
    pin = f"{POINT}::geography"
    row = conn.execute(
        "SELECT id, ward_no, name_en FROM ward WHERE in_coverage"
        f" AND ST_DWithin(geom::geography, {pin}, %s)"
        f" ORDER BY ST_Covers(geom, {POINT}) DESC, ST_Distance(geom::geography, {pin}) LIMIT 1",
        (lng, lat, settings.boundary_buffer_m, lng, lat, lng, lat),
    ).fetchone()
    return {"ward_id": row[0], "ward_no": row[1], "ward_name": row[2]} if row else None


def in_delhi(conn: psycopg.Connection, lat: float, lng: float) -> bool:
    return conn.execute(
        f"SELECT ST_Covers(ST_GeomFromGeoJSON(%s), {POINT})", (DELHI_OUTLINE, lng, lat)
    ).fetchone()[0]


def general_channels(conn: psycopg.Connection) -> list[dict]:
    """City-wide helplines (contact.scope = 'all') for reports we can't route."""
    rows = conn.execute(
        "SELECT channel_type, label, value, deep_link FROM contact WHERE scope = 'all' ORDER BY id"
    ).fetchall()
    return [{"type": r[0], "label": r[1], "value": r[2], "deep_link": r[3]} for r in rows]


NATIONAL = {"name": "CPGRAMS", "url": "https://pgportal.gov.in"}


STATE_GAP_M = 2000  # state outlines are simplified to ~1 km, so borders have small gaps


def find_state(conn: psycopg.Connection, lat: float, lng: float):
    """(code, name) of the state the pin is in. If no outline covers it (a pin in a
    border gap), take the nearest state within 2 km; ::geography makes that metres."""
    row = conn.execute(
        f"SELECT code, name FROM state_boundary WHERE ST_Covers(geom, {POINT}) LIMIT 1",
        (lng, lat),
    ).fetchone()
    if row:
        return row
    pin = f"{POINT}::geography"
    return conn.execute(
        f"SELECT code, name FROM state_boundary WHERE ST_DWithin(geom::geography, {pin}, %s)"
        f" ORDER BY ST_Distance(geom::geography, {pin}) LIMIT 1",
        (lng, lat, STATE_GAP_M, lng, lat),
    ).fetchone()


def state_directory(conn: psycopg.Connection, lat: float, lng: float) -> dict:
    """Outside coverage: which state the pin is in, and that state's grievance portal.
    Unknown state, or an entry not marked verified -> CPGRAMS (national) only."""
    row = find_state(conn, lat, lng)
    entry = states().get(row[0]) if row else None
    ok = bool(entry) and entry.get("verified", "").lower() in ("yes", "true", "1")
    return {
        "state": row[1] if row else None,
        "portal_name": entry["portal_name"] if ok else None,
        "portal_url": entry["portal_url"] if ok else None,
        "helpline": entry["helpline"] if ok else None,
        "verified": ok,
        "national": NATIONAL,
    }


# ---------- Sprint 5 routing: the contract §1 flow, one small function per step ----------

WIDE_M = 18.29  # 60 ft right-of-way: the 2012 PWD/MCD split
SNAP_M = 25     # a road counts if it is within 25 m of the pin
LANE_CLASSES = {"residential", "service", "living_street"}
MAIN_CLASSES = {"trunk", "primary", "secondary"}  # where the divided-road rule may apply
PARALLEL_DEG = 20
ALONGSIDE_M = 30  # a twin must run beside the way for 30 m, not just touch its end (B-051)
EVIDENCE = {"pwd_list": 1.0, "road_ref": 1.0, "pwd_list_parallel": 0.95, "user_answer": 0.95,
            "width": 0.9, "image": 0.85}
THRESHOLD = 0.7
SUGGESTED_SLA_DAYS = 15


def owner_zone(conn, lat: float, lng: float) -> tuple[str, str] | None:
    """(type, name) of the owner zone at the pin, highest priority (lowest number) first.
    Lookup layers (discom_area, ifc_trunk_drain) are not in OWNER_ZONES, so never owners."""
    return conn.execute(
        "SELECT type, name FROM special_zone WHERE type = ANY(%s)"
        f" AND ST_Covers(geom, {POINT}) ORDER BY priority, id LIMIT 1",
        (OWNER_ZONES, lng, lat),
    ).fetchone()


# The bearing is taken on the ~20 m piece of each way around the point nearest the pin
# (B-041): a long or curvy way's start->end bearing can be far off the piece that matters.
# f = where along the way that point is (0..1), d = 10 m as a fraction of the way's length.
ROAD_COLS = (
    "SELECT id, osm_way_id, name, ref, highway_class, pwd_listed, row_m,"
    " degrees(ST_Azimuth(ST_LineInterpolatePoint(geom, greatest(f - d, 0)),"
    "                    ST_LineInterpolatePoint(geom, least(f + d, 1)))) AS bearing"
    f" FROM (SELECT *, ST_LineLocatePoint(geom, {POINT}) AS f,"
    "        10 / greatest(ST_Length(geom::geography), 10) AS d FROM road_segment) r")


def nearest_road(conn, lat: float, lng: float) -> dict | None:
    """The closest road within 25 m, or None.
    Divided roads: OSM often has the two carriageways as two ways, and only one of them is
    matched to the PWD list. So if the closest one is a main road that isn't listed, but a
    listed way of the same class runs alongside it, that listed way is used ('parallel').
    Alongside = at least 30 m of this way lies within 25 m of the listed one (B-051: a road
    that only starts where a listed one ends is not its twin), and the bearings differ by at
    most 20 deg. Way to way, not pin to way (B-041): the twin can be much shorter than the
    way the pin is on. Never for lanes: a colony lane beside a main road stays with MCD."""
    pin = f"{POINT}::geography"
    by_distance = f" ORDER BY ST_Distance(geom::geography, {pin})"
    with conn.cursor(row_factory=dict_row) as cur:
        road = cur.execute(ROAD_COLS + f" WHERE ST_DWithin(geom::geography, {pin}, %s)"
                           + by_distance + " LIMIT 1",
                           (lng, lat, lng, lat, SNAP_M, lng, lat)).fetchone()
        if not road or road["pwd_listed"] or road["highway_class"] not in MAIN_CLASSES:
            return road
        this = "(SELECT geom FROM road_segment WHERE id = %s)"
        twins = cur.execute(
            ROAD_COLS + " WHERE pwd_listed AND highway_class = %s"
            f" AND ST_DWithin(geom::geography, {this}::geography, %s)"
            # how much of this way lies within 25 m of the listed one
            f" AND ST_Length(ST_Intersection({this}, ST_Buffer(geom::geography, %s)::geometry)"
            "::geography) >= %s" + by_distance,
            (lng, lat, road["highway_class"], road["id"], SNAP_M, road["id"], SNAP_M,
             ALONGSIDE_M, lng, lat)).fetchall()
    if road["bearing"] is None:  # a tiny closed way: start = end, no bearing (C6)
        return road
    for twin in twins:
        if twin["bearing"] is None:
            continue
        turn = abs(road["bearing"] - twin["bearing"]) % 180  # either direction of digitising
        if min(turn, 180 - turn) <= PARALLEL_DEG:
            return twin | {"parallel": True}
    return road


def road_class(road: dict | None, image: dict | None) -> tuple[dict, dict]:
    """(road facts, where each came from). Width comes from the data first (row_m, or a
    lane-type road), then the photo (main road / lane, only if the model was >= 70% sure),
    else it stays unknown and we ask."""
    road = road or {}
    refs = (road.get("ref") or "").upper().replace("-", "").replace(" ", "").split(";")
    facts = {"nhai": "NH48" in refs, "pwd_list": bool(road.get("pwd_listed")),
             "mathura": "mathura road" in (road.get("name") or "").lower(),
             "width": None, "road_name": road.get("name")}
    src = {"nhai": "road_ref", "mathura": "pwd_list",
           "pwd_list": "pwd_list_parallel" if road.get("parallel") else "pwd_list"}
    if road.get("row_m"):
        facts["width"], src["width"] = ("wide" if road["row_m"] >= WIDE_M else "narrow"), "width"
    elif road.get("highway_class") in LANE_CLASSES:
        facts["width"], src["width"] = "narrow", "width"
    elif image and image.get("confidence", 0) >= THRESHOLD \
            and image.get("road_judgement") in ("main_road", "lane"):
        facts["width"] = "wide" if image["road_judgement"] == "main_road" else "narrow"
        src["width"] = "image"
    return facts, src


def discom(conn, lat: float, lng: float) -> str | None:
    """Which power company's area the pin is in (lookup layer 'discom_area'; the zone
    name starts with the company code, e.g. 'BRPL (coverage wards)'), or None.
    The areas are unions of ward polygons, so the gaps between wards (often the main road
    that forms the border) belong to none: like find_ward, take the nearest area within
    15 m (B-040)."""
    pin = f"{POINT}::geography"
    row = conn.execute(
        "SELECT name FROM special_zone WHERE type = 'discom_area'"
        f" AND ST_DWithin(geom::geography, {pin}, %s)"
        f" ORDER BY ST_Distance(geom::geography, {pin}) LIMIT 1",
        (lng, lat, settings.boundary_buffer_m, lng, lat),
    ).fetchone()
    return row[0].split()[0] if row else None


def ifc_drain(conn, lat: float, lng: float) -> bool:
    """Is an I&FC trunk drain (lookup layer) within 25 m of the pin?"""
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM special_zone WHERE type = 'ifc_trunk_drain'"
        f" AND ST_DWithin(geom::geography, {POINT}::geography, %s))", (lng, lat, SNAP_M),
    ).fetchone()[0]


def gather_facts(conn, category: str, lat: float, lng: float, image: dict | None,
                 answers: dict) -> dict:
    """Everything the rules look at; None = unknown (a question can settle it).
    facts['_src'] says where each fact came from (the evidence)."""
    zone = owner_zone(conn, lat, lng)
    facts, src = road_class(nearest_road(conn, lat, lng), image)
    facts |= {"category": category, "zone": zone[0] if zone else "none",
              "zone_name": zone[1] if zone else None, "ifc": ifc_drain(conn, lat, lng),
              "discom": discom(conn, lat, lng), "drain": None, "ghat": None}
    src |= {"zone": f"zone:{facts['zone']}", "ifc": "zone:ifc_trunk_drain",
            "discom": "zone:discom_area"}
    if image and image.get("confidence", 0) >= THRESHOLD \
            and image.get("drain_type") in ("open_nala", "sewer_manhole"):
        facts["drain"], src["drain"] = image["drain_type"], "image"
    return apply_answers(facts | {"_src": src}, answers)


def apply_answers(facts: dict, answers: dict) -> dict:
    """The user's answers beat the data."""
    facts = facts | {"_src": dict(facts["_src"])}
    for qid, answer in answers.items():
        q = QUESTIONS.get(qid)
        if q and answer in q["sets"]:
            facts[q["fact"]], facts["_src"][q["fact"]] = q["sets"][answer], "user_answer"
    return facts


def fact(facts: dict, key: str):
    """A fact's value; None = unknown. The road owner follows from the other road facts."""
    if key == "owner":
        if facts["nhai"]:
            return "NHAI"
        if facts["mathura"] or facts["pwd_list"] or facts["width"] == "wide":
            return "PWD"
        return "MCD" if facts["width"] == "narrow" else None
    return facts["zone"] if key == "not_zone" else facts[key]


def match_rule(rules: list[dict], facts: dict) -> tuple[str, object]:
    """First rule (by priority) whose category and conditions fit the facts:
    ('rule', row) | ('ask', question_id) if that rule needs a fact we don't know yet
    | ('none', None). A pure function (no DB), so every rule can be tested on its own."""
    for rule in rules:
        spec = RULES.get(rule["rule_id"], {})
        if "when" not in spec:  # overlay / ask / candidate / data_missing / shadowed
            continue
        if "*" not in rule["categories"] and facts["category"] not in rule["categories"]:
            continue
        unknown = None
        for key, want in spec["when"].items():
            have = fact(facts, key)
            if have is None:
                unknown = unknown or key
            elif (have == want) == (key == "not_zone"):  # not_zone wants a different zone
                break
        else:
            return ("ask", FACT_QUESTION[unknown]) if unknown else ("rule", rule)
    return "none", None


def evidence_for(rule: dict, facts: dict) -> list[str]:
    """Where the facts this rule used came from: 'pwd_list', 'width', 'zone:cantonment', ..."""
    out = []
    for key in RULES[rule["rule_id"]]["when"]:
        if key == "owner":  # the owner came from the PWD list or from the width
            if facts["pwd_list"]:
                e = facts["_src"]["pwd_list"]  # 'pwd_list' or 'pwd_list_parallel'
            else:
                e = "pwd_list" if facts["mathura"] else facts["_src"].get("width")
        else:
            e = None if key == "not_zone" else facts["_src"].get(key)
        if e and e not in out:
            out.append(e)
    return out


def confidence(rule: dict, evidence: list[str]) -> float:
    """rule confidence x weakest evidence x 0.8 if uncited x 0.9 if not checked for a year."""
    c = rule["confidence"] * min([EVIDENCE.get(e, 1.0) for e in evidence] or [1.0])
    if not rule.get("rule_source_url"):
        c *= 0.8
    if rule.get("last_verified") and (date.today() - rule["last_verified"]).days > 365:
        c *= 0.9
    return round(c, 2)


def label(c: float) -> str:
    return "very_likely" if c >= 0.8 else "likely" if c >= 0.6 else "possible"


def dept_name(code: str) -> str:
    return departments().get(code, {}).get("name_en", code)


def why(rule: dict, facts: dict, code: str, evidence: list[str], second=False) -> tuple:
    """(message_key, one plain-English line) saying why this department."""
    dept, road = dept_name(code), facts.get("road_name")
    if second:
        return "why_rule", f"{dept} may also be responsible here (rule {rule['rule_id']})"
    if "road_ref" in evidence:
        return "why_road_ref", f"{road or 'This road'} is part of NH-48, so {dept} maintains it"
    if "pwd_list_parallel" in evidence:
        return "why_pwd_list_parallel", "Divided road; the other carriageway is on the PWD list"
    if "pwd_list" in evidence and road:
        return "why_pwd_list", f"{road} is on the PWD list of roads, so {dept} maintains it"
    if {"width", "image"} & set(evidence):
        size = "60 ft or wider" if facts["width"] == "wide" else "narrower than 60 ft"
        return "why_width", f"The road{f' ({road})' if road else ''} is {size}, so {dept} looks after it"
    if f"zone:{facts['zone']}" in evidence and facts["zone_name"]:
        return "why_zone", f"The spot is inside {facts['zone_name']}, so {dept} is responsible"
    name = categories().get(facts["category"], {}).get("name_en", facts["category"]).lower()
    return "why_rule", f"{dept} handles {name} here (rule {rule['rule_id']})"


def option(rule, code, facts, evidence, second=False) -> dict:
    """One possible answer: a department, the rule behind it, and why."""
    return {"rule": rule, "code": code, "facts": facts, "evidence": evidence, "second": second}


def score(o: dict) -> float:
    """An option's confidence; the 2nd candidate of a rule counts a little less."""
    return confidence(o["rule"], o["evidence"]) * (0.8 if o["second"] else 1)


def card(o: dict) -> dict:
    """An option as the UI shows it."""
    return {"department": {"code": o["code"], "name_en": dept_name(o["code"])},
            "confidence_label": label(score(o)), "rule_id": o["rule"]["rule_id"],
            "why": why(o["rule"], o["facts"], o["code"], o["evidence"], o["second"])[1]}


def load_rules(conn) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        rules = cur.execute("SELECT * FROM routing_rule ORDER BY priority, rule_id").fetchall()
    if not rules:
        raise RuntimeError("routing_rule is empty: run scripts/load_rules.py")
    return rules


def fallback(rules: list[dict]) -> dict:
    return next(r for r in rules if r["rule_id"] == "GN-03")


def question(qid: str, retry: bool = False) -> dict:
    q = QUESTIONS[qid]
    return {"kind": "issue_question", "question_id": qid, "text": q["text"],
            "message_key": f"q_{qid}", "retry": retry,
            "options": [{"value": v, "label": lbl} for v, lbl in [*q["options"], NOT_SURE]]}


def pick_question(options: list[dict], retry: bool = False) -> dict:
    return {"kind": "pick_candidate", "message_key": "q_pick_candidate", "retry": retry,
            "text": "More than one department could handle this. Which fits?",
            "candidates": [card(o) for o in sorted(options, key=score, reverse=True)]}


def not_sure_options(rules: list[dict], facts: dict, qid: str) -> list[dict]:
    """'Not sure' -> the department each of the question's real answers would lead to."""
    out = []
    for answer in QUESTIONS[qid]["sets"]:
        trial = apply_answers(facts, {qid: answer})
        kind, rule = match_rule(rules, trial)
        if kind == "rule" and rule["department_code"] not in [o["code"] for o in out]:
            out.append(option(rule, rule["department_code"], trial, evidence_for(rule, trial)))
    return out


def options_for(rules: list[dict], facts: dict, answers: dict):
    """('ask', question) or ('options', [option, ...]) for this report."""
    unsure = [q for q, a in answers.items() if a == "not_sure" and q in QUESTIONS]
    if unsure:
        return "options", not_sure_options(rules, facts, unsure[0]) or \
            [option(fallback(rules), fallback(rules)["department_code"], facts, [])]
    kind, rule = match_rule(rules, facts)
    if kind == "ask":
        return "ask", question(rule)
    rule = rule or fallback(rules)  # GN-03: no rule matched
    ev = evidence_for(rule, facts)
    spec, out = RULES[rule["rule_id"]], [option(rule, rule["department_code"], facts, ev)]
    second = spec.get("second")
    if second and second != rule["department_code"] and \
            (spec.get("always_pick") or confidence(rule, ev) < THRESHOLD):
        out.append(option(rule, second, facts, ev, second=True))
    return "options", out


def decide(conn, category: str, lat: float, lng: float, image: dict | None = None,
           answers: dict | None = None) -> dict:
    """The whole §1 flow. outcome: out_of_coverage | ask | pick | decided."""
    answers = answers or {}
    ward = find_ward(conn, lat, lng)
    if ward is None:
        return {"outcome": "out_of_coverage"}
    rules = load_rules(conn)
    kind, found = options_for(rules, gather_facts(conn, category, lat, lng, image, answers),
                              answers)
    if kind == "ask":
        return {"outcome": "ask", "question": found}
    chosen = found[0]
    if len(found) >= 2:  # two departments could do it: the user picks (evidence user_answer)
        picked = [o for o in found if o["code"] == answers.get("pick_candidate")]
        if not picked:
            return {"outcome": "pick", "question": pick_question(found)}
        chosen = picked[0]
    return decision(ward, chosen, found, picked=len(found) >= 2)


def decision(ward: dict, chosen: dict, options: list[dict], picked=False) -> dict:
    """picked: the user chose this department from the cards (B-042). Then the evidence is
    the card's own evidence + 'user_answer' once. A 2nd candidate keeps none: its evidence
    belongs to the first card (Mathura Road's 'pwd_list' is PWD's, not NHAI's). The why
    line is the chosen card's own reason, saying the user chose it."""
    rule, facts = chosen["rule"], chosen["facts"]
    key, line = why(rule, facts, chosen["code"], chosen["evidence"], chosen["second"])
    evidence = chosen["evidence"]
    if picked:
        own = [] if chosen["second"] else [e for e in evidence if e != "user_answer"]
        evidence = [*own, "user_answer"]
    if picked:  # "You chose NHAI. It may also be responsible here (rule RD-02)"
        dept = dept_name(chosen["code"])
        line = "It" + line[len(dept):] if line.startswith(dept) else line
        key, line = "why_user_answer", f"You chose {dept}. {line}"
    conf = confidence(rule, evidence)
    cands = [card(o) for o in options]
    if facts["category"] == "ROAD_CAVE_IN" and chosen["code"] != "DJB":  # RD-07
        cands.append({"department": {"code": "DJB", "name_en": dept_name("DJB")},
                      "confidence_label": "possible", "rule_id": "RD-07",
                      "why": "A burst water main or sewer under the road often causes a cave-in"})
    return {**ward, "outcome": "decided", "rule_id": rule["rule_id"],
            "department_code": chosen["code"], "confidence": conf,
            "confidence_label": label(conf), "evidence": evidence,
            "candidates": cands, "why": line, "why_key": key,
            "sla_days": rule["sla_days"] or SUGGESTED_SLA_DAYS,
            "curated": bool(rule.get("rule_source_url")), "fake": False}


def route(conn, category: str, lat: float, lng: float):
    """Routing with no answers yet: the decision dict, or None when out of coverage."""
    d = decide(conn, category, lat, lng)
    return None if d["outcome"] == "out_of_coverage" else d
