"""Constants for the Glowmarkt (Bright) integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

DOMAIN: Final = "bright_meter"

BASE_URL: Final = "https://api.glowmarkt.com/api/v0-1"

# Public application ID for the Bright consumer app, published in the Glowmarkt
# "API Data Retrieval — Individual User for Bright" documentation (v1.8,
# 09 April 2026). It identifies the calling application, not the user.
APPLICATION_ID: Final = "b0f1b774-a586-4f72-9edd-27ead8aa7a8d"

CONF_BACKFILL_DAYS: Final = "backfill_days"
DEFAULT_BACKFILL_DAYS: Final = 365
MAX_BACKFILL_DAYS: Final = 3650

# Consumption data reaches Glowmarkt from the DCC with roughly a 30 minute lag,
# so polling more often than this just burns API calls for no new data.
UPDATE_INTERVAL: Final = timedelta(minutes=30)

# The API caps PT1H reading queries at 31 days. Chunk at 30 to leave headroom so
# an inclusive from/to range can never trip the limit.
HOURLY_CHUNK_DAYS: Final = 30

# Glowmarkt only pulls fresh readings from the DCC when something asks it to —
# the Bright app does this whenever you open it. Without it the readings
# endpoint keeps answering 0 for hours it has no data for. The catchup call is
# rate limited to once every two hours, so stay just outside that.
CATCHUP_INTERVAL: Final = timedelta(hours=2, minutes=5)

# Readings arrive late and get revised, sometimes days later, so re-import a
# trailing window on every pass rather than only strictly-new hours. A week
# reaches past the 24 to 48 hour delays reported against the DCC feed, and costs
# one request per resource per pass.
REFRESH_WINDOW_HOURS: Final = 7 * 24

# How far back to look for the stored running total that anchors a re-import.
# Longer than the refresh window, so a feed that stalled for a while still
# finds its anchor instead of rebuilding the whole series.
ANCHOR_LOOKBACK_DAYS: Final = 30

# How long to let an in-flight backfill finish when the entry is going away.
# It must be allowed to finish rather than be cancelled: its recorder queries
# run in an executor thread, and abandoning them leaves that thread working
# against a database the recorder is already disposing of.
BACKFILL_SHUTDOWN_TIMEOUT: Final = 30

PERIOD_HOURLY: Final = "PT1H"
PERIOD_DAILY: Final = "P1D"
FUNCTION_SUM: Final = "sum"

# Glowmarkt reports money in pence; Home Assistant wants major units.
PENCE_PER_POUND: Final = 100.0
CURRENCY_GBP: Final = "GBP"


@dataclass(frozen=True, kw_only=True)
class ClassifierInfo:
    """Describes how one Glowmarkt resource classifier maps into HA."""

    label: str
    is_cost: bool
    icon: str


# Only classifiers listed here produce entities and statistics. Reactive-power
# classifiers (electricity.import.reactive / electricity.export.reactive) are
# deliberately omitted: they are only meaningful for large non-domestic sites.
SUPPORTED_CLASSIFIERS: Final[dict[str, ClassifierInfo]] = {
    "electricity.consumption": ClassifierInfo(
        label="Electricity", is_cost=False, icon="mdi:flash"
    ),
    "electricity.consumption.cost": ClassifierInfo(
        label="Electricity cost", is_cost=True, icon="mdi:currency-gbp"
    ),
    "electricity.export": ClassifierInfo(
        label="Electricity export", is_cost=False, icon="mdi:transmission-tower-export"
    ),
    "gas.consumption": ClassifierInfo(label="Gas", is_cost=False, icon="mdi:fire"),
    "gas.consumption.cost": ClassifierInfo(
        label="Gas cost", is_cost=True, icon="mdi:currency-gbp"
    ),
}
