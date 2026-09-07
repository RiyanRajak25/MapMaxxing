import os

from dotenv import load_dotenv

load_dotenv()

GEE_PROJECT_ID = os.getenv("GEE_PROJECT_ID", "")
GEE_SERVICE_ACCOUNT_EMAIL = os.getenv("GEE_SERVICE_ACCOUNT_EMAIL", "")
GEE_SERVICE_ACCOUNT_KEY = os.getenv("GEE_SERVICE_ACCOUNT_KEY", "")



def _origin_url(value: str) -> str:
	value = value.strip().rstrip("/")
	if value and not value.startswith(("http://", "https://")):
		return f"https://{value}"
	return value


CORS_ORIGINS = [
	_origin_url(origin)
	for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
	if origin.strip()
]

DEFAULT_CLOUD_THRESHOLD = int(os.getenv("DEFAULT_CLOUD_THRESHOLD", "20"))

# Hard cap on AOI size (km^2) so a single synchronous request stays fast and
# within Earth Engine's per-request compute limits.
MAX_AOI_KM2 = float(os.getenv("MAX_AOI_KM2", "3000"))

# AMCBI constraint bounds on k = Blue / SWIR1. See app/services/amcbi.py for why
# the default differs from the 0.6 published in the paper.
AMCBI_K_MIN = float(os.getenv("AMCBI_K_MIN", "0.42"))
AMCBI_K_MAX = float(os.getenv("AMCBI_K_MAX", "1.0"))

# Minimum mapping unit for change, in pixels. Change patches smaller than this
# are noise, not construction - see app/services/change_filters.py for the
# measurement this is calibrated on. Set to 1 to disable the filter entirely.
CHANGE_MIN_PIXELS = int(os.getenv("CHANGE_MIN_PIXELS", "11"))

# Correct the earlier period onto the later one's radiometric scale before
# classifying. Measured on Bhopal neighbourhoods that cannot have changed, the
# 2017 composite under-detects built-up by ~12% relative to 2023, which roughly
# doubled the reported growth. See app/services/normalization.py. Set to 0 to
# reproduce uncorrected numbers.
NORMALIZE_PERIODS = os.getenv("NORMALIZE_PERIODS", "1") not in {"0", "false", "False"}
NO_CHANGE_PERCENTILE = int(os.getenv("NO_CHANGE_PERCENTILE", "50"))

# How far inside Eq. 3's bounds a pixel must sit before a change call is made.
# Declaring change by comparing two independent hard thresholds double-counts the
# noise of both periods; requiring a margin at both ends means a pixel hovering
# on a bound produces no call rather than a coin flip. 0 restores the plain
# comparison. See app/services/change_filters.py for the measured trade-off.
CHANGE_CONFIDENCE_MARGIN = float(os.getenv("CHANGE_CONFIDENCE_MARGIN", "0.02"))
