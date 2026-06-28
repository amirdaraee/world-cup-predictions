"""Build the WC2026 Form Book static site from the two data JSONs.

Reads:  fifa_world_cup_2026.json, fifa_world_cup_2026_group_matches.json
Writes: wc26_site/  (index.html, matches.html, teams/*.html, matches/*.html, style.css)

Re-run after refreshing data; output is fully regenerated.
"""
import json
import re
import shutil
import sys
from html import escape
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "docs"   # served by GitHub Pages (main branch /docs)
DOMAIN = "wcformbook.com"   # custom domain; build emits docs/CNAME for Pages
SITE_NAME = "WC26 Form Book"
DEFAULT_DESC = ("World Cup 2026 probabilities from an open statistical model: "
                "match fair prices vs the betting market, tournament odds, and "
                "a locked bracket graded in public.")
BUILD_V = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")  # css cache-buster

teams_data = json.load(open(DATA / "fifa_world_cup_2026.json"))
matches_data = json.load(open(DATA / "fifa_world_cup_2026_group_matches.json"))
try:
    _sim = json.load(open(DATA / "wc26_simulations.json"))
    SIMS, SIM_LOGLOSS = _sim["simulations"], _sim.get("backtest_logloss")
    SIM_AT = _sim.get("generated")
except FileNotFoundError:
    SIMS, SIM_LOGLOSS, SIM_AT = {}, None, None
try:
    _mkt = json.load(open(DATA / "wc26_market_prices.json"))
    PRICES, PRICES_AT = _mkt["prices"], _mkt["fetched_at"]
    FUT_PRICES = _mkt.get("futures", {})
except FileNotFoundError:
    PRICES, PRICES_AT, FUT_PRICES = {}, None, {}
try:
    CORNERS = json.load(open(DATA / "wc26_corners.json"))["matches"]
except FileNotFoundError:
    CORNERS = {}
try:
    LLM = json.load(open(DATA / "wc26_llm_analysis.json"))
except FileNotFoundError:
    LLM = {"teams": {}, "players": {}, "matches": {}}
try:
    _elo = json.load(open(DATA / "wc26_elo.json"))
    ELO, ELO_BT = _elo["matches"], _elo.get("backtest", {})
    ELO_RATINGS = _elo.get("top_ratings", {})
except FileNotFoundError:
    ELO, ELO_BT, ELO_RATINGS = {}, {}, {}
try:
    TOTALS_LOCKED = json.load(open(DATA / "wc26_totals_locked.json"))
except FileNotFoundError:
    TOTALS_LOCKED = {"matches": {}}
try:
    TOURNEY = json.load(open(DATA / "wc26_tournament.json"))["teams"]
except FileNotFoundError:
    TOURNEY = {}
try:
    PRED = json.load(open(DATA / "wc26_predictions.json"))
except FileNotFoundError:
    PRED = None
try:
    AWARDS = json.load(open(DATA / "wc26_awards.json"))
except FileNotFoundError:
    AWARDS = None
try:
    PLAYERS = json.load(open(DATA / "wc26_players.json"))["squads"]
except FileNotFoundError:
    PLAYERS = {}
try:
    _prof = json.load(open(DATA / "wc26_player_profiles.json"))
    PROFILES, PROFILES_AT = _prof["profiles"], _prof["fetched_at"]
except FileNotFoundError:
    PROFILES, PROFILES_AT = {}, None
try:
    SCORERS = json.load(open(DATA / "wc26_scorers.json"))["scorers"]
except FileNotFoundError:
    SCORERS = {}
try:
    ESPN_IDS = json.load(open(DATA / "wc26_espn_ids.json"))["ids"]
except FileNotFoundError:
    ESPN_IDS = {}
try:
    TEAM_IDS = {n: d["team_id"]
                for n, d in json.load(open(DATA / "wc26_matches.json")).items()}
except FileNotFoundError:
    TEAM_IDS = {}
from wc26_simulate import params as _params, score_grid as _score_grid
RHO = _params()["rho"]
BOOST = _params()["min2_boost"]
import unicodedata


def _nrm(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


_PROF_BY_LAST = { _nrm(k).split()[-1]: k for k in {} }


def profile_for(name):
    if name in PROFILES:
        return name
    last = _nrm(name).split()[-1]
    hits = [k for k in PROFILES if _nrm(k).split()[-1] == last]
    return hits[0] if len(hits) == 1 else None


def player_link(name, depth=0):
    key = profile_for(name)
    if key:
        return (f'<a href="{"../" * depth}players/{slug(key)}.html">'
                f'{escape(name)}</a>')
    return escape(name)

TEAMS = {t["country"]: t for t in teams_data["teams"]}
MATCHES = matches_data["matches"]
try:
    KOS = [m for m in json.load(open(DATA / "wc26_knockout_matches.json"))["matches"]
           if m["home"] in TEAMS and m["away"] in TEAMS]
except FileNotFoundError:
    KOS = []
ROUND_SHORT = {"Round of 32": "R32", "Round of 16": "R16",
               "Quarter-finals": "QF", "Semi-finals": "SF",
               "3rd Place Final": "3rd", "Final": "Final"}

team_group = {}
for m in MATCHES:
    team_group[m["home"]] = m["group"]
    team_group[m["away"]] = m["group"]


ISO2 = {
    "United States": "US", "Canada": "CA", "Mexico": "MX", "Panama": "PA",
    "Curaçao": "CW", "Haiti": "HT", "Argentina": "AR", "Brazil": "BR",
    "Colombia": "CO", "Uruguay": "UY", "Ecuador": "EC", "Paraguay": "PY",
    "Germany": "DE", "France": "FR", "Spain": "ES", "Portugal": "PT",
    "Netherlands": "NL", "Belgium": "BE", "Austria": "AT", "Switzerland": "CH",
    "Turkey": "TR", "Norway": "NO", "Sweden": "SE", "Czech Republic": "CZ",
    "Bosnia and Herzegovina": "BA", "Croatia": "HR", "Morocco": "MA",
    "Algeria": "DZ", "Ghana": "GH", "Cape Verde": "CV", "Senegal": "SN",
    "Egypt": "EG", "Ivory Coast": "CI", "South Africa": "ZA", "DR Congo": "CD",
    "Tunisia": "TN", "Japan": "JP", "South Korea": "KR", "Iran": "IR",
    "Australia": "AU", "Saudi Arabia": "SA", "Uzbekistan": "UZ", "Jordan": "JO",
    "Qatar": "QA", "Iraq": "IQ", "New Zealand": "NZ",
}


def flag(name):
    if name == "England":
        return "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
    if name == "Scotland":
        return "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"
    code = ISO2.get(name)
    if not code:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code)


def slug(name):
    import unicodedata
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def match_slug(m):
    base = f"{slug(m['home'])}-vs-{slug(m['away'])}"
    if m.get("round"):
        base += "-" + slug(ROUND_SHORT.get(m["round"], m["round"]))
    return base


def fmt_date(iso):
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%-d %b %Y"), dt.strftime("%H:%M")


def stats(team):
    ms = team["last_10_matches"]
    w = sum(1 for m in ms if m["result"] == "W")
    d = sum(1 for m in ms if m["result"] == "D")
    l = sum(1 for m in ms if m["result"] == "L")
    gf = ga = o25 = btts = cs = 0
    for m in ms:
        f, a = (int(x) for x in m["score"].split("-"))
        gf += f
        ga += a
        if f + a > 2:
            o25 += 1
        if f > 0 and a > 0:
            btts += 1
        if a == 0:
            cs += 1
    n = len(ms) or 1
    return {
        "record": f"{w}–{d}–{l}",
        "gf_pg": gf / n, "ga_pg": ga / n,
        "cs": cs, "o25": o25, "btts": btts, "n": len(ms),
    }


def form_chips(team, count=5):
    chips = "".join(
        f'<i class="f {m["result"]}">{m["result"]}</i>'
        for m in team["last_10_matches"][:count]
    )
    tip = (f"last {count} internationals, most recent first - "
           "W win, D draw (incl. pen shoot-outs), L loss")
    return f'<span class="form" title="{tip}">{chips}</span>'


def team_link(name, depth=0):
    pre = "../" * depth
    t = TEAMS[name]
    return f'<a class="tlink" href="{pre}teams/{slug(name)}.html">{escape(name)}</a>'


def page(title, body, depth=0, crumb="", lang="en", rtl=False, alt_lang=None,
         desc=None, canon=None):
    pre = "../" * depth
    full_title = f"{title} · {SITE_NAME}"
    desc = escape(desc or DEFAULT_DESC)
    canon_url = f"https://{DOMAIN}/{canon}" if canon is not None else None
    seo = (f'<meta name="description" content="{desc}">\n'
           '<meta name="robots" content="index, follow, max-image-preview:large">\n'
           f'<meta property="og:title" content="{escape(full_title)}">\n'
           f'<meta property="og:description" content="{desc}">\n'
           f'<meta property="og:site_name" content="{SITE_NAME}">\n'
           f'<meta property="og:locale" content="{"fa_IR" if rtl else "en_US"}">\n'
           '<meta property="og:type" content="website">\n'
           f'<meta property="og:image" content="https://{DOMAIN}/img/og.png">\n'
           + (f'<meta property="og:url" content="{canon_url}">\n' if canon_url else "")
           + '<meta name="twitter:card" content="summary_large_image">\n'
           f'<meta name="twitter:title" content="{escape(full_title)}">\n'
           f'<meta name="twitter:description" content="{desc}">\n'
           f'<meta name="twitter:image" content="https://{DOMAIN}/img/og.png">\n'
           + (f'<link rel="canonical" href="{canon_url}">\n' if canon_url else ""))
    # favicons (SVG covers Google Search + all modern browsers; manifest for PWA)
    icons = (f'<link rel="icon" type="image/svg+xml" href="{pre}favicon.svg">\n'
             f'<link rel="mask-icon" href="{pre}favicon.svg" color="#211d16">\n'
             f'<link rel="manifest" href="{pre}site.webmanifest">\n'
             '<meta name="theme-color" content="#211d16">')
    jsonld = (
        '<script type="application/ld+json">'
        + json.dumps({
            "@context": "https://schema.org", "@type": "WebSite",
            "name": SITE_NAME, "url": f"https://{DOMAIN}/",
            "description": DEFAULT_DESC,
            "inLanguage": "fa" if rtl else "en"}, ensure_ascii=False)
        + '</script>')
    # our title= attrs become CSS tooltips (instant, styled, tap-friendly)
    body = body.replace(' title="', ' data-tip="')
    fa_font = ('<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;800&display=swap" rel="stylesheet">'
               if rtl else "")
    switcher = (f'<div class="langsw"><a href="{alt_lang[0]}">{alt_lang[1]}</a></div>'
                if alt_lang else "")
    return f"""<!DOCTYPE html>
<html lang="{lang}" dir="{'rtl' if rtl else 'ltr'}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(full_title)}</title>
{seo}{icons}
{jsonld}
<link rel="preconnect" href="https://fonts.googleapis.com">
<script>
// Google Consent Mode v2 — analytics stays OFF until the visitor opts in.
// gtag.js is not even loaded until consent is granted (this run or a prior
// visit), so no GA cookie or request fires beforehand.
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('consent', 'default', {{
  ad_storage: 'denied', ad_user_data: 'denied',
  ad_personalization: 'denied', analytics_storage: 'denied'
}});
function wcfbLoadGA() {{
  gtag('consent', 'update', {{ analytics_storage: 'granted' }});
  if (window.wcfbGALoaded) return;
  window.wcfbGALoaded = true;
  var s = document.createElement('script');
  s.async = true;
  s.src = 'https://www.googletagmanager.com/gtag/js?id=G-EG56KHB1SX';
  document.head.appendChild(s);
  gtag('js', new Date());
  gtag('config', 'G-EG56KHB1SX');
}}
try {{
  if (localStorage.getItem('wcfb_consent') === 'granted') wcfbLoadGA();
}} catch (e) {{}}
</script>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,900&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
{fa_font}<link rel="stylesheet" href="{pre}style.css?v={BUILD_V}">
<script>
(function() {{
  var t = localStorage.getItem("theme");
  if (t) document.documentElement.dataset.theme = t;
}})();
function toggleTheme() {{
  var el = document.documentElement;
  var dark = el.dataset.theme === "dark" ||
    (!el.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  el.dataset.theme = dark ? "light" : "dark";
  localStorage.setItem("theme", el.dataset.theme);
}}
</script>
</head>
<body>
<header class="masthead">
  <a class="skip" href="#main">skip to content</a>
  <button class="themebtn" onclick="toggleTheme()" title="light / dark" aria-label="toggle light or dark theme">◐</button>
  {switcher}
  <div class="kicker">The Form Book - research edition</div>
  <a class="wordmark" href="{pre}index.html">World&nbsp;Cup&nbsp;26</a>
  <nav><a href="{pre}index.html">Knockouts</a><span>·</span><a href="{pre}groups.html">Groups</a><span>·</span><a href="{pre}matches.html">Matches</a><span>·</span><a href="{pre}futures.html">Futures</a><span>·</span><a href="{pre}awards.html">Awards</a><span>·</span><a href="{pre}bracket.html">Bracket</a><span>·</span><a href="{pre}report.html">Report</a><span>·</span><a href="{pre}follow-the-model.html">Betting ROI</a><span>·</span><a href="{pre}glossary.html">Glossary</a><span>·</span><a href="{pre}method.html">Method</a></nav>
</header>
{f'<div class="crumb">{crumb}</div>' if crumb else ''}
<main id="main">
{body}
</main>
<footer>
  <p>Form chips read most-recent first. Stats computed over each team's last 10 completed internationals.</p>
  <p>Model run: {SIM_AT or "-"} · Polymarket snapshot: {PRICES_AT or "-"} · every run archived in runs/</p>
  <p>Data: API-Football · FIFA rankings 2026 · <a href="{pre}method.html">how this works</a> ·
  <a href="{pre}archive.html">previous versions</a> ·
  <a href="https://github.com/amirdaraee/world-cup-predictions">code on GitHub</a> ·
  personal research, verify before staking. ·
  <a href="#" onclick="wcfbCookies();return false;">cookie choices</a></p>
</footer>
<div id="cookie-consent" role="dialog" aria-label="Cookie consent" hidden>
  <p>We use cookies to analyse traffic and understand how the site is
  used. You can accept or decline — your choice is remembered.</p>
  <div class="cc-btns">
    <button id="cc-reject" class="cc-no">Decline</button>
    <button id="cc-accept" class="cc-yes">Accept</button>
  </div>
</div>
<script>
(function() {{
  var box = document.getElementById('cookie-consent');
  if (!box) return;
  function stored() {{ try {{ return localStorage.getItem('wcfb_consent'); }}
                       catch (e) {{ return null; }} }}
  function decide(v) {{
    try {{ localStorage.setItem('wcfb_consent', v); }} catch (e) {{}}
    box.hidden = true;
    if (v === 'granted' && window.wcfbLoadGA) wcfbLoadGA();
  }}
  if (!stored()) box.hidden = false;   // first visit: ask
  document.getElementById('cc-accept').onclick = function() {{ decide('granted'); }};
  document.getElementById('cc-reject').onclick = function() {{ decide('denied'); }};
  // let anyone reopen the choice from the footer (easy to withdraw consent)
  window.wcfbCookies = function() {{ box.hidden = false; }};
}})();
</script>
</body>
</html>"""


# ---------- index: group tables ----------
def build_index():
    groups = {}
    for name, g in team_group.items():
        groups.setdefault(g, []).append(name)

    cards = []
    for g in sorted(groups):
        standings = group_standings(g)
        if standings:
            rows = []
            for pos, (name, r) in enumerate(standings, 1):
                t = TEAMS[name]
                host = ' <sup class="host">host</sup>' if t.get("host") else ""
                cls = "q1" if pos <= 2 else "q3" if pos == 3 else ""
                rows.append(
                    f'<tr class="{cls}"><td class="flagcell">{flag(name)}</td>'
                    f'<td>{team_link(name)}{host}</td>'
                    f'<td class="num">{r["p"]}</td>'
                    f'<td class="num">{r["gf"] - r["ga"]:+d}</td>'
                    f'<td class="num"><b>{r["pts"]}</b></td>'
                    f"<td>{form_chips(t)}</td></tr>")
            cards.append(f"""<section class="group" id="group-{g.lower()}">
<h2><span>Group</span> {g}</h2>
<table>
<thead><tr><th scope="col" aria-label="flag"></th><th scope="col">Team</th><th scope="col" class="num" title="matches played">P</th><th scope="col" class="num" title="goal difference">GD</th><th scope="col" class="num" title="points - top two advance, the eight best thirds join them">Pts</th><th scope="col" title="last 5 internationals, most recent first">Form</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</section>""")
            continue
        rows = []
        for name in sorted(groups[g], key=lambda n: TEAMS[n]["fifa_ranking"]):
            t = TEAMS[name]
            host = ' <sup class="host">host</sup>' if t.get("host") else ""
            rows.append(
                f'<tr><td class="flagcell">{flag(name)}</td>'
                f"<td>{team_link(name)}{host}</td>"
                f'<td class="num">{t["fifa_ranking"]}</td>'
                f"<td>{form_chips(t)}</td></tr>"
            )
        cards.append(f"""<section class="group" id="group-{g.lower()}">
<h2><span>Group</span> {g}</h2>
<table>
<thead><tr><th scope="col" aria-label="flag"></th><th scope="col">Team</th><th scope="col" class="num" title="official FIFA ranking, 1 Apr 2026 - shown for orientation; the model does not use it">FIFA</th><th scope="col" title="last 5 internationals, most recent first - W win, D draw (pens count as draws), L loss">Form</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</section>""")

    # --- today / next matchday strip ---
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    allm = MATCHES + KOS
    days = sorted({m["date_utc"][:10] for m in allm if m["date_utc"][:10] >= today})
    strip = ""
    if days:
        day = days[0]
        label = "Today" if day == today else f"Next: {fmt_date(day + 'T00:00:00')[0]}"
        rows = []
        for m in sorted((x for x in allm if x["date_utc"][:10] == day),
                        key=lambda x: x["date_utc"]):
            sim = SIMS.get(str(m["match_id"]))
            _, time_ = fmt_date(m["date_utc"])
            if sim:
                ml = sim["moneyline"]
                pick = max(ml, key=ml.get)
                pick_label = {"home": m["home"], "away": m["away"], "draw": "Draw"}[pick]
                call = f'{escape(pick_label)} {ml[pick]*100:.0f}%'
            else:
                call = "-"
            if m.get("score"):
                hit = sim and max(sim["moneyline"], key=sim["moneyline"].get) \
                    == actual_result(m["score"])
                verdict = (f'<b class="score">{m["score"]}</b> '
                           + ('<i class="f W">✓</i>' if hit else '<i class="f L">✗</i>'))
            else:
                verdict = f'{time_} UTC'
            rows.append(
                f'<tr><td class="fixture"><a href="matches/{match_slug(m)}.html">'
                f'{escape(m["home"])} <em>v</em> {escape(m["away"])}</a></td>'
                f'<td><b class="gchip">{m["group"] if not m.get("round") else ROUND_SHORT.get(m["round"], "KO")}</b></td>'
                f'<td>{call}</td><td class="num">{verdict}</td></tr>')
        strip = f"""<section class="todaybox">
<h2>{label}</h2>
<table><thead><tr><th>Fixture</th><th>Grp</th>
<th title="the model's most likely outcome and its probability">Model call</th>
<th class="num" title="kick-off, or final score with the model's verdict">Status</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</section>"""

    body = f"""<h1>The twelve groups</h1>
{strip}
<p class="standfirst">48 teams, seeded here by FIFA ranking. Click any team for its full dossier.</p>
<p class="fineprint">Form = results of the last five internationals, <b>most recent first</b>
(W win, D draw - penalty shoot-outs count as draws, L loss); the team page shows all ten with
opponents and competitions. Friendlies are included here but weigh less in the model.
The FIFA column is the official 1 April 2026 ranking, shown for orientation only -
the simulation rates teams purely from match results and disagrees in places (France is FIFA #1
but mid-pack by title odds; the model prefers recent goals to reputation). The <sup class="host">host</sup>
marker matters beyond ceremony: hosts get a fitted home-crowd boost (~+30% scoring) whenever they
play in their own country, which is most of their matches.</p>
<div class="groups">{''.join(cards)}</div>"""
    (OUT / "groups.html").write_text(page(
        "Groups", body, canon="groups.html",
        desc="Live World Cup 2026 group standings and match-by-match win "
             "probabilities from an open Dixon-Coles model, blended with "
             "Polymarket betting-market prices."))


# ---------- matches list ----------
def venue_str(m):
    """Escaped 'Venue, City', skipping parts that are missing — knockout
    fixtures can carry a null venue/city until one is assigned, and
    escape(None) raises (this crashed the nightly build, 2026-06-25)."""
    return ", ".join(escape(x) for x in (m.get("venue"), m.get("city")) if x)


def build_matches_list():
    by_day = {}
    for m in MATCHES + KOS:
        by_day.setdefault(m["date_utc"][:10], []).append(m)

    sections = []
    for day in sorted(by_day):
        date_label, _ = fmt_date(by_day[day][0]["date_utc"])
        rows = []
        for m in sorted(by_day[day], key=lambda x: x["date_utc"]):
            _, time_ = fmt_date(m["date_utc"])
            home_t, away_t, fav = escape(m["home"]), escape(m["away"]), None
            if m.get("score"):
                res = f'<b class="score">{m["score"]}</b>'
            else:
                sim = SIMS.get(str(m["match_id"]))
                ml = sim.get("moneyline") if sim else None
                if ml:
                    fav = "home" if ml["home"] >= ml["away"] else "away"
                    res = (f'<span class="dim" data-tip="model win probability">'
                           f'{ml[fav] * 100:.0f}%</span>')
                    if fav == "home":
                        home_t = f"<b>{home_t}</b>"
                    else:
                        away_t = f"<b>{away_t}</b>"
                else:
                    res = '<span class="dim">-</span>'
            rows.append(f"""<tr>
<td class="num">{time_}</td>
<td>{('<b class="gchip">' + ROUND_SHORT.get(m["round"], "KO") + '</b>') if m.get("round") else ('<a class="gchip" href="groups.html#group-' + m['group'].lower() + '">' + m['group'] + '</a>')}</td>
<td class="fixture"><a href="matches/{match_slug(m)}.html">{home_t} <em>v</em> {away_t}</a></td>
<td class="num">{res}</td>
<td class="venue">{venue_str(m)}</td>
</tr>""")
        sections.append(f"""<section class="matchday">
<h2>{date_label}</h2>
<table>
<thead><tr><th class="num" title="kick-off time, UTC - 00:00-02:00 games are US/Mexico evenings">UTC</th><th title="group A-L">Grp</th><th>Fixture</th><th class="num">Result</th><th>Venue</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</section>""")

    body = f"""<h1>Group stage - all 72 fixtures</h1>
<p class="standfirst">Every match links to a head-to-head card comparing both teams' last ten.</p>
<p class="fineprint">Kick-offs are UTC - the 00:00–02:00 oddities are evening games in US/Mexico time
zones, landing a calendar day later than the local date Polymarket uses in its market slugs.
On matchday 3 both games in a group kick off simultaneously (FIFA's anti-collusion rule since 1982),
so their in-play prices move together.</p>
{''.join(sections)}"""
    (OUT / "matches.html").write_text(page(
        "Matches", body, canon="matches.html",
        desc="Every World Cup 2026 fixture with model win/draw/loss "
             "probabilities, expected goals and fair prices versus the live "
             "betting market."))


# ---------- team pages ----------
def last10_table(team):
    rows = []
    for m in team["last_10_matches"]:
        note = f' <small>({escape(m["note"])})</small>' if "note" in m else ""
        rows.append(f"""<tr>
<td class="num">{m['date']}</td>
<td>{escape(m['opponent'])}</td>
<td class="num">{m['home_away']}</td>
<td class="num score">{m['score']}</td>
<td><i class="f {m['result']}">{m['result']}</i>{note}</td>
<td class="comp">{escape(m['competition'])}</td>
</tr>""")
    return f"""<table class="last10">
<thead><tr><th class="num">Date</th><th>Opponent</th><th class="num" title="home or away, from this team's perspective">H/A</th><th class="num" title="goals for-against, from this team's perspective">Score</th><th title="result: W win, D draw (pens count as draws), L loss">Res</th><th>Competition</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>"""


def stat_strip(s):
    return f"""<dl class="stats">
<div><dt title="wins-draws-losses over the last 10 internationals">Record</dt><dd>{s['record']}</dd></div>
<div><dt title="goals scored per game, last 10">Goals / game</dt><dd>{s['gf_pg']:.1f}</dd></div>
<div><dt title="goals conceded per game, last 10">Conceded / game</dt><dd>{s['ga_pg']:.1f}</dd></div>
<div><dt title="games without conceding, last 10">Clean sheets</dt><dd>{s['cs']}/{s['n']}</dd></div>
<div><dt title="games with 3+ total goals - the standard totals betting line">Over 2.5</dt><dd>{s['o25']}/{s['n']}</dd></div>
<div><dt title="both teams scored - a standard prediction-market category">BTTS</dt><dd>{s['btts']}/{s['n']}</dd></div>
</dl>"""


def futures_row(name):
    f = TOURNEY.get(name)
    if not f:
        return ""
    cells = "".join(
        f'<div><dt>{lbl}</dt><dd>{f[k]*100:.1f}%</dd></div>'
        for lbl, k in (("Win group", "win_group"), ("Reach R32", "r32"),
                       ("QF", "qf"), ("SF", "sf"), ("Final", "final"),
                       ("Champion", "champion")))
    return f'<dl class="stats odds" title="from 100,000 simulated tournaments - see Futures">{cells}</dl>'


def key_players(name):
    squad = PLAYERS.get(name)
    if not squad:
        return ""
    total = sum(p["goals"] for p in squad) or 1
    scorers = sorted(squad, key=lambda p: -p["goals"])[:4]
    gks = [p for p in squad if p["position"] == "Goalkeeper"]
    gk = max(gks, key=lambda p: p["apps"]) if gks else None
    rows = "".join(
        f'<tr><td>{player_link(p["name"], 1)}</td><td>{p["position"]}</td>'
        f'<td class="num">{p["age"]}</td><td class="num">{p["goals"]}</td>'
        f'<td class="num">{p["goals"]/total*100:.0f}%</td>'
        f'<td class="num">{p["apps"]}</td></tr>'
        for p in scorers if p["goals"] > 0)
    if gk:
        rows += (f'<tr><td>{escape(gk["name"])}</td><td>Goalkeeper</td>'
                 f'<td class="num">{gk["age"]}</td><td class="num">-</td>'
                 f'<td class="num">-</td><td class="num">{gk["apps"]}</td></tr>')
    if not rows:
        return ""
    return f"""<h2>Key players</h2>
<table class="ko">
<thead><tr><th>Player</th><th>Position</th><th class="num">Age</th>
<th class="num" title="international goals 2024-26">Goals 24–26</th>
<th class="num" title="share of the team's international goals - the Golden Boot model's input">Share</th>
<th class="num" title="international appearances 2024-26">Apps</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def build_team_pages():
    for name, t in TEAMS.items():
        g = team_group.get(name, "?")
        fixtures = [m for m in MATCHES if name in (m["home"], m["away"])]
        fx_rows = []
        for m in sorted(fixtures, key=lambda x: x["date_utc"]):
            date_label, time_ = fmt_date(m["date_utc"])
            opp = m["away"] if m["home"] == name else m["home"]
            ha = "H" if m["home"] == name else "A"
            fx_rows.append(
                f'<tr><td class="num">{date_label} {time_}</td><td class="num">{ha}</td>'
                f'<td><a href="../matches/{match_slug(m)}.html">{escape(opp)}</a></td>'
                f'<td class="venue">{venue_str(m)}</td></tr>'
            )
        host = '<span class="chip">Host nation</span>' if t.get("host") else ""
        crest = (f'<img class="headshot crest" '
                 f'src="https://media.api-sports.io/football/teams/{TEAM_IDS[name]}.png" '
                 f'alt="{escape(name)} crest" loading="lazy">'
                 if name in TEAM_IDS else "")
        body = f"""<div class="teamhead">
{crest}
<h1>{escape(name)}</h1>
<p class="meta">
<a class="chip" href="../groups.html#group-{g.lower()}">Group {g}</a>
<span class="chip">FIFA #{t['fifa_ranking']}</span>
<span class="chip">{t['confederation']}</span>
{host}
</p>
{form_chips(t, 10)}
</div>
{futures_row(name)}
{key_players(name)}
{stat_strip(stats(t))}
<p class="fineprint">Over 2.5 (three-plus total goals) and BTTS (both teams scored) are counted
here because they're the totals categories prediction markets actually trade. Caveat on the
last ten: a few teams padded theirs with friendlies against club or B-team opposition - those
rows say little, and the model's training data excludes non-international opponents entirely.
Matches decided on penalties are recorded as draws (with a note): that's how FIFA counts them
and how the model scores them.</p>
<h2>Last ten internationals</h2>
{last10_table(t)}
<h2>Group {g} fixtures</h2>
<table>
<thead><tr><th class="num">Kick-off (UTC)</th><th class="num">H/A</th><th>Opponent</th><th>Venue</th></tr></thead>
<tbody>{''.join(fx_rows)}</tbody>
</table>
{llm_section(LLM["teams"].get(name), "The analyst's notes")}"""
        crumb = f'<a href="../groups.html">Groups</a> / Group {g} / {escape(name)}'
        (OUT / "teams" / f"{slug(name)}.html").write_text(
            page(name, body, depth=1, crumb=crumb))


# ---------- match pages ----------
def compare_rows(h, a):
    sh, sa = stats(h), stats(a)
    rows = [
        ("FIFA ranking", f"#{h['fifa_ranking']}", f"#{a['fifa_ranking']}"),
        ("Form (last 5)", form_chips(h), form_chips(a)),
        ("Record (10)", sh["record"], sa["record"]),
        ("Goals / game", f"{sh['gf_pg']:.1f}", f"{sa['gf_pg']:.1f}"),
        ("Conceded / game", f"{sh['ga_pg']:.1f}", f"{sa['ga_pg']:.1f}"),
        ("Clean sheets", f"{sh['cs']}/10", f"{sa['cs']}/10"),
        ("Over 2.5 goals", f"{sh['o25']}/10", f"{sa['o25']}/10"),
        ("Both teams scored", f"{sh['btts']}/10", f"{sa['btts']}/10"),
    ]
    tips = {
        "FIFA ranking": "official FIFA ranking, 1 Apr 2026 - not used by the model",
        "Form (last 5)": "most recent first - W win, D draw, L loss",
        "Record (10)": "wins-draws-losses over the last 10 internationals",
        "Clean sheets": "games without conceding, of the last 10",
        "Over 2.5 goals": "games with 3+ total goals, of the last 10",
        "Both teams scored": "games where neither side kept a clean sheet, of the last 10",
    }
    return "".join(
        f'<tr><td class="cl">{l}</td><th title="{tips.get(k, "")}">{k}</th>'
        f'<td class="cr">{r}</td></tr>'
        for k, l, r in rows
    )


def pct(p):
    return f"{p * 100:.0f}¢"


def actual_result(score):
    h, a = (int(x) for x in score.split("-"))
    return "home" if h > a else "away" if a > h else "draw"


def group_standings(g):
    """Real standings for a group once any of its matches have finished.
    Returns ordered rows or None if nothing played yet."""
    rows = {t: {"p": 0, "gf": 0, "ga": 0, "pts": 0}
            for m in MATCHES if m["group"] == g
            for t in (m["home"], m["away"])}
    played = 0
    for m in MATCHES:
        if m["group"] != g or not m.get("score"):
            continue
        played += 1
        hg, ag = (int(x) for x in m["score"].split("-"))
        for t, f, a in ((m["home"], hg, ag), (m["away"], ag, hg)):
            r = rows[t]
            r["p"] += 1
            r["gf"] += f
            r["ga"] += a
            r["pts"] += 3 if f > a else 1 if f == a else 0
    if not played:
        return None
    return sorted(rows.items(),
                  key=lambda kv: (-kv[1]["pts"], -(kv[1]["gf"] - kv[1]["ga"]),
                                  -kv[1]["gf"], kv[0]))


def match_grid_svg(sim):
    """Compact scoreline heatmap for one match card."""
    g = _score_grid(sim["xg"]["home"], sim["xg"]["away"], RHO, BOOST)
    K = 5
    cell = 52
    L, T = 64, 56
    W = L + (K + 1) * cell + 16
    H = T + (K + 1) * cell + 40
    pmax = max(max(row[:K + 1]) for row in g[:K + 1])
    s = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">'
         f'<rect width="{W}" height="{H}" fill="var(--paper, #f6f1e6)"/>')
    font = 'font-family="ui-monospace,Menlo,monospace"'
    s += (f'<text x="{L-10}" y="{T-30}" font-size="11" fill="var(--ink-soft, #6b6353)" '
          f'text-anchor="end" {font}>{escape(sim["home"][:14])} ↓</text>')
    s += (f'<text x="{L+(K+1)*cell}" y="{T-30}" font-size="11" fill="var(--ink-soft, #6b6353)" '
          f'text-anchor="end" {font}>{escape(sim["away"][:14])} →</text>')
    for k in range(K + 1):
        s += (f'<text x="{L + k*cell + cell/2 - 1}" y="{T-8}" font-size="11" '
              f'fill="var(--ink-soft, #6b6353)" text-anchor="middle" {font}>{k}</text>')
        s += (f'<text x="{L-10}" y="{T + k*cell + cell/2 + 4}" font-size="11" '
              f'fill="var(--ink-soft, #6b6353)" text-anchor="end" {font}>{k}</text>')
    for i in range(K + 1):
        for j in range(K + 1):
            p = g[i][j]
            col = ("var(--green, #14633f)" if i > j else "var(--red, #a72a1e)" if j > i else "var(--amber, #9a7b2d)")
            op = max(min(p / pmax, 1.0) * 0.92, 0.04)
            x, y = L + j * cell, T + i * cell
            s += (f'<rect x="{x}" y="{y}" width="{cell-3}" height="{cell-3}" '
                  f'fill="{col}" opacity="{op:.3f}"/>')
            if p >= 0.015:
                fill = "var(--paper, #f6f1e6)" if op > 0.45 else "var(--ink, #211d16)"
                s += (f'<text x="{x + (cell-3)/2}" y="{y + cell/2 + 3}" '
                      f'font-size="11" fill="{fill}" text-anchor="middle" '
                      f'{font}>{p*100:.0f}</text>')
    return s + "</svg>"


def sim_section(m):
    sim = SIMS.get(str(m["match_id"]))
    if not sim:
        return ""
    ml = sim["moneyline"]
    # post-match verdict banner
    verdict = ""
    if m.get("score"):
        res = actual_result(m["score"])
        pick = max(ml, key=ml.get)
        hit = pick == res
        res_label = {"home": m["home"], "away": m["away"], "draw": "a draw"}[res]
        mkt = PRICES.get(str(m["match_id"]), {}).get("moneyline", {})
        mkt_note = (f" - the market had it at {mkt[res]*100:.0f}¢"
                    if mkt.get(res) is not None else "")
        verdict = (f'<div class="verdict {"hit" if hit else "miss"}">'
                   f'Final {m["score"]}: {escape(res_label)} - the model gave that '
                   f'{ml[res]*100:.0f}%{mkt_note} '
                   f'{"✓ called it" if hit else "✗ model leaned " + ({"home": m["home"], "away": m["away"], "draw": "draw"}[pick])}'
                   f'</div>')
    hf = (f'<p class="meta center">Home-field advantage applied to {escape(sim["home_field"])}.</p>'
          if sim.get("home_field") else
          '<p class="meta center">Neutral venue - no home-field advantage.</p>')
    bar = f"""<div class="mlbar">
<span class="seg home" style="flex:{ml['home']:.4f}"><b>{escape(m['home'])}</b> {pct(ml['home'])}</span>
<span class="seg draw" style="flex:{ml['draw']:.4f}"><b>Draw</b> {pct(ml['draw'])}</span>
<span class="seg away" style="flex:{ml['away']:.4f}"><b>{escape(m['away'])}</b> {pct(ml['away'])}</span>
</div>"""
    elo = ELO.get(str(m["match_id"]))
    second = ""
    if elo:
        gap = elo["disagreement"]
        if gap >= 0.15:
            verdict = (f'<b class="elo-disagree">an independent Elo model '
                       f'disagrees with this fixture by {gap*100:.0f} points</b>'
                       f' — treat the favourite, and any bet here, with extra '
                       f'caution')
        else:
            verdict = (f'<span class="dim">an independent Elo model agrees '
                       f'within {gap*100:.0f} points</span>')
        # flag only — no second set of percentages to tempt an average; the
        # Elo numbers themselves live on the method page (it is the weaker
        # model on the same exam, kept for disagreement, not for blending)
        second = (
            f'<p class="meta center secondop">Second opinion: {verdict}. '
            f'<span class="dim">A deliberate cross-check, not a number to '
            f'average in — its blend with the main model failed validation. '
            f'The Elo figures live on '
            f'<a href="../method.html#second-opinion">the method page</a>.'
            f'</span></p>')
    t = sim["totals"]
    rec = PRICES.get(str(m["match_id"]), {})
    mkt = rec.get("moneyline", {})
    mt = rec.get("totals", {})         # Polymarket "-more-markets" book
    mb = rec.get("btts")
    ms = rec.get("spread", {})
    mtt = rec.get("team_totals", {})
    mht = rec.get("halftime", {})
    m2h = rec.get("second_half", {})
    mfs = rec.get("first_to_score", {})
    tt = sim.get("team_totals", {})
    ht = sim.get("halftime", {})
    h2 = sim.get("second_half", {})
    fs = sim.get("first_to_score", {})

    def flip(p):   # market price of the No/Under side of a binary
        return None if p is None else 1 - p
    rows = [
        ("__head", "Result", None),
        (f"{m['home']} to win", ml["home"], mkt.get("home")),
        ("Draw", ml["draw"], mkt.get("draw")),
        (f"{m['away']} to win", ml["away"], mkt.get("away")),
        ("__head", "Goals", None),
        ("Over 1.5 goals", t["over_1.5"], mt.get("over_1.5")),
        ("Under 1.5 goals", 1 - t["over_1.5"], flip(mt.get("over_1.5"))),
        ("Over 2.5 goals", t["over_2.5"], mt.get("over_2.5")),
        ("Under 2.5 goals", 1 - t["over_2.5"], flip(mt.get("over_2.5"))),
        ("Over 3.5 goals", t["over_3.5"], mt.get("over_3.5")),
        ("Under 3.5 goals", 1 - t["over_3.5"], flip(mt.get("over_3.5"))),
        ("Both teams score - Yes", sim["btts"], mb),
        ("Both teams score - No", 1 - sim["btts"], flip(mb)),
        (f"{m['home']} −1.5 (win by 2+)", sim["spread"]["home_-1.5"], ms.get("home_-1.5")),
        (f"{m['away']} −1.5 (win by 2+)", sim["spread"]["away_-1.5"], ms.get("away_-1.5")),
    ]
    if tt:
        rows += [("__head", "Team goals", None)] + [
            (f"{m[side]} over {line} goals",
             tt[side]["over_" + line], mtt.get(f"{side}_over_{line}"))
            for side in ("home", "away")
            for line in ("0.5", "1.5", "2.5")]
    if ht and h2:
        rows += [
            ("__head", "Halves", None),
            (f"{m['home']} leading at halftime", ht["home"], mht.get("home")),
            ("Draw at halftime", ht["draw"], mht.get("draw")),
            (f"{m['away']} leading at halftime", ht["away"], mht.get("away")),
            (f"{m['home']} win the 2nd half", h2["home"], m2h.get("home")),
            ("2nd half drawn", h2["draw"], m2h.get("draw")),
            (f"{m['away']} win the 2nd half", h2["away"], m2h.get("away")),
        ]
    if fs:
        rows += [
            ("__head", "First to score", None),
            (f"{m['home']} score first", fs["home"], mfs.get("home")),
            (f"{m['away']} score first", fs["away"], mfs.get("away")),
            ("Neither (0-0)", fs["neither"], mfs.get("neither")),
        ]
    cn = CORNERS.get(str(m["match_id"]), {})
    mcn = rec.get("corners", {})
    if cn:
        rows += [("__head", "Corners (base-rate model)", None)] + [
            row for line in ("9.5", "10.5") for row in (
                (f"Over {line} corners", cn[f"over_{line}"],
                 mcn.get(f"over_{line}")),
                (f"Under {line} corners", 1 - cn[f"over_{line}"],
                 flip(mcn.get(f"over_{line}"))))]
    market_rows = []
    for k, v, price in rows:
        if k == "__head":
            market_rows.append(
                f'<tr class="subhead"><td colspan="5">{escape(v)}</td></tr>')
            continue
        if price is not None:
            edge = (v - price) * 100
            cls = "pos" if edge >= 3 else "neg" if edge <= -3 else ""
            mkt_cell = f'<td class="num">{price * 100:.1f}¢</td>' \
                       f'<td class="num edge {cls}">{edge:+.1f}¢</td>'
        else:
            mkt_cell = '<td class="num dim">-</td><td class="num dim">-</td>'
        market_rows.append(
            f'<tr><td>{escape(k)}</td><td class="num">{v * 100:.1f}%</td>'
            f'<td class="num fair">{pct(v)}</td>{mkt_cell}</tr>')
    market_note = (f'<p class="meta center">Polymarket prices as of {PRICES_AT}. '
                   'Positive edge = model thinks Yes is underpriced.</p>'
                   if mkt else
                   '<p class="meta center">No Polymarket market listed for this fixture yet.</p>')
    scorelines = " · ".join(
        f"<b>{s['score']}</b> <small>{s['p'] * 100:.0f}%</small>" for s in sim["top_scores"])
    bt = f" · backtest log-loss {SIM_LOGLOSS} vs 1.046 baseline on 1,071 matches" if SIM_LOGLOSS else ""
    grid_inline = match_grid_svg(sim)
    return f"""<section class="sim">
<h2>Simulation - fair prices vs market</h2>
{verdict}
<p class="fineprint">Fair price is the model's probability written in Polymarket cents - a 56¢
fair price means 56%, and a share bought below it profits on average <em>if the model is right</em>.
Edge = fair minus market: green means the market sells the outcome cheaper than the model values it.
xG here is Dixon-Coles expected goals from attack/defence ratings, not the shot-based xG of
broadcast graphics. Spread −1.5 = win by two or more clear goals.</p>
{hf}
<p class="meta center">Model expected goals: {escape(m['home'])} {sim['xg']['home']} - {sim['xg']['away']} {escape(m['away'])}</p>
{bar}
{second}
<table class="markets">
<thead><tr><th>Market</th><th class="num" title="model probability of this outcome">Probability</th><th class="num" title="the model probability written as a share price - buy below this and you profit on average if the model is right">Fair</th><th class="num" title="live Polymarket YES price at last snapshot">Polymarket</th><th class="num" title="fair minus market - positive (green) means the market sells it cheaper than the model values it">Edge</th></tr></thead>
<tbody>{''.join(market_rows)}</tbody>
</table>
{market_note}
<figure class="matchgrid">{grid_inline}</figure>
<p class="scorelines">Most likely scorelines: {scorelines}</p>
<p class="modelnote">Dixon-Coles weighted Poisson · internationals 2018–2026, tuned
hyperparameters{bt}.
A market priced below fair is value <em>if you trust the model</em>; it knows nothing about
injuries, lineups or motivation.</p>
</section>"""


def build_match_pages():
    for m in MATCHES + KOS:
        h, a = TEAMS[m["home"]], TEAMS[m["away"]]
        date_label, time_ = fmt_date(m["date_utc"])
        score = f'<div class="bigscore">{m["score"]}</div>' if m["score"] else ""
        if m.get("penalties"):
            score += f'<p class="meta center">{escape(m["penalties"])} on penalties</p>' 
        pride = (m["home"], m["away"]) == ("Egypt", "Iran")
        pride_html = ("""<div class="pridebar" role="presentation"></div>
<p class="meta center pridenote">Seattle's host committee designated this fixture its
<b>Pride match</b> 🏳️‍🌈 - chosen before the draw, and now contested by both federations.
The rainbow above is the venue's, the probabilities below are ours: the model prices
football only. <a href="https://www.espn.com/soccer/story/_/id/47264840/egypt-iran-complain-world-cup-pride-match-seattle">Background</a>.</p>"""
                      if pride else "")
        wrap_open = '<div class="pridepage">' if pride else ""
        wrap_close = '<div class="pridebar" role="presentation"></div></div>' if pride else ""
        pm_slug = PRICES.get(str(m["match_id"]), {}).get("slug")
        links = []
        if pm_slug:
            links.append(f'<a href="https://polymarket.com/event/{pm_slug}" '
                         f'target="_blank" rel="noopener">Polymarket ↗</a>')
        eid = ESPN_IDS.get(str(m["match_id"]))
        if eid:
            links.append(f'<a href="https://www.espn.com/soccer/match/_/gameId/{eid}" '
                         f'target="_blank" rel="noopener">ESPN ↗</a>')
        ext = '<p class="meta center extlinks">' + " · ".join(links) + "</p>"
        body = f"""{pride_html}{wrap_open}<div class="card">
<p class="meta center">{(escape(m["round"]) if m.get("round") else f"Group {m['group']} · Matchday {m['matchday']}")} · {date_label}, {time_} UTC<br>
{venue_str(m)}</p>
{ext}
<div class="versus">
<h1>{team_link(m['home'], 1)}</h1>
<span class="v">v</span>
<h1>{team_link(m['away'], 1)}</h1>
</div>
{score}
<table class="compare">{compare_rows(h, a)}</table>
</div>
{sim_section(m)}
<div class="twocol">
<section><h2>{escape(m['home'])} - last ten</h2>{last10_table(h)}</section>
<section><h2>{escape(m['away'])} - last ten</h2>{last10_table(a)}</section>
</div>
{llm_section((LLM["matches"].get(str(m["match_id"])) or {}).get("review"), "The analyst's review")}
{llm_section((LLM["matches"].get(str(m["match_id"])) or {}).get("preview"), "The analyst's preview")}{wrap_close}"""
        stage = m["round"] if m.get("round") else f'Matchday {m["matchday"]}'
        crumb = (f'<a href="../matches.html">Matches</a> / {escape(stage)} / '
                 f'{escape(m["home"])} v {escape(m["away"])}')
        (OUT / "matches" / f"{match_slug(m)}.html").write_text(
            page(f"{m['home']} v {m['away']}", body, depth=1, crumb=crumb))


# ---------- futures page ----------
def build_futures():
    if not TOURNEY:
        return
    champ_mkt = FUT_PRICES.get("champion", {})
    rows = []
    for name, p in TOURNEY.items():
        if name not in TEAMS:
            continue
        cells = "".join(
            f'<td class="num bar{" hot" if p[k] >= 0.5 else ""}" '
            f'style="--p:{p[k] * 100:.1f}%">{p[k] * 100:.1f}%</td>'
            for k in ("win_group", "r32", "qf", "sf", "final", "champion"))
        cm = champ_mkt.get(name)
        if cm is not None:
            edge = (p["champion"] - cm) * 100
            cls = "pos" if edge >= 2 else "neg" if edge <= -2 else ""
            cells += (f'<td class="num">{cm * 100:.1f}¢</td>'
                      f'<td class="num edge {cls}">{edge:+.1f}¢</td>')
        else:
            cells += '<td class="num dim">-</td><td class="num dim">-</td>'
        g = team_group.get(name, "?")
        rows.append(f'<tr><td>{team_link(name)}</td>'
                    f'<td><a class="gchip" href="groups.html#group-{g.lower()}">{g}</a></td>{cells}</tr>')
    body = f"""<h1>Tournament futures</h1>
<p class="standfirst">100,000 Monte Carlo tournaments on a 200-model bootstrap ensemble,
using the official FIFA bracket.</p>
<p class="fineprint">Reach R32 is not the same as win group - eight third-placed teams advance
too, which is why mid-tier teams clear 70% there. Columns nest (champion ⊂ final ⊂ SF…), and
each column sums across all 48 teams to the slots available: 12 group wins, 2 finalists, 1 champion.
Read every percentage as a fair Polymarket price for that future. The ensemble matters: simulating
with 200 bootstrap refits instead of one model widens uncertainty and shaves points off the
favourite - that haircut is honesty, not noise. Anomalies are modelled too, as zero-mean
randomness with assumed magnitudes: a per-tournament form shock (injuries, chemistry) on every
team, knockout attrition (a 10% chance each tie leaves lasting damage - cards, knocks), and a
fatigue penalty after 120-minute matches. None of it favours anyone on average; all of it
favours outsiders, because chaos always does.</p>
<table class="futures">
<thead><tr><th>Team</th><th title="group A-L">Grp</th><th class="num" title="finish top of the group">Win group</th><th class="num" title="advance to the round of 32 - top two per group plus the eight best third-placed teams">Reach R32</th>
<th class="num" title="reach the quarter-finals">QF</th><th class="num" title="reach the semi-finals">SF</th><th class="num" title="reach the final">Final</th><th class="num" title="win the tournament - column sums to 100% across all teams">Champion</th><th class="num" title="live Polymarket price for Champion">Mkt</th><th class="num" title="model minus market on Champion - positive means the market sells it cheaper than the ensemble values it">Edge</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>"""
    (OUT / "futures.html").write_text(page(
        "Futures", body, canon="futures.html",
        desc="World Cup 2026 outright odds — group winners, knockout run and "
             "title chances from a 100,000-run tournament simulation, next to "
             "the market."))


def trend_chart():
    """Cumulative log-loss of model vs market vs blend as matches grade."""
    import math
    graded = sorted((p for p in (PRED or {}).get("group_matches", [])
                     if "actual_score" in p and p.get("p_market")),
                    key=lambda p: p["date_utc"])
    if len(graded) < 3:
        return ('<p class="fineprint">The model-vs-market trend chart draws '
                'itself here once a few matches have been graded.</p>')
    series = {"model": [], "market": [], "blend": []}
    tot = {k: 0.0 for k in series}
    for i, p in enumerate(graded, 1):
        res = {"H": "H", "D": "D", "A": "A"}[p["actual_result"]]
        msum = sum(p["p_market"].values())
        probs = {"model": p["p_model"][res],
                 "market": p["p_market"][res] / msum,
                 "blend": p["p"][res]}
        for k, v in probs.items():
            tot[k] -= math.log(max(v, 1e-9))
            series[k].append(tot[k] / i)
    W, H_, L, R, T, B = 660, 300, 56, 14, 26, 36
    pw, ph = W - L - R, H_ - T - B
    lo = min(min(s) for s in series.values()) * 0.97
    hi = max(max(s) for s in series.values()) * 1.03
    cols = {"model": "var(--green, #14633f)", "market": "var(--red, #a72a1e)", "blend": "var(--amber, #9a7b2d)"}
    s = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H_}" '
         f'style="max-width:660px;width:100%">'
         f'<rect width="{W}" height="{H_}" fill="var(--paper, #f6f1e6)"/>')
    font = 'font-family="ui-monospace,Menlo,monospace"'
    n = len(graded)
    for k, vals in series.items():
        pts = " ".join(
            f"{L + pw * i / max(n - 1, 1):.1f},"
            f"{T + ph * (1 - (v - lo) / (hi - lo)):.1f}"
            for i, v in enumerate(vals))
        s += (f'<polyline points="{pts}" fill="none" stroke="{cols[k]}" '
              f'stroke-width="2"/>')
        s += (f'<text x="{L + pw + 2}" y="{T + ph * (1 - (vals[-1] - lo) / (hi - lo)) + 4}" '
              f'font-size="10" fill="{cols[k]}" {font}>{k}</text>')
    s += (f'<text x="{L}" y="16" font-size="12" fill="var(--ink, #211d16)" {font}>'
          f'running log-loss after {n} graded matches (lower = better forecaster)</text>')
    s += (f'<text x="{L}" y="{H_-10}" font-size="10" fill="var(--ink-soft, #6b6353)" {font}>'
          f'{graded[0]["date_utc"][:10]} → {graded[-1]["date_utc"][:10]}</text>')
    return f'<figure>{s}</svg><figcaption>The whole experiment in one line: ' \
           f'whichever curve is lowest is winning the forecasting contest.</figcaption></figure>'


# ---------- method-page evolution chart ----------
EVO_MILESTONES = {   # run-stamp prefix -> label shown on the chart
    "20260609-2104": "v1: DC + Monte Carlo",
    "20260610-0023": "anomaly variance",
    "20260610-1526": "squad-value prior",
    "20260611-2333": "nightly auto-runs",
}


def evolution_section(lang="en"):
    """Champion odds across every archived tournament run, with method
    milestones marked — how much each iteration actually moved the needle."""
    import glob
    files = sorted(glob.glob(str(ROOT / "runs" / "*_tournament.json")))
    series = []   # (stamp, {team: champion_p})
    for f in files:
        try:
            d = json.load(open(f))["teams"]
        except (ValueError, KeyError):
            continue
        series.append((Path(f).name[:13],
                       {t: v["champion"] for t, v in d.items()}))
    if len(series) < 3:
        return ""
    teams = sorted({t for _, s in series for t in s},
                   key=lambda t: -max(s.get(t, 0) for _, s in series))[:7]
    W, H_, L, R, T, B = 660, 320, 40, 86, 40, 30
    pw, ph = W - L - R, H_ - T - B
    hi = max(s.get(t, 0) for _, s in series for t in teams) * 1.12
    n = len(series)
    x = lambda i: L + pw * i / (n - 1)
    y = lambda v: T + ph * (1 - v / hi)
    font = 'font-family="ui-monospace,Menlo,monospace"'
    palette = ["#14633f", "#a72a1e", "#856a1d", "#211d16",
               "#4a6fa5", "#7b4aa5", "#3f8f8a"]
    s = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H_}" '
         f'style="max-width:660px;width:100%">'
         f'<rect width="{W}" height="{H_}" fill="var(--paper, #f6f1e6)"/>')
    # milestone markers — numbered circles; names live in the table below
    # (text labels clipped outside the viewbox at the chart edges)
    stamps_ = [st for st, _ in series]
    marks = sorted((stamps_.index(st), label)
                   for st, label in EVO_MILESTONES.items() if st in stamps_)
    for no, (i, label) in enumerate(marks, 1):
        s += (f'<line x1="{x(i):.1f}" y1="{T - 6}" x2="{x(i):.1f}" '
              f'y2="{T + ph}" stroke="var(--ink-soft, #6b6353)" '
              f'stroke-dasharray="3,3"/>')
        s += (f'<circle cx="{x(i):.1f}" cy="{T - 14}" r="8" '
              f'fill="var(--ink, #211d16)"/>')
        s += (f'<text x="{x(i):.1f}" y="{T - 10.5}" font-size="9.5" '
              f'fill="var(--paper, #f6f1e6)" text-anchor="middle" '
              f'{font}>{no}</text>')
    used_ys = []
    for ti, t in enumerate(teams):
        vals = [s_.get(t, 0) for _, s_ in series]
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        col = palette[ti % len(palette)]
        s += (f'<polyline points="{pts}" fill="none" stroke="{col}" '
              f'stroke-width="2"/>')
        ly = y(vals[-1]) + 3
        while any(abs(ly - u) < 11 for u in used_ys):   # de-overlap labels
            ly += 11
        used_ys.append(ly)
        s += (f'<text x="{L + pw + 4}" y="{ly:.1f}" font-size="10" '
              f'fill="{col}" {font}>{escape(t)} '
              f'{vals[-1] * 100:.0f}%</text>')
    s += (f'<text x="{L}" y="14" font-size="12" fill="var(--ink, #211d16)" {font}>'
          + ("championship odds, every archived run" if lang == "en" else
             "championship odds, every archived run")
          + '</text>')
    s += (f'<text x="{L}" y="{H_ - 8}" font-size="10" '
          f'fill="var(--ink-soft, #6b6353)" {font}>'
          f'{series[0][0][:8]} → {series[-1][0][:8]} · {n} runs</text></svg>')

    # biggest movers per milestone (vs the run immediately before)
    baseline = "baseline" if lang == "en" else "نقطهٔ شروع"
    mover_rows = []
    for no, (i, label) in enumerate(marks, 1):
        if i == 0:
            moved = baseline
        else:
            prev, cur = series[i - 1][1], series[i][1]
            deltas = sorted(((t, (cur.get(t, 0) - prev.get(t, 0)) * 100)
                             for t in cur), key=lambda kv: -abs(kv[1]))[:3]
            moved = ", ".join(f"{escape(t)} {d:+.1f}pp" for t, d in deltas
                              if abs(d) >= 0.3) or "—"
        mover_rows.append(f'<tr><td class="num">{no}</td>'
                          f"<td>{escape(label)}</td><td>{moved}</td></tr>")
    if lang == "fa":
        head = "هر تکرار، چقدر پیش‌بینی را جابه‌جا کرد"
        intro = ("نمودار زیر شانس قهرمانی را در تمام اجراهای آرشیوشده نشان می‌دهد؛ "
                 "خط‌چین‌ها لحظهٔ ورود هر روش جدید هستند. جدول، بزرگ‌ترین "
                 "جابه‌جایی هر مرحله را خلاصه می‌کند. خط‌های صافِ روزهای آخر هم "
                 "خودشان پیام دارند: ممیزی‌های کالیبراسیون و بازارهای جدید "
                 "پیش‌بینی‌ها را تغییر ندادند ــ و قرار هم نبود تغییر دهند.")
        cols = "<th>#</th><th>روش</th><th>بزرگ‌ترین جابه‌جایی‌ها</th>"
    else:
        head = "What each iteration did to the numbers"
        intro = ("The chart shows championship odds across every archived "
                 "simulation run; dashed lines mark the moment each new method "
                 "landed. The table summarises the biggest moves. The flat "
                 "stretch at the end carries its own message: the calibration "
                 "audits and the new market surfaces changed no predictions — "
                 "by design, since both were pricing the same grid.")
        cols = "<th>#</th><th>Method</th><th>Biggest moves</th>"
    return f"""<h2>{head}</h2>
<p class="fineprint">{intro}</p>
<figure>{s}</figure>
<table class="markets evo"><thead><tr>{cols}</tr></thead>
<tbody>{''.join(mover_rows)}</tbody></table>"""


# ---------- shared accuracy headline ----------
def accuracy_headline(acc):
    """The scorecard's headline strip. Leads with the proper-score
    comparison vs the market, not raw hit-rate: a probabilistic model is
    judged on calibration, and raw "results called" collapses in draw-heavy
    stretches (a draw is rarely any single match's likeliest result) even
    when the probabilities are sound. The honest benchmark is the market —
    if we're level with or ahead of it, the model is doing its job whatever
    the hit-rate reads. Returns the inner HTML of <dl class="stats">."""
    cmp = acc.get("compare") or {}
    model_ll = (cmp.get("model") or {}).get("logloss")
    market_ll = (cmp.get("market") or {}).get("logloss")
    if model_ll is not None and market_ll is not None:
        d = market_ll - model_ll          # +ve = model ahead of the market
        vs = (f"ahead by {d:.3f}" if d > 0.0005
              else f"behind by {-d:.3f}" if d < -0.0005 else "level")
    else:
        vs = "—"
    pct = (acc.get("result_pct") or 0) * 100
    items = (
        ("Matches graded", acc.get("graded_group_matches", 0)),
        ("Model log-loss", model_ll if model_ll is not None else "—"),
        ("vs the market", vs),
        ("Results called", f"{acc.get('result_hits', 0)} ({pct:.0f}%)"),
    )
    return "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in items)


# ---------- bracket / predictions page ----------
def build_bracket():
    if not PRED:
        return
    acc = PRED.get("accuracy")
    if acc and acc.get("graded_group_matches"):
        acc_html = f'<dl class="stats">{accuracy_headline(acc)}</dl>'
        if acc.get("compare"):
            comp_rows = "".join(
                f'<tr><td>{src.title()}</td>'
                f'<td class="num">{v["brier"]}</td>'
                f'<td class="num">{v["logloss"]}</td></tr>'
                for src, v in acc["compare"].items())
            acc_html += f"""<table class="ko">
<thead><tr><th>Probability source</th><th class="num" title="mean squared error of the probabilities - lower is better; guessing equally scores 0.667">Brier</th><th class="num" title="penalises confident wrong calls hardest - lower is better; guessing equally scores 1.099">Log-loss</th></tr></thead>
<tbody>{comp_rows}</tbody></table>
<p class="meta center">Lower is better - this settles whether the model, the market,
or the blend prices matches best (market graded on {acc['market_priced_matches']} priced matches).</p>"""
    else:
        acc_html = ('<p class="standfirst">No matches graded yet - run '
                    '<code>wc26_update_results.py</code> then rebuild once games finish.</p>')

    rounds = {}
    for k in PRED["knockout"]:
        rounds.setdefault(k["round"], []).append(k)
    ko_html = ""
    for rname in ("Round of 32", "Round of 16", "Quarter-final",
                  "Semi-final", "Third place", "Final"):
        if rname not in rounds:
            continue
        lines = "".join(
            f'<tr><td class="num">{k["match"]}</td>'
            f'<td>{team_link(k["team1"])} <em>v</em> {team_link(k["team2"])}</td>'
            f'<td class="pick">{escape(k["pick"])}</td>'
            f'<td class="num">{k["p_pick"] * 100:.0f}%</td></tr>'
            for k in rounds[rname])
        ko_html += f"""<section><h2>{rname}</h2>
<table class="ko"><thead><tr><th class="num" title="official FIFA match number">#</th><th>Tie (predicted)</th><th>Pick</th><th class="num" title="the model's own probability for its pick - 50% means a forced coin-flip call">Conf.</th></tr></thead>
<tbody>{lines}</tbody></table></section>"""

    gt_html = ""
    for g in sorted(PRED["group_tables"]):
        rows = "".join(
            f'<tr><td class="num">{i + 1}</td><td>{team_link(r["team"])}</td>'
            f'<td class="num">{r["xpts"]:.1f}</td></tr>'
            for i, r in enumerate(PRED["group_tables"][g]))
        gt_html += f"""<section class="group"><h2><span>Group</span> {g}</h2>
<table><thead><tr><th class="num">#</th><th>Team</th><th class="num">xPts</th></tr></thead>
<tbody>{rows}</tbody></table></section>"""

    rows = []
    for p in sorted(PRED["group_matches"], key=lambda x: x["date_utc"]):
        if "actual_score" in p:
            mark = '<i class="f W">✓</i>' if p["hit"] else '<i class="f L">✗</i>'
            actual = f'<td class="num score">{p["actual_score"]}</td><td>{mark}</td>'
        else:
            actual = '<td class="num dim">-</td><td class="dim">-</td>'
        conf = p["p"][p["pred_result"]] * 100
        pick = {"H": p["home"], "D": "Draw", "A": p["away"]}[p["pred_result"]]
        rows.append(
            f'<tr><td class="num">{p["date_utc"][:10]}</td>'
            f'<td><b class="gchip">{p["group"]}</b></td>'
            f'<td>{escape(p["home"])} <em>v</em> {escape(p["away"])}</td>'
            f'<td><b>{escape(pick)}</b></td>'
            f'<td class="num">{conf:.0f}%</td>'
            f'<td class="num score dim">{p["pred_score"]}</td>{actual}</tr>')
    track_html = f"""<table>
<thead><tr><th class="num">Date</th><th title="group A-L">Grp</th><th>Fixture</th>
<th title="the model's 1X2 call — THE prediction that gets graded">Result pick</th><th class="num" title="probability of the result pick (market-blended)">Conf.</th><th class="num" title="single most likely exact scoreline — typically a 10-15% shot among ~170 possible scores. A low draw is often the modal score even when one side is clearly favoured to WIN; this column is colour, not the call">Modal score</th><th class="num" title="filled in by wc26_update_results.py as games finish">Actual</th><th></th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""

    body = f"""<h1>The predicted tournament</h1>
<p class="standfirst">Every match called in advance - locked {PRED['locked_at']},
graded against reality as results land. Picks are modal outcomes from the mean model;
the official FIFA bracket decides who meets whom.</p>
<div class="champ">Predicted champion: <b>{team_link(PRED['champion'])}</b></div>
<p class="fineprint">Two pick columns, two different questions - don't conflate them. <b>Result
pick</b> is the call: who wins (or a draw), with the model's probability beside it. <b>Modal
score</b> is the single most likely exact scoreline, typically a 10-15% shot - and because goals
are rare, a low draw like 1-1 is often the modal score even when one team is clearly favoured to
win. Betting the modal score as if it were the result call is a misread (South Korea v Czechia,
matchday 1: modal score 1-1, result pick South Korea at 47% - Korea won 2-1).</p>
<p class="fineprint">A locked bracket never updates - when reality diverges, the picks stay frozen,
because a prediction you can revise isn't a prediction. The Conf column is the model's own
probability for its pick: a 50% pick is a coin flip it was forced to call, so judge the model by
the scorecard's probability metrics, not the ✓/✗ column. Brier and log-loss score the
probabilities themselves (lower is better; guessing ⅓-⅓-⅓ scores 0.667 Brier, 1.099 log-loss) -
and the model/market/blend comparison is the real experiment here: it settles which forecaster
deserves your trust with actual match results.</p>
<h2>Scorecard</h2>
{acc_html}
<p class="fineprint">The full accuracy analysis - trends, calibration, sharpest and
roughest calls, matchday breakdowns - lives on the <a href="report.html">Report</a> page.</p>
{ko_html}
<h2>Predicted group tables</h2>
<div class="groups">{gt_html}</div>
<h2>Group-stage match tracker</h2>
{track_html}"""
    (OUT / "bracket.html").write_text(page(
        "Bracket", body, canon="bracket.html",
        desc="Our locked pre-tournament World Cup 2026 bracket prediction, "
             "graded match-by-match in public against the real results."))


# ---------- awards page ----------
def fmt_cents(v):
    return f"{v * 100:.1f}¢" if v is not None else "-"


def build_awards():
    if not AWARDS:
        return

    def edge_cell(e):
        if e is None:
            return '<td class="num dim">-</td>'
        cls = "pos" if e >= 0.03 else "neg" if e <= -0.03 else ""
        return f'<td class="num edge {cls}">{e * 100:+.1f}¢</td>'

    boot_rows = "".join(
        f'<tr><td>{player_link(b["player"])}</td><td>{team_link(b["team"])}</td>'
        f'<td class="num">{b["intl_goals_24_26"]}</td>'
        f'<td class="num">{b["exp_goals"]:.1f}</td>'
        f'<td class="num fair">{b["p_model"] * 100:.1f}¢</td>'
        f'<td class="num">{fmt_cents(b["market"])}</td>{edge_cell(b["edge"])}</tr>'
        for b in AWARDS["golden_boot"][:20])
    nation_rows = "".join(
        f'<tr><td>{team_link(n["team"])}</td>'
        f'<td class="num">{n["exp_goals"]:.1f}</td>'
        f'<td class="num fair">{n["p_model"] * 100:.1f}¢</td>'
        f'<td class="num">{fmt_cents(n["market"])}</td>{edge_cell(n["edge"])}</tr>'
        for n in AWARDS["top_scorer_nation"][:12])
    glove_rows = "".join(
        f'<tr><td class="num">{i + 1}</td><td>{player_link(g["player"])}</td>'
        f'<td>{team_link(g["team"])}</td>'
        f'<td class="num">{g["p_final"] * 100:.1f}%</td>'
        f'<td class="num">{g["conceded_pg"]:.2f}</td>'
        f'<td class="num">{fmt_cents(g.get("market"))}</td></tr>'
        for i, g in enumerate(AWARDS["golden_glove"][:12]))
    ball_rows = "".join(
        f'<tr><td>{player_link(b["player"])}</td>'
        f'<td>{team_link(b["team"]) if b["team"] else "-"}</td>'
        f'<td class="num">{fmt_cents(b["market"])}</td>'
        f'<td class="num">{b["team_champion_p"] * 100:.1f}%'
        f'</td></tr>' if b["team_champion_p"] is not None else
        f'<tr><td>{escape(b["player"])}</td><td>-</td>'
        f'<td class="num">{fmt_cents(b["market"])}</td><td class="num dim">-</td></tr>'
        for b in AWARDS["golden_ball"][:12])

    body = f"""<h1>The honours board</h1>
<p class="standfirst">Golden Boot and top-scoring nation are modelled - each player's
goals simulated across the same 100,000 tournaments behind the Futures page, using his
share of his team's international goals since 2024. Glove and Ball are softer ground:
one is a labelled heuristic, the other pure market.</p>

<h2>Golden Boot - modelled</h2>
<p class="fineprint">xG (tourn.) is the player's expected tournament goals: his share of his
team's international goals since 2024, applied to his team's goal count in each simulated
tournament - so deep-run odds are baked in, and a striker on a likely semi-finalist beats an
equal scorer on a group-stage exit. The share is not adjusted for opposition quality, which is
why CONCACAF strikers rate high and the market disagrees. A "-" market means Polymarket simply
hasn't listed the player: the model's co-favourite is unpriced, which is either an oversight
or a verdict.</p>
<table class="ko">
<thead><tr><th>Player</th><th>Team</th><th class="num" title="international goals 2024-26, all competitions - the basis of his share of team goals">Intl goals 24–26</th>
<th class="num" title="expected tournament goals: his share applied to his team's goals across 100k simulated tournaments - deep runs included">xG (tourn.)</th><th class="num" title="probability of winning the Golden Boot, as a fair price">Model</th><th class="num" title="live Polymarket YES price">Polymarket</th><th class="num" title="model minus market - positive means underpriced">Edge</th></tr></thead>
<tbody>{boot_rows}</tbody></table>

{boot_race_table()}

<h2>Top scoring nation - modelled</h2>
<table class="ko">
<thead><tr><th>Team</th><th class="num">Exp. goals</th><th class="num">Model</th>
<th class="num">Polymarket</th><th class="num">Edge</th></tr></thead>
<tbody>{nation_rows}</tbody></table>

<h2>Golden Glove - heuristic lean</h2>
<p class="standfirst">Rank = P(reach final) × defensive record. Not a calibrated
probability; the market column is the bettable number.</p>
<table class="ko">
<thead><tr><th class="num" title="heuristic rank: reach-final probability x defensive record - not a calibrated probability">#</th><th>Keeper</th><th>Team</th><th class="num" title="team's probability of reaching the final (Glove winners almost always come from finalists)">P(final)</th>
<th class="num" title="team goals conceded per game, last 10">Conceded/g</th><th class="num" title="live Polymarket YES price">Polymarket</th></tr></thead>
<tbody>{glove_rows}</tbody></table>

<h2>Golden Ball - market only</h2>
<p class="standfirst">Best-player awards are voted, not scored; we show the market
with each candidate's team title odds for context.</p>
<table class="ko">
<thead><tr><th>Player</th><th>Team</th><th class="num">Polymarket</th>
<th class="num">Team champion (model)</th></tr></thead>
<tbody>{ball_rows}</tbody></table>
<p class="modelnote">{escape(AWARDS["method"])} Prices as of {AWARDS["prices_at"]}.</p>"""
    (OUT / "awards.html").write_text(page(
        "Awards", body, canon="awards.html",
        desc="World Cup 2026 Golden Boot and top-scorer-nation odds from a "
             "goals model, alongside the live award-market prices."))


# ---------- player dossier pages ----------
def build_player_pages():
    if not PROFILES:
        return
    boot = {b["player"]: b for b in (AWARDS or {}).get("golden_boot", [])}
    (OUT / "players").mkdir(exist_ok=True)
    for name, prof in PROFILES.items():
        bio = prof.get("bio", {})
        team = prof["team"]
        photo = (f'<img class="headshot" src="{bio["photo"]}" '
                 f'alt="{escape(name)}" loading="lazy">' if bio.get("photo") else "")
        facts = "".join(
            f'<div><dt>{k}</dt><dd>{escape(str(v))}</dd></div>'
            for k, v in (("Age", bio.get("age")), ("Born", bio.get("birth_date")),
                         ("Birthplace", bio.get("birth_place")),
                         ("Height", bio.get("height")), ("Weight", bio.get("weight")))
            if v)
        outlook = ""
        b = boot.get(name) or next(
            (v for k, v in boot.items()
             if _nrm(k).split()[-1] == _nrm(name).split()[-1]), None)
        if b:
            mkt = f"{b['market']*100:.1f}¢" if b.get("market") else "unpriced"
            outlook = f"""<h2>Golden Boot outlook</h2>
<dl class="stats">
<div><dt>Model odds</dt><dd>{b['p_model']*100:.1f}¢</dd></div>
<div><dt>Polymarket</dt><dd>{mkt}</dd></div>
<div><dt>Exp. tournament goals</dt><dd>{b['exp_goals']:.1f}</dd></div>
<div><dt>Intl goals 24-26</dt><dd>{b['intl_goals_24_26']}</dd></div>
</dl>
<p class="fineprint">Model odds come from this player's share of {escape(team)}'s goals
applied across 100,000 simulated tournaments - his team's path to the final is priced in.
Details on the <a href="../method.html">Method</a> page.</p>"""
        tg = next((v["goals"] for k, v in SCORERS.items()
                   if _nrm(k).split()[-1] == _nrm(name).split()[-1]), None)
        race = (f'<p class="standfirst">World Cup 2026 so far: '
                f'<b>{tg} goal{"s" if tg != 1 else ""}</b></p>' if tg else "")
        season_html = ""
        for season in sorted(prof.get("seasons", {}), reverse=True):
            rows = "".join(
                f'<tr><td>{escape(r["team"])}</td><td class="comp">{escape(r["competition"])}</td>'
                f'<td class="num">{r["apps"]}</td><td class="num">{r["minutes"]}</td>'
                f'<td class="num">{r["goals"]}</td><td class="num">{r["assists"]}</td>'
                f'<td class="num">{r["rating"] if r["rating"] else "-"}</td></tr>'
                for r in prof["seasons"][season])
            season_html += f"""<h2>{season} season, all competitions</h2>
<table class="ko">
<thead><tr><th>Team</th><th>Competition</th><th class="num">Apps</th>
<th class="num">Min</th><th class="num">Goals</th><th class="num">Assists</th>
<th class="num" title="average match rating where the league is covered">Rating</th></tr></thead>
<tbody>{rows}</tbody></table>"""
        body = f"""<div class="teamhead">
{photo}
<h1>{escape(bio.get("fullname") or name)}</h1>
<p class="meta"><a class="chip" href="../teams/{slug(team)}.html">{escape(team)}</a></p>
{race}
</div>
<dl class="stats">{facts}</dl>
{outlook}
{season_html}
{llm_section(LLM["players"].get(name), "The analyst's notes")}
<p class="fineprint">Reference dossier; data from API-Football, profiles fetched
{PROFILES_AT}. Club and country statistics per competition; rating coverage varies by league.</p>"""
        crumb = (f'<a href="../awards.html">Awards</a> / '
                 f'<a href="../teams/{slug(team)}.html">{escape(team)}</a> / {escape(name)}')
        (OUT / "players" / f"{slug(name)}.html").write_text(
            page(name, body, depth=1, crumb=crumb))


def boot_race_table():
    if not SCORERS:
        return ('<p class="fineprint">The live scorer race appears here once the '
                'tournament\'s first goals are scored.</p>')
    boot = {_nrm(b["player"]).split()[-1]: b
            for b in (AWARDS or {}).get("golden_boot", [])}
    rows = []
    for name, s in list(SCORERS.items())[:15]:
        b = boot.get(_nrm(name).split()[-1])
        odds = f'{b["p_model"]*100:.1f}¢' if b else '<span class="dim">-</span>'
        mkt = (f'{b["market"]*100:.1f}¢' if b and b.get("market")
               else '<span class="dim">-</span>')
        rows.append(f'<tr><td>{player_link(name)}</td>'
                    f'<td>{escape(s["team"].title())}</td>'
                    f'<td class="num"><b>{s["goals"]}</b></td>'
                    f'<td class="num">{odds}</td><td class="num">{mkt}</td></tr>')
    return f"""<h2>The race, live</h2>
<table class="ko">
<thead><tr><th>Player</th><th>Team</th><th class="num">Goals</th>
<th class="num" title="model Golden Boot odds">Model</th>
<th class="num">Polymarket</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""



# ---------- accuracy report page ----------
def build_report():
    md_of = {m["match_id"]: m["matchday"] for m in MATCHES}
    graded = sorted((p for p in (PRED or {}).get("group_matches", [])
                     if "actual_score" in p), key=lambda p: p["date_utc"])
    acc = (PRED or {}).get("accuracy") or {}

    if not graded:
        body = """<h1>The report card</h1>
<p class="standfirst">Every prediction on this site was locked before the tournament;
this page grades them against reality after every matchday. Nothing here yet -
the first results arrive tonight, and the first report follows the first grading run.</p>
<p class="fineprint">What will appear: result hit rate and proper scores (Brier,
log-loss) for the model, the market and the blend; the running trend of who is
forecasting best; calibration buckets; the sharpest and roughest calls; and a
matchday-by-matchday breakdown. The locked picks themselves are on the
<a href="bracket.html">Bracket</a> page.</p>"""
        (OUT / "report.html").write_text(page(
        "Report", body, canon="report.html",
        desc="How accurate are the predictions? Live World Cup 2026 model "
             "accuracy — log-loss versus the market, calibration and hit rate, "
             "updated each matchday."))
        return

    # headline strip — leads with log-loss vs the market (see accuracy_headline)
    head = f'<dl class="stats">{accuracy_headline(acc)}</dl>'

    comp = ""
    if acc.get("compare"):
        rows = "".join(
            f'<tr><td>{srcn.title()}</td><td class="num">{v["brier"]}</td>'
            f'<td class="num">{v["logloss"]}</td></tr>'
            for srcn, v in acc["compare"].items())
        comp = f"""<h2>Who forecasts best so far</h2>
<table class="ko"><thead><tr><th>Source</th>
<th class="num" title="mean squared error - guessing equally scores 0.667">Brier</th>
<th class="num" title="penalises confident errors - guessing equally scores 1.099">Log-loss</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="fineprint">Market graded on its {acc.get("market_priced_matches", 0)} priced
matches. Lower is better; the gap between these rows is this whole project's thesis
being settled in public. The market is the benchmark that matters — when the
model is level with or below the market's log-loss, it is forecasting well even
if the raw "results called" number looks poor, because the same matches that
beat the model (an unusually draw-heavy run) beat the market too.</p>"""

    # matchday breakdown
    by_md = {}
    for p in graded:
        by_md.setdefault(md_of.get(p["match_id"], "?"), []).append(p)
    md_rows = ""
    for md in sorted(by_md):
        ps = by_md[md]
        hits = sum(1 for p in ps if p.get("hit"))
        ex = sum(1 for p in ps if p["pred_score"] == p.get("actual_score"))
        import math as _m
        ll = -sum(_m.log(max(p["p"][p["actual_result"]], 1e-9))
                  for p in ps) / len(ps)
        md_rows += (f'<tr><td>Matchday {md}</td><td class="num">{len(ps)}</td>'
                    f'<td class="num">{hits}/{len(ps)}</td>'
                    f'<td class="num">{ex}</td><td class="num">{ll:.3f}</td></tr>')
    md_html = f"""<h2>Matchday by matchday</h2>
<table class="ko"><thead><tr><th>Stage</th><th class="num">Played</th>
<th class="num">Results called</th><th class="num">Exact scores</th>
<th class="num">Log-loss (blend)</th></tr></thead><tbody>{md_rows}</tbody></table>"""

    # sharpest / roughest calls vs the market
    scored = []
    for p in graded:
        if not p.get("p_market"):
            continue
        msum = sum(p["p_market"].values())
        res = p["actual_result"]
        scored.append((p["p_model"][res] - p["p_market"][res] / msum, p))
    scored.sort(key=lambda x: -x[0])

    def call_rows(items):
        out = ""
        for delta, p in items:
            res_label = {"H": p["home"], "A": p["away"], "D": "Draw"}[p["actual_result"]]
            out += (f'<tr><td>{escape(p["home"])} v {escape(p["away"])} '
                    f'<b class="score">{p["actual_score"]}</b></td>'
                    f'<td>{escape(res_label)}</td>'
                    f'<td class="num">{p["p_model"][p["actual_result"]] * 100:.0f}%</td>'
                    f'<td class="num">{p["p_market"][p["actual_result"]] / sum(p["p_market"].values()) * 100:.0f}%</td>'
                    f'<td class="num">{delta * 100:+.0f}pp</td></tr>')
        return out

    calls = ""
    if scored:
        calls = f"""<h2>Sharpest calls (model saw it, market didn't)</h2>
<table class="ko"><thead><tr><th>Match</th><th>Outcome</th>
<th class="num">Model gave it</th><th class="num">Market gave it</th>
<th class="num">Edge realised</th></tr></thead>
<tbody>{call_rows(scored[:5])}</tbody></table>
<h2>Roughest calls (market saw it, model didn't)</h2>
<table class="ko"><thead><tr><th>Match</th><th>Outcome</th>
<th class="num">Model gave it</th><th class="num">Market gave it</th>
<th class="num">Edge realised</th></tr></thead>
<tbody>{call_rows(list(reversed(scored[-5:])))}</tbody></table>"""

    # calibration buckets (model probabilities, all three outcomes pooled)
    buckets = {}
    for p in graded:
        for k in ("H", "D", "A"):
            q = p["p_model"][k]
            b = min(int(q * 5), 4)
            hit = 1 if p["actual_result"] == k else 0
            buckets.setdefault(b, []).append((q, hit))
    cal_rows = ""
    for b in sorted(buckets):
        qs = buckets[b]
        cal_rows += (f'<tr><td>{b * 20}-{b * 20 + 20}%</td>'
                     f'<td class="num">{len(qs)}</td>'
                     f'<td class="num">{sum(q for q, _ in qs) / len(qs) * 100:.0f}%</td>'
                     f'<td class="num">{sum(h for _, h in qs) / len(qs) * 100:.0f}%</td></tr>')
    cal = f"""<h2>Calibration so far</h2>
<table class="ko"><thead><tr><th>Claimed probability</th><th class="num">Claims</th>
<th class="num">Average claim</th><th class="num">Actually happened</th></tr></thead>
<tbody>{cal_rows}</tbody></table>
<p class="fineprint">A calibrated forecaster's last two columns match. The full
group stage (72 matches) is now graded, so these buckets carry real signal —
read them as the model's honest track record, not a small-sample teaser. The
knockout rounds add fewer matches, so expect the later buckets to settle
slowly.</p>"""

    goals_html = goals_section(graded) + totals_section(graded)

    body = f"""<h1>The report card</h1>
<p class="standfirst">Locked predictions, graded after every matchday. Re-generated
automatically by the nightly run; previous editions live in the
<a href="archive.html">versions archive</a>.</p>
<p class="fineprint">How to read this: a probability model lives or dies on its
log-loss against the market, not on how often its single likeliest pick lands.
Raw "results called" sinks whenever draws cluster — a draw is rarely any one
match's most-likely score — so the headline below leads with the model's
log-loss and how it sits versus the market. For a money view, see what
flat-staking the model's market edges would have returned:
<a href="follow-the-model.html">if you'd followed the model →</a></p>
{head}
{comp}
{trend_chart()}
{md_html}
{goals_html}
{calls}
{cal}"""
    (OUT / "report.html").write_text(page(
        "Report", body, canon="report.html",
        desc="How accurate are the predictions? Live World Cup 2026 model "
             "accuracy — log-loss versus the market, calibration and hit rate, "
             "updated each matchday."))


def goals_grade(graded):
    """Grade total-goals predictions from the locked most-likely scoreline
    against actual scores. Pure: list of graded picks in, stats dict out."""
    def total(score):
        h, a = score.split("-")
        return int(h) + int(a)

    n = pred_sum = act_sum = err_sum = exact = ou_n = ou_hit = 0
    for p in graded:
        if "actual_score" not in p or "pred_score" not in p:
            continue
        pt, at = total(p["pred_score"]), total(p["actual_score"])
        n += 1
        pred_sum += pt
        act_sum += at
        err_sum += abs(pt - at)
        exact += (pt == at)
        # over/under 2.5 direction of the modal scoreline vs reality
        # (a conservative proxy — the modal total skews low; see fineprint)
        ou_n += 1
        ou_hit += ((pt > 2.5) == (at > 2.5))
    if not n:
        return None
    return {"n": n, "pred_per": pred_sum / n, "act_per": act_sum / n,
            "mae": err_sum / n, "exact": exact,
            "ou_n": ou_n, "ou_hit": ou_hit}


def totals_grade(locked, actual_total_by_mid):
    """Grade forward-locked over/under vs actual totals. `locked` is the
    wc26_totals_locked 'matches' dict; `actual_total_by_mid` maps mid ->
    actual goal total. Returns per-source (model/market/blend) Brier,
    log-loss and hit count, or None if nothing has been graded yet."""
    import math
    srcs = ("over_model", "over_market", "over_blend")
    agg = {s: {"n": 0, "brier": 0.0, "ll": 0.0, "hit": 0} for s in srcs}
    n = 0
    for mid, rec in locked.items():
        at = actual_total_by_mid.get(mid)
        if at is None:
            continue
        over = 1 if at > rec.get("line", 2.5) else 0
        graded = False
        for s in srcs:
            p = rec.get(s)
            if p is None:
                continue
            st = agg[s]
            st["n"] += 1
            st["brier"] += (p - over) ** 2
            st["ll"] -= math.log(max(p if over else 1 - p, 1e-9))
            st["hit"] += int((p > 0.5) == bool(over))
            graded = True
        n += graded
    if not n:
        return None
    out = {"n": n}
    for s in srcs:
        st = agg[s]
        if st["n"]:
            out[s] = {"brier": round(st["brier"] / st["n"], 4),
                      "logloss": round(st["ll"] / st["n"], 4),
                      "hit": st["hit"], "of": st["n"]}
    return out


def totals_section(graded):
    """Calibrated O/U 2.5 scorecard from the forward-locked totals — model
    vs market vs blend, graded on matches locked pre-kickoff (so no
    look-ahead). Absent until the first locked match finishes."""
    locked = TOTALS_LOCKED.get("matches", {})
    if not locked:
        return ""
    actual = {}
    for p in graded:
        try:
            h, a = p["actual_score"].split("-")
            actual[str(p["match_id"])] = int(h) + int(a)
        except (KeyError, ValueError):
            continue
    g = totals_grade(locked, actual)
    if not g:
        return ""
    label = {"over_model": "Model", "over_market": "Market",
             "over_blend": "Blend"}
    rows = "".join(
        f'<tr><td>{label[s]}</td>'
        f'<td class="num">{g[s]["hit"]}/{g[s]["of"]}</td>'
        f'<td class="num">{g[s]["brier"]}</td>'
        f'<td class="num">{g[s]["logloss"]}</td></tr>'
        for s in ("over_model", "over_market", "over_blend") if s in g)
    return f"""<h2>Totals (Over/Under 2.5)</h2>
<table class="ko"><thead><tr><th>Source</th>
<th class="num" title="times the Over/Under call was right">Direction</th>
<th class="num" title="mean squared error on P(over) - guessing 0.5 scores 0.25">Brier</th>
<th class="num" title="penalises confident errors">Log-loss</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="fineprint">A <em>calibrated</em> totals grade, unlike the modal-scoreline
read above: the model's actual Over 2.5 probability scored against reality. These
lines were locked <b>pre-kickoff from {escape(TOTALS_LOCKED.get("locked_from", "?"))}</b>
(the pre-tournament bracket didn't store totals), so they grade only matches that
kicked off after that — no look-ahead. Lower Brier/log-loss is better.</p>"""


def goals_section(graded):
    g = goals_grade(graded)
    if not g:
        return ""
    st = "".join(
        f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in (
            ("Goals/game actual", f"{g['act_per']:.2f}"),
            ("Goals/game predicted", f"{g['pred_per']:.2f}"),
            ("Avg total error", f"{g['mae']:.2f}"),
            ("Exact total", f"{g['exact']}/{g['n']}"),
            ("O/U 2.5 direction", f"{g['ou_hit']}/{g['ou_n']} "
             f"({g['ou_hit'] / g['ou_n'] * 100:.0f}%)"),
        ))
    return f"""<h2>Goals</h2>
<dl class="stats">{st}</dl>
<p class="fineprint">"Predicted" is the total of each match's locked most-likely
scoreline - a deliberately conservative point estimate, since the single most-likely
scoreline sits below the average total, so expect this to trail the actual rate.
It grades whether the model reads a match as high- or low-scoring, not a calibrated
totals line. The full over/under probabilities are on each match page; their betting
accuracy (hit rate and closing-line value) is tracked privately in the betting
paper-trading log.</p>"""


# ---------- methodology page ----------
def inline_svg(name):
    """Inline a generated chart so it inherits the page theme (CSS vars).
    The viewBox is re-balanced so content sits centred in the framed
    figure — the charts carry y-axis labels on the left only, which
    otherwise leaves all the dead space on one side."""
    try:
        svg = (ROOT / "charts" / f"{name}.svg").read_text()
    except FileNotFoundError:
        return f'<img src="img/{name}.svg" alt="{name} chart">'
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if not m:
        return svg
    w = float(m.group(1))
    xs = [float(v) for v in re.findall(r'(?:\bx|x1|cx)="(-?[\d.]+)"', svg)]
    xs += [float(a) + float(b) for a, b in
           re.findall(r'<rect x="(-?[\d.]+)" y="-?[\d.]+" width="([\d.]+)"', svg)]
    if not xs:
        return svg
    pad = max(min(xs), w - max(xs))
    new_w = max(xs) - min(xs) + 2 * pad
    return svg.replace(
        m.group(0),
        f'viewBox="{min(xs) - pad:.0f} 0 {new_w:.0f} {m.group(2)}"', 1)


def llm_section(e, heading):
    """AI-analyst colour — clearly labeled, gracefully absent when not
    generated. The grounding contract (frozen dossier + model numbers
    only) is documented in pipeline/wc26_llm.py."""
    if not e or not e.get("text"):
        return ""
    paras = "".join(f"<p>{escape(p.strip())}</p>"
                    for p in e["text"].split("\n\n") if p.strip())
    return (f'<section class="llm"><h2>{heading}</h2>{paras}'
            f'<p class="fineprint llmnote">AI-written colour, generated by '
            f'{escape(e.get("model", "an LLM"))} on {escape(e.get("generated", "?"))} '
            f'from a frozen source dossier plus the model\'s own numbers — '
            f'the probabilities above are the product, this is commentary.</p>'
            f'</section>')


def build_method():
    try:
        prm = json.load(open(DATA / "wc26_params.json"))
        P = prm["params"]
    except FileNotFoundError:
        prm, P = {}, {}
    elo_ratings = " · ".join(f"{escape(t)} {r}"
                             for t, r in list(ELO_RATINGS.items())[:12]) \
        or "not generated yet"
    body = f"""<h1>How the numbers are made</h1>
<p class="standfirst">Everything on this site is generated by open code from public data -
no hand-picked scores, no vibes. The full pipeline is on
<a href="https://github.com/amirdaraee/world-cup-predictions">GitHub</a>; this page is the
plain-language tour.</p>

<h2>In plain words (no math, promise)</h2>
<p>Imagine you watched every football match a team played for years, and every time they
scored you put a pebble in their jar. Teams with heavy jars are strong. Recent pebbles
count more than old ones, because teams change. That's the heart of it: <b>we weigh
pebble jars.</b></p>
<p>We also peek at what each team's players would cost to buy - a team full of expensive
stars gets a little extra credit even if they've had a quiet year, the way you'd still
pick the tall kid for basketball.</p>
<p>For any match, the model is like rolling two weighted dice - one for each team's goals.
Roll them once, you get one possible score. We let a computer roll them for the
<em>entire World Cup</em>, all the way to the final… then do the whole tournament again,
one hundred thousand times. If Argentina lifts the trophy in 19,000 of those imaginary
tournaments, we say: 19%.</p>
<p>One more trick: thousands of people bet real money on these games, and their prices are
the crowd's best guess. We listen to the crowd a little, because the crowd hears news -
injuries, line-ups - before any goal statistics can.</p>
<p>And the honesty rule: we wrote down every prediction <b>before</b> the tournament,
locked the page, and grade ourselves in public as the real results come in. No erasing,
no "we knew it all along." A percentage is a promise about <em>how often</em> we should
be right - when the model says 70%, it should be wrong about 3 times in 10, and we
count those too.</p>

<h2>The data</h2>
<p>Three sources. <b>49,450 international matches since 1872</b> (the community-maintained
martj42 dataset) - the model trains on the <b>8,081 played since 2018</b>. Team form and
fixtures come from <b>API-Football</b>. Live market prices come from <b>Polymarket's</b> public
API. The training data was audited against the primary source: four wrong scores were found,
adjudicated, and patched (the corrections file and methodology are in the repo).</p>

<h2>The match model</h2>
<p>A <b>Dixon-Coles weighted Poisson</b> - the classic bookmaking model: every team gets an
attack and a defence rating fitted to who they scored against and conceded to, recent matches
counting more (half-life {P.get('half_life', '?')} days), friendlies down-weighted
(×{P.get('friendly_w', '?')}), thin-data teams shrunk toward average, and a low-score
correction (ρ={P.get('rho', '?')}) because real football produces more 0-0s and 1-1s than
independent Poissons admit. Hosts get a fitted home-crowd boost (~+30% scoring) when playing
in their own country. From two expected-goals numbers the model computes the <em>entire</em>
scoreline probability grid - every market on the match cards is an exact sum over it,
no sampling.</p>
<p>One newer input (added 10 June, before matchday 1): a <b>squad-value prior</b>. Fitted
ratings are nudged by β·z(log squad market value, Transfermarkt) - talent on paper, not just
results on grass. β=0.35 was chosen by held-out validation, where the prior improved log-loss
from 0.854 to 0.818 at its optimum - the single largest upgrade in the project - shipped
slightly below the optimum as a haircut for look-ahead (current values partly reflect past
results). Structurally it matters most in the knockout rounds, which have no betting market
to blend with: it gives them the player-awareness the group stage already had.</p>
<figure>{inline_svg("grid")}
<figcaption>The actual fitted grid for the opening match (probabilities in %).
Moneyline = the green vs red triangles; over 2.5 = everything below the third
anti-diagonal; BTTS = everything outside row 0 and column 0.</figcaption></figure>
<p class="fineprint">Hyperparameters were not hand-picked: they were chosen by cross-validation
on four rolling 12-month windows, 4,228 held-out matches ({prm.get('tuned_on', '')}).
Validation log-loss <b>{prm.get('cv_logloss', '?')}</b> against a 1.046 always-guess-the-base-rates
baseline (lower is better; this is bookmaker-grade calibration for internationals).
Two ideas the data rejected along the way: capping blowout scorelines, and shorter memory.
Both lost on held-out matches - the tune log is in the repo.</p>

<h2>Market blending</h2>
<p>For group-match picks, model probabilities are blended with live Polymarket prices
(a log-opinion pool, 35% weight on the market) - the market knows about lineups and injuries
hours before any goals data can. The raw model is kept separate for edge-finding, and the
<a href="bracket.html">scorecard</a> grades <b>model, market, and blend independently</b> as
results arrive, so the tournament itself decides which forecaster deserves trust.</p>

<h2>The tournament simulation</h2>
<p><b>100,000 full tournaments per run</b> - about 10 million simulated matches - on the
official FIFA bracket, including the round-of-32 template and third-place allocation
constraints. Each simulated tournament draws one of <b>200 bootstrap-refitted models</b>
(so parameter uncertainty flows into the odds), plus zero-mean anomaly shocks: a per-team
tournament form shock (injuries, chemistry - σ=0.06 on scoring rates), knockout attrition
(10% chance per tie of lasting damage), and a fatigue penalty after 120-minute matches.
None of it favours anyone on average; all of it favours outsiders, because variance always
taxes the favourite.</p>
<p class="fineprint">Monte Carlo noise at this scale: a 16.6% championship estimate carries a
standard error of about ±0.12 percentage points. Verified empirically - two independent
100,000-tournament runs agreed on every favourite within 0.0–0.2pp. More simulations would not
change the numbers; every meaningful uncertainty lives in the model, not the dice.</p>

<h2>Player awards</h2>
<p>Golden Boot and top-scoring-nation odds are simulated on top of the same 100k tournaments:
each player's tournament goals are drawn from his share of his team's international goals
since 2024, applied to his team's goal count in each simulated run - so reaching a final
buys more chances to score. Shares are not opposition-adjusted (stated limitation).
Golden Ball and Glove have no calibrated model and are shown market-priced only.</p>

<h2>Accountability</h2>
<p>The complete bracket - all 72 group matches and every knockout round to the champion -
was <b>locked before the tournament</b> and is graded publicly as results land: result hit
rate, exact scores, and probability quality (Brier / log-loss) for model, market, and blend.
Every calculation run is archived with a timestamp. A prediction you can revise afterwards
isn't a prediction.</p>

<h2>The science, in more depth</h2>

<h3>Why Poisson?</h3>
<p>Goals are rare events: ~2.7 per 90 minutes, arriving in any minute with small, roughly
independent probability. Counting processes like that converge to the
<a href="https://en.wikipedia.org/wiki/Poisson_distribution"><b>Poisson
distribution</b></a> - one number, the rate λ ("expected goals"), determines the probability of
0, 1, 2… goals: P(k) = λᵏe^(−λ)/k!. So the whole model reduces to estimating two rates per
match. The rates come from a <b>log-linear model</b>: log λ<sub>home</sub> = μ + attack
<sub>home</sub> + defence<sub>away</sub> (+ home advantage) - additive on the log scale,
multiplicative on goals, which is why a strong attack against a leaky defence compounds.</p>
<figure>{inline_svg("poisson")}
<figcaption>Why the assumption holds: goals per team per match across the 8,081 training
internationals (bars) against the Poisson curve at the same mean (dots).</figcaption></figure>

<h3>The Dixon-Coles correction</h3>
<p>Two independent Poissons slightly mispredict reality in one corner: real football produces
more 0-0 and 1-1 draws than independence allows (teams settle, sit deep, kill games).
Dixon &amp; Coles (1997) patched exactly the four low-score cells of the grid with one
correlation parameter ρ - ours is −0.05, fitted, modest. This 30-year-old model family is
still the backbone of professional odds compilation.</p>

<h3>Fitting: weighted maximum likelihood</h3>
<p><a href="https://en.wikipedia.org/wiki/Maximum_likelihood_estimation"><b>Maximum
likelihood</b></a> asks: which ratings make the observed 8,081 results least
surprising? Each match enters the likelihood with a weight: exponential <b>time decay</b>
(a result loses half its influence every 1,000 days - recency matters, but slowly for
national teams) and ×0.8 for friendlies. <b>Shrinkage</b> adds a few phantom "average"
matches to every team, pulling thin-data teams (Curaçao has far fewer internationals than
Brazil) toward the middle - the
<a href="https://en.wikipedia.org/wiki/Bias%E2%80%93variance_tradeoff">bias-variance
trade</a>: a slightly biased estimate beats a wildly noisy one.</p>
<figure>{inline_svg("decay")}
<figcaption>How much a result counts in the fit, by age - the tuned 1,000-day half-life
means a 2023 thrashing still whispers, but recent form speaks.</figcaption></figure>

<h3>Honest validation: cross-validation and scoring rules</h3>
<p>A model graded on data it trained on flatters itself. We grade <b>out-of-sample</b>
(<a href="https://en.wikipedia.org/wiki/Cross-validation_(statistics)">cross-validation</a>):
fit on matches up to a cutoff, predict the next 12 months, repeat over four rolling windows
(4,228 held-out matches). Quality is measured with
<a href="https://en.wikipedia.org/wiki/Scoring_rule"><b>proper scoring rules</b></a> -
functions a forecaster can only optimise by reporting its true beliefs: <b>log-loss</b>
(−log of the probability given to what happened; brutal on confident errors) and the
<a href="https://en.wikipedia.org/wiki/Brier_score"><b>Brier score</b></a>
(mean squared error of probabilities). Ours: log-loss 0.891 vs 1.046 for always guessing
base rates. Accuracy percentages are <em>not</em> proper scores - a forecaster can game them
by always picking favourites - which is why the scorecard leads with Brier and log-loss.</p>
<figure>{inline_svg("calibration")}
<figcaption>Calibration on the held-out year: each dot is a probability bucket; on the
dashed diagonal, claimed confidence equals observed reality.</figcaption></figure>

<h3>Monte Carlo and the law of large numbers</h3>
<p>The tournament has no closed-form answer - group tiebreakers, third-place allocation and
bracket paths interact too messily. <a href="https://en.wikipedia.org/wiki/Monte_Carlo_method"><b>Monte Carlo</b></a>
sidesteps the math: simulate the whole tournament 100,000 times and count. The
<a href="https://en.wikipedia.org/wiki/Law_of_large_numbers"><b>law of large
numbers</b></a> guarantees the counts converge to the true model probabilities; at N=100,000 the standard error of a championship
estimate is √(p(1−p)/N) ≈ ±0.12pp. We verified it empirically: two independent runs agreed on
every favourite within 0.2pp.</p>

<h3>The bootstrap: knowing what we don't know</h3>
<p>One fitted model pretends its ratings are exact. They aren't - they're estimates from
finite data. The
<a href="https://en.wikipedia.org/wiki/Bootstrapping_(statistics)"><b>bootstrap</b></a>
measures that: re-weight the dataset at random
(each match's weight multiplied by an exponential draw), refit, repeat 200 times. The spread
of those 200 models <em>is</em> the parameter uncertainty, and every simulated tournament
draws one of them. Effect: favourites' odds shrink toward the field - overconfidence
removed before it costs money.</p>

<h3>Combining forecasters: the opinion pool</h3>
<p>The model and the market know different things (goals history vs lineups and news). The
<b>logarithmic opinion pool</b> - multiply the probabilities, each raised to a weight, then
renormalise - is the standard way to merge them: p ∝ p<sub>model</sub>^0.65 ·
p<sub>market</sub>^0.35. Unlike simple averaging it respects how probabilities compound, and
the weight is an explicit, testable choice: the scorecard grades model, market and blend
separately, so the data itself will tell us if 0.35 was wrong.</p>

<h2>One model, many markets</h2>
<p>Most of the markets on the match cards are not separate models — they are different
questions asked of the <em>same</em> scoreline grid. Team totals are its row and column
sums. The extra over/under lines are just more cells added up. First-to-score follows from
a one-line race argument: if both teams generate chances as independent streams, the first
goal belongs to a team in proportion to its scoring rate. Half-time and second-half results
treat a half as a shorter match: scale both teams' expected goals by the share of goals
that arrive before the break — <b>0.447</b>, fitted on 2,360 goals across six recent
international tournaments (first halves are quieter everywhere, World Cups most of all) —
and reuse the whole machinery. One fitted parameter unlocked a dozen markets, and the
live half-time prices landed within a few cents of the model on day one.</p>
<p>Corners are the exception: goals data says nothing about them, so they get their own
model — a negative binomial fitted to 250 matches from five recent tournaments. The honest
finding: a team's <em>goal</em> threat does not predict its corner count out of sample, so
the shipped model is a pure base rate (about 9.2 corners per match). Where the corner books
are genuinely traded it agrees with them almost exactly; its only job is to flag the
untraded lines that drift far from reality.</p>

<h2 id="second-opinion">A second opinion</h2>
<p>Behind every match sits an <b>Elo companion model</b>: margin-aware Elo ratings fitted on
the same match history, mapped to goal rates by a Poisson regression and run through the same
score grid. It exists because the main model's 200-member ensemble varies the data but shares
one theory — a structurally different model is the only check on the theory itself. Both
models took the identical exam (train before June 2025, graded on 1,071 later matches):
Dixon-Coles 0.819 log-loss, Elo 0.858, and the 50/50 blend 0.831 — worse than Dixon-Coles
alone, so the blend was <b>rejected</b> and the Elo numbers feed nothing you see on the site.
What survives is the disagreement: the two models differ by about 8 points on a typical
fixture, so each match page carries a one-line flag when the gap is unusually wide — a warning
to trust (and bet) that fixture less. The Elo model's own probabilities deliberately do
<em>not</em> appear on the match pages: it is the weaker model on the same exam, and showing
its numbers beside the real ones would only tempt a reader to split the difference with a
model we have shown makes the prediction worse. The current Elo ratings, top of the field, so
you can see what it thinks: {elo_ratings}.</p>

<h2>The AI analyst</h2>
<p>The short analysis sections that close each match, team and player page are written by a
language model (the byline at the bottom of each one says which, and when). Two rules keep
them honest. First, a <b>grounding contract</b>: the model sees only a frozen source dossier —
encyclopedia summaries fetched once, plus our own form data and squad lists — and the
probabilities this site computes. Same inputs, reproducible outputs, no outside facts. Second,
a <b>separation of concerns</b>: the text is colour, not calculation. Nothing the analyst
writes feeds the goal model, the simulations, or the published probabilities — the numbers
would be identical if the words didn't exist. Post-match reviews grade the model and the market
against the pre-match prices preserved for the public scorecard, so the AI can't flatter anyone
with hindsight. (One honest footnote: since 13 June a separate news-checking assistant can
read injury and lineup news before a bet is placed — but it is reduce-only by construction.
It can block or shrink a planned bet; it can never add one, raise a stake, or touch any number
on this site.)</p>

<h2>How the method evolved</h2>
<p class="fineprint">The lab notebook, abridged (full notes ship in the repo). Every change
was validated on held-out matches before it touched the site — and two were rejected for
failing exactly that test.</p>
<ol class="timeline">
<li><b>9 June</b> — Dixon-Coles weighted Poisson chosen over plain Poisson (misprices draws),
Elo-to-goals hybrids (same accuracy, more moving parts) and machine learning (data-starved at
international level). Fitted on 8,081 internationals since 2018: held-out log-loss 0.897
vs a 1.046 baseline.</li>
<li><b>9 June, evening</b> — hyperparameters tuned by rolling cross-validation. Longer memory
won (half-life 548 → 1000 days): national teams change slower than club sides. Log-loss
0.897 → 0.854.</li>
<li><b>9 June, night</b> — uncertainty made explicit: a 200-model bootstrap ensemble feeding
100,000 simulated tournaments on the official bracket, plus the market blend
(log-opinion pool, w=0.35) where Polymarket prices a match. The full bracket
<b>locked</b>, the public scorecard opened.</li>
<li><b>10 June</b> — zero-mean anomaly variance in the simulations: per-tournament form
shocks, knockout attrition, extra-time fatigue. Nobody is helped on average, but favourites
hand tail mass back to the field (Argentina 19.0% → 16.6%).</li>
<li><b>10 June</b> — the squad-value prior: Transfermarkt market values nudge fitted ratings
(β=0.35). Held-out log-loss 0.854 → 0.818 — the single largest upgrade in the project.</li>
<li><b>11 June</b> — the exact-score calibration audit, our favourite <i>negative</i> result:
a tempting correction (high-scoring draws looked 28% underpriced in one test window) failed
validation on three earlier windows and was <b>not</b> shipped. The grid was already
calibrated; resisting a fix is also a method.</li>
<li><b>11 June</b> — full market coverage: Polymarket's exact-score, totals, BTTS and spread
books wired in, so every probability the model produces now sits beside a live price and
its edge.</li>
<li><b>12 June</b> — one model, many markets: team totals, more goal lines and
first-to-score derived from the existing grid; half markets via a fitted first-half goal
share (0.447 on 2,360 goals); futures priced against the ensemble. Corners brought the
second rejected idea: goal threat failed to predict corner counts out of sample, so their
model is a deliberate base rate.</li>
<li><b>12 June, later</b> — an external code review found two real bugs (a contaminated
validation harness for the squad-value prior — the clean rerun reproduced the published
numbers exactly — and unseeded randomness in the tournament engine, now deterministic) and
prompted two defenses: execution-time price protection on bets, and a paper-trading log that
grades every flagged edge for closing-line value. Predictions unchanged; trust in them,
better earned.</li>
<li><b>13 June</b> — a second opinion: an Elo→goals companion model (margin-aware ratings,
Poisson goals map, the same score grid) was built and given the identical exam. Verdict on
the same 1,071 held-out matches: Dixon-Coles 0.819, Elo 0.858, and their 50/50 blend 0.831 —
<em>worse than DC alone</em>, so the blend was rejected and Elo never feeds the published
numbers. It ships as a display-only second opinion on every match page; the two models'
disagreement (they differ by ~8pp on a typical fixture) is used only as a tripwire.</li>
<li><b>Ongoing</b> — every night the real results come in, the locked bracket is regraded in
public, the model refits on the newest matches, and the day's site is frozen into an
immutable snapshot.</li>
</ol>

{evolution_section("en")}

<h2>Known blind spots</h2>
<p class="fineprint">No lineups, injuries or suspensions (the market blend carries those);
no weather or altitude terms (magnitudes unfittable from our data - treated as caution flags,
not model inputs); no within-match simulation (final scores only); anomaly magnitudes are
stated assumptions, not estimates; matchday-3 dead-rubber motivation is handled by manual
review, the way odds compilers do it. The model is a fair-value anchor, not an oracle.</p>

<p class="fineprint">این صفحه به فارسی هم در دسترس است -
<a href="method-fa.html">نسخهٔ فارسی</a>.</p>"""
    (OUT / "method.html").write_text(
        page("Method", body, alt_lang=("method-fa.html", "فارسی"),
             canon="method.html",
             desc="How the model works: a weighted Dixon-Coles Poisson fit on "
                  "international results, validated out-of-sample, with every "
                  "assumption and known blind spot documented."))


def build_method_fa():
    elo_ratings = " · ".join(f"{escape(t)} {r}"
                             for t, r in list(ELO_RATINGS.items())[:12]) \
        or "هنوز ساخته نشده"
    body = f'''<h1>اعداد چگونه ساخته می‌شوند</h1>
<p class="standfirst">همهٔ آنچه در این سایت می‌بینید با کدِ باز و داده‌های عمومی ساخته شده است؛
نه نتیجه‌ای که دستی انتخاب شده باشد و نه حاصل حدس و گمان. کد کامل پروژه روی
<a href="https://github.com/amirdaraee/world-cup-predictions">گیت‌هاب</a> در دسترس است.
این صفحه فقط توضیح می‌دهد که این اعداد از کجا می‌آیند و پشت صحنهٔ آن‌ها چه می‌گذرد.</p>

<h2>به زبان خیلی ساده (بدون ریاضی، قول می‌دهیم)</h2>
<p>تصور کنید سال‌ها همهٔ بازی‌های یک تیم را تماشا کرده‌اید و برای هر گلی که زده، یک سنگریزه
در شیشه‌اش انداخته‌اید. تیم‌هایی که شیشهٔ سنگین‌تری دارند قوی‌ترند. سنگریزه‌های تازه از
قدیمی‌ها مهم‌ترند، چون تیم‌ها عوض می‌شوند. قلب ماجرا همین است: <b>ما شیشه‌های سنگریزه را
وزن می‌کنیم.</b></p>
<p>یک نگاهی هم به قیمت بازیکنان هر تیم می‌اندازیم - تیمی که پر از ستاره‌های گران است حتی
اگر سال آرامی داشته، کمی اعتبار اضافه می‌گیرد؛ همان‌طور که برای بسکتبال باز هم بچهٔ
قدبلند را انتخاب می‌کنید.</p>
<p>برای هر مسابقه، مدل مثل انداختن دو تاسِ دستکاری‌شده است - یکی برای گل‌های هر تیم. یک بار
بیندازید، یک نتیجهٔ ممکن می‌گیرید. ما به کامپیوتر می‌گوییم این تاس‌ها را برای <em>کلِ
جام جهانی</em> بیندازد، تا خود فینال… و بعد کل تورنمنت را دوباره از اول، صدهزار بار.
اگر آرژانتین در ۱۹هزار تا از آن جام‌های خیالی قهرمان شود، می‌گوییم: ۱۹٪.</p>
<p>یک ترفند دیگر: هزاران نفر روی همین بازی‌ها پول واقعی شرط می‌بندند و قیمت‌هایشان بهترین
حدسِ جمعیت است. ما کمی هم به جمعیت گوش می‌دهیم، چون جمعیت خبرها - مصدومیت، ترکیب - را
زودتر از هر آمارِ گلی می‌شنود.</p>
<p>و قانون صداقت: همهٔ پیش‌بینی‌ها را <b>قبل از</b> شروع تورنمنت نوشتیم، صفحه را قفل کردیم
و با رسیدن نتایج واقعی، جلوی چشم همه به خودمان نمره می‌دهیم. نه پاک‌کردنی در کار است نه
«از اول می‌دانستیم». درصدْ قولی است دربارهٔ این‌که <em>چند وقت یک‌بار</em> باید درست
بگوییم - وقتی مدل می‌گوید ۷۰٪، یعنی باید از هر ۱۰ بار حدوداً ۳ بار هم اشتباه کند، و آن
اشتباه‌ها را هم می‌شماریم.</p>

<h2>داده‌ها</h2>
<p>سه منبع داده داریم. مهم‌ترین آن‌ها مجموعهٔ متن‌باز martj42 است که ۴۹٬۴۵۰ بازی ملی از سال
۱۸۷۲ را در بر می‌گیرد. مدل روی ۸٬۰۸۱ بازیِ برگزارشده از سال ۲۰۱۸ به بعد آموزش می‌بیند.
فرم فعلی تیم‌ها و برنامهٔ مسابقات از API-Football می‌آیند و قیمت‌های لحظه‌ای بازار از
API عمومی پالی‌مارکت دریافت می‌شوند.</p>
<p>داده‌های آموزشی پیش از استفاده با منابع اصلی تطبیق داده شدند. در این فرایند چهار نتیجهٔ
اشتباه پیدا شد و اصلاح شد. فایل این اصلاحات نیز در مخزن پروژه موجود است.</p>

<h2>مدل بازی</h2>
<p>هستهٔ سیستم یک مدل پواسونِ وزن‌دار دیکسون–کولز است؛ همان مدلی که سال‌هاست در صنعت
شرط‌بندی و پیش‌بینی فوتبال استفاده می‌شود.</p>
<p>برای هر تیم دو عدد تخمین زده می‌شود: قدرت حمله و قدرت دفاع. این اعداد از روی کیفیت
حریفان و تعداد گل‌هایی که تیم مقابل آن‌ها زده یا خورده به دست می‌آیند. بازی‌های جدیدتر
اهمیت بیشتری دارند (نیمه‌عمر ۱۰۰۰ روز)، مسابقات دوستانه وزن کمتری می‌گیرند (×۰٫۸)،
و تیم‌هایی که دادهٔ کمی دارند بیش از حد از میانگین دور نمی‌شوند.</p>
<p>همچنین یک اصلاح برای نتایج کم‌گل (ρ=−۰٫۰۵) در نظر گرفته می‌شود، چون فوتبال واقعی بیشتر
از چیزی که دو پواسون مستقل پیش‌بینی می‌کنند به نتایجی مثل ۰-۰ و ۱-۱ ختم می‌شود. تیم‌های
میزبان نیز زمانی که در کشور خودشان بازی می‌کنند از مزیت زمین خانگی بهره می‌برند که
تقریباً معادل ۳۰ درصد افزایش در نرخ گل‌زنی است.</p>
<p>از روی دو مقدار «گل انتظاری»، مدل شبکهٔ کامل احتمال تمام نتایج ممکن را می‌سازد. تمام
بازارهای هر مسابقه مستقیماً از همین شبکه محاسبه می‌شوند و در این مرحله هیچ شبیه‌سازی‌ای
انجام نمی‌شود.</p>
<p>یک ورودی تازه هم از ۱۰ ژوئن (پیش از شروع بازی‌ها) اضافه شد: «پیشینِ ارزش ترکیب».
امتیاز برازش‌شدهٔ هر تیم به اندازهٔ β×z(لگاریتم ارزش بازاری ترکیب، از Transfermarkt)
جابه‌جا می‌شود - یعنی استعدادِ روی کاغذ، نه فقط نتایجِ روی چمن. مقدار β=۰٫۳۵ با
اعتبارسنجی خارج از نمونه انتخاب شد؛ این پیشین لگ‌لاس را از ۰٫۸۵۴ به ۰٫۸۱۸ بهبود داد -
بزرگ‌ترین ارتقای این پروژه. اثر اصلی آن در مراحل حذفی است که بازاری برای ترکیب‌شدن
ندارند: همان آگاهی از بازیکنان که مرحلهٔ گروهی از طریق بازار داشت.</p>
<figure>{inline_svg("grid")}
<figcaption>شبکهٔ واقعی برازش‌شده برای بازی افتتاحیه (احتمال‌ها به درصد). برد مکزیک =
خانه‌های سبز؛ تساوی = قطر کهربایی؛ برد آفریقای جنوبی = خانه‌های قرمز.</figcaption></figure>
<p class="fineprint">ابرپارامترها به‌صورت دستی انتخاب نشده‌اند. آن‌ها با اعتبارسنجی متقابل
روی چهار پنجرهٔ غلتان ۱۲ ماهه و ۴٬۲۲۸ بازی خارج از نمونه انتخاب شده‌اند. لگ‌لاس
اعتبارسنجی ۰٫۸۹۱۲۷ بود، در حالی که حدسِ همیشگی بر اساس نرخ پایه به ۱٫۰۴۶ می‌رسید
(عدد کمتر بهتر است). دو ایده نیز در آزمایش‌ها رد شدند: سقف‌گذاری روی بردهای پرگل و
استفاده از حافظهٔ کوتاه‌تر.</p>

<h2>ترکیب با بازار</h2>
<p>برای پیش‌بینی مسابقات مرحلهٔ گروهی، احتمال‌های مدل با قیمت‌های لحظه‌ای پالی‌مارکت
ترکیب می‌شوند (میانگین‌گیری لگاریتمی با وزن ۳۵ درصد برای بازار).</p>
<p>دلیل این کار ساده است: بازار معمولاً ساعت‌ها زودتر از آن‌که نتایج و داده‌های گل اثرشان
را نشان دهند، از ترکیب تیم‌ها، مصدومیت‌ها و اخبار باخبر می‌شود. در عین حال نسخهٔ خام مدل
نیز جداگانه نگه داشته می‌شود تا بتوان اختلاف آن با بازار را بررسی کرد و فرصت‌های
ارزش‌گذاری را پیدا کرد.</p>
<p>عملکرد مدل، بازار و نسخهٔ ترکیبی نیز به‌صورت جداگانه و با نتایج واقعی در
<a href="bracket.html">کارنامه</a> سنجیده می‌شود.</p>

<h2>شبیه‌سازی تورنمنت</h2>
<p>در هر اجرا، ۱۰۰٬۰۰۰ تورنمنت کامل ــ معادل حدود ۱۰ میلیون مسابقهٔ شبیه‌سازی‌شده ــ روی
براکت رسمی فیفا اجرا می‌شود. این شبیه‌سازی قالب دور ۳۲ تیمی و تمام محدودیت‌های مربوط به
جایگذاری تیم‌های سوم را نیز در نظر می‌گیرد.</p>
<p>هر تورنمنتِ شبیه‌سازی‌شده یکی از ۲۰۰ مدلِ بازنمونه‌گیری‌شده (Bootstrap) را انتخاب
می‌کند تا عدم‌قطعیت پارامترها نیز در احتمال‌ها منعکس شود. علاوه بر این، چند منبع تصادفی
با میانگین صفر وارد شبیه‌سازی می‌شوند: تغییر فرم تیم‌ها در طول تورنمنت (مصدومیت،
هماهنگی و عوامل مشابه ــ σ=۰٫۰۶)، فرسایش مراحل حذفی (۱۰ درصد احتمال آسیب ماندگار در هر
مسابقهٔ حذفی) و جریمهٔ خستگی پس از بازی‌های ۱۲۰ دقیقه‌ای.</p>
<p>هیچ‌کدام از این عوامل در میانگین به نفع تیم خاصی نیستند؛ اما در عمل معمولاً کمی به سود
تیم‌های کوچک‌تر تمام می‌شوند، چون اتفاقات غیرمنتظره معمولاً بخشی از برتری مدعیان را از
بین می‌برند.</p>

<h2>کمی علم: مفاهیم و اصطلاح‌ها</h2>

<h3>چرا پواسون؟</h3>
<p>گل یک رخداد نسبتاً کمیاب است: به‌طور متوسط حدود ۲٫۷ گل در هر ۹۰ دقیقه و در هر دقیقه با
احتمالی کوچک و تقریباً مستقل.</p>
<p>شمارش چنین رخدادهایی به
<a href="https://fa.wikipedia.org/wiki/توزیع_پواسون">توزیع پواسون</a> منجر می‌شود. در این
توزیع، یک عدد یعنی نرخ λ («گل انتظاری») برای تعیین کامل احتمال ۰، ۱، ۲ و تعداد بیشتری گل
کافی است. در نتیجه کل مسئله به تخمین دو نرخ برای هر مسابقه تقلیل پیدا می‌کند؛ نرخ‌هایی
که از یک مدل لگ-خطی به دست می‌آیند:</p>
<p>لگاریتم نرخ = پایه + قدرت حملهٔ تیم + ضعف دفاع حریف (+ مزیت میزبانی)</p>
<figure>{inline_svg("poisson")}
<figcaption>چرا این فرض درست است: توزیع گلِ هر تیم در هر بازی در ۸٬۰۸۱ مسابقهٔ آموزشی
(ستون‌ها) در برابر منحنی پواسون با همان میانگین (نقطه‌ها).</figcaption></figure>

<h3>تصحیح دیکسون–کولز</h3>
<p>دو پواسون مستقل در یک بخش مهم از واقعیت دچار خطا می‌شوند. فوتبال واقعی بیشتر از چیزی که
استقلال آماری پیش‌بینی می‌کند نتایج ۰-۰ و ۱-۱ تولید می‌کند، چون تیم‌ها گاهی بازی را
کنترل می‌کنند و ریسک را کاهش می‌دهند.</p>
<p>دیکسون و کولز در سال ۱۹۹۷ دقیقاً همین بخش کم‌گل جدول احتمالات را با یک پارامتر همبستگی
ρ اصلاح کردند. با وجود گذشت نزدیک به سه دهه، این خانواده از مدل‌ها هنوز یکی از پایه‌های
اصلی محاسبهٔ ضرایب حرفه‌ای فوتبال است.</p>

<h3>برازش: درست‌نمایی بیشینهٔ وزن‌دار</h3>
<p><a href="https://fa.wikipedia.org/wiki/برآورد_درست‌نمایی_بیشینه">درست‌نمایی بیشینه</a>
به این سؤال پاسخ می‌دهد: چه مجموعه‌ای از پارامترها نتایج مشاهده‌شده را کمترین میزان
غافلگیرکننده می‌کند؟</p>
<p>هر مسابقه با وزن خاص خود وارد مدل می‌شود: افت زمانی نمایی (هر نتیجه پس از ۱٬۰۰۰ روز نیمی
از اثر خود را از دست می‌دهد) و ضریب ×۰٫۸ برای مسابقات دوستانه.</p>
<figure>{inline_svg("decay")}
<figcaption>سهم هر نتیجه در برازش بر حسب قدمت آن - با نیمه‌عمر ۱٬۰۰۰ روزه، نتایج قدیمی
زمزمه می‌کنند و فرم اخیر بلند حرف می‌زند.</figcaption></figure>
<p>انقباض (Shrinkage) نیز برای هر تیم چند مسابقهٔ خیالیِ «متوسط» اضافه می‌کند تا تیم‌هایی
که دادهٔ کمی دارند بیش از حد از میانگین فاصله نگیرند. این همان مصالحهٔ کلاسیک بین اریب و
واریانس است: یک برآورد کمی اریب معمولاً از یک برآورد پرنوسان بهتر عمل می‌کند.</p>

<h3>اعتبارسنجی صادقانه و قواعد نمره‌دهی</h3>
<p>مدلی که روی داده‌های آموزشی خودش ارزیابی شود، در واقع دارد خودش را فریب می‌دهد.</p>
<p>به همین دلیل ارزیابی خارج از نمونه
(<a href="https://fa.wikipedia.org/wiki/اعتبارسنجی_متقابل">اعتبارسنجی متقابل</a>)
انجام می‌شود: تا یک تاریخ مشخص مدل برازش می‌شود،
۱۲ ماه بعد پیش‌بینی می‌کند و این فرایند روی چهار پنجره تکرار می‌شود.</p>
<p>کیفیت پیش‌بینی‌ها با قواعد نمره‌دهی سَره سنجیده می‌شود؛ یعنی معیارهایی که فقط در صورت
بیان صادقانهٔ باور واقعی بهینه می‌شوند. لگ‌لاس (منفی لگاریتم احتمالی که به رخداد واقعی
داده شده) با پیش‌بینی‌های بیش‌ازحد مطمئن بسیار سخت‌گیرانه برخورد می‌کند. نمرهٔ برایر نیز
میانگین مربع خطای احتمال‌ها را اندازه می‌گیرد.</p>
<p>درصد «پیش‌بینی درست» یک معیار سَره نیست، چون با انتخاب همیشگی فاوریت می‌توان آن را
دستکاری کرد. به همین دلیل کارنامهٔ مدل با برایر و لگ‌لاس شروع می‌شود.</p>
<figure>{inline_svg("calibration")}
<figcaption>کالیبراسیون روی سالِ خارج از نمونه: هر نقطه یک دستهٔ احتمال است؛ روی قطرِ
خط‌چین، اطمینانِ ادعاشده با واقعیتِ مشاهده‌شده برابر است.</figcaption></figure>

<h3>مونت‌کارلو و قانون اعداد بزرگ</h3>
<p>برای یک تورنمنت راه‌حل بسته و فرمولی وجود ندارد. قوانین تساوی گروه‌ها، جایگذاری تیم‌های
سوم و مسیر براکت بیش از حد پیچیده و درهم‌تنیده‌اند.</p>
<p><a href="https://fa.wikipedia.org/wiki/روش_مونت-کارلو">روش مونت‌کارلو</a>
این مشکل را دور می‌زند: کل تورنمنت را ۱۰۰٬۰۰۰ بار شبیه‌سازی کن و نتایج را بشمار.</p>
<p><a href="https://fa.wikipedia.org/wiki/قانون_اعداد_بزرگ">قانون اعداد بزرگ</a>
تضمین می‌کند که این شمارش‌ها به احتمال واقعی مدل همگرا شوند. خطای
استاندارد برآورد شانس قهرمانی در این مقیاس حدود ±۰٫۱۲ واحد درصد است و در عمل نیز تأیید
شده است: دو اجرای مستقل برای همهٔ مدعیان کمتر از ۰٫۲ واحد درصد اختلاف داشتند.</p>

<h3>بوت‌استرپ: دانستنِ آنچه نمی‌دانیم</h3>
<p>یک مدلِ برازش‌شده ممکن است این تصور را ایجاد کند که پارامترهایش دقیق و قطعی‌اند. در
واقع این‌طور نیست؛ این پارامترها فقط برآوردهایی هستند که از داده‌ای محدود به دست
آمده‌اند.</p>
<p>بوت‌استرپ دقیقاً برای اندازه‌گیری همین عدم‌قطعیت به کار می‌رود: وزن مسابقات به‌صورت
تصادفی تغییر می‌کند، مدل دوباره برازش می‌شود و این فرایند ۲۰۰ بار تکرار می‌شود.</p>
<p>پراکندگی این ۲۰۰ مدل همان عدم‌قطعیت پارامترهاست و هر تورنمنتِ شبیه‌سازی‌شده یکی از
آن‌ها را انتخاب می‌کند. نتیجه این است که بخشی از اعتمادبه‌نفس بیش‌ازحد مدعیان حذف می‌شود
و احتمال‌ها واقع‌بینانه‌تر می‌شوند.</p>

<h3>ترکیب پیش‌بینی‌گرها: میانگین لگاریتمی</h3>
<p>مدل و بازار اطلاعات متفاوتی دارند. یکی تاریخچهٔ گل‌ها را می‌بیند و دیگری ترکیب‌ها،
مصدومیت‌ها و اخبار را.</p>
<p>میانگین لگاریتمی احتمال‌ها ــ یعنی ضرب احتمال‌ها با توان‌های وزنی و سپس نرمال‌سازی ــ
روش استاندارد ترکیب این منابع اطلاعاتی است.</p>
<p>وزن ۰٫۳۵ برای بازار نیز یک انتخاب شفاف و آزمون‌پذیر است. عملکرد هر سه نسخه ــ مدل،
بازار و ترکیب ــ جداگانه ثبت می‌شود تا خود داده‌ها نشان دهند این وزن انتخاب مناسبی بوده
است یا نه.</p>

<h2>پاسخ‌گویی</h2>
<p>براکت کامل ــ شامل هر ۷۲ مسابقهٔ مرحلهٔ گروهی و تمام مراحل حذفی تا تعیین قهرمان ــ پیش
از آغاز تورنمنت قفل می‌شود و با ثبت نتایج واقعی به‌صورت عمومی در
<a href="bracket.html">کارنامه</a> ارزیابی می‌شود.</p>
<p>هر اجرای محاسبات با مهر زمانی آرشیو می‌شود و نسخه‌های روزانهٔ سایت در بخش
<a href="archive.html">نسخه‌های قبلی</a> منجمد باقی می‌مانند.</p>
<p>پیش‌بینی‌ای که بتوان آن را بعداً تغییر داد، پیش‌بینی نیست.</p>

<h2>یک مدل، بازارهای بسیار</h2>
<p>بیشتر بازارهای روی کارت هر مسابقه مدل‌های جداگانه نیستند؛ پرسش‌های متفاوتی هستند از
<em>همان</em> شبکهٔ احتمال نتایج. مجموع گل هر تیم، جمع سطرها و ستون‌های همان شبکه است.
خطوط بالا/پایین بیشتر فقط جمع سلول‌های بیشترند. «اولین گل» از یک استدلال یک‌خطی مسابقه‌ای
به دست می‌آید: اگر دو تیم به‌طور مستقل موقعیت بسازند، گل اول به نسبت نرخ گلزنی به یکی از
دو تیم می‌رسد. نتیجهٔ نیمهٔ اول و دوم هم هر نیمه را یک مسابقهٔ کوتاه‌تر در نظر می‌گیرد:
گل انتظاری هر دو تیم در سهم گل‌های پیش از استراحت ضرب می‌شود ــ <b>۰٫۴۴۷</b>، برازش‌شده
روی ۲٬۳۶۰ گل از شش تورنمنت ملی اخیر (نیمهٔ اول همه‌جا کم‌گل‌تر است، در جام‌های جهانی
بیش از همه) ــ و همان ماشین قبلی دوباره به کار می‌افتد. یک پارامتر، دوازده بازار را باز
کرد و قیمت‌های زندهٔ نیمهٔ اول از روز اول تا چند سنت با مدل هم‌خوان بودند.</p>
<p>کرنرها استثنا هستند: داده‌های گل دربارهٔ آن‌ها چیزی نمی‌گویند، پس مدل خودشان را دارند ــ
یک دوجمله‌ای منفی برازش‌شده روی ۲۵۰ مسابقه از پنج تورنمنت اخیر. یافتهٔ صادقانه: قدرت
<em>گلزنی</em> یک تیم، تعداد کرنرهایش را خارج از نمونه پیش‌بینی نمی‌کند؛ بنابراین مدل
عرضه‌شده یک نرخ پایهٔ خالص است (حدود ۹٫۲ کرنر در هر بازی). هرجا دفتر کرنر واقعاً معامله
می‌شود، مدل تقریباً دقیقاً با آن هم‌نظر است؛ تنها کارش نشان‌کردن خطوط معامله‌نشده‌ای است
که از واقعیت دور افتاده‌اند.</p>

<h2 id="second-opinion">نظر دوم</h2>
<p>پشت هر مسابقه یک <b>مدل همراه الو</b> ایستاده است: ریتینگ الو حساس به اختلاف گل،
برازش‌شده روی همان تاریخچهٔ مسابقات، که با یک رگرسیون پواسون به نرخ گل و سپس به همان شبکهٔ
نتایج تبدیل می‌شود. دلیل وجودش این است که ۲۰۰ عضو مدل اصلی داده را تغییر می‌دهند اما همه یک
نظریه دارند ــ تنها محکِ خودِ نظریه، مدلی با ساختار متفاوت است. هر دو مدل امتحان یکسانی
دادند (آموزش پیش از ژوئن ۲۰۲۵، نمره روی ۱٬۰۷۱ مسابقهٔ بعدی): دیکسون-کولز ۰٫۸۱۹،
الو ۰٫۸۵۸، و ترکیب ۵۰/۵۰ آن‌ها ۰٫۸۳۱ ــ بدتر از دیکسون-کولز به‌تنهایی؛ پس ترکیب
<b>رد شد</b> و اعداد الو وارد هیچ عددی که در سایت می‌بینید نمی‌شوند. آنچه می‌ماند
اختلاف‌نظر است: دو مدل روی یک مسابقهٔ معمولی حدود ۸ واحد فاصله دارند، پس صفحهٔ هر مسابقه
فقط یک خط هشدار دارد وقتی فاصله غیرعادی باز است ــ نشانه‌ای که به آن مسابقه کمتر اعتماد
(و کمتر شرط) کنید. خودِ احتمال‌های الو عمداً روی صفحهٔ مسابقه‌ها نمی‌آیند: الو مدل ضعیف‌تر
در همان امتحان است و نمایش اعدادش کنار اعداد واقعی فقط خواننده را وسوسه می‌کند که با مدلی
که نشان داده‌ایم پیش‌بینی را بدتر می‌کند میانگین بگیرد. ریتینگ‌های کنونی الو، بالای جدول،
تا ببینید چه فکر می‌کند: {elo_ratings}.</p>

<h2>تحلیلگر هوش مصنوعی</h2>
<p>بخش‌های کوتاه تحلیلی که در انتهای هر صفحهٔ مسابقه، تیم و بازیکن می‌آیند، نوشتهٔ یک مدل
زبانی هستند (امضای پایین هر بخش می‌گوید کدام مدل و چه زمانی). دو قانون آن‌ها را صادق نگه
می‌دارد. اول، <b>قرارداد استناد</b>: مدل فقط یک پروندهٔ منجمد از منابع را می‌بیند ــ
خلاصه‌های دانش‌نامه‌ای که یک بار دریافت شده‌اند، به‌علاوهٔ داده‌های فرم و فهرست بازیکنان
خودمان ــ و احتمال‌هایی که همین سایت محاسبه می‌کند. ورودی یکسان، خروجی تکرارپذیر، بدون
واقعیت بیرونی. دوم، <b>تفکیک وظایف</b>: این متن رنگ است، نه محاسبه. هیچ‌چیز از نوشتهٔ
تحلیلگر به مدل گل، شبیه‌سازی‌ها یا احتمال‌های منتشرشده راه ندارد ــ اگر این کلمات نبودند،
اعداد همین بودند. مرور پس از مسابقه هم مدل و بازار را با قیمت‌های پیش از بازی می‌سنجد که برای
کارنامهٔ عمومی نگه داشته شده‌اند؛ پس هوش مصنوعی نمی‌تواند با عقل پس از واقعه از کسی
تعریف کند. (یک پانوشت صادقانه: از ۱۳ ژوئن یک دستیار جداگانهٔ بررسی اخبار می‌تواند پیش از
ثبت شرط، اخبار مصدومیت و ترکیب را بخواند ــ اما ساختارش فقط کاهنده است. می‌تواند شرطی را
حذف یا کوچک کند؛ هرگز نمی‌تواند شرطی اضافه کند، مبلغی را بالا ببرد، یا به عددی در این
سایت دست بزند.)</p>

<h2>سیر تکامل روش</h2>
<p class="fineprint">خلاصهٔ دفترچهٔ آزمایشگاه (یادداشت‌های کامل در مخزن کد موجود است). هر تغییر
پیش از انتشار روی داده‌های دیده‌نشده اعتبارسنجی شد ــ و دو مورد هم دقیقاً در همین آزمون رد شدند.</p>
<ol class="timeline">
<li><b>۹ ژوئن</b> ــ انتخاب پواسون وزن‌دار دیکسون-کولز به‌جای پواسون ساده (تساوی‌ها را اشتباه
قیمت می‌گذارد)، ترکیب‌های Elo (دقت مشابه با پیچیدگی بیشتر) و یادگیری ماشین (در فوتبال ملی
دادهٔ کافی ندارد). برازش روی ۸٬۰۸۱ بازی ملی از ۲۰۱۸: log-loss برابر ۰٫۸۹۷ در برابر خط
پایهٔ ۱٫۰۴۶.</li>
<li><b>۹ ژوئن، عصر</b> ــ تنظیم ابرپارامترها با اعتبارسنجی متقاطع غلتان. حافظهٔ بلندتر برنده شد
(نیمه‌عمر از ۵۴۸ به ۱۰۰۰ روز): تیم‌های ملی کندتر از باشگاه‌ها تغییر می‌کنند. بهبود log-loss
از ۰٫۸۹۷ به ۰٫۸۵۴.</li>
<li><b>۹ ژوئن، شب</b> ــ صریح‌کردن عدم‌قطعیت: آنسامبل بوت‌استرپ ۲۰۰ مدلی برای ۱۰۰٬۰۰۰
تورنمنت شبیه‌سازی‌شده روی براکت رسمی، به‌علاوهٔ ترکیب با قیمت‌های بازار (وزن ۰٫۳۵) در
مسابقاتی که پلی‌مارکت قیمت دارد. براکت کامل <b>قفل شد</b> و کارنامهٔ عمومی آغاز شد.</li>
<li><b>۱۰ ژوئن</b> ــ واریانس ناهنجاری با میانگین صفر در شبیه‌سازی‌ها: شوک فرم هر تورنمنت،
فرسایش مرحلهٔ حذفی، خستگی وقت اضافه. به‌طور متوسط به هیچ تیمی کمک نمی‌شود، اما بخت‌های اول
بخشی از احتمال خود را به بقیه پس می‌دهند (آرژانتین از ۱۹٫۰٪ به ۱۶٫۶٪).</li>
<li><b>۱۰ ژوئن</b> ــ پیشینِ ارزش ترکیب: ارزش بازار بازیکنان (ترانسفرمارکت) امتیازهای
برازش‌شده را کمی جابه‌جا می‌کند (β=۰٫۳۵). بهبود log-loss از ۰٫۸۵۴ به ۰٫۸۱۸ ــ بزرگ‌ترین
ارتقای پروژه.</li>
<li><b>۱۱ ژوئن</b> ــ ممیزی کالیبراسیون نتیجهٔ دقیق؛ نتیجهٔ منفیِ محبوب ما: یک اصلاح
وسوسه‌انگیز (تساوی‌های پرگل در یک پنجرهٔ آزمون ۲۸٪ ارزان‌قیمت به نظر می‌رسیدند) در
اعتبارسنجی روی سه پنجرهٔ قدیمی‌تر رد شد و <b>عرضه نشد</b>. شبکهٔ نمرات از قبل کالیبره بود؛
مقاومت در برابر «اصلاح» هم بخشی از روش است.</li>
<li><b>۱۱ ژوئن</b> ــ پوشش کامل بازار: دفترهای نتیجهٔ دقیق، مجموع گل، گلزنی هر دو تیم و
هندیکپ پلی‌مارکت متصل شدند تا هر احتمالی که مدل می‌سازد کنار قیمت زنده و اختلافش بنشیند.</li>
<li><b>۱۲ ژوئن</b> ــ یک مدل، بازارهای بسیار: مجموع گل تیمی، خطوط گل بیشتر و «اولین گل»
از همان شبکهٔ موجود؛ بازارهای نیمه با سهم برازش‌شدهٔ گل‌های نیمهٔ اول (۰٫۴۴۷ روی ۲٬۳۶۰
گل)؛ بازارهای آیندهٔ تورنمنت در برابر آنسامبل قیمت‌گذاری شدند. کرنرها دومین ایدهٔ
ردشده را آوردند: قدرت گلزنی نتوانست تعداد کرنر را خارج از نمونه پیش‌بینی کند، پس مدلشان
آگاهانه یک نرخ پایه است.</li>
<li><b>۱۲ ژوئن، بعدتر</b> ــ یک بازبینی بیرونی کد دو باگ واقعی یافت (آلودگی در ابزار
اعتبارسنجی پیشینِ ارزش ترکیب ــ اجرای پاکیزه دقیقاً همان اعداد منتشرشده را بازتولید کرد ــ
و تصادفی‌بودن بدون بذر در موتور تورنمنت که حالا قطعی است) و دو دفاع به همراه آورد:
محافظت قیمت در لحظهٔ اجرا برای شرط‌ها و دفتر شرط‌بندی کاغذی که هر فرصت یافت‌شده را با
ارزش خط پایانی می‌سنجد. پیش‌بینی‌ها تغییری نکردند؛ اعتماد به آن‌ها بهتر کسب شد.</li>
<li><b>۱۳ ژوئن</b> ــ نظر دوم: یک مدل همراه الو→گل ساخته شد و همان امتحان را داد. حکم روی
همان ۱٬۰۷۱ مسابقهٔ کنارگذاشته: دیکسون-کولز ۰٫۸۱۹، الو ۰٫۸۵۸، ترکیب ۵۰/۵۰ آن‌ها ۰٫۸۳۱ ــ
<em>بدتر از دیکسون-کولز به‌تنهایی</em>؛ پس ترکیب رد شد و الو هرگز به اعداد منتشرشده راه
ندارد. فقط به‌عنوان نظر دوم نمایشی روی صفحهٔ هر مسابقه می‌نشیند و اختلافش با مدل اصلی تنها
یک سیم هشدار است.</li>
<li><b>ادامه‌دار</b> ــ هر شب نتایج واقعی دریافت می‌شود، براکت قفل‌شده به‌صورت عمومی ارزیابی
می‌شود، مدل روی تازه‌ترین بازی‌ها دوباره برازش می‌شود و نسخهٔ آن روزِ سایت منجمد می‌شود.</li>
</ol>

{evolution_section("fa")}

<h2>نقاط کور شناخته‌شده</h2>
<p class="fineprint">ترکیب بازیکنان، مصدومیت‌ها و محرومیت‌ها مستقیماً در مدل حضور ندارند؛
این اطلاعات از طریق بازار وارد می‌شوند. آب‌وهوا و ارتفاع زمین ضریب مستقلی ندارند، چون با
داده‌های موجود قابل برآورد نیستند و فقط به‌عنوان هشدار در نظر گرفته می‌شوند. شبیه‌سازی
درون‌بازی انجام نمی‌شود و فقط نتیجهٔ نهایی مدل‌سازی می‌شود. اندازهٔ شوک‌های تصادفی
فرض‌هایی اعلام‌شده هستند، نه پارامترهایی که از داده برآورد شده باشند. همچنین انگیزهٔ
تیم‌ها در مسابقات کم‌اهمیت هفتهٔ سوم مرحلهٔ گروهی با بازبینی دستی مدیریت می‌شود؛ تقریباً
همان روشی که بسیاری از محاسبه‌گرهای حرفه‌ای ضرایب از آن استفاده می‌کنند.</p>
<p>این مدل قرار نیست آینده را پیشگویی کند. هدفش ارائهٔ یک مبنای منصفانه و سازگار برای
ارزش‌گذاری احتمالات است.</p>'''
    (OUT / "method-fa.html").write_text(
        page("روش‌شناسی", body, lang="fa", rtl=True,
             alt_lang=("method.html", "English")))


# ---------- version archive ----------
def build_archive_index():
    arch = OUT / "archive"
    snaps = sorted([d.name for d in arch.iterdir() if d.is_dir()],
                   reverse=True) if arch.exists() else []
    if snaps:
        rows = "".join(
            f'<tr><td class="num">{len(snaps) - i}</td>'
            f'<td><a href="archive/{s}/index.html">{s}</a></td></tr>'
            for i, s in enumerate(snaps))
        table = f"""<table class="ko" style="max-width:420px">
<thead><tr><th class="num">#</th><th>Snapshot</th></tr></thead>
<tbody>{rows}</tbody></table>"""
    else:
        table = '<p class="standfirst">No snapshots yet - the first will be taken before kickoff.</p>'
    body = f"""<h1>Previous versions</h1>
<p class="standfirst">The model recalculates after every matchday, but predictions shouldn't
vanish when they age - each snapshot below is the complete site, frozen as it stood that day.
Compare any past view against what actually happened.</p>
<p class="fineprint">Snapshots are immutable copies (every page and stylesheet). The live site
always reflects the latest data; the locked bracket on the live site never changes by design -
what changes between versions are the simulations, prices, futures and award odds.</p>
{table}"""
    (OUT / "archive.html").write_text(page("Versions", body))


def take_snapshot():
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dst = OUT / "archive" / stamp
    k = 2
    while dst.exists():          # same-day re-snapshots get a suffix -
        dst = OUT / "archive" / f"{stamp}-{k}"   # never overwrite history
        k += 1
    stamp = dst.name
    dst.mkdir(parents=True)
    for item in OUT.iterdir():
        if item.name == "archive":
            continue
        if item.is_dir():
            shutil.copytree(item, dst / item.name)
        else:
            shutil.copy(item, dst / item.name)
    # banner on every archived page, linking back to the live site
    for f in dst.rglob("*.html"):
        depth = len(f.relative_to(dst).parts) - 1
        up = "../" * (2 + depth)
        banner = (f'<div class="snapnote">Snapshot of {stamp} - predictions as they '
                  f'stood then. <a href="{up}index.html">Back to the latest →</a></div>')
        f.write_text(f.read_text().replace("<body>", f"<body>\n{banner}", 1))
    print(f"snapshot frozen: docs/archive/{stamp}/")


CSS = """/* WC26 Form Book - editorial racing-form aesthetic */
:root {
  --paper: #f6f1e6;
  --paper-2: #efe8d8;
  --ink: #211d16;
  --ink-soft: #6b6353;
  --rule: #cfc4ab;
  --green: #14633f;
  --red: #a72a1e;
  --amber: #856a1d;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  background-image: repeating-linear-gradient(0deg, transparent 0 2px, rgba(33,29,22,.012) 2px 4px);
  color: var(--ink);
  font: 15px/1.5 "IBM Plex Mono", monospace;
}
main { max-width: 1080px; margin: 0 auto; padding: 0 24px 48px; }
h1, h2, .wordmark, .versus h1 { font-family: "Fraunces", serif; }
h1 { font-size: 2.6rem; font-weight: 900; letter-spacing: -.02em; margin: 1.2rem 0 .4rem; }
h2 { font-size: 1.25rem; font-weight: 600; border-bottom: 2px solid var(--ink); padding-bottom: .25rem; margin: 2rem 0 .8rem; }
a { color: var(--green); text-decoration: none; }
a:hover { text-decoration: underline; text-underline-offset: 3px; }

/* method-page evolution timeline */
ol.timeline { list-style: none; margin: 1.2rem 0; padding: 0; }
ol.timeline li {
  position: relative; padding-inline-start: 22px; margin: 0;
  padding-bottom: .9rem; border-inline-start: 2px solid var(--rule);
}
ol.timeline li:last-child { border-inline-start-color: transparent; }
ol.timeline li::before {
  content: ""; position: absolute; inset-inline-start: -6px; top: .42em;
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--green); border: 2px solid var(--paper);
}
ol.timeline b { font-family: "Fraunces", serif; }

/* Elo second opinion */
.secondop{max-width:680px;margin:6px auto}
.elo-disagree{color:#ffc94d}
/* plain-language glossary — term/definition grid that fills the column */
dl.glossary{display:grid;grid-template-columns:minmax(9rem,15rem) 1fr;
  column-gap:1.8rem;row-gap:.7rem;align-items:baseline;margin:0 0 1.6rem}
dl.glossary dt{font-weight:600;margin:0}
dl.glossary dd{margin:0;color:var(--ink-soft)}
@media (max-width:640px){
  dl.glossary{grid-template-columns:1fr;row-gap:0}
  dl.glossary dt{margin-top:1rem}
  dl.glossary dd{margin-top:.15rem}}
/* cookie consent (Google Consent Mode v2) */
#cookie-consent{position:fixed;right:1rem;bottom:1rem;z-index:50;
  max-width:min(340px,calc(100vw - 2rem));background:var(--paper-2);
  border:1px solid var(--rule);border-radius:10px;padding:.85rem 1.1rem;
  box-shadow:0 6px 24px rgba(0,0,0,.25);font-size:.88rem}
#cookie-consent p{margin:0 0 .6rem}
#cookie-consent .cc-btns{display:flex;gap:.5rem;justify-content:flex-end}
#cookie-consent button{font:inherit;cursor:pointer;border-radius:7px;
  padding:.35rem .9rem;border:1px solid var(--rule)}
#cookie-consent .cc-no{background:transparent;color:var(--ink-soft)}
#cookie-consent .cc-yes{background:var(--ink);color:var(--paper);
  border-color:var(--ink)}
/* AI analyst sections */
section.llm {
  max-width: 640px; margin: 2rem auto 0; padding: 1rem 1.3rem;
  background: var(--paper-2); border: 1px solid var(--rule);
}
section.llm h2 { margin-top: 0; border-bottom-color: var(--rule); }
section.llm .llmnote { border-left-color: var(--amber); margin-bottom: 0; }

/* market-table section dividers */
table.markets tr.subhead td {
  font-family: "Fraunces", serif; font-weight: 600; font-size: .82rem;
  letter-spacing: .06em; text-transform: uppercase; color: var(--ink-soft);
  border-bottom: 1px solid var(--rule); padding-top: .9em;
}

/* masthead */
.masthead {
  text-align: center; padding: 26px 16px 14px;
  border-bottom: 3px double var(--ink); margin-bottom: 8px;
}
.kicker { font-size: .72rem; letter-spacing: .28em; text-transform: uppercase; color: var(--ink-soft); }
.wordmark {
  font-size: clamp(2.4rem, 6vw, 3.6rem); font-weight: 900; color: var(--ink);
  display: inline-block; line-height: 1; margin: 6px 0 10px; letter-spacing: -.03em;
}
.wordmark:hover { text-decoration: none; color: var(--green); }
.masthead nav { display: flex; flex-wrap: wrap; justify-content: center;
  align-items: baseline; row-gap: .25rem;
  font-size: .85rem; text-transform: uppercase; letter-spacing: .18em; }
.masthead nav span { color: var(--rule); margin: 0 10px; }
.crumb { max-width: 1080px; margin: 10px auto 0; padding: 0 24px; font-size: .78rem; color: var(--ink-soft); }
.standfirst { color: var(--ink-soft); margin-top: 0; }

/* tables */
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
th { font-size: .68rem; text-transform: uppercase; letter-spacing: .12em; color: var(--ink-soft); font-weight: 500; text-align: left; }
td, th { padding: .45rem .6rem .45rem 0; border-bottom: 1px solid var(--rule); vertical-align: baseline; }
tbody tr:hover { background: var(--paper-2); }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.num { color: var(--ink-soft); }
.score { color: var(--ink) !important; font-weight: 600; }
.comp, .venue { color: var(--ink-soft); font-size: .78rem; }

/* form chips */
.form { display: inline-flex; gap: 3px; }
.f {
  font-style: normal; font-weight: 600; font-size: .68rem;
  width: 1.45em; height: 1.45em; display: inline-flex; align-items: center; justify-content: center;
  border-radius: 2px; color: var(--paper);
}
.f.W { background: var(--green); }
.f.L { background: var(--red); }
.f.D { background: var(--amber); }

/* groups grid */
.groups { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 28px 36px; margin-top: 24px; }
.group h2 { display: flex; align-items: baseline; gap: .5rem; font-size: 1.6rem; font-weight: 900; border-bottom: 2px solid var(--ink); }
.group h2 span { font-size: .7rem; font-family: "IBM Plex Mono", monospace; font-weight: 500; letter-spacing: .25em; text-transform: uppercase; color: var(--ink-soft); }
.group td:last-child, .group th:last-child { text-align: right; }
.flagcell { width: 1.6em; font-size: 1.05rem; padding-right: .2rem; }
.tlink { color: var(--ink); font-weight: 600; }
.tlink:hover { color: var(--green); }
sup.host { color: var(--red); font-size: .62rem; letter-spacing: .08em; }

/* matchday sections */
.matchday h2 { font-size: 1.1rem; }
/* each matchday is its own table; fix the columns so kickoff, group, fixture,
   result and venue line up vertically down the whole page (not per-section) */
.matchday table { table-layout: fixed; width: 100%; }
.matchday td:nth-child(1) { width: 3.8rem; }   /* kickoff time */
.matchday td:nth-child(2) { width: 2.8rem; }   /* group / KO chip */
.matchday td:nth-child(4) { width: 3.8rem; }   /* result */
.matchday td:nth-child(5) { width: 34%; }      /* venue */
.fixture a { color: var(--ink); font-weight: 600; }
.fixture a:hover { color: var(--green); }
.fixture em { font-style: italic; color: var(--ink-soft); font-weight: 400; }
/* knockout board (homepage) */
.kohead { font-size: 1.1rem; margin: 1.4rem 0 .8rem; }
.kochamps h2 { font-size: 1rem; margin: 0 0 .6rem; }
.kochamprow { display: flex; flex-wrap: wrap; gap: .5rem; }
.kochamp { display: flex; gap: .45rem; align-items: baseline; border: 1px solid var(--rule);
  padding: .3rem .7rem; border-radius: 999px; font-size: .85rem; }
.kochamp a { color: var(--ink); text-decoration: none; }
.kochamp b { color: var(--green); font-variant-numeric: tabular-nums; }
/* left/right knockout bracket */
/* fills the column width so there's no horizontal scroll on normal screens;
   only narrow phones (< the 700px min) fall back to scrolling */
.brackwrap { overflow-x: auto; padding-bottom: .5rem; margin-top: 1rem; }
.bracket { display: flex; align-items: stretch; gap: 7px; min-width: 700px; }
.bcol { display: flex; flex-direction: column; justify-content: space-around;
  flex: 1 1 0; min-width: 0; gap: 6px; }
.bfinal { justify-content: center; flex: 0 0 auto; min-width: 92px; }
.bnode { display: flex; flex-direction: column; border: 1px solid var(--rule);
  border-radius: 6px; background: var(--paper-2); font-size: .74rem;
  text-decoration: none; color: inherit; overflow: hidden; }
.bnode.r32:hover { border-color: var(--green); }
.bnode.slot { background: transparent; border-style: dashed; }
.bnode.final { border-color: var(--green); }
.btbd .bt { color: var(--ink-soft); font-size: .68rem; letter-spacing: .02em; }
td.twin { color: var(--green); } td.tloss { color: var(--ink-soft); }
.brow { display: flex; justify-content: space-between; gap: .35rem; padding: .22rem .4rem; }
.brow + .brow { border-top: 1px solid var(--rule); }
.bt { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bp { color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.bwin .bt { font-weight: 600; color: var(--ink); }
.bwin .bp { color: var(--ink); }
.bsc { text-align: center; font-size: .68rem; color: var(--ink-soft);
  border-top: 1px solid var(--rule); padding: .12rem; }
.bchamp { text-align: center; margin-top: .6rem; font-size: .82rem; }
.bchamp a { color: var(--ink); text-decoration: none; font-weight: 600; }
.bchamp b { color: var(--green); margin-left: .35rem; }
.bchamp span { display: block; font-size: .68rem; color: var(--ink-soft); margin-top: .1rem; }
/* mobile: round-by-round list instead of the sideways tree */
.kolist { display: none; }
.koround { margin-top: 1.3rem; }
.koround h3 { font-size: .95rem; margin: 0 0 .55rem; border-bottom: 1px solid var(--rule);
  padding-bottom: .25rem; }
.kogrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.kolist .bnode { font-size: .82rem; }
@media (max-width: 760px) {
  .brackwrap { display: none; }
  .kolist { display: block; }
}
.gchip { font-size: .72rem; border: 1px solid var(--ink); padding: 0 .35em; border-radius: 2px; }

/* team page */
.teamhead { text-align: center; padding: 1rem 0 .5rem; }
.headshot { width: 110px; height: 110px; border-radius: 50%;
  border: 2px solid var(--ink); object-fit: cover; margin: 0 auto .4rem; display: block;
  background: #fff; }
.headshot.crest { object-fit: contain; padding: 14px; }
.teamhead h1 { margin-bottom: .6rem; }
.meta { display: flex; gap: .5rem; justify-content: center; flex-wrap: wrap; margin: .4rem 0 .9rem; }
.meta.center { display: block; text-align: center; color: var(--ink-soft); font-size: .8rem; }
.chip { border: 1px solid var(--ink); border-radius: 999px; padding: .1em .8em; font-size: .72rem; text-transform: uppercase; letter-spacing: .1em; }
.stats { display: flex; flex-wrap: wrap; gap: 0; border: 2px solid var(--ink); margin: 1.4rem 0; }
.stats div { flex: 1 1 110px; padding: .7rem .9rem; border-right: 1px solid var(--rule); text-align: center; }
.stats div:last-child { border-right: 0; }
.stats dt { font-size: .62rem; text-transform: uppercase; letter-spacing: .14em; color: var(--ink-soft); }
.stats dd { margin: .15rem 0 0; font-size: 1.25rem; font-weight: 600; font-family: "Fraunces", serif; }

/* match page */
.card { text-align: center; }
.versus { display: flex; align-items: baseline; justify-content: center; gap: 1.2rem; flex-wrap: wrap; margin: .6rem 0; }
.versus h1 { font-size: clamp(1.6rem, 4.5vw, 2.8rem); margin: 0; }
.versus h1 a { color: var(--ink); }
.versus h1 a:hover { color: var(--green); text-decoration: none; }
.versus .v { font-family: "Fraunces", serif; font-style: italic; color: var(--red); font-size: 1.4rem; }
.bigscore { font-family: "Fraunces", serif; font-size: 3rem; font-weight: 900; }
.compare { max-width: 640px; margin: 1.2rem auto 0; }
.compare th { text-align: center; font-size: .68rem; padding: .5rem; }
.compare .cl { text-align: right; font-weight: 600; width: 38%; }
.compare .cr { text-align: left; font-weight: 600; width: 38%; }
.compare td .form { vertical-align: middle; }
.twocol { display: grid; grid-template-columns: 1fr 1fr; gap: 36px; margin-top: 1rem; }
.twocol > * { min-width: 0; }   /* let grid items shrink so wide tables scroll, not overflow */
.twocol .comp { display: none; }
@media (max-width: 860px) { .twocol { grid-template-columns: 1fr; } }

footer { max-width: 1080px; margin: 0 auto; padding: 18px 24px 40px; border-top: 3px double var(--ink); font-size: .72rem; color: var(--ink-soft); }
footer p { margin: .2rem 0; }

/* mobile */
@media (max-width: 700px) {
  main { padding: 0 14px 40px; }
  .crumb { padding: 0 14px; }
  h1 { font-size: 1.9rem; }
  .wordmark { font-size: 2.1rem; }
  .masthead { padding: 18px 8px 10px; }
  .masthead nav { font-size: .62rem; letter-spacing: .02em; }
  .masthead nav span { margin: 0 3px; }
  table { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .group table { display: table; }   /* narrow enough to render full-width */
  table { font-size: .78rem; }
  td, th { padding: .4rem .45rem .4rem 0; }
  td.num, th.num { white-space: nowrap; }
  .comp, .venue { white-space: nowrap; }
  .stats div { flex: 1 1 90px; padding: .5rem .4rem; }
  .stats dd { font-size: 1.05rem; }
  .mlbar .seg { font-size: .58rem; padding: .5em .15em; }
  .versus { gap: .6rem; }
  .versus .v { font-size: 1rem; }
  .bigscore { font-size: 2.2rem; }
  .champ { font-size: 1.15rem; padding: .6rem .8rem; }
  .fineprint { font-size: .72rem; }
  .twocol { gap: 20px; }
  .langsw { top: 8px; right: 8px; font-size: .72rem; }
  figure { margin: 1rem auto; }
  h3 { font-size: .95rem; }
}

/* simulation section */
.sim { max-width: 640px; margin: 2.5rem auto 0; }
.sim h2 { text-align: center; border-bottom: 2px solid var(--ink); }
.mlbar { display: flex; gap: 2px; margin: 1rem 0 1.2rem; border: 2px solid var(--ink); padding: 2px; }
.mlbar .seg {
  min-width: 0; overflow: hidden; white-space: nowrap; text-align: center;
  font-size: .72rem; padding: .55em .2em; color: var(--paper);
}
.mlbar .seg b { display: block; font-weight: 600; }
.mlbar .home { background: var(--green); }
.mlbar .draw { background: var(--amber); }
.mlbar .away { background: var(--red); }
.markets td.fair { font-weight: 600; }
.markets td.edge.pos { color: var(--green); font-weight: 600; }
.markets td.edge.neg { color: var(--red); font-weight: 600; }
.markets td.dim { color: var(--rule); }
.futures td.hot { color: var(--green); font-weight: 600; }
.champ { text-align: center; font-family: "Fraunces", serif; font-size: 1.5rem;
  border: 3px double var(--ink); padding: .8rem 1rem; margin: 1.2rem auto; max-width: 560px; }
.ko .pick { font-weight: 600; }
.ko em, .standfirst code { font-style: italic; color: var(--ink-soft); }
.scorelines { text-align: center; font-size: .85rem; }
.scorelines small { color: var(--ink-soft); }
.modelnote { font-size: .72rem; color: var(--ink-soft); text-align: center; max-width: 54ch; margin: 1.2rem auto 0; }
[data-tip]:not([data-tip=""]) {
  cursor: help; position: relative;
  text-decoration: underline dotted var(--ink-soft);
  text-underline-offset: 3px;
}
[data-tip]:not([data-tip=""]):hover::after {
  content: attr(data-tip);
  position: absolute; left: 0; top: calc(100% + 4px); z-index: 20;
  background: var(--ink); color: var(--paper);
  font: 400 .72rem/1.45 "IBM Plex Mono", monospace;
  text-transform: none; letter-spacing: 0; text-align: left;
  padding: .5em .75em; border-radius: 2px;
  width: max-content; max-width: 250px; white-space: normal;
  pointer-events: none;
}
th.num[data-tip]:hover::after, dt[data-tip]:hover::after { left: auto; right: 0; }
span.form[data-tip]:hover::after { left: auto; right: 0; }
.fineprint {
  font-size: .76rem; color: var(--ink-soft); line-height: 1.55;
  border-left: 3px solid var(--rule); padding-left: .9rem; margin: .4rem 0 1.4rem;
}
.snapnote {
  background: var(--amber); color: var(--paper); text-align: center;
  font-size: .78rem; padding: .5em 1em;
}
.snapnote a { color: var(--paper); font-weight: 600; text-decoration: underline; }
figure { margin: 1.3rem auto; text-align: center; }
figure img, figure svg { width: 100%; max-width: 660px; height: auto; display: block;
  margin: 0 auto; border: 1px solid var(--rule); }
figcaption { font-size: .72rem; color: var(--ink-soft); margin: .35rem auto 0;
  max-width: 600px; text-align: center; }

/* standings shading, micro-bars, sticky heads, a11y, print */
tr.q1 td { background: color-mix(in srgb, var(--green) 9%, transparent); }
tr.q3 td { background: color-mix(in srgb, var(--amber) 10%, transparent); }
.futures td.bar { background: linear-gradient(90deg,
  color-mix(in srgb, var(--green) 16%, transparent) var(--p), transparent var(--p)); }
@media (min-width: 701px) {
  thead th { position: sticky; top: 0; background: var(--paper); z-index: 5; }
}
/* skip link: clip-hidden (not left:-999px, which extends the page in RTL) */
.skip { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.skip:focus { width: auto; height: auto; margin: 0; overflow: visible; clip: auto;
  left: 8px; top: 6px; background: var(--ink); color: var(--paper);
  padding: .4em .8em; z-index: 50; }
a.gchip { color: var(--ink); }
@media print {
  .masthead nav, .themebtn, .langsw, footer, .snapnote, .skip { display: none !important; }
  body { background: #fff; color: #000; font-size: 11px; }
  .mlbar .seg, .f { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  main { max-width: none; padding: 0; }
  h1 { font-size: 1.6rem; }
  section { break-inside: avoid; }
}

/* today strip, verdicts, match grids, key players */
.todaybox { border: 3px double var(--ink); padding: .2rem 1rem .8rem; margin: 1.2rem 0; }
.todaybox h2 { border-bottom: 1px solid var(--rule); font-size: 1rem;
  text-transform: uppercase; letter-spacing: .15em; font-family: "IBM Plex Mono", monospace; }
.pridebar { height: 7px; margin: .4rem 0 1rem; border: 1px solid var(--rule);
  background: linear-gradient(90deg, #e40303 0 16.6%, #ff8c00 0 33.3%, #ffed00 0 50%,
  #008026 0 66.6%, #24408e 0 83.3%, #732982 0 100%); }
.extlinks { font-size: .78rem; }
.pridenote { max-width: 640px; margin-left: auto; margin-right: auto; }
.pridepage h1, .pridepage h2 {
  border-image: linear-gradient(90deg, #e40303, #ff8c00, #ffed00, #008026, #24408e, #732982) 1;
}
.pridepage h2 { border-bottom-width: 2px; border-bottom-style: solid; }
.pridepage .versus .v {
  background: linear-gradient(180deg, #e40303, #ff8c00, #ffed00, #008026, #24408e, #732982);
  -webkit-background-clip: text; background-clip: text; color: transparent;
  font-weight: 700;
}
.pridepage .group h2, .pridepage .sim h2 { border-image-slice: 1; }
.verdict { max-width: 640px; margin: .8rem auto; padding: .6rem .9rem; font-size: .85rem;
  border: 2px solid var(--green); }
.verdict.miss { border-color: var(--red); }
.matchgrid svg { max-width: 400px; }
.stats.odds dd { color: var(--green); }

/* dark mode: paper-on-ink - follows the OS unless the button overrides */
.themebtn {
  position: absolute; top: 14px; left: 18px; cursor: pointer;
  background: none; border: 1px solid var(--ink); border-radius: 999px;
  color: var(--ink); font-size: .95rem; width: 1.9em; height: 1.9em; line-height: 1;
}
.themebtn:hover { background: var(--ink); color: var(--paper); }
@media (prefers-color-scheme: dark) {
  html:not([data-theme="light"]) {
    --paper: #181510; --paper-2: #211d16; --ink: #e9e2d2; --ink-soft: #a39a86;
    --rule: #423c2f; --green: #4aa377; --red: #d05548; --amber: #c79a3d;
  }
  html:not([data-theme="light"]) body { background-image:
    repeating-linear-gradient(0deg, transparent 0 2px, rgba(233,226,210,.012) 2px 4px); }
  html:not([data-theme="light"]) .f,
  html:not([data-theme="light"]) .mlbar .seg,
  html:not([data-theme="light"]) .snapnote { color: #181510; }
}
html[data-theme="dark"] {
  --paper: #181510; --paper-2: #211d16; --ink: #e9e2d2; --ink-soft: #a39a86;
  --rule: #423c2f; --green: #4aa377; --red: #d05548; --amber: #c79a3d;
}
html[data-theme="dark"] body { background-image:
  repeating-linear-gradient(0deg, transparent 0 2px, rgba(233,226,210,.012) 2px 4px); }
html[data-theme="dark"] .f,
html[data-theme="dark"] .mlbar .seg,
html[data-theme="dark"] .snapnote { color: #181510; }


/* language switcher + Farsi (RTL) */
.masthead { position: relative; }
.langsw { position: absolute; top: 14px; right: 18px; font-size: .8rem; }
.langsw a { border: 1px solid var(--ink); border-radius: 999px; padding: .15em .8em; color: var(--ink); }
.langsw a:hover { background: var(--ink); color: var(--paper); text-decoration: none; }
html[dir="rtl"] body, html[dir="rtl"] main, html[dir="rtl"] h1, html[dir="rtl"] h2,
html[dir="rtl"] h3, html[dir="rtl"] footer { font-family: "Vazirmatn", "IBM Plex Mono", sans-serif; }
html[dir="rtl"] h1 { font-weight: 800; letter-spacing: 0; }
html[dir="rtl"] h2, html[dir="rtl"] h3 { font-weight: 700; letter-spacing: 0; }
html[dir="rtl"] .fineprint { border-left: 0; border-right: 3px solid var(--rule);
  padding-left: 0; padding-right: .9rem; }
html[dir="rtl"] figure svg { direction: ltr; }
h3 { font-family: "Fraunces", serif; font-size: 1.02rem; font-weight: 600; margin: 1.4rem 0 .3rem; }
.sim .fineprint { margin-left: auto; margin-right: auto; }
"""

GLOSSARY = [
    ("Start here — the big picture", [
        ("What this site is", "A computer model that estimates the chance of "
         "every World Cup result, written out as probabilities. It then puts "
         "its numbers next to the betting market's prices and grades itself in "
         "public after every match. Nothing here is a tip or a certainty — it "
         "is one informed opinion, shown with its workings and its track "
         "record so you can judge it."),
        ("Probability", "How likely something is, from 0% (never) to 100% "
         "(certain). \"Brazil 62% to win\" means that if this exact match "
         "were played many times, the model expects Brazil to win about 62 of "
         "every 100. A 62% favourite still loses fairly often — that is not "
         "the model being wrong, it is what 62% means."),
        ("Favourite and underdog", "The favourite is the side more likely to "
         "win; the underdog is the less likely. Upsets are normal — over a "
         "whole tournament, a good chunk of underdogs win."),
    ]),
    ("Reading the odds", [
        ("Odds", "Another way of writing a probability, usually as a payout. "
         "\"3.0 (decimal) odds\" means a winning $1 bet returns $3 (your $1 "
         "back plus $2 profit) — which implies about a 33% chance."),
        ("Implied probability", "The probability hidden inside a price. A "
         "price of 50¢ on a yes/no market implies a 50% chance. We turn every "
         "market price into an implied probability so the model and the market "
         "can be compared on the same scale."),
        ("Fair price", "The model's probability written as a price in cents — "
         "a 56% chance is a 56¢ fair price. Buy below it and you profit on "
         "average <em>if the model is right</em>; pay above it and you are "
         "overpaying by the model's lights."),
    ]),
    ("The markets — what can be predicted", [
        ("1X2 / moneyline", "The basic question: home win, draw, or away win "
         "(the \"1\", \"X\" and \"2\"). \"Moneyline\" is the same thing in "
         "betting language — just who wins, draw included."),
        ("Over / Under (totals)", "A bet on the <em>number of goals</em> in a "
         "match rather than who wins. \"Over 2.5\" wins if there are 3 or more "
         "goals; \"Under 2.5\" wins with 2 or fewer. The half-goal avoids a "
         "tie."),
        ("The line", "The number a total or handicap is set at — 2.5 goals, "
         "9.5 corners. The half stops the bet from landing exactly on the "
         "number."),
        ("Both teams to score (BTTS)", "Yes if each side scores at least one "
         "goal, No otherwise — independent of who wins."),
        ("Exact score / correct score", "Predicting the precise final score "
         "(2–1, 0–0). Hard to hit — even the single likeliest score usually "
         "sits well under a 1-in-6 chance — so the payouts are large."),
        ("Spread / handicap", "Giving the favourite a goal start to make a "
         "lopsided match a coin-flip. \"England −1.5\" wins only if England "
         "win by 2 or more clear goals."),
        ("First to score", "Which team scores the opening goal — or neither, "
         "if it finishes 0–0."),
        ("Halves", "The same result/goal questions applied to just the first "
         "or second half of a match."),
        ("Corners", "Bets on the number of corner kicks, usually an over/under "
         "total. Driven more by playing style than by who is winning."),
        ("Futures", "Bets settled later in the tournament rather than on one "
         "match: to win the World Cup, to reach the final, to win the group. "
         "Long shots pay big and tie up your stake for weeks."),
    ]),
    ("How the model makes the numbers", [
        ("Statistical model", "A set of equations fitted to past results that "
         "outputs a probability. It has no opinions, no nerves and no eye for "
         "the game — only the maths of what has happened before."),
        ("Poisson", "The standard maths for counting goals: it turns a team's "
         "average scoring rate into the chance of 0, 1, 2, 3… goals. Plain "
         "Poisson slightly misprices draws, which is why we use a refinement."),
        ("Dixon–Coles", "The refinement: weighted Poisson that corrects the "
         "low-scoring scorelines plain Poisson gets wrong and leans on recent "
         "matches more than old ones. The engine behind this whole site."),
        ("Attack and defence ratings", "Each team gets two numbers — how many "
         "goals it tends to score, and to concede. A match's goal estimate "
         "comes from one side's attack against the other's defence."),
        ("Expected goals (xG)", "Here, the number of goals the model expects a "
         "team to score in a match, from those ratings. Note: this is "
         "<em>not</em> the shot-by-shot xG on TV broadcasts — same name, "
         "different thing."),
        ("Home advantage", "The measured edge a team gets playing at home (or, "
         "at a World Cup, as a host). Removed at neutral venues."),
        ("Recency weighting", "Newer matches count for more than old ones, "
         "because squads and form change. A result from last month says more "
         "than one from three years ago."),
        ("Shrinkage / regression to the mean", "Pulling extreme estimates back "
         "toward average, so a team that happens to win 6–0 once is not "
         "treated as six-goal-good forever. Guards against reading too much "
         "into small samples."),
        ("Score grid", "A table of the probability of every scoreline (0–0, "
         "1–0, 2–1…). Every market on the site — winner, totals, both-teams, "
         "exact score — is read off this one grid."),
        ("Monte Carlo simulation", "Playing the whole tournament out at random "
         "tens of thousands of times, then counting how often each team wins. "
         "How the futures (champion, reach-the-final) are estimated."),
        ("Ensemble", "Running many slightly different versions of the model "
         "and averaging them, so the answer carries its own uncertainty "
         "instead of pretending to one exact number."),
        ("Elo", "A simpler rating system (famous from chess) that updates after "
         "every match. We run it as a structurally different <em>second "
         "opinion</em>; where it and the main model disagree sharply, that is "
         "a flag to trust the match less."),
        ("The blend", "Mixing the model's probability with the market's price "
         "to get a combined view, on the logic that the market sees things the "
         "model can't (injuries, lineups). The site keeps the raw model "
         "separate so each can be graded on its own."),
    ]),
    ("The market side", [
        ("Prediction market", "A marketplace where people buy and sell shares "
         "in an outcome; the price floats to reflect the crowd's collective "
         "estimate of its probability. Polymarket is the one used here."),
        ("Liquidity", "How much money is actively trading a market. Thin "
         "(illiquid) markets have unreliable prices — a single small bet can "
         "move them — so the site ignores them."),
        ("Overround / vig", "The bookmaker's built-in margin: add up the "
         "implied probabilities of all outcomes and they total more than "
         "100%. That extra is the house edge, which you have to beat to "
         "profit."),
        ("Closing line", "The final price just before a match starts, after "
         "all the smart money is in. It is the market's best guess, and a "
         "hard benchmark to beat."),
    ]),
    ("Edge, value and staking", [
        ("Edge", "The gap between the model's probability and the market's "
         "price. Model 60%, market 52¢ → an 8-cent edge, the model's claim "
         "that the market is too cheap. The whole point — and the thing most "
         "often an illusion."),
        ("Value", "A bet where your estimated probability beats the price. "
         "Value betting means backing edges, win or lose on the day, and "
         "trusting the maths over many bets."),
        ("Kelly criterion", "A formula for how much to stake given your edge "
         "and bankroll — bet more on bigger edges, less on thin ones. We use a "
         "<em>fraction</em> of it, because the edge is never as certain as it "
         "looks."),
        ("Bankroll", "The total pot set aside for betting. Stakes are sized as "
         "a slice of it so no single bet can do real damage."),
        ("Closing-line value (CLV)", "Did the price move toward your bet after "
         "you made it? Consistently positive CLV is the early sign you are "
         "finding real edge — long before wins and losses prove anything."),
        ("Paper trading", "Recording bets without staking money, to test "
         "whether an edge is real before risking anything on it."),
    ]),
    ("Grading the forecasts", [
        ("Hit rate / accuracy", "How often the predicted result actually "
         "happened. Useful but crude — it ignores how <em>confident</em> the "
         "call was."),
        ("Brier score", "A sharper grade: the squared error of a probability "
         "against what happened. Lower is better; a forecaster who just "
         "guesses 'anything could happen' scores middling, and confident "
         "wrong calls are punished hard."),
        ("Log-loss", "Like the Brier score but harsher still on confident "
         "mistakes — saying 95% and being wrong costs a lot. The main yardstick "
         "for comparing the model, the market and the blend."),
        ("Calibration", "Whether the probabilities mean what they say: of all "
         "the things called 70%, do about 70% happen? A calibrated forecaster's "
         "claims match reality over the long run."),
        ("Baseline", "The score to beat — what you'd get by guessing with no "
         "skill (e.g. always the base rates). A model is only worth anything "
         "if it beats the baseline."),
        ("Backtest / out-of-sample", "Testing the model on matches it was "
         "<em>not</em> trained on, to see if it really predicts rather than "
         "just memorises. The honest test."),
        ("Look-ahead", "The cardinal sin: letting the model peek at "
         "information it wouldn't have had at the time (like results that came "
         "later). It flatters the numbers and makes a backtest a lie; avoiding "
         "it is most of the work."),
        ("Locked predictions", "Picks frozen before the tournament and graded "
         "in public as results come in, so the scorecard can't be quietly "
         "rewritten after the fact."),
    ]),
    ("Honest limits", [
        ("Variance / luck", "Football is high-variance: the better side loses "
         "all the time over 90 minutes. A model can be right about the odds and "
         "still look wrong on the day — judge it over many matches, not one."),
        ("Sample size", "How much data a number rests on. Early-tournament "
         "grades are built on a handful of matches and will swing around; wait "
         "for a full group stage before trusting any of them."),
        ("What the model can't see", "It knows results, not injuries, "
         "suspensions, lineups, weather or motivation. That is its biggest "
         "blind spot, and why its confident disagreements with the market "
         "deserve suspicion, not blind backing."),
    ]),
]


def build_glossary():
    toc = " · ".join(
        f'<a href="#{slug(title)}">{escape(title)}</a>'
        for title, _ in GLOSSARY)
    sections = ""
    for title, terms in GLOSSARY:
        items = "".join(f"<dt>{escape(t)}</dt><dd>{d}</dd>" for t, d in terms)
        sections += (f'<h2 id="{slug(title)}">{escape(title)}</h2>'
                     f'<dl class="glossary">{items}</dl>')
    body = f"""<h1>Plain-language glossary</h1>
<p class="standfirst">New to predictions, probabilities or betting markets? This
page explains every term on the site — and the field around it — in plain
English, no maths assumed. Skim it, or jump to a section.</p>
<p class="fineprint">{toc}</p>
{sections}
<p class="fineprint">Missing a term, or one that still reads like jargon? The
whole site is open on
<a href="https://github.com/amirdaraee/world-cup-predictions">GitHub</a> — and
remember, none of this is betting advice.</p>"""
    (OUT / "glossary.html").write_text(page(
        "Glossary", body, canon="glossary.html",
        desc="Plain-English glossary of football prediction and betting-market "
             "terms — probability, log-loss, closing-line value and more, no "
             "maths assumed."))


def build_follow_tool():
    """Public-data ROI report: how a flat stake on every moneyline signal the
    model flagged (edge >= threshold over the lock-time market) would have done,
    graded on actual results. Reads wc26_follow_tool.json; no personal data."""
    try:
        d = json.load(open(DATA / "wc26_follow_tool.json"))
    except FileNotFoundError:
        return
    s = d.get("summary") or {}
    if not s.get("n_bets"):
        return
    profit, roi = s["profit"], s["roi"]
    item = lambda k, v: f"<div><dt>{k}</dt><dd>{v}</dd></div>"
    stats = (item("Signals", s["n_bets"])
             + item("Win rate", f"{s['win_rate'] * 100:.0f}%")
             + item("Staked", f"${s['staked']:.0f}")
             + item("Returned", f"${s['returned']:.2f}")
             + item("Profit", f"${profit:+.2f}")
             + item("ROI", f"{roi * 100:+.1f}%"))
    rows = "".join(
        f'<tr><td>{escape(b["match"])} — {escape(b["side"])}</td>'
        f'<td class="num">{b["model"] * 100:.0f}%</td>'
        f'<td class="num">{b["market"] * 100:.0f}%</td>'
        f'<td class="num">+{b["edge"] * 100:.0f}c</td>'
        f'<td class="{"twin" if b["won"] else "tloss"}">{"won" if b["won"] else "lost"}</td>'
        f'<td class="num">{b["profit"]:+.2f}</td></tr>' for b in d["bets"])
    verb = "made" if profit >= 0 else "lost"
    body = f"""<h1>If you'd followed the model</h1>
<p class="standfirst">A hypothetical: a flat ${s['stake_unit']:.0f} on every moneyline
signal the model flagged — where its own probability beat the Polymarket price by at
least {s['threshold'] * 100:.0f}c — staked at the pre-match price and graded on the
actual result. Public data only: no personal bets, no look-ahead. Not betting advice.</p>
<dl class="stats">{stats}</dl>
<p class="fineprint">Over the group stage the model's moneyline edges {verb} money. The
flagged bets were mostly underdogs the model rated higher than the market — and they
mostly didn't land. Closing-line value and a longer sample are the real test; one group
stage is thin, and one or two big-priced winners swing the total. This grades the public
model's signals, not any real wagering. See the <a href="report.html">accuracy
report</a>.</p>
<table class="ko"><thead><tr><th>Signal</th>
<th class="num" title="the model's win probability">Model</th>
<th class="num" title="the Polymarket price at lock time">Market</th>
<th class="num" title="model probability minus market price">Edge</th>
<th>Result</th><th class="num">P&amp;L&nbsp;($)</th></tr></thead>
<tbody>{rows}</tbody></table>"""
    (OUT / "follow-the-model.html").write_text(page(
        "Following the model", body, canon="follow-the-model.html",
        desc="A hypothetical flat-stake backtest of the model's World Cup 2026 moneyline "
             "signals versus the market, graded on actual results. Public data; not "
             "betting advice."))


FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="6" fill="#211d16"/>'
    '<path d="M16 6.5l7.1 5.16-2.71 8.34H11.6L8.9 11.66z" fill="#f6f1e6"/>'
    '</svg>\n')


def _advance_probs(sim):
    """Per-side chance of going through a knockout tie: the 90-minute win
    probability plus half the draw mass (a level tie is a ~coin-flip on pens)."""
    ml = sim["moneyline"]
    d = ml["draw"]
    return ml["home"] + d / 2, ml["away"] + d / 2


# official 2026 bracket (Wikipedia / wc26_tournament.py). slot1 of each R32 tie
# is always a group winner/runner-up, which pins each real fixture to a match
# number; the feeder maps give the flow R32 -> R16 -> QF -> SF -> final.
KO_R32_SLOT1 = {73: ("R", "A"), 74: ("W", "E"), 75: ("W", "F"), 76: ("W", "C"),
                77: ("W", "I"), 78: ("R", "E"), 79: ("W", "A"), 80: ("W", "L"),
                81: ("W", "D"), 82: ("W", "G"), 83: ("R", "K"), 84: ("W", "H"),
                85: ("W", "B"), 86: ("W", "J"), 87: ("W", "K"), 88: ("R", "D")}
KO_FEED = {89: (74, 77), 90: (73, 75), 91: (76, 78), 92: (79, 80),
           93: (83, 84), 94: (81, 82), 95: (86, 88), 96: (85, 87),
           97: (89, 90), 98: (93, 94), 99: (91, 92), 100: (95, 96),
           101: (97, 98), 102: (99, 100), 104: (101, 102)}
# columns top->bottom per side, outer (R32) to inner (SF); final in the centre
KO_LEFT = [[74, 77, 73, 75, 83, 84, 81, 82], [89, 90, 93, 94], [97, 98], [101]]
KO_RIGHT = [[76, 78, 79, 80, 86, 88, 85, 87], [91, 92, 95, 96], [99, 100], [102]]
KO_STAGE_KEY = {"r16": "qf", "qf": "sf", "sf": "final", "final": "champion"}


def _actual_standings():
    """Group winner/runner-up from the real results (pts, then GD, GF)."""
    from collections import defaultdict
    tab = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for m in MATCHES:
        if not str(m.get("status", "")).startswith("Match Finished") \
                or not m.get("score"):
            continue
        hs, as_ = (int(x) for x in m["score"].split("-"))
        for t, gf, ga in ((m["home"], hs, as_), (m["away"], as_, hs)):
            r = tab[m["group"]][t]
            r[0] += 3 if gf > ga else 1 if gf == ga else 0
            r[1] += gf - ga
            r[2] += gf
    rank = {}
    for g, teams in tab.items():
        order = sorted(teams, key=lambda t: (-teams[t][0], -teams[t][1],
                                             -teams[t][2]))
        if len(order) >= 2:
            rank[("W", g)], rank[("R", g)] = order[0], order[1]
    return rank


def build_knockouts():
    """The homepage: the real knockout bracket — informational, not a forecast.
    Round-of-32 ties carry the model's go-through odds while unplayed; later
    rounds show which tie feeds each slot and fill with the ACTUAL winner as
    matches are played. No projected winners, no predicted champion."""
    rank = _actual_standings()
    fx_by_no, team_fx = {}, {}
    for m in KOS:
        if m.get("round") == "Round of 32":
            team_fx[m["home"]] = m
            team_fx[m["away"]] = m
    for no, slot in KO_R32_SLOT1.items():
        fx = team_fx.get(rank.get(slot))
        if fx:
            fx_by_no[no] = fx
    if len(fx_by_no) != 16:
        body = ("<h1>The knockouts</h1><p class=\"standfirst\">The Round of 32 "
                "is still resolving — the bracket appears once the group stage "
                "is complete.</p>")
        (OUT / "index.html").write_text(page("Knockouts", body, canon=""))
        return

    round_of = {**{n: "r32" for n in range(73, 89)},
                **{n: "r16" for n in range(89, 97)},
                **{n: "qf" for n in range(97, 101)},
                101: "sf", 102: "sf", 104: "final"}

    def played(m):
        return str(m.get("status", "")).startswith("Match Finished") and m.get("score")

    def score_winner(m):
        hs, as_ = (int(x) for x in m["score"].split("-"))
        if hs != as_:
            return m["home"] if hs > as_ else m["away"]
        pens = m.get("penalties")
        if isinstance(pens, str) and "-" in pens:
            ph, pa = (int(x) for x in pens.split("-"))
            return m["home"] if ph > pa else m["away"]
        return None

    def finished_between(t1, t2):
        for m in KOS:
            if {m["home"], m["away"]} == {t1, t2} and played(m):
                return m
        return None

    def actual_winner(no):
        """The real team that has won through to fill this slot, else None."""
        if round_of[no] == "r32":
            m = fx_by_no[no]
            return score_winner(m) if played(m) else None
        f1, f2 = KO_FEED[no]
        t1, t2 = actual_winner(f1), actual_winner(f2)
        if t1 and t2:
            m = finished_between(t1, t2)
            if m:
                return score_winner(m)
        return None

    def abbr(name):
        return "".join(c for c in name if c.isalpha())[:3].upper()

    def slot_row(feeder_no):
        """One row of an inner node: the actual winner once known, otherwise a
        muted placeholder naming the tie that decides it."""
        w = actual_winner(feeder_no)
        if w:
            return f'<span class="brow bwin"><span class="bt">{escape(w)}</span></span>'
        if round_of[feeder_no] == "r32":
            m = fx_by_no[feeder_no]
            tag = f'{abbr(m["home"])} / {abbr(m["away"])}'
        else:
            tag = "—"
        return f'<span class="brow btbd"><span class="bt">{tag}</span></span>'

    def node_html(no):
        if round_of[no] == "r32":
            m = fx_by_no[no]
            sim = SIMS.get(str(m["match_id"]))
            ph, pa = _advance_probs(sim) if sim else (None, None)
            done = played(m)
            w = score_winner(m) if done else None
            rows = ""
            for team, p in ((m["home"], ph), (m["away"], pa)):
                cell = (f'<span class="bp">{p * 100:.0f}</span>'
                        if (p is not None and not done) else "")
                rows += (f'<span class="brow{" bwin" if team == w else ""}">'
                         f'<span class="bt">{escape(team)}</span>{cell}</span>')
            sc = f'<span class="bsc">{escape(m["score"])}</span>' if done else ""
            return (f'<a class="bnode r32" href="matches/{match_slug(m)}.html">'
                    f'{rows}{sc}</a>')
        f1, f2 = KO_FEED[no]
        return f'<div class="bnode slot">{slot_row(f1)}{slot_row(f2)}</div>'

    def side_html(cols):
        return "".join('<div class="bcol">'
                       + "".join(node_html(no) for no in col) + "</div>"
                       for col in cols)

    final_col = (f'<div class="bcol bfinal">'
                 f'<div class="bnode final">{slot_row(101)}{slot_row(102)}</div>'
                 f'<div class="bchamp"><span>Final</span></div></div>')
    bracket = (f'<div class="bracket">{side_html(KO_LEFT)}{final_col}'
               f'{side_html(list(reversed(KO_RIGHT)))}</div>')

    # mobile: the same nodes as a round-by-round vertical list (no sideways
    # tree, no horizontal scroll on a phone)
    rounds = [("Round of 32", list(range(73, 89))),
              ("Round of 16", list(range(89, 97))),
              ("Quarter-finals", [97, 98, 99, 100]),
              ("Semi-finals", [101, 102]), ("Final", [104])]
    mobile = "".join(
        f'<section class="koround"><h3>{label}</h3><div class="kogrid">'
        + "".join(node_html(no) for no in nos) + "</div></section>"
        for label, nos in rounds)

    body = f"""<h1>The knockouts</h1>
<p class="standfirst">Thirty-two teams, single elimination to the title. This is the
real draw: the outer columns are the Round-of-32 ties, and the number on each side is
the model's chance of it going through (a level tie settled on penalties). The inner
rounds name the tie that feeds each slot and fill in with the actual winner as matches
are played — it tracks the tournament, it doesn't pick it.</p>
<div class="brackwrap">{bracket}</div>
<div class="kolist">{mobile}</div>
<p class="fineprint">Outer ties link to the full match page and are graded as they are
played; inner slots show the actual winner once decided. Go-through odds come from the
same Dixon-Coles model as the rest of the site. Not betting advice. See the
<a href="groups.html">group stage</a> or the model's <a href="bracket.html">graded
bracket</a> and <a href="futures.html">title odds</a>.</p>"""
    (OUT / "index.html").write_text(page(
        "Knockouts", body, canon="",
        desc="The World Cup 2026 knockout bracket: the real Round-of-32 draw with model "
             "go-through odds, filling in with actual winners as the tournament is played."))


def emit_seo_assets():
    """favicon, web manifest, robots.txt and a sitemap of every live page —
    written into docs/ each build (build_all wipes and regenerates docs/)."""
    (OUT / "favicon.svg").write_text(FAVICON_SVG)
    (OUT / "site.webmanifest").write_text(json.dumps({
        "name": SITE_NAME, "short_name": "Form Book", "description": DEFAULT_DESC,
        "start_url": "/", "display": "standalone",
        "background_color": "#f6f1e6", "theme_color": "#211d16",
        "icons": [{"src": "/favicon.svg", "type": "image/svg+xml", "sizes": "any"}],
    }, indent=2, ensure_ascii=False))
    (OUT / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n"
        f"Sitemap: https://{DOMAIN}/sitemap.xml\n")
    # sitemap: every live .html except the immutable archive snapshots
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = []
    for f in sorted(OUT.rglob("*.html")):
        rel = f.relative_to(OUT).as_posix()
        if rel.startswith("archive/"):
            continue
        loc = f"https://{DOMAIN}/" + ("" if rel == "index.html" else rel)
        urls.append(f"  <url><loc>{escape(loc)}</loc>"
                    f"<lastmod>{today}</lastmod></url>")
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>\n")


def build_all(snapshot=False):
    if OUT.exists():
        # keep the archive across rebuilds; regenerate everything else
        for item in OUT.iterdir():
            if item.name == "archive":
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    (OUT / "teams").mkdir(parents=True, exist_ok=True)
    (OUT / "matches").mkdir(exist_ok=True)
    if (ROOT / "charts").exists():   # method-page charts (generated by wc26_charts.py)
        (OUT / "img").mkdir(exist_ok=True)
        for f in list((ROOT / "charts").glob("*.svg")) + list((ROOT / "charts").glob("*.png")):
            shutil.copy(f, OUT / "img" / f.name)
    (OUT / "style.css").write_text(CSS)
    (OUT / "CNAME").write_text(DOMAIN + "\n")   # Pages custom domain
    build_index()       # -> groups.html
    build_knockouts()   # -> index.html (the homepage)
    build_matches_list()
    build_team_pages()
    build_match_pages()
    build_futures()
    build_bracket()
    build_awards()
    build_player_pages()
    build_report()
    build_follow_tool()
    build_glossary()
    build_method()
    build_method_fa()
    build_archive_index()
    if snapshot:
        take_snapshot()
        build_archive_index()   # live index now lists the new snapshot
    emit_seo_assets()   # favicon, manifest, robots.txt, sitemap (after all pages)
    n = len(list(OUT.glob("*.html"))) + len(list((OUT / "teams").glob("*.html"))) \
        + len(list((OUT / "matches").glob("*.html")))
    snaps = len(list((OUT / "archive").iterdir())) if (OUT / "archive").exists() else 0
    print(f"built {n} live pages in {OUT} ({snaps} archived snapshots)")


if __name__ == "__main__":
    build_all(snapshot="snapshot" in sys.argv)
