"""Support for Xiaomi Mi Home Air Conditioner Companion sensors."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .climate import XiaomiAirConditioningCompanion
from .const import DATA_KEY


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the load power sensor."""
    climate = hass.data[DATA_KEY][config_entry.data[CONF_HOST]]
    async_add_entities([XiaomiLoadPowerSensor(climate)])


class XiaomiLoadPowerSensor(SensorEntity):
    """Representation of the air conditioner's load power."""

    _attr_has_entity_name = True
    _attr_name = "Load power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, climate: XiaomiAirConditioningCompanion) -> None:
        """Initialize the load power sensor."""
        self._climate = climate
        self._attr_unique_id = f"{climate.unique_id}_load_power"
        self._attr_device_info = climate.device_info

    @property
    def available(self) -> bool:
        """Return whether the climate device is available."""
        return self._climate.available

    @property
    def native_value(self) -> int | float | None:
        """Return the current load power."""
        return self._climate.load_power

    async def async_update(self) -> None:
        """Update the shared climate device before reading its load power."""
        await self._climate.async_update()
