"""Location for a new report: GPS, the location chip, and the panel with map, nudges and
landmark search (S07, UX §14).

Everything lives in st.session_state, so any rerun keeps the pin:
- "pin":  {"lat", "lng", "source", "acc", "label"}; source is gps | gps_moved | landmark |
          map_click (contract field `pin_source`)
- "gps":  the best GPS fix so far {"lat", "lng", "acc"}; "gps_drift" / "gps_error" flags
- "loc_open": True while the location panel is open
- "pin_prev": the pin before the last map tap, for "Pin moved · Undo"
"""

import math

import folium
import streamlit as st
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation

from lib.api import ApiError, geocode
from strings import t

DELHI = (28.6139, 77.2090)  # map centre before there is a pin
LOW_ACCURACY_M = 50  # REQUIREMENTS: warn when GPS accuracy is worse than this
DRIFT_M = 30  # two fixes further apart than this (and than their accuracy) = drift
NUDGE_M = 10  # one arrow tap
M_PER_DEG = 111_320  # metres per degree of latitude (close enough for 10-30 m)


def meters(a: dict, b: dict) -> float:
    """Distance between two points. A flat-earth formula is fine at these short distances."""
    dy = (b["lat"] - a["lat"]) * M_PER_DEG
    dx = (b["lng"] - a["lng"]) * M_PER_DEG * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy)


def merge_fix(old: dict | None, new: dict) -> tuple[dict, bool]:
    """Keep the fix with the better (smaller) accuracy, and say if the two fixes disagree.

    Two fixes only "disagree" if they are further apart than 30 m AND than either fix's own
    accuracy: two ±80 m fixes 50 m apart are normal GPS noise, not drift.
    """
    if old is None:
        return new, False
    best = new if new["acc"] < old["acc"] else old
    return best, meters(old, new) > max(DRIFT_M, old["acc"], new["acc"])


def set_pin(lat: float, lng: float, source: str, acc=None, label: str = "") -> None:
    st.session_state.pin = {"lat": lat, "lng": lng, "source": source, "acc": acc,
                            "label": label}
    st.session_state.pop("pin_prev", None)  # a new pin replaces any "Undo" offer


def read_gps(component_key: str) -> None:
    """Ask the browser for a fix. The value arrives on a later rerun; use each one once."""
    loc = get_geolocation(component_key=component_key)
    seen = st.session_state.setdefault("gps_seen", set())
    if not loc or component_key in seen:
        return
    seen.add(component_key)
    if "coords" not in loc:  # permission denied, no GPS, or timeout
        st.session_state.gps_error = True
        return
    c = loc["coords"]
    fix = {"lat": c["latitude"], "lng": c["longitude"], "acc": c["accuracy"]}
    best, drift = merge_fix(st.session_state.get("gps"), fix)
    st.session_state.gps = best
    st.session_state.gps_drift = st.session_state.get("gps_drift", False) or drift
    pin = st.session_state.get("pin")
    if pin is None or pin["source"] == "gps":  # never undo a pin the user placed by hand
        set_pin(best["lat"], best["lng"], "gps", acc=best["acc"])


def gps_round_key() -> str:
    return f"gps_{st.session_state.setdefault('gps_round', 0)}"


def reset_location() -> None:
    """Forget the pin and ask for GPS again (a new component key = a fresh reading)."""
    for key in ("pin", "gps", "gps_drift", "gps_error", "candidates", "submitting"):
        st.session_state.pop(key, None)
    st.session_state.gps_round = st.session_state.get("gps_round", 0) + 1


def nudge(d_north: int, d_east: int) -> None:
    pin = st.session_state.pin
    lat = pin["lat"] + d_north * NUDGE_M / M_PER_DEG
    lng = pin["lng"] + d_east * NUDGE_M / (M_PER_DEG * math.cos(math.radians(pin["lat"])))
    source = "gps_moved" if pin["source"] == "gps" else pin["source"]
    set_pin(lat, lng, source, acc=pin["acc"], label=pin["label"])


def on_map_click() -> None:
    """A tap is easy to make by accident, so keep the old pin and offer Undo (B-021)."""
    click = (st.session_state.get("pin_map") or {}).get("last_clicked")
    if click:
        prev = st.session_state.get("pin")
        set_pin(click["lat"], click["lng"], "map_click")
        if prev:
            st.session_state.pin_prev = prev


def undo_pin() -> None:
    st.session_state.pin = st.session_state.pop("pin_prev")


def pick_candidate(c: dict) -> None:
    set_pin(c["lat"], c["lng"], "landmark", label=c["label"])
    st.session_state.pop("candidates", None)


def shaky() -> bool:
    """E02: warn only while the pin is still the raw GPS one."""
    gps, pin = st.session_state.get("gps"), st.session_state.get("pin")
    if not gps or not pin or pin["source"] != "gps":
        return False
    return gps["acc"] > LOW_ACCURACY_M or st.session_state.get("gps_drift", False)


def chip_label() -> str:
    """The location chip in the composer toolbar (UX §14)."""
    pin = st.session_state.get("pin")
    if pin:
        where = pin["label"] or (t("pin_gps") if pin["source"].startswith("gps")
                                 else t("pin_map"))
        where = where if len(where) <= 28 else where[:27] + "…"
        acc = f" · ±{pin['acc']:.0f} m" if pin["acc"] and pin["source"] != "landmark" else ""
        return f"{where}{acc} · {t('change')}"
    if st.session_state.get("gps_error"):
        return t("add_location")
    return t("locating")


def draw_map() -> None:
    pin = st.session_state.get("pin")
    # The base map never changes, so streamlit-folium keeps the same map; the marker is
    # passed separately (feature_group_to_add) and `center` follows the pin.
    m = folium.Map(location=DELHI, zoom_start=11, tiles="OpenStreetMap")
    marker = folium.FeatureGroup(name="pin")
    if pin:
        folium.Marker((pin["lat"], pin["lng"])).add_to(marker)
    st_folium(m, key="pin_map", height=320, use_container_width=True,
              returned_objects=["last_clicked"], on_change=on_map_click,
              feature_group_to_add=marker,
              center=(pin["lat"], pin["lng"]) if pin else DELHI, zoom=17 if pin else 11)


def landmark_search(token: str) -> None:
    with (st.form("landmark_form", border=False),
          st.container(horizontal=True, vertical_alignment="bottom")):
            text = st.text_input(t("landmark_label"), placeholder=t("landmark_ph"),
                                 max_chars=200, key="landmark")
            found = st.form_submit_button(t("find"), key="find_landmark",
                                          icon=":material/search:")
    if found and text.strip():
        try:
            st.session_state.candidates = geocode(token, text.strip())[:5]
        except ApiError as e:
            st.session_state.candidates = []
            st.error(t(e.message_key))
            return
        if not st.session_state.candidates:
            st.info(t("landmark_none"))
    for i, c in enumerate(st.session_state.get("candidates", [])):
        st.button(c["label"], key=f"cand_{i}", icon=":material/location_on:",
                  width="stretch", on_click=pick_candidate, args=(c,))


def read_gps_now() -> None:
    """Called on every run of the new-report page (the reader itself is invisible)."""
    read_gps(gps_round_key())
    if st.session_state.get("submitting"):
        read_gps(gps_round_key() + "_submit")  # second fix, taken at submit


def render_location_panel(token: str) -> None:
    with st.container(border=True, key="card_location"):
        with st.container(horizontal=True, horizontal_alignment="distribute",
                          vertical_alignment="center"):
            st.markdown(f"#### {t('loc_title')}")
            st.button(t("done"), key="loc_done", type="tertiary",
                      on_click=lambda: st.session_state.update(loc_open=False))
        if st.session_state.get("gps_error") and not st.session_state.get("pin"):
            st.markdown(f'<div class="notice">{t("no_gps")}</div>', unsafe_allow_html=True)
        st.caption(t("loc_help"))
        draw_map()
        pin = st.session_state.get("pin")
        with st.container(horizontal=True, vertical_alignment="center", key="nudges"):
            for label, dn, de in (("↑", 1, 0), ("↓", -1, 0), ("←", 0, -1), ("→", 0, 1)):
                st.button(label, key=f"nudge_{label}", help=t("nudge"), disabled=not pin,
                          on_click=nudge, args=(dn, de))
            st.space("stretch")
            st.button(t("use_gps"), key="gps_again", icon=":material/my_location:",
                      on_click=reset_location)
        if pin:
            st.caption(f"{pin['lat']:.5f}, {pin['lng']:.5f}")
        landmark_search(token)
