from account import PHOTO_KEY, STORE, TOKEN_KEY


def test_unblurred_photo_draft_never_outlives_the_tab():
    # SEC-012: the draft photo is not blurred yet, so it must not sit in localStorage.
    assert STORE[PHOTO_KEY] == "sessionStorage"
    assert STORE[TOKEN_KEY] == "localStorage"  # login still survives a refresh (B-012)
