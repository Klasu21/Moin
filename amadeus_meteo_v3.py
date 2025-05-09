# =============================================================================
# amadeus_meteo_v2.py
# =============================================================================
# PURPOSE
# -------
# Streamlit demo that brings together four third‑party services:
#
#   1. Amadeus Self‑Service APIs      – activities catalogue + city search
#   2. Open‑Meteo Archive API         – historical weather for past dates
#   3. Streamlit                      – instant Python‑to‑web GUI framework
#   4. streamlit‑searchbox component  – live type‑ahead city selector
#
# UX HIGHLIGHTS
# -------------
# •  Live search for any city (Amadeus "City Search" endpoint)  
# •  Paginated activities list with page‑size selector  
# •  Sort by Rating / Price (↑ / ↓) or keep Amadeus’ natural order  
# •  Historical weather table for the *same calendar day* back three years  
# •  **Weather‑aware category preset**  
#       – If it rained ≥ 2 of 3 years → "rain" preset  
#       – Else: temperature < 15 °C → "cold/no‑rain" preset  
#       – Else: "warm/no‑rain" preset  
#   The preset is optional; users can edit categories afterwards.  
# •  Everything is held in `st.session_state` so navigation is smooth.
#
# NOTES FOR REAL PROJECTS
# -----------------------
# •  Hard‑coded Amadeus test keys are fine for demos; move them to secrets
#    or environment variables in production.
# •  The Amadeus test base URL is used.  Swap to the production URL +
#    production credentials for live data.
# •  No rate‑limit / retry logic here (simpler code); add if you expect load.
# =============================================================================

# ----------------------------------------------------------------------------- 
# 0. Imports
# -----------------------------------------------------------------------------
import math                                      # ceil() for pagination
from datetime import datetime, timedelta         # date arithmetic
from typing import List, Dict                    # type hints

import pandas as pd                              # display weather as table
import requests                                  # HTTP calls to external APIs
import streamlit as st                           # the web framework
from streamlit_searchbox import st_searchbox     # live autocomplete widget


# ----------------------------------------------------------------------------- 
# 1. Amadeus credentials (TEST ENVIRONMENT)
# -----------------------------------------------------------------------------
# NOTE: keep these in environment variables / Streamlit secrets in production.
API_KEY    = "EZJVed7pmAl5WAbkSbSAkTPlmvOiaA63"
API_SECRET = "SlZ3evRWgNyiL0KF"


# ----------------------------------------------------------------------------- 
# 2. Amadeus helper functions
# -----------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=1800)
def get_access_token() -> str:
    """
    Retrieve (and cache) a bearer token. 30‑min TTL ≈ token lifespan.
    """
    r = requests.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     API_KEY,
            "client_secret": API_SECRET,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _amadeus_city_query(token: str, keyword: str, max_results: int = 8) -> List[Dict]:
    """
    Query the City Search endpoint.  Filter out rows without coordinates.
    """
    r = requests.get(
        "https://test.api.amadeus.com/v1/reference-data/locations/cities",
        headers={"Authorization": f"Bearer {token}"},
        params={"keyword": keyword, "max": max_results},
        timeout=10,
    )
    if r.status_code != 200:
        return []  # fail silently – widget will simply show no suggestions
    hits = []
    for item in r.json().get("data", []):
        geo = item.get("geoCode", {})
        if "latitude" in geo and "longitude" in geo:
            hits.append(
                {
                    "name": item.get("name", "Unknown"),
                    "iata": item.get("iataCode", ""),
                    "lat":  geo["latitude"],
                    "lon":  geo["longitude"],
                }
            )
    return hits


def city_searchbox_source(user_input: str, **_) -> List[Dict]:
    """
    Adapter for streamlit‑searchbox. Adds mandatory 'label' field.
    """
    token = get_access_token()
    matches = _amadeus_city_query(token, user_input)
    for m in matches:
        m["label"] = f"{m['name']} ({m['iata']})" if m["iata"] else m["name"]
    return matches


def get_activities(token: str, lat: float, lon: float, radius: int) -> List[Dict]:
    """
    Fetch activities around lat/lon within radius km.  Amadeus test endpoint.
    """
    r = requests.get(
        "https://test.api.amadeus.com/v1/shopping/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"latitude": lat, "longitude": lon, "radius": radius},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["data"]


# ----------------------------------------------------------------------------- 
# 3. Open‑Meteo helper functions
# -----------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=6000)
def fetch_weather_once(lat: float, lon: float, iso_date: str) -> Dict:
    """
    Weather for ONE date. Cache to avoid duplicate calls while user is typing.
    """
    r = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude":   lat,
            "longitude":  lon,
            "start_date": iso_date,
            "end_date":   iso_date,
            "daily":      "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone":   "Europe/Berlin",
        },
        timeout=10,
    )
    return r.json().get("daily", {}) if r.status_code == 200 else {}


def last_three_years_weather(lat: float, lon: float, ref: datetime) -> List[Dict]:
    """
    Build table for ref‑date minus 1,2,3 years. Skip if missing.
    """
    rows = []
    for n in range(1, 4):
        dt = ref - timedelta(days=365 * n)
        data = fetch_weather_once(lat, lon, dt.strftime("%Y-%m-%d"))
        if data:
            rows.append(
                {
                    "Year":      dt.year,
                    "Max °C":    data["temperature_2m_max"][0],
                    "Min °C":    data["temperature_2m_min"][0],
                    "Precip mm": data["precipitation_sum"][0],
                }
            )
    return rows


def classify_weather(rows: List[Dict]) -> tuple[bool, float]:
    """
    Decide rain_flag + average temperature from weather_rows list.
    """
    rain_flag = sum(r["Precip mm"] > 0 for r in rows) >= 2
    avg_temp = sum((r["Max °C"] + r["Min °C"]) / 2 for r in rows) / len(rows) if rows else float("nan")
    return rain_flag, avg_temp


def preset_categories(rain: bool, avg_t: float) -> List[str]:
    """
    Map rain / avg_temp to default categories.
    """
    if rain:
        return ["Museums", "Restaurants", "Historical", "Sightseeing"]
    if avg_t < 15:
        return ["Museums", "Historical", "Tours", "Sightseeing"]
    return ["Wine", "Historical"]


# ----------------------------------------------------------------------------- 
# 4. Streamlit page config + session defaults
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Amadeus Activities", page_icon="🎒")
st.title("🎒 Amadeus Activities & Weather Explorer")

# Create keys if not present (first run or browser refresh)
for k, v in {
    "page": 1,
    "have_results": False,
    "active_cats": [],
    "use_preset": False,
}.items():
    st.session_state.setdefault(k, v)

# ----------------------------------------------------------------------------- 
# 5. Apply pending programmatic category update *before* widget creation
# -----------------------------------------------------------------------------
if "cat_filter_new" in st.session_state:
    st.session_state.cat_filter = st.session_state.pop("cat_filter_new")
    st.session_state.active_cats = st.session_state.cat_filter

# ----------------------------------------------------------------------------- 
# 6. City autocomplete
# -----------------------------------------------------------------------------
city = st_searchbox(
    city_searchbox_source,
    key="city_search",
    placeholder="Start typing a city …",
    no_results_msg="No city found",
)
if not city:
    st.info("Start typing a city name to see suggestions.")
    st.stop()

lat, lon = city["lat"], city["lon"]
st.success(f"Selected **{city['label']}**  →  {lat:.2f}, {lon:.2f}")

# ----------------------------------------------------------------------------- 
# 7. Global controls
# -----------------------------------------------------------------------------
radius   = st.slider("Search radius (km)", 1, 20, 5)
ref_date = st.date_input("Travel date (for weather comparison)", datetime.today())

left, right = st.columns(2)
page_size  = left.selectbox("Activities per page", [5, 10, 20], index=1)
sort_order = right.selectbox(
    "Sort",
    ["None", "Rating ↓", "Rating ↑", "Price ↓", "Price ↑"],
    index=0,
)

# ----------------------------------------------------------------------------- 
# 8. Category multiselect + preset button
# -----------------------------------------------------------------------------
CATEGORY_LIST = ["Tours", "Museums", "Restaurants",
                 "Wine", "Historical", "Sightseeing"]

KEYWORDS = {
    "Tours":       ["tour"],
    "Museums":     ["museum"],
    "Restaurants": ["restaurant", "food"],
    "Wine":        ["wine"],
    "Historical":  ["castle", "palace", "cathedral", "ruins"],
    "Sightseeing": ["sightseeing", "view", "panorama"],
}

# --- build widget ---------------------------------------------------
if "cat_filter" in st.session_state:
    # Key already exists ⟶ Streamlit remembers its value; give *no* default
    st.multiselect("Kategorie‑Filter",
                   CATEGORY_LIST,
                   key="cat_filter")
else:
    # First run ⟶ we *must* provide the initial selection
    st.multiselect("Kategorie‑Filter",
                   CATEGORY_LIST,
                   key="cat_filter",
                   default=st.session_state.active_cats)

# --- keep our own tracking list in sync ----------------------------
st.session_state.active_cats = st.session_state.cat_filter

# --- manual change cancels pending preset --------------------------
if st.session_state.use_preset and st.session_state.cat_filter != preset_categories(False, 0):
    st.session_state.use_preset = False

# --- preset button just flips the flag -----------------------------
if st.button("🔄 Wetter‑basierten Filter anwenden"):
    st.session_state.use_preset = True

# ----------------------------------------------------------------------------- 
# 9. Search trigger
# -----------------------------------------------------------------------------
if st.button("Find Activities"):
    st.session_state.update(have_results=True, page=1)

if not st.session_state.have_results:
    st.stop()

# ----------------------------------------------------------------------------- 
# 10. Fetch activities + weather
# -----------------------------------------------------------------------------
try:
    token = get_access_token()
    acts_raw = get_activities(token, lat, lon, radius)
except Exception as e:
    st.error(f"API error: {e}")
    st.stop()

weather_rows = last_three_years_weather(lat, lon, ref_date)
st.subheader("📅 Weather on this date (last 3 years)")
if weather_rows:
    st.table(pd.DataFrame(weather_rows).set_index("Year"))
    rain_flag, avg_temp = classify_weather(weather_rows)
    st.markdown(
        f"{'Regen' if rain_flag else 'Kein Regen'} erwartet, "
        f"Durchschnittstemperatur **{avg_temp:.1f} °C**."
    )
else:
    st.warning("No weather data available.")
    rain_flag, avg_temp = False, float("nan")

# If preset requested, store new selection and rerun
if st.session_state.use_preset:
    st.session_state.cat_filter_new = preset_categories(rain_flag, avg_temp)
    st.session_state.use_preset = False
    # call new st.rerun() if available, else legacy function
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()

# Explanation expander
with st.expander("Wie funktioniert der Wetter‑Filter?"):
    st.markdown(
        """
**So funktioniert der Wetter‑basierte Filter**

1. **Regenerkennung**  
   Ein Tag zählt als *Regen*, wenn an mindestens **2 von 3 Jahren** an diesem Datum Niederschlag > 0 mm gemessen wurde.

2. **Temperatur‑Mittelwert**  
   ⌀ Temp = Mittel aus Tages‑Max und ‑Min jedes Jahres.

---

### Regeln für die Kategorie‑Vorauswahl

| Wetterlage | Vorausgewählte Kategorien |
|------------|---------------------------|
| Regen (≥2 von 3 Jahren) | Museums, Restaurants, Historical, Sightseeing |
| Kein Regen & ⌀ Temp < 15 °C | Museums, Historical, Tours, Sightseeing |
| Kein Regen & ⌀ Temp ≥ 15 °C | Wine, Historical |
"""
    )

# ----------------------------------------------------------------------------- 
# 11. Apply category filter, sort, paginate
# -----------------------------------------------------------------------------
kw_list = [
    kw for cat in st.session_state.active_cats for kw in KEYWORDS[cat]
] if st.session_state.active_cats else []

acts_filtered = [
    a for a in acts_raw
    if not kw_list or any(
        kw in (a.get("name", "") + " " + a.get("shortDescription", "")).lower()
        for kw in kw_list
    )
]

# Sorting
if sort_order != "None":
    reverse = "↓" in sort_order
    if "Rating" in sort_order:
        acts_filtered.sort(key=lambda a: float(a.get("rating") or -1), reverse=reverse)
    else:
        acts_filtered.sort(
            key=lambda a: float(a.get("price", {}).get("amount", float("inf"))),
            reverse=reverse,
        )

# Pagination
total = len(acts_filtered)
pages = max(1, math.ceil(total / page_size))
st.session_state.page = max(1, min(st.session_state.page, pages))
page = st.session_state.page
page_slice = acts_filtered[(page - 1) * page_size : page * page_size]

# ----------------------------------------------------------------------------- 
# 12. UI – pagination controls & headline
# -----------------------------------------------------------------------------
st.subheader("🗺️ Activities")
p_prev, p_mid, p_next = st.columns([1, 2, 1])
p_prev.button("⬅️ Prev", disabled=page == 1,
              on_click=lambda: st.session_state.__setitem__("page", page - 1))
p_next.button("Next ➡️", disabled=page == pages,
              on_click=lambda: st.session_state.__setitem__("page", page + 1))
p_mid.write(f"Page **{page}/{pages}** — {len(page_slice)} of {total}")

# ----------------------------------------------------------------------------- 
# 13. Render activity cards
# -----------------------------------------------------------------------------
if not page_slice:
    st.info("No activities match current filters.")

for a in page_slice:
    st.markdown(f"### {a.get('name', 'No Name')}")
    st.write(f"**Rating:** {a.get('rating', 'N/A')}")
    st.write(f"**Description:** {a.get('shortDescription', 'No description available.')}")

    price = a.get("price", {})
    price_txt = (
        f"{float(price.get('amount')):,.2f} {price.get('currencyCode', '')}"
        if price.get("amount") else "N/A"
    )
    st.write(f"**Price:** {price_txt}")
    st.write(f"**Duration:** {a.get('minimumDuration', 'N/A')}")

    if a.get("pictures"):
        st.image(a["pictures"][0], width=400)

    if a.get("bookingLink"):
        st.markdown(f"[📅 Book]({a['bookingLink']})", unsafe_allow_html=True)

    st.markdown("---")  # separator between activities
