"""Every piece of UI text, in one place. The interface is English only (UX §14).

Users may still write in Hindi or Hinglish; their own text is shown exactly as written.
API errors come back as a `message_key`, so every key the backend can send needs a line here.
"""

TEXT = {
    "app_name": "Civic Grievance Router",
    # Sidebar
    "new_report": "New report",
    "my_reports": "My reports",
    "recent": "Recent",
    "no_recent": "No reports yet",
    "connected": "connected",
    "offline": "offline",
    "settings": "Settings",
    "text_size": "Text size",
    "sign_out": "Sign out",
    # Login
    "login_title": "Sign in to continue",
    "login_help": "Your name is enough for now. Email is optional.",
    "name_label": "Name",
    "email_label": "Email (optional)",
    "continue": "Continue",
    "need_name": "Please enter your name.",
    "bad_email": "This email doesn't look right.",
    # Composer (new report)
    "new_title": "What's the problem?",
    "new_help": "Add a photo and a short description. English, Hindi or Hinglish all work.",
    "text_label": "Describe the problem",
    "text_ph": "e.g. big pothole in front of the school…",
    "camera": "Camera",
    "upload": "Upload",
    "remove_photo": "Remove",
    "privacy_hint": "Don't include your name or address.",
    "photo_not_kept": "Photo couldn't be kept after refresh, please re-attach.",
    "unverified": "Unverified location",
    "prepare": "Prepare complaint",
    "preparing": "Preparing your complaint…",
    "checking_place": "Checking your location once more…",
    # Location chip and panel
    "locating": "Locating…",
    "add_location": "Add location",
    "change": "Change",
    "pin_gps": "Your location",
    "pin_map": "Pin on map",
    "loc_title": "Set the location",
    "loc_help": "Click the map to move the pin, or use the arrows (about 10 m each).",
    "no_gps": "We couldn't get your location. Search a nearby landmark or click the map.",
    "loc_shaky": "Location may be off, please check the pin.",
    "use_gps": "Use my location",
    "nudge": "Move the pin about 10 m",
    "landmark_label": "Landmark",
    "landmark_ph": "e.g. Kalindi Kunj Park",
    "find": "Search",
    "landmark_none": "No place found. Try another landmark or click the map.",
    "done": "Done",
    "pin_moved": "Pin moved {n} m",
    "undo": "Undo",
    # Dossier
    "dossier_title": "Your complaint is ready",
    "we_dont_send": "We don't send this. You file it.",
    "who_fixes": "Who fixes this",
    "ward": "Ward {n} · {name}",
    "curated": "Curated data",
    "deadline_suggested": "Suggested deadline · {n} days",
    "deadline": "Deadline · {n} days",
    "you_reported": "What you reported",
    "complaint_letter": "Complaint letter",
    "copy_hint": "Tap the copy icon to copy the letter.",
    "call": "Call {n}",
    "photo": "Photo (faces and number plates blurred)",
    "photo_withheld": "Photo withheld for privacy",
    "demo_ai": "Demo: AI is simulated, results are not real.",
    "conf_very_likely": "Very likely",
    "conf_likely": "Likely",
    "conf_unsure": "Not sure",
    # Out of coverage (FR-3.2): no department claim, only general helplines
    "uncovered_title": "We don't cover this area yet",
    "uncovered_help": "We can't name the right department for this place yet. "
    "These helplines take complaints from anywhere in Delhi.",
    "outside_title": "This is outside Delhi",
    "outside_help": "Contact your local authority, or use CPGRAMS (national portal).",
    "open_cpgrams": "Open CPGRAMS",
    # My reports
    "reports_help": "Everything you've reported, newest first.",
    "reports_empty": "No reports yet. Saw a problem? Report it.",
    "open": "Open",
    "state_dossier_ready": "Complaint ready",
    "state_out_of_coverage": "Area not covered",
    # Categories and severity
    "cat_pothole": "Pothole",
    "cat_garbage": "Garbage dump",
    "cat_waterlogging": "Waterlogging / sewage",
    "cat_streetlight": "Broken streetlight",
    "sev_high": "High risk",
    "sev_medium": "Medium risk",
    "sev_low": "Low risk",
    # Notices from the API (shown as pills on the dossier)
    "low_gps_accuracy": "Approximate location",
    "photo_unverified_location": "Photo location not verified",
    "location_reported_by_phone": "Location reported by phone (not verified)",
    # API error keys (message_key, docs/contracts/sprint2-api.md + sprint3-api.md)
    "need_photo_or_text": "Please add a photo or describe the problem.",
    "photo_too_large": "The photo is larger than 5 MB. Please choose a smaller one.",
    "photo_not_jpeg_png": "This photo isn't a JPG or PNG. Please choose another one.",
    "too_many_photos": "Please send only one photo.",
    "text_too_long": "The text is over 1000 characters. Please shorten it.",
    "need_lat_and_lng": "The location came through incomplete. Please try again.",
    "invalid_input": "Something in the form isn't right. Please check and try again.",
    "dossier_not_found": "We couldn't find this complaint.",
    "media_not_found": "We couldn't find this photo.",
    "not_found": "This page doesn't exist.",
    "api_unreachable": "Couldn't reach the server. Please try again in a moment.",
    "server_error": "Something went wrong on our side. Please try again.",
    "login_required": "Please sign in again to continue.",
    "need_pin": "Please set the location first.",
    "rate_limited_daily": "You've sent many reports today. Please try again tomorrow.",
    "rate_limited_same_spot": "You already reported this problem here today. See My reports.",
    "rate_limited_cooldown": "Too many unclear photos. Please wait 15 minutes and try again.",
    "rate_limited_login": "Too many sign-in attempts. Please wait a few minutes and try again.",
    "geocode_failed":"Couldn't search for places right now. Click the map instead.",
}


def t(key: str, **fmt) -> str:
    # Fall back to the key itself, so a missing string never crashes a page.
    text = TEXT.get(key, key)
    return text.format(**fmt) if fmt else text
