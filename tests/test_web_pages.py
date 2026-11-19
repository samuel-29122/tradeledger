"""Phases 5-6: every website page renders, shows real data, and the shell
(navigation, headings, section labelling) behaves as documented."""

import pytest

PAGES = [
    "/",
    "/trades",
    "/trades/new",
    "/trades/1",
    "/trades/1/edit",
    "/trades/1/delete",
    "/instruments",
    "/analytics",
    "/about",
]


@pytest.mark.parametrize("url", PAGES)
def test_every_page_renders_with_sample_data(seeded_client, url):
    response = seeded_client.get(url)
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert "<h1" in html
    assert 'id="main"' in html  # skip-link target
    assert 'class="skip-link"' in html


@pytest.mark.parametrize("url", ["/", "/trades", "/trades/new", "/instruments", "/analytics", "/about"])
def test_pages_render_with_an_empty_database(client, url):
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/no-such-page", "/trades/999", "/trades/999/edit", "/trades/999/delete"])
def test_unknown_pages_and_trades_are_html_404s(seeded_client, url):
    response = seeded_client.get(url)
    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert b"Page not found" in response.data


def test_non_numeric_trade_id_is_404(seeded_client):
    assert seeded_client.get("/trades/abc").status_code == 404


# ---------- Navigation shell ----------


def test_sidebar_lists_every_section(client):
    html = client.get("/").get_data(as_text=True)
    for label in ("Overview", "Trade journal", "Add trade", "Instruments", "Analytics", "About"):
        assert label in html


@pytest.mark.parametrize(
    "url, label",
    [
        ("/", "Overview"),
        ("/trades", "Trade journal"),
        ("/trades/new", "Add trade"),
        ("/instruments", "Instruments"),
        ("/about", "About"),
    ],
)
def test_current_section_is_marked_for_assistive_technology(client, url, label):
    html = client.get(url).get_data(as_text=True)
    marker = html.index('aria-current="page"')
    # The label of the marked link follows the attribute closely.
    assert label in html[marker : marker + 200]
    assert html.count('aria-current="page"') == 1


def test_trade_pages_highlight_the_journal(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    marker = html.index('aria-current="page"')
    assert "Trade journal" in html[marker : marker + 200]


def test_menu_button_is_wired_for_accessibility(client):
    html = client.get("/").get_data(as_text=True)
    assert 'aria-controls="sidebar"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="sidebar"' in html


def test_no_fake_account_features(client):
    """Phase 5 banned FAKE account features. Phase 8 added real ones, so
    "Sign out" must now be a working POST form (with a CSRF token), and the
    still-fake features stay absent."""
    html = client.get("/").get_data(as_text=True)
    assert '<form method="post" action="/logout">' in html
    assert 'name="csrf_token"' in html.split('action="/logout"')[1][:200]
    lowered = html.lower()
    for fake in ("avatar", "notifications", 'href="/logout"'):
        assert fake not in lowered


def test_scripts_and_styles_are_external_files_only(client):
    """The Content-Security-Policy forbids inline scripts and styles, so
    templates must not contain any."""
    for url in ("/", "/trades/new", "/instruments"):
        html = client.get(url).get_data(as_text=True)
        assert "<script>" not in html
        assert "<style" not in html
        assert " style=" not in html
        assert " onclick=" not in html


@pytest.mark.parametrize(
    "path",
    [
        "/static/js/app.js",
        "/static/js/early.js",
        "/static/js/trade-form.js",
        "/static/css/style.css",
        "/static/css/base.css",
        "/static/css/layout.css",
        "/static/css/components.css",
        "/static/css/pages.css",
        "/static/css/analytics.css",
        "/static/js/analytics.js",
    ],
)
def test_static_assets_are_served(client, path):
    with client.get(path) as response:  # closes the static file
        assert response.status_code == 200


# ---------- Overview ----------


def test_overview_shows_real_counts_and_recent_trades(seeded_client):
    html = seeded_client.get("/").get_data(as_text=True)
    assert "Recent trades" in html
    # 16 sample trades: 2 open, 14 closed, 5 instruments.
    assert ">2<" in html and ">14<" in html and ">5<" in html
    assert "XAUUSD" in html


def test_overview_empty_state(client):
    html = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" in html
    assert "Your journal is empty" in html


# ---------- Journal ----------


def test_journal_lists_every_column(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    for heading in (
        "Entry time",
        "Instrument",
        "Status",
        "Entry",
        "Stop-loss",
        "Exit",
        "Size",
        "Net P&amp;L",
        "realised R",
        "Strategy",
        "Actions",
    ):
        assert heading in html
    assert "Showing 1–16 of 16 trades" in html


def test_journal_labels_outcomes_in_words_not_only_colour(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    # Badges contain the word itself (CSS only changes its letter case).
    for label in ("Win", "Loss", "Breakeven", "Open"):
        assert f">{label}</span>" in html
    assert "Long" in html and "Short" in html


def test_journal_money_carries_its_currency(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    assert "393.00 USD" in html  # sample trade 1
    assert "GBP" in html  # UK100 trades are in GBP


def test_journal_filters_by_status(seeded_client):
    html = seeded_client.get("/trades?status=OPEN").get_data(as_text=True)
    assert "Showing 1–2 of 2 trades matching your filters" in html


def test_journal_filters_by_strategy(seeded_client):
    html = seeded_client.get("/trades?strategy=Breakout").get_data(as_text=True)
    assert "of 6 trades" in html


def test_journal_search_covers_the_whole_database(seeded_client):
    html = seeded_client.get("/trades?q=xau").get_data(as_text=True)
    assert "of 4 trades" in html
    assert "EURUSD</a>" not in html


def test_journal_search_with_no_matches_shows_empty_state(seeded_client):
    html = seeded_client.get("/trades?q=zzzz-nothing").get_data(as_text=True)
    assert "No trades match" in html


def test_journal_reset_link_present_when_filtering(seeded_client):
    html = seeded_client.get("/trades?status=CLOSED").get_data(as_text=True)
    assert 'href="/trades"' in html
    assert "Reset" in html


@pytest.mark.parametrize(
    "query",
    [
        "status=MAYBE",
        "start=2026-13-01",
        "instrument_id=abc",
        "start=2026-03-10&end=2026-03-01",
        "q=" + "x" * 101,
    ],
)
def test_invalid_filters_are_400_with_an_explanation(seeded_client, query):
    response = seeded_client.get("/trades?" + query)
    assert response.status_code == 400
    assert b'aria-invalid="true"' in response.data


def test_pagination_keeps_filters(app, seeded_client):
    app.config["JOURNAL_PER_PAGE"] = 5
    html = seeded_client.get("/trades?status=CLOSED").get_data(as_text=True)
    assert "Showing 1–5 of 14 trades" in html
    assert "status=CLOSED" in html and "page=2" in html
    page3 = seeded_client.get("/trades?status=CLOSED&page=3").get_data(as_text=True)
    assert "Showing 11–14 of 14" in page3


def test_out_of_range_page_is_404(app, seeded_client):
    app.config["JOURNAL_PER_PAGE"] = 5
    assert seeded_client.get("/trades?page=99").status_code == 404


# ---------- Trade details ----------


def test_detail_shows_engine_values(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "393.00 USD" in html  # net P&L
    assert "400.00 USD" in html  # gross
    assert "200.00 USD" in html  # initial risk
    assert "+1.97R" in html  # realised R, display-rounded
    assert "Edit" in html and "Delete" in html


def test_detail_for_open_trade_has_no_invented_result(seeded_client):
    html = seeded_client.get("/trades/14").get_data(as_text=True)
    assert ">Open</span>" in html
    assert "Not closed yet" in html
    assert "no realised result yet" in html
    assert "(after fees)" not in html  # the result card is absent
    assert "Planned R:R" in html


def test_detail_for_legacy_trade_says_exit_time_unknown(app, seeded_client):
    with app.app_context():
        from tradeledger.db import get_db

        db = get_db()
        db.execute("UPDATE trades SET closed_at = NULL WHERE id = 1")
        db.commit()
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "Not recorded" in html
    assert "exit time of this trade was never recorded" in html
    assert "393.00 USD" in html  # the result is still computed


# ---------- Instruments, analytics, about ----------


def test_instruments_page_explains_multiplier_and_warns(seeded_client):
    html = seeded_client.get("/instruments").get_data(as_text=True)
    assert "Contract multiplier" in html
    assert "broker" in html
    assert "EURUSD" in html
    assert "/edit" not in html  # instruments are not editable


def test_analytics_is_the_full_dashboard(seeded_client):
    # Phase 6 replaced the Phase 5 preview with the full dashboard.
    html = seeded_client.get("/analytics").get_data(as_text=True)
    assert "Preview" not in html
    for name in ("curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions"):
        assert f'data-chart="{name}"' in html
    assert "Results by instrument" in html and "Results by strategy" in html


def test_sidebar_no_longer_labels_analytics_as_preview(client):
    # Phase 6: the "Preview" tag was removed when the dashboard was finished.
    html = client.get("/").get_data(as_text=True)
    analytics = html.index(">Analytics<")
    assert "Preview" not in html[analytics : analytics + 200]


def test_about_shows_roadmap_and_boundaries(client):
    html = client.get("/about").get_data(as_text=True)
    assert "Build progress" in html
    assert "Phase 6" in html and "Phase 7" in html
    assert "127.0.0.1" in html  # Phase 7: the Host check is explained
