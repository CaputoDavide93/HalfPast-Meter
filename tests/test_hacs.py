"""The repository is installable by HACS.

HACS's own action cannot read a private repository's files (it fetches them
anonymously, and gets a 404), so while this repository is private its
`hacsjson` and `integration_manifest` checks are skipped in CI. These tests
make the same checks locally instead, with the schemas copied from HACS's
source (hacs/integration, custom_components/hacs/utils/validate.py, MIT), so
going public is a visibility switch, not a repair job.
"""

from __future__ import annotations

import json
from pathlib import Path

import voluptuous as vol
from awesomeversion import AwesomeVersion
from homeassistant.helpers.config_validation import url as url_validator

ROOT = Path(__file__).resolve().parents[1]

# Copied from HACS: HACS_MANIFEST_JSON_SCHEMA (country validator simplified).
HACS_JSON_SCHEMA = vol.Schema(
    {
        vol.Optional("content_in_root"): bool,
        vol.Optional("country"): vol.Any(str, [str]),
        vol.Optional("filename"): str,
        vol.Optional("hacs"): str,
        vol.Optional("hide_default_branch"): bool,
        vol.Optional("homeassistant"): str,
        vol.Optional("persistent_directory"): str,
        vol.Optional("render_readme"): bool,
        vol.Optional("zip_release"): bool,
        vol.Required("name"): str,
    },
    extra=vol.PREVENT_EXTRA,
)

# Copied from HACS: INTEGRATION_MANIFEST_JSON_SCHEMA.
MANIFEST_SCHEMA = vol.Schema(
    {
        vol.Required("codeowners"): list,
        vol.Required("documentation"): url_validator,
        vol.Required("domain"): str,
        vol.Required("issue_tracker"): url_validator,
        vol.Required("name"): str,
        vol.Required("version"): vol.Coerce(AwesomeVersion),
    },
    extra=vol.ALLOW_EXTRA,
)


def test_hacs_json_is_valid() -> None:
    """hacs.json has a name and nothing HACS does not know."""
    HACS_JSON_SCHEMA(json.loads((ROOT / "hacs.json").read_text()))


def test_exactly_one_integration_under_custom_components() -> None:
    """HACS installs one integration per repository."""
    integrations = [
        p for p in (ROOT / "custom_components").iterdir()
        if p.is_dir() and not p.name.startswith((".", "__"))
    ]
    assert [p.name for p in integrations] == ["bright_meter"]


def test_manifest_has_what_hacs_requires() -> None:
    """domain, documentation, issue_tracker, codeowners, name and version."""
    manifest = json.loads(
        (ROOT / "custom_components" / "bright_meter" / "manifest.json").read_text()
    )
    MANIFEST_SCHEMA(manifest)


def test_brand_icon_ships_in_the_component() -> None:
    """HACS requires a brand directory with at least icon.png."""
    assert (ROOT / "custom_components" / "bright_meter" / "brand" / "icon.png").is_file()


def test_the_hacs_name_matches_the_integration() -> None:
    """One name in HACS and in Home Assistant."""
    hacs = json.loads((ROOT / "hacs.json").read_text())
    manifest = json.loads(
        (ROOT / "custom_components" / "bright_meter" / "manifest.json").read_text()
    )
    assert hacs["name"] == manifest["name"]
