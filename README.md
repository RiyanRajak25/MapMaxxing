# Urban Change Detection

Draw an area on a map, pick two time periods, and get a built-up change map plus
area statistics — computed from free Copernicus Sentinel-2 imagery.

Built-up areas are detected with **AMCBI** (Advanced Multispectral Constraint-Based
Built-up Index) from Smitha et al., *"Built-up area mapping for sustainable urban
planning"*, Applied Geomatics (2026) — included in this repo as
`s12518-026-00754-7.pdf`.

## Why AMCBI

Classic built-up indices (NDBI, NBI, BAEI) confuse built-up surfaces with bare
soil, because both are bright in SWIR. They also need a hand-tuned threshold per
scene, which does not transfer across places or seasons.

AMCBI adds a constraint step that fixes both problems:

1. **Spectral transformation** (Eq. 1) — a normalized difference of
   `(SWIR1 + Red)` against `(NIR + Blue)`.
2. **Constraint elimination** (Eq. 2-3) — the ratio `k = Blue / SWIR1` separates
   built-up (which sits near the 1:1 blue/SWIR1 line, `k ~ 0.67`) from bare soil
   and rock (far above it, `k ~ 0.4`). Pixels failing `0.6 <= k <= 1` are forced
   negative regardless of how strongly step 1 scored them.

The result needs **no thresholding**: positive means built-up, negative means not.
The paper reports F1 > 0.95 and overall agreement > 97% across six sites spanning
tropical, arid, humid and steppe climates.

`backend/tests/test_amcbi_reference.py` verifies this implementation reproduces the
paper's classification for all five land-cover types in its Fig. 2 — including the
bare soil and rock elimination that is the method's central claim.

> **Note on Eq. 3.** The paper prints its constraint as `0.6 >= k >= 1`, which is
> unsatisfiable. This implementation reads it as `k_min <= k <= k_max`, following
> the surrounding text and Fig. 2. The tests prove that reading is right.

### The k threshold had to be recalibrated

**This project does not use the paper's `k >= 0.6`, and you should know why.**

The paper's Fig. 2a reports built-up blue ~0.21 and SWIR1 ~0.31, giving `k ~ 0.68`.
Measured on real Sentinel-2 L2A surface reflectance over Bhopal, built-up gives
blue ~0.10 and SWIR1 ~0.21, so `k ~ 0.46-0.56`. The paper's blue is roughly double
true bottom-of-atmosphere reflectance, which is the signature of *top-of-atmosphere*
values: Rayleigh scattering inflates the blue band, and atmospheric correction
removes it.

Applied to L2A, `k >= 0.6` rejects nearly all genuine built-up. Measured over a
Bhopal composite:

| | share of AOI |
| --- | --- |
| passes Eq. 1 (`AMCBI* > 0`) | 48.2% |
| passes `k >= 0.6` | **2.3%** |
| classified built-up | 1.7% |

Every hand-checked urban location in Bhopal (New Market, Old City, Habibganj,
MP Nagar) passed Eq. 1 and failed the k constraint. Built-up came out at 8.9 km²
for a city covering well over 100 km².

The default is now **`AMCBI_K_MIN = 0.42`**, which sits below the lowest measured
built-up `k` (0.463) and above the 75th percentile of the scene (0.389), which is
dominated by soil and vegetation. Effect on the Bhopal 2017→2024 case study:

| | k = 0.6 | k = 0.42 |
| --- | --- | --- |
| Built-up 2017 | 8.9 km² | 63.2 km² |
| Built-up 2024 | 14.9 km² | 85.4 km² |
| Growth | 67.5% | **35.1%** |

The paper reports **31.8%** for the same city and years. Both `AMCBI_K_MIN` and
`AMCBI_K_MAX` are configurable in `.env`, and `scripts/calibrate_k.py` measures the
right value for a new region.

## How it works

```
AOI + two date ranges
   -> Sentinel-2 L2A scenes (COPERNICUS/S2_SR_HARMONIZED), filtered by cloud cover
   -> cloud/shadow/cirrus/snow masked via the SCL band, median composite per period
   -> AMCBI computed per period -> binary built-up map per period
   -> restricted to pixels cloud-free in BOTH periods
   -> difference -> gain / loss layers + area statistics
```

Two details that matter for correctness:

- The **HARMONIZED** collection is used so scenes before and after the January 2022
  processing baseline change (which added a +1000 reflectance offset) are
  comparable. Without it, multi-year comparisons are biased.
- Statistics are computed only over pixels that are cloud-free in **both** periods,
  so the two dates are always measured over the same ground. Otherwise differences
  in cloud coverage would masquerade as real change.

## Setup

### 1. Earth Engine access (do this first)

Imagery is read through Google Earth Engine, free for noncommercial use.

**Earth Engine does not accept `AIzaSy...` Cloud API keys** — only OAuth. See
[`backend/SETUP.md`](backend/SETUP.md); the short version is `earthengine
authenticate` plus a project registered at
https://code.earthengine.google.com/register.

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

cp .env.example .env            # set GEE_PROJECT_ID
python scripts/verify_gee_auth.py
python scripts/run_bhopal.py    # the paper's case study, end to end
```

Once verification passes:

```bash
uvicorn app.main:app --reload --port 8000
```

Check http://localhost:8000/health — it should report
`"earth_engine": "connected"`. API docs at http://localhost:8000/docs.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

## Using it

1. Click **Load example: Bhopal, India**, or draw your own area with the square or
   polygon tool.
2. Set the two date ranges. **Use the same months in both periods** — vegetation
   and soil moisture shift seasonally, and the paper's Table 5 shows this is the
   single biggest source of spurious change.
3. Adjust the cloud-cover limit if no imagery is found.
4. Click **Run analysis**.

Map layers, all rendered as flat masked classes (transparent where the class is
absent, so the basemap stays readable):

| Layer | Colour |
| --- | --- |
| New built-up (gain) | red `#ff2d2d` |
| Lost built-up (loss) | grey `#9e9e9e` |
| Built-up, period 1 | amber `#ffc300` |
| Built-up, period 2 | cyan `#00e5ff` |
| Open water | blue `#1d7fe0` |

Red for growth is deliberate: on this tool, expansion onto a wetland is the
finding of concern, not a success metric. The sidebar uses the same colours.

**Zoom in to see change pixels.** Change is detected per 10 m pixel. At city zoom
one screen pixel covers 35-70 m, so scattered change washes out. Past zoom 14 each
change pixel is at least one screen pixel.

## Known limits

- **Archive start.** Sentinel-2 surface reflectance begins **2017-03-28**. Earlier
  dates are rejected with an explanation.
- **Seasonality.** Comparing a wet-season date against a dry-season one overstates
  change. Match your months.
- **Snow and high altitude.** The paper flags snow-covered mountains as a failure
  case; snow is masked here, leaving gaps rather than errors.
- **Resolution.** Sentinel-2 is 10 m — reliable for neighbourhood-scale sprawl, not
  individual buildings.
- **The `k` constant is reflectance-basis dependent** (see above). The default
  0.42 was calibrated on Sentinel-2 L2A over Bhopal. Other regions, seasons, or a
  different correction level may need retuning — run
  `python scripts/calibrate_k.py`, or set `AMCBI_K_MIN` in `.env`.
- **Loss is noisier than gain.** Real cities rarely lose built-up area, so a large
  loss figure usually indicates classification instability between composites
  (cloud edges, moisture, shadow) rather than demolition. Treat gain as the more
  trustworthy signal.
- **AOI size** is capped (default 3000 km²). Change `MAX_AOI_KM2` in `.env`.

## Case study: Bhoj Wetland encroachment

The paper picks Bhopal because "unchecked urbanization along the periphery of the
lakes" degrades the Bhoj Wetland (Ramsar site, 2002). A single city-wide growth
figure cannot test that claim, so `scripts/run_bhoj_wetland.py` measures built-up
change binned by distance from the shoreline. Water is delineated per period with
MNDWI rather than a fixed polygon; rings are fixed to the earlier shoreline so
both dates are measured against the same geography.

Result for 2017/18 vs 2023/24 dry season:

| Zone | Land | Built 2017 | Built 2024 | Growth | % built 2017 |
| --- | --- | --- | --- | --- | --- |
| 0-500 m from water | 29.8 km² | 3.86 km² | 4.07 km² | **+5.4%** | 13.0% |
| 500-1000 m | 27.1 km² | 5.03 km² | 5.95 km² | +18.3% | 18.5% |
| 1000-2000 m | 50.5 km² | 10.58 km² | 13.12 km² | +24.1% | 21.0% |
| beyond 2000 m | 281.5 km² | 29.92 km² | 39.80 km² | **+33.0%** | 10.6% |
| **All zones** | | 49.39 km² | 62.94 km² | **27.4%** | |

27.4% overall against the paper's 31.8% for the city.

**The gradient runs opposite to naive expectation.** Growth rises monotonically
with distance from the lake: the shoreline belt grew least. This is not a
saturation artefact - that belt was only 13% built-up in 2017, so land was
available and largely not built on. Plausible causes are statutory (Ramsar
status, Van Vihar National Park on the Upper Lake's southern shore, Full Tank
Level construction restrictions), but imagery shows the pattern, not the cause.

Two caveats the script prints and you should not ignore:

- **Water area came out +20.4%.** That is almost certainly lake level differing
  between two dry seasons, not the wetland growing. MNDWI measures the surface on
  the imaging dates.
- **Ring assignment runs at 25 m**, not 10 m. Earth Engine caps convolution
  kernels at 512 px, so the distance transform is computed coarser than the
  imagery. Ring boundaries are accurate to roughly a pixel at that scale.

## Alternatives to Earth Engine

Earth Engine is a dependency worth being deliberate about: noncommercial-use only,
quota-limited, and it requires project registration. The same Copernicus data is
available without it, via STAC catalogues and Cloud-Optimized GeoTIFFs — COGs allow
windowed HTTP range reads, so only the AOI is fetched, not whole multi-GB scenes.

| Source | Auth | Compute |
| --- | --- | --- |
| **AWS Earth Search** (`earth-search.aws.element84.com/v1`) | none | yours |
| **Microsoft Planetary Computer** | anonymous token | yours |
| **CDSE** (dataspace.copernicus.eu) | free account | yours, or server-side via openEO / Sentinel Hub |

Before switching, know what Earth Engine currently absorbs for us:

1. **The processing baseline offset.** Scenes after 2022-01-25 carry
   `BOA_ADD_OFFSET = -1000`. This is *not* cosmetic for AMCBI: a normalized
   difference does not cancel an additive offset, and `k = Blue / SWIR1` is
   distorted outright. Getting it wrong makes multi-year comparisons quietly
   incorrect rather than obviously broken. `S2_SR_HARMONIZED` handles it; raw AWS
   assets do not. Element 84's `sentinel-2-c1-l2a` (Collection 1) is consistently
   reprocessed and is the safer non-GEE choice.
2. **Resolution mismatch.** B11 is 20 m, B2/B4/B8 are 10 m — resample before the
   band math.
3. **Reprojection and mosaicking.** Scenes are UTM; AOIs are WGS84 and can straddle
   MGRS tiles.
4. **Tile serving.** Earth Engine returns `{z}/{x}/{y}` URLs; self-hosted needs
   `rio-tiler` or pre-rendered PNGs.

`app/services/imagery.py` is a clean seam for the source swap, but
`app/services/change_detection.py` returns Earth Engine tile URLs, so a second
rendering path is needed too. This is not a drop-in replacement.

## Not built yet

- The paper's full accuracy module (SDI / Jeffries-Matusita / Bhattacharyya / F1)
  against user-supplied reference points
- GeoTIFF and vector export
- NDBI / NBI / BAEI comparison layers
- More than two time periods at once

## Layout

```
backend/
  app/services/amcbi.py             AMCBI, Eq. 1-3
  app/services/imagery.py           Sentinel-2 search, cloud masking, compositing
  app/services/change_detection.py  bi-temporal diff, gain/loss, area statistics
  app/services/gee_auth.py          OAuth / service account initialization
  app/routers/change_detection.py   POST /api/change-detection
  scripts/verify_gee_auth.py        credential check
  scripts/run_bhopal.py             the paper's case study, end to end
  scripts/diagnose_amcbi.py         decompose Eq. 3, see which condition rejects what
  scripts/calibrate_k.py            measure the right k threshold for a region
  tests/                            AMCBI validation against the paper and L2A
frontend/
  src/components/                   map, AOI drawing, controls, statistics
  src/presets.js                    Bhopal AOI and date windows
  src/api/client.js                 backend client
```

## Tests

```bash
cd backend
.venv\Scripts\python.exe -m pytest tests/ -q
```
