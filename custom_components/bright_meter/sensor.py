"""Sensor entities for the Bright smart meter integration.

None of these carries a state_class. Long-term statistics for the meter streams
are imported directly from the Glowmarkt API as external statistics (see
coordinator.py), which places every hour where it belongs however late the
reading lands. A state_class here would make the recorder build a second series
for the same energy, stamped at the time the entity changed, and anyone who
added both to the Energy dashboard would count it twice.

What the entities report instead is what the delayed DCC feed can state
truthfully: how far the data reaches, the last whole local day, and the rates
the meter itself holds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GlowmarktConfigEntry
from .const import CURRENCY_GBP, DOMAIN, PENCE_PER_POUND
from .coordinator import BrightData, GlowmarktCoordinator, GlowmarktResource

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BrightSensorDescription(SensorEntityDescription):
    """A sensor, and how to read its value for one resource."""

    value_fn: Callable[[BrightData, GlowmarktResource], float | datetime | None]
    attrs_fn: Callable[[BrightData, GlowmarktResource], dict[str, Any]] = (
        lambda data, resource: {}
    )


def _slug(resource: GlowmarktResource) -> str:
    return resource.classifier.replace(".", "_")


def _last_day(data: BrightData, resource: GlowmarktResource) -> float | None:
    total = data.last_day.get(resource.resource_id)
    return None if total is None else total.value


def _last_day_attrs(data: BrightData, resource: GlowmarktResource) -> dict[str, Any]:
    total = data.last_day.get(resource.resource_id)
    return {
        "day": None if total is None else total.day.isoformat(),
        "statistic_id": resource.statistic_id,
    }


def _rate(data: BrightData, resource: GlowmarktResource) -> float | None:
    tariff = data.tariffs.get(resource.resource_id)
    if tariff is None or tariff.unit_rate_p is None:
        return None
    return round(tariff.unit_rate_p / PENCE_PER_POUND, 6)


def _standing(data: BrightData, resource: GlowmarktResource) -> float | None:
    tariff = data.tariffs.get(resource.resource_id)
    if tariff is None or tariff.standing_charge_p is None:
        return None
    return round(tariff.standing_charge_p / PENCE_PER_POUND, 6)


def _descriptions(resource: GlowmarktResource) -> list[BrightSensorDescription]:
    """The sensors one resource gets."""
    slug = _slug(resource)
    descriptions = [
        BrightSensorDescription(
            key=f"{slug}_last_day",
            translation_key=f"{slug}_last_day",
            device_class=(
                SensorDeviceClass.MONETARY
                if resource.info.is_cost
                else SensorDeviceClass.ENERGY
            ),
            native_unit_of_measurement=(
                CURRENCY_GBP if resource.info.is_cost else UnitOfEnergy.KILO_WATT_HOUR
            ),
            suggested_display_precision=2 if resource.info.is_cost else 3,
            value_fn=_last_day,
            attrs_fn=_last_day_attrs,
        )
    ]
    if resource.info.is_cost:
        return descriptions

    descriptions.append(
        BrightSensorDescription(
            key=f"{slug}_data_until",
            translation_key=f"{slug}_data_until",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda data, r: data.data_until.get(r.resource_id),
        )
    )
    if resource.info.fuel is not None:
        fuel = resource.info.fuel
        descriptions += [
            BrightSensorDescription(
                key=f"{fuel}_unit_rate",
                translation_key=f"{fuel}_unit_rate",
                native_unit_of_measurement=f"{CURRENCY_GBP}/{UnitOfEnergy.KILO_WATT_HOUR}",
                suggested_display_precision=4,
                value_fn=_rate,
            ),
            BrightSensorDescription(
                key=f"{fuel}_standing_charge",
                translation_key=f"{fuel}_standing_charge",
                native_unit_of_measurement=f"{CURRENCY_GBP}/d",
                suggested_display_precision=4,
                value_fn=_standing,
            ),
        ]
    return descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowmarktConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        BrightSensor(coordinator, resource, description)
        for resource in coordinator.resources
        for description in _descriptions(resource)
    )


class BrightSensor(CoordinatorEntity[GlowmarktCoordinator], SensorEntity):
    """One figure about one Glowmarkt stream."""

    _attr_has_entity_name = True
    entity_description: BrightSensorDescription

    def __init__(
        self,
        coordinator: GlowmarktCoordinator,
        resource: GlowmarktResource,
        description: BrightSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._resource = resource
        self._attr_unique_id = f"{resource.resource_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, resource.ve_id)},
            name=resource.ve_name,
            manufacturer="Hildebrand",
            model="Bright (DCC)",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> float | datetime | None:
        """The figure, or None while it is not known."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data, self._resource)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The day a total covers, and the statistic to use in the Energy dashboard."""
        if self.coordinator.data is None:
            return None
        return (
            self.entity_description.attrs_fn(self.coordinator.data, self._resource)
            or None
        )
