"""Constants for dbus_bluetooth_tracker."""

DOMAIN = "dbus_bluetooth_tracker"
CONF_TRACKED_MACS = "tracked_macs"
CONF_INTERVAL = "interval_seconds"
CONF_CONSIDER_HOME = "consider_home"
CONF_ADAPTER = "adapter"
CONF_DBUS_ADDRESS = "dbus_address"
CONF_SEEN_INTERVAL = "seen_interval_seconds"


DEFAULT_ADAPTER = "auto"


DEFAULT_INTERVAL = 30
DEFAULT_SEEN_INTERVAL = 60
DEFAULT_CONSIDER_HOME = 180

PLATFORMS = ["device_tracker"]

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

SCAN_NEARBY_DEVICES_SCHEMA = vol.Schema(
    {
        vol.Optional("timeout", default=10): cv.positive_int,
    }
)

