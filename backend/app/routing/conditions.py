"""How each rule in data/rules_draft.csv is checked.

The CSV `condition` column is prose for people ("road NOT in inventory AND row_m >= 18.29"),
so each rule's condition is written here once, as a small dict of facts it needs:

  zone      owner zone at the pin ('cantonment', ..., 'none')   not_zone   any zone but this
  nhai      on the NH-48 NHAI stretch          mathura   on Mathura Road (NH-2)
  pwd_list  road is in PWD's inventory         width     'wide' (>= 60 ft) | 'narrow'
  owner     road owner 'PWD' | 'MCD'           discom    'BRPL' | 'BYPL' | 'TPDDL'
  drain     'open_nala' | 'sewer_manhole'      ifc       an I&FC trunk drain within 25 m
  ghat      'bank' | 'water'

`second` is the department shown as the 2nd candidate (from the CSV notes).
Rules that never pick the owner on their own carry a `role` instead:
  overlay       EM-*: the emergency "call first" card (app/intake/emergency.py)
  ask           "X unknown -> ask" rules: reached as the question for that fact
  candidate     only ever a 2nd candidate (RD-07)
  data_missing  needs a map layer we don't have yet (never fires)
  shadowed      always decided earlier by a higher-priority rule
"""

OWNER_ZONES = ["ndmc_area", "cantonment", "yamuna_floodplain", "dda_land", "metro_premises",
               "railway_land", "industrial_area", "asi_protected", "forest_ridge",
               "cpwd_colony", "jj_cluster"]  # discom_area / ifc_trunk_drain are lookups only

FLOOD = "yamuna_floodplain"

RULES = {
    **{f"EM-0{i}": {"role": "overlay"} for i in range(1, 7)},
    "AR-01": {"when": {"zone": "cantonment"}},
    "AR-02": {"when": {"zone": "cantonment"}, "second": "BRPL"},
    "AR-03": {"when": {"zone": "ndmc_area"}},
    "AR-04": {"when": {"zone": "ndmc_area"}},
    "ZN-01": {"when": {"zone": "metro_premises"}},
    "ZN-02": {"when": {"zone": "railway_land"}},
    "ZN-03": {"when": {"zone": "asi_protected"}, "second": "MCD"},
    "ZN-04": {"when": {"zone": FLOOD, "ghat": "water"}},
    "ZN-05": {"when": {"zone": FLOOD, "ghat": "bank"}, "second": "IFC"},
    "ZN-06": {"when": {"zone": FLOOD}, "second": "MCD"},
    "ZN-07": {"role": "data_missing", "reason": "no embankment (bund) layer"},
    "ZN-08": {"when": {"zone": "dda_land"}},
    "ZN-09": {"when": {"zone": "industrial_area"}, "second": "MCD"},
    "ZN-10": {"when": {"zone": "jj_cluster"}, "second": "MCD"},
    "ZN-11": {"when": {"zone": "cpwd_colony"}},
    "RD-01": {"when": {"nhai": True}},
    "RD-02": {"when": {"mathura": True}, "second": "NHAI", "always_pick": True},
    "RD-03": {"when": {"pwd_list": True}},
    "RD-04": {"when": {"width": "wide"}, "second": "MCD"},
    "RD-05": {"when": {"width": "narrow"}},
    "RD-06": {"role": "ask", "question": "road_class"},
    "RD-07": {"role": "candidate", "reason": "DJB shown 2nd for every road cave-in"},
    "DR-01": {"when": {"drain": "open_nala", "ifc": True}},
    "DR-02": {"when": {"drain": "open_nala", "owner": "PWD"}},
    "DR-03": {"when": {"drain": "open_nala", "owner": "MCD"}},
    "DR-04": {"when": {"drain": "sewer_manhole"}},
    "DR-05": {"role": "ask", "question": "drain_type"},
    "SW-01": {"when": {}},
    "SW-02": {"when": {"drain": "sewer_manhole"}},
    "SW-03": {"when": {"drain": "open_nala", "owner": "PWD"}},
    "SW-04": {"when": {}, "second": "MCD"},  # any other manhole: DJB first, road owner 2nd
    "WT-01": {"when": {}},
    "WS-01": {"when": {}},
    "WS-02": {"when": {}},
    "WS-03": {"when": {}},
    "SL-01": {"when": {"owner": "PWD"}},
    "SL-02": {"when": {"discom": "BRPL"}},
    "SL-03": {"when": {"discom": "BYPL"}},
    "SL-04": {"when": {"discom": "TPDDL"}},
    "SL-05": {"role": "ask", "question": "which_discom"},
    "EL-01": {"when": {"discom": "BRPL"}},
    "EL-02": {"when": {"discom": "BYPL"}},
    "EL-03": {"when": {"discom": "TPDDL"}},
    "EL-04": {"role": "ask", "question": "which_discom"},
    "TR-01": {"when": {}},
    # not_zone first: inside the Ridge we never ask about the road (TR-04 decides)
    "TR-02": {"when": {"not_zone": "forest_ridge", "owner": "PWD"}, "second": "MCD_HORT"},
    "TR-03": {"when": {"not_zone": "forest_ridge"}},
    "TR-04": {"when": {"zone": "forest_ridge"}},
    "PK-01": {"when": {}, "second": "DDA"},
    "AN-01": {"when": {}},
    "AN-02": {"when": {}},
    "TF-01": {"when": {}},
    "TF-02": {"when": {}},
    "TP-01": {"when": {}, "second": "TRANSPORT"},
    "TP-02": {"when": {}},
    "PL-01": {"when": {}},
    "PL-02": {"when": {}},
    "PL-03": {"when": {"not_zone": FLOOD}, "second": "IFC"},
    "EN-01": {"role": "shadowed", "reason": "RD-03/04/05 always decide footpath encroachment"},
    "BL-01": {"when": {}},
    "BL-02": {"when": {}},
    "OT-01": {"when": {}},
    "OT-02": {"when": {}},
    "OT-03": {"when": {}},
    "GN-01": {"when": {}},
    "GN-02": {"role": "data_missing", "reason": "no way yet to tell a central-body matter"},
    "GN-03": {"when": {}},  # the '*' fallback: no rule matched
}

# One question per unknown fact. `sets` = what each answer means for that fact.
QUESTIONS = {
    "road_class": {
        "fact": "width", "text": "Is this on a main road or an inner lane?",
        "options": [("main_road", "Main road"), ("inner_lane", "Inner lane / gali")],
        "sets": {"main_road": "wide", "inner_lane": "narrow"}},
    "drain_type": {
        "fact": "drain", "text": "Is it a big open nala, or a sewer line / manhole?",
        "options": [("open_nala", "Big open nala"), ("sewer_manhole", "Sewer line / manhole")],
        "sets": {"open_nala": "open_nala", "sewer_manhole": "sewer_manhole"}},
    "ghat_position": {
        "fact": "ghat", "text": "Is it on the river bank (ghat steps) or in the water?",
        "options": [("bank", "On the bank / ghat steps"), ("water", "In the water")],
        "sets": {"bank": "bank", "water": "water"}},
    "which_discom": {
        "fact": "discom", "text": "Which company sends your electricity bill?",
        # Neutral company ids, not department codes (B-044): the text model only ever
        # returns one of these, and this plain mapping turns it into the department.
        "options": [("brpl", "BSES Rajdhani (BRPL)"), ("bypl", "BSES Yamuna (BYPL)"),
                    ("tpddl", "Tata Power (TPDDL)")],
        "sets": {"brpl": "BRPL", "bypl": "BYPL", "tpddl": "TPDDL"}},
}
NOT_SURE = ("not_sure", "Not sure")
# Which question settles an unknown fact (the road owner comes from the road's width).
FACT_QUESTION = {"width": "road_class", "owner": "road_class", "drain": "drain_type",
                 "ghat": "ghat_position", "discom": "which_discom"}
