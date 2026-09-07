# Urban Change Detection — project context

Detects built-up area change between two time periods over a user-drawn AOI, using
free Copernicus Sentinel-2 imagery via Google Earth Engine. Implements **AMCBI**
from `s12518-026-00754-7.pdf` (Smitha et al., Applied Geomatics 2026), included in
this repo.

Python/FastAPI backend + React/Leaflet frontend. See `README.md` for setup.

## Commands

```powershell
# Backend (from backend/)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
.\.venv\Scripts\python.exe -m pytest tests/ -q

# Verification scripts, in the order they are useful
.\.venv\Scripts\python.exe scripts\verify_gee_auth.py     # credentials work?
.\.venv\Scripts\python.exe scripts\run_bhopal.py          # paper's case study
.\.venv\Scripts\python.exe scripts\run_bhoj_wetland.py    # encroachment by distance
.\.venv\Scripts\python.exe scripts\diagnose_amcbi.py      # which Eq. 3 condition rejects what
.\.venv\Scripts\python.exe scripts\diagnose_stability.py  # how much detected change is noise
.\.venv\Scripts\python.exe scripts\diagnose_loss.py       # what the "lost" buildings really are
.\.venv\Scripts\python.exe scripts\verify_stable_core.py  # is normalisation justified?
.\.venv\Scripts\python.exe scripts\evaluate_normalization.py  # its effect on each test
.\.venv\Scripts\python.exe scripts\evaluate_change_filters.py  # tune the speckle filter
.\.venv\Scripts\python.exe scripts\evaluate_voting.py     # voting vs median compositing
.\.venv\Scripts\python.exe scripts\calibrate_k.py         # measure k for a new region

# Frontend (from frontend/)
npm run dev
npm run build
```

**`uvicorn --reload` does not reliably pick up edits here.** The project sits in a
OneDrive-synced folder, where the file-watch events the reloader depends on get
missed. A stale server then rejects valid requests with confusing
`Field required` errors naming fields that no longer exist in the source, and
orphaned `multiprocessing` children can keep port 8000 held after the parent
dies. After changing backend code, kill every python process whose command line
mentions this project (check `Get-CimInstance Win32_Process`, not just the port)
and start uvicorn fresh **without** `--reload`.

Auth is **user OAuth** (`earthengine authenticate`), not a service account.
`GEE_PROJECT_ID=gee-urban` in `backend/.env`. Earth Engine does **not** accept
`AIzaSy...` Cloud API keys — it returns "API keys are not supported by this API."

## Things that are not obvious from the code

### 1. The paper's k = 0.6 is wrong for L2A. Do not "restore" it.

Eq. 3 constrains `k = Blue / SWIR1`. The paper uses `k >= 0.6`, derived from
Fig. 2a where built-up blue is ~0.21. Real Sentinel-2 **L2A surface reflectance**
over built-up gives blue ~0.10 — the paper's figures are on a *top-of-atmosphere*
scale (Rayleigh scattering inflates blue; atmospheric correction removes it).

Applying 0.6 to L2A rejected 97.7% of the scene. Bhopal came out at 8.9 km²
built-up for a city covering 100+ km², with 67.5% "growth".

Default is now `AMCBI_K_MIN = 0.42` (below the lowest measured built-up k of 0.463,
above the scene's soil/vegetation bulk at 0.389). Bhopal then gives **35.1%**
against the paper's reported **31.8%**.

`tests/test_amcbi_reference.py` pins both claims separately: the paper's Eq. 3
reading against the paper's own spectra, and the L2A calibration against measured
Bhopal reflectances. If someone changes K_MIN, those tests are what catch it.

### 2. Eq. 3 as published is unsatisfiable

It prints as `(0.6 >= k >= 1)`. Read as `K_MIN <= k <= K_MAX` per the surrounding
text. The upper bound excludes water, where blue exceeds SWIR1.

### 3. Statistics use pixels valid in BOTH periods

`change_detection.py` intersects the two cloud masks before measuring. Without
this, differing cloud cover between dates masquerades as real change.

### 4. Use S2_SR_HARMONIZED, never S2_SR

Scenes after 2022-01-25 carry `BOA_ADD_OFFSET = -1000`. A normalized difference
does not cancel an additive offset, and `k` is distorted outright. HARMONIZED
corrects it. This silently corrupts multi-year comparisons otherwise.

### 5. Sentinel-2 L2A starts 2017-03-28

Earlier dates are rejected with an explanation. L1C reaches back to 2015 but lacks
atmospheric correction, which the k constraint depends on.

### 6. Earth Engine limits already hit here

- `reduceRegion` over ~20 bands exceeds the 80 MiB per-tile budget → `tileScale=4`.
- Convolution kernels cap at **512 px**. A 2500 m euclidean kernel at 10 m native
  is 501 px with no headroom, so `wetland.py` reprojects to 25 m first — in the
  image's **native UTM**, because web Mercator would stretch distances ~9% at 23°N.

### 7. Map layers must be masked binaries, not the continuous index

Rendering AMCBI's -1..1 range paints non-built-up (negative) as opaque black over
the whole AOI, hiding every layer beneath. All result layers use `.selfMask()` with
a single-colour flat palette and explicit `min`/`max`.

Leaflet also shares one `tilePane`, where paint order follows DOM insertion and the
basemap can end up on top. Every layer sets an explicit `zIndex` (basemap 100,
results 210-250). Colours live in **two places that must stay in sync**:
`backend/app/services/change_detection.py` and `frontend/src/components/LayerToggle.jsx`.

### 7a. Most raw change is noise, and loss is how you know

The k margin from §1 is thin: real built-up sits only ~0.05-0.08 above `K_MIN`,
and `k = Blue / SWIR1` divides by the band with the largest residual left by
atmospheric correction, at the low reflectance where its relative error is worst.
The noise is the same size as the margin, so boundary pixels flip between periods
on measurement error alone.

**Loss is the free accuracy check.** Buildings do not disappear, so in a growing
city, measured loss is almost entirely false change. Unfiltered, Bhopal 2017→2024
reported **12.60 km² of loss against a 63.22 km² city — 20% of Bhopal
demolished.** That was the tell.

**The null test quantifies it without any reference data.** Compare two composites
of the *same* dry season, where true change is zero by construction:
Nov–Dec 2023 vs Jan–Feb 2024 reported **30.6 km² of change and −6.2% "growth"**.
That is the noise floor. `scripts/diagnose_stability.py` runs it.

**What the lost pixels actually are** (`scripts/diagnose_loss.py`):

- **59.3%** still satisfy Eq. 1 in 2024 — they still look built-up spectrally,
  and only the `k` constraint rejected them.
- Their **median 2024 k is 0.391** against `K_MIN = 0.42`. The typical
  "demolished" pixel missed the bound by **0.029**. 53.5% sit within 0.04 of it.
- None of the four known dense-urban probes fall in the loss class — their k
  runs 0.473-0.608. **The churn is at the sparse and mixed-pixel margins of the
  urban fabric, not the core.**
- A tail of lost pixels has k > 1 (p90 = 2.36). Those are shoreline pixels that
  became water — real surface change, but lake level, not demolition. See the
  MNDWI caveat below.

Three *separate* errors came out of this, needing different treatment:

- **Boundary churn.** Fixed: hysteresis. A gain call requires decisively
  non-built-up before *and* decisively built-up after, by
  `CHANGE_CONFIDENCE_MARGIN` (0.02); loss requires the reverse. A pixel hovering
  on a bound gets no call rather than a coin flip. Symmetric, so it cannot bias
  direction. `K_MAX` is never relaxed — above it is water.
- **Speckle.** Fixed: a minimum mapping unit drops change patches under
  `CHANGE_MIN_PIXELS` (11 px = 1100 m²), also symmetric.
- **Net-level bias.** Largely a *radiometric* problem, now corrected — see §7b.
  Filtering could never touch it: the whole classification level shifts, and
  per-scene voting made it **worse** (−10.3%), which is the evidence it is
  systematic rather than random. Two mitigations, both now in place: a fixed
  calendar window (periods are years, §7c) and relative normalisation (§7b).

Measured trade-off for the margin (Bhopal, MMU already applied). True loss is
~0, so the real-loss column is almost all error, and the null columns should be 0:

| margin | real gain | real loss | null gain | null loss |
| --- | --- | --- | --- | --- |
| none | 21.51 | 3.66 | 2.62 | 6.06 |
| **0.02** (shipped) | **11.61** | **1.64** | **1.23** | **1.98** |
| 0.04 | 6.14 | 0.76 | 0.67 | 0.67 |

0.02 is the knee: known error falls 5× while gain keeps just over half its
recall. It does **not** rescue every standing building — the median lost pixel is
0.029 below `K_MIN`, outside a 0.02 margin — and the tests say so explicitly.

Consequences to respect:

- **`loss_km2` is a reliability metric, not a finding.** Buildings do not
  disappear, so in a growing city it reads out classifier commission error under
  exactly that AOI and date pair. The API flags this with
  `loss_is_error_estimate`; the UI labels the layer "No longer detected
  (instability)". Never report it as demolition.
- **Read `net_change_km2` as the growth figure, not `gain_km2`.** Gain
  deliberately under-counts — hysteresis withholds marginal pixels — so it does
  not reconcile with net. Raw and filtered values are both in the response.
- Do not quote growth to three significant figures. **35.1% carries roughly
  ±10 points.** A one-year run over Bhopal reports +13.0% where reality is 2-4%.
- A high `change_filtered_pct` is a warning about that run, not a landscape
  property. Bhopal 2017→2024 sits at **72.0%**.

### 7b. The 2017 imagery under-detects built-up. Uncorrected, growth reads double.

**This is the single largest error found in the project, larger than the k
miscalibration.** L2A is atmospherically corrected but not comparable across
years: Sen2Cor's processing baseline changed repeatedly between 2017 and 2024,
and residual aerosol/sun-angle/BRDF differences leave a per-date offset. It lands
hardest on **blue** — the numerator of `k`, the term with the thinnest margin.

Measured on ground that **cannot** have changed — dense Bhopal neighbourhoods
built decades before 2017 and still standing, so their built-up fraction is a
fixed quantity and any difference is pure classifier bias
(`scripts/verify_stable_core.py`):

| stable core built-up fraction | 2017 | 2023 | gap |
| --- | --- | --- | --- |
| uncorrected | 0.605 | 0.677 | **−0.071** |
| normalised | 0.638 | 0.677 | −0.039 |

The 2017 composite under-detects built-up by **~12% relative**. Starting from an
artificially low base inflated Bhoj Wetland growth from ~13% to a reported
**27.4%**.

**Fix:** pseudo-invariant-feature normalisation, mean-sigma variant. Rank pixels
by spectral distance, keep the stablest 50% as presumed-unchanged, fit per-band
gain/offset so period 1's mean and SD match period 2's over that set, apply.
Period 2 is *always* the reference — `K_MIN = 0.42` was calibrated on 2023-24
reflectances, so moving period 2 would slide the imagery out from under its own
calibration. See `app/services/normalization.py`.

Two independent routes agree on the answer: scaling 2017 up by the measured 12%
core bias predicts **+13.9%**; running the pipeline on normalised imagery gives
**+13.1%**.

**Limits — do not oversell this.** Normalisation closes about **45%** of the
measured bias, not all of it. A −0.039 residual remains on stable ground, so
reported growth is still biased **high**; correcting the residual too would put
Bhoj nearer **+7%**. Treat the normalised figure as an *upper bound*. It also
does not fix year-to-year level instability: a one-year null test still reports
+9.4% where reality is 2-4%.

### 7c. Periods are years, not date ranges

Free start/end dates made it trivial to compare November against April and read
seasonal phenology as urban growth. A year now expands server-side to the fixed
post-monsoon dry season, **1 Nov → end of Feb** (`imagery.dry_season_window`,
leap-year aware). This is the *only* mitigation that exists for seasonal bias, so
do not reintroduce free ranges. Only years with a **completed** season are
offered — a half-finished season composites fewer scenes, and that imbalance is
itself what destabilises the classification level.

### 8. Change is 10 m per pixel

At city zoom one screen pixel covers 35-70 m, so scattered change washes out. Users
need zoom 14+. Do not dilate pixels for display — it misrepresents measured area.

## Validated results

Prototype scope is the **Bhoj Wetland only**; Greater Bhopal was dropped from the
app (`scripts/run_bhopal.py` is kept purely to reproduce the paper's figure).

Bhoj Wetland, **2017 vs 2024** dry seasons (the paper's study period), normalised
— the numbers to quote. Verified through the live API, not just the scripts:

| Zone | Growth | Built-up 2017 |
| --- | --- | --- |
| **All zones** | **+13.9%** | 54.85 km² → 62.45 km² |
| 0-500 m from water | **−2.0%** | 3.71 km² |
| 500-1000 m | +7.0% | 5.24 km² |
| 1000-2000 m | +11.6% | 11.44 km² |
| beyond 2000 m | **+17.4%** | 34.43 km² |

Confident gain/loss 6.86 / 1.73 km² against raw 21.45 / 13.84 km² (75.6%
rejected). Tile layers verified by decoding pixels at z14: all five render with
the right palette and 0.7-3.9% coverage.

The monotonic gradient **survives normalisation** — a real robustness check, not
a restatement. The shoreline belt reads slightly negative, i.e. no measurable
encroachment.

Uncorrected numbers, kept only so the size of the bias stays visible: Bhoj
+27.4% all-zones, +5.4% shoreline, +33.0% outer; Greater Bhopal +35.1%. **Do not
quote these.**

Measured error floors (`scripts/diagnose_stability.py`, Bhopal AOI):

| Test | True change | Reported |
| --- | --- | --- |
| Same dry season, split in half | 0% | −6.2%, 30.6 km² churn |
| One year apart, matched windows | ~2-4% | +13.0% |
| Real 7-year run | — | +35.1%, 47.4 km² raw churn |

The 7-year signal is ~4× the within-season swing, so its direction and rough
magnitude hold. Its precision does not. The wetland distance gradient is
unaffected by the speckle filter — ring growth is computed from the
classifications, not from gain/loss — so that conclusion stands unchanged.

The wetland gradient rises monotonically *with distance from the lake* — growth is
lowest at the shoreline. That belt was only 13% built-up in 2017, so this is not
saturation. Plausible causes are statutory (Ramsar 2002, Van Vihar National Park,
Full Tank Level restrictions), but imagery shows the pattern, not the cause. Do not
overstate this.

MNDWI water area came out +20.4% between periods. That is lake level between dry
seasons, not wetland growth. Do not report it as real change.

## Working agreements

- **Verify, do not assume.** Numbers that look plausible have been wrong here twice.
  Sanity-check magnitudes against real-world knowledge before reporting.
- Tile rendering is verified by decoding actual PNG pixels
  (see the coverage/colour check pattern), not by HTTP 200 alone.
- No browser automation is available in this environment. UI changes compile and
  serve but are visually unverified — say so rather than implying otherwise.

## Not built yet

- The paper's accuracy module (SDI / Jeffries-Matusita / Bhattacharyya / F1)
  against user-supplied reference points
- GeoTIFF / CSV export
- NDBI / NBI / BAEI comparison layers (paper's Table 3 head-to-head)
- More than two periods at once
