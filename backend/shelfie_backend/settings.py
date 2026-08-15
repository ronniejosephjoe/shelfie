"""
Django settings for shelfie_backend.

Kept deliberately small: this is an 8-hour take-home, not a production
service. No auth, no deployment config — see README for what was cut
and why.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# pytesseract shells out to the tesseract binary, passing it an image via
# a temp file written through Python's tempfile module -- which defaults
# to $TMPDIR, or plain /tmp if that's unset. On macOS, /tmp is a symlink
# to /private/tmp, and at least one real Homebrew tesseract/Leptonica
# build we tested against (found by actually running this against a real
# Mac, not by inspection) fails to read temp files through that symlink
# in some shell contexts, silently returning zero OCR results -- which
# spine_detector.py's broad except-and-return-[] then reports as "no
# spines found" instead of the environment bug it actually is. Pointing
# tempfile at a real (non-symlinked) directory inside the project sidesteps
# it entirely, regardless of what TMPDIR happens to be in the parent shell.
import tempfile as _tempfile  # noqa: E402

_SAFE_TMP_DIR = BASE_DIR / "tmp"
_SAFE_TMP_DIR.mkdir(exist_ok=True)
_tempfile.tempdir = str(_SAFE_TMP_DIR)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-secret-key-not-for-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

# Wide open on purpose: this runs on a laptop for a demo, reached from an
# Expo app on the same LAN. Not something to ship as-is.
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "catalog",
    "scanner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Dev-only: the Expo app is served from a Metro dev server on an
# arbitrary LAN address/port, so we can't pin an origin list ahead of time.
CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = "shelfie_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "shelfie_backend.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
    ],
    # No auth: out of scope for this exercise (see README -> "What we cut").
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

# --- Shelfie-specific config -------------------------------------------
# Catalog CSV lives at the repo root so it's easy to find and diff.
CATALOG_CSV_PATH = os.environ.get(
    "CATALOG_CSV_PATH", str(BASE_DIR.parent / "catalog.csv")
)

# Confidence thresholds used by catalog.matching.MatchResult classification.
# See catalog/matching.py for how the score itself is computed.
MATCH_AUTO_ACCEPT_THRESHOLD = float(os.environ.get("MATCH_AUTO_ACCEPT_THRESHOLD", "0.86"))
MATCH_REVIEW_THRESHOLD = float(os.environ.get("MATCH_REVIEW_THRESHOLD", "0.55"))

# Hosted vision-language model. See scanner/services/vlm_client.py.
VLM_PROVIDER = os.environ.get("VLM_PROVIDER", "mock")  # "openai" | "gemini" | "mock"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")

# Gemini: the free-tier-eligible option (no credit card needed at
# aistudio.google.com), added specifically because OpenAI's API
# requires billing to be enabled with no free quota. See vlm_client.py.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_VISION_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-3.1-flash-lite")
# Free-tier calls are $0 regardless of what GEMINI_PRICING_PER_1M says;
# set this once you've actually enabled billing on the Google account,
# so estimated_cost_usd stays honest instead of guessing paid-tier
# rates against what might still be free traffic.
GEMINI_BILLING_ENABLED = os.environ.get("GEMINI_BILLING_ENABLED", "false").lower() == "true"

# Free-tier Gemini quotas are low (single-digit-to-teens requests per
# minute) and the pipeline reads every detected spine sequentially --
# found by actually running a 26-spine photo through it and watching
# most calls come back 429 after the first several. This spaces calls
# out proactively; see GeminiVisionClient's docstring in vlm_client.py
# for the full reasoning and the reactive backoff that backs it up.
# Set to 0 for a paid-tier account with no meaningful RPM ceiling.
GEMINI_MIN_CALL_INTERVAL_SECONDS = float(os.environ.get("GEMINI_MIN_CALL_INTERVAL_SECONDS", "4.5"))

VLM_TIMEOUT_SECONDS = float(os.environ.get("VLM_TIMEOUT_SECONDS", "20"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "shelfie": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
    },
}
