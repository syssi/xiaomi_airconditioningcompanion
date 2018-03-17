"""
Support for Xiaomi Mi Home Air Conditioner Companion (AC Partner)

For more details about this platform, please refer to the documentation
https://home-assistant.io/components/climate.xiaomi_miio
"""
import logging
import asyncio
from functools import partial
from datetime import timedelta
import voluptuous as vol

from homeassistant.core import callback
from homeassistant.helpers.entity import ToggleEntity
from homeassistant.components.climate import (
    PLATFORM_SCHEMA, ClimateDevice, ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW, ATTR_OPERATION_MODE,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_TARGET_TEMPERATURE_HIGH,
    SUPPORT_TARGET_TEMPERATURE_LOW, SUPPORT_OPERATION_MODE, SUPPORT_FAN_MODE,
    SUPPORT_SWING_MODE, SUPPORT_ON_OFF, )
from homeassistant.const import (
    TEMP_CELSIUS, ATTR_TEMPERATURE, ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME, CONF_HOST, CONF_TOKEN, STATE_ON, STATE_OFF,
    STATE_IDLE, )
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.event import async_track_state_change
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ['python-miio>=0.3.8']

DEPENDENCIES = ['sensor']

SUCCESS = ['ok']

DEFAULT_TOLERANCE = 0.3
DEFAULT_NAME = 'Xiaomi AC Companion'

DEFAULT_MIN_TEMP = 16
DEFAULT_MAX_TEMP = 30
DEFAULT_STEP = 1

ATTR_AIR_CONDITION_MODEL = 'ac_model'
ATTR_SWING_MODE = 'swing_mode'
ATTR_FAN_SPEED = 'fan_speed'
ATTR_LOAD_POWER = 'load_power'
ATTR_LED = 'led'

SUPPORT_FLAGS = (SUPPORT_TARGET_TEMPERATURE | SUPPORT_TARGET_TEMPERATURE_HIGH |
                 SUPPORT_TARGET_TEMPERATURE_LOW | SUPPORT_FAN_MODE |
                 SUPPORT_OPERATION_MODE | SUPPORT_SWING_MODE | SUPPORT_ON_OFF)

CONF_SENSOR = 'target_sensor'

SCAN_INTERVAL = timedelta(seconds=15)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_TOKEN): vol.All(cv.string, vol.Length(min=32, max=32)),
    vol.Required(CONF_SENSOR): cv.entity_id,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
})


# pylint: disable=unused-argument
@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the air conditioning companion from config."""
    from miio import AirConditioningCompanion, DeviceException

    host = config.get(CONF_HOST)
    name = config.get(CONF_NAME)
    token = config.get(CONF_TOKEN)
    sensor_entity_id = config.get(CONF_SENSOR)

    _LOGGER.info("Initializing with host %s (token %s...)", host, token[:5])

    try:
        device = AirConditioningCompanion(host, token)
        device_info = device.info()
        model = device_info.model
        unique_id = "{}-{}".format(model, device_info.mac_address)
        _LOGGER.info("%s %s %s detected",
                     model,
                     device_info.firmware_version,
                     device_info.hardware_version)
    except DeviceException as ex:
        _LOGGER.error("Device unavailable or token incorrect: %s", ex)
        raise PlatformNotReady

    async_add_devices([XiaomiAirConditioningCompanion(
        hass, name, device, unique_id, sensor_entity_id)],
        update_before_add=True)


class XiaomiAirConditioningCompanion(ClimateDevice):
    """Representation of a Xiaomi Air Conditioning Companion."""

    def __init__(self, hass, name, device, unique_id, sensor_entity_id):

        """Initialize the climate device."""
        self.hass = hass
        self._name = name
        self._device = device
        self._unique_id = unique_id
        self._sensor_entity_id = sensor_entity_id

        self._available = False
        self._state = None
        self._state_attrs = {
            ATTR_AIR_CONDITION_MODEL: None,
            ATTR_LOAD_POWER: None,
            ATTR_TEMPERATURE: None,
            ATTR_SWING_MODE: None,
            ATTR_FAN_SPEED: None,
            ATTR_OPERATION_MODE: None,
            ATTR_LED: None,
        }

        self._unit_of_measurement = TEMP_CELSIUS
        self._target_temperature_high = DEFAULT_MAX_TEMP
        self._target_temperature_low = DEFAULT_MIN_TEMP
        self._max_temp = DEFAULT_MAX_TEMP + 1
        self._min_temp = DEFAULT_MIN_TEMP - 1
        self._target_temp_step = DEFAULT_STEP

        self._air_condition_model = None
        self._target_temperature = None
        self._target_humidity = None
        self._current_temperature = None
        self._current_humidity = None
        self._current_swing_mode = None
        self._current_operation = None
        self._current_fan_mode = None

        if sensor_entity_id:
            async_track_state_change(
                hass, sensor_entity_id, self._async_sensor_changed)
            sensor_state = hass.states.get(sensor_entity_id)
            if sensor_state:
                self._async_update_temp(sensor_state)

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from sensor."""
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        try:
            self._current_temperature = self.hass.config.units.temperature(
                float(state.state), unit)
        except ValueError as ex:
            _LOGGER.error('Unable to update from sensor: %s', ex)

    @asyncio.coroutine
    def _async_sensor_changed(self, entity_id, old_state, new_state):
        """Handle temperature changes."""
        if new_state is None:
            return
        self._async_update_temp(new_state)

    @asyncio.coroutine
    def _try_command(self, mask_error, func, *args, **kwargs):
        """Call a AC companion command handling error messages."""
        from miio import DeviceException
        try:
            result = yield from self.hass.async_add_job(
                partial(func, *args, **kwargs))

            _LOGGER.debug("Response received: %s", result)

            return result == SUCCESS
        except DeviceException as exc:
            _LOGGER.error(mask_error, exc)
            self._available = False
            return False

    @asyncio.coroutine
    def async_turn_on(self, speed: str = None, **kwargs) -> None:
        """Turn the miio device on."""
        result = yield from self._try_command(
            "Turning the miio device on failed.", self._device.on)

        if result:
            self._state = True

    @asyncio.coroutine
    def async_turn_off(self, **kwargs) -> None:
        """Turn the miio device off."""
        result = yield from self._try_command(
            "Turning the miio device off failed.", self._device.off)

        if result:
            self._state = False

    @asyncio.coroutine
    def async_update(self):
        """Update the state of this climate device."""
        from miio import DeviceException
        from miio.airconditioningcompanion import SwingMode

        try:
            state = yield from self.hass.async_add_job(self._device.status)
            _LOGGER.debug("Got new state: %s", state)

            self._available = True
            self._state = state.is_on
            self._state_attrs.update({
                ATTR_AIR_CONDITION_MODEL: state.air_condition_model,
                ATTR_LOAD_POWER: state.load_power,
                ATTR_TEMPERATURE: state.temperature,
                # TODO: Refactor swing_mode
                ATTR_SWING_MODE: SwingMode.On.name if state.swing_mode else SwingMode.Off.name,
                ATTR_FAN_SPEED: state.fan_speed.name,
                ATTR_OPERATION_MODE: state.mode.name,
                ATTR_LED: state.led,
            })

            self._current_operation = state.mode.name.lower()
            # FIXME: The property is called "target_temperature" with python-miio 0.3.9
            self._target_temperature = state.temperature

            self._current_fan_mode = state.fan_speed.name
            self._current_swing_mode = \
                SwingMode.On.name if state.swing_mode else SwingMode.Off.name

            if not self._sensor_entity_id:
                self._current_temperature = state.temperature

            if self._air_condition_model is None:
                self._air_condition_model = state.air_condition_model

        except DeviceException as ex:
            self._available = False
            _LOGGER.error("Got exception while fetching the state: %s", ex)

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._max_temp

    @property
    def target_temperature_step(self):
        """Return the target temperature step."""
        return self._target_temp_step

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._name

    @property
    def available(self):
        """Return true when state is known."""
        return self._available

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        return self._state_attrs

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_high(self):
        """Return the upper bound of the target temperature we try to reach."""
        return self._target_temperature_high

    @property
    def target_temperature_low(self):
        """Return the lower bound of the target temperature we try to reach."""
        return self._target_temperature_low

    @property
    def current_humidity(self):
        """Return the current humidity."""
        return self._current_humidity

    @property
    def target_humidity(self):
        """Return the humidity we try to reach."""
        return self._target_humidity

    @property
    def current_operation(self):
        """Return current operation ie. heat, cool, idle."""
        return self._current_operation

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        from miio.airconditioningcompanion import OperationMode
        return [mode.name for mode in OperationMode]

    @property
    def current_fan_mode(self):
        """Return the current fan mode."""
        return self._current_fan_mode

    @property
    def fan_list(self):
        """Return the list of available fan modes."""
        from miio.airconditioningcompanion import FanSpeed
        return [speed.name for speed in FanSpeed]

    @property
    def is_on(self) -> bool:
        """Return True if the entity is on"""
        return self._state

    @asyncio.coroutine
    def async_set_temperature(self, **kwargs):
        """Set target temperature."""
        if kwargs.get(ATTR_TEMPERATURE) is not None:
            self._target_temperature = kwargs.get(ATTR_TEMPERATURE)

        if kwargs.get(ATTR_TARGET_TEMP_HIGH) is not None:
            self._target_temperature_high = kwargs.get(ATTR_TARGET_TEMP_HIGH)

        if kwargs.get(ATTR_TARGET_TEMP_LOW) is not None:
            self._target_temperature_low = kwargs.get(ATTR_TARGET_TEMP_LOW)

        if kwargs.get(ATTR_OPERATION_MODE) is not None:
            self._current_operation = kwargs.get(ATTR_OPERATION_MODE)
        else:
            if self._target_temperature < self._target_temperature_low:
                self._target_temperature = self._target_temperature_low
            elif self._target_temperature > self._target_temperature_high:
                self._target_temperature = self._target_temperature_high

        yield from self._send_configuration()

    @asyncio.coroutine
    def async_set_humidity(self, humidity):
        """Set the target humidity."""
        self._target_humidity = humidity

    @asyncio.coroutine
    def async_set_swing_mode(self, swing_mode):
        """Set target temperature."""
        self._current_swing_mode = swing_mode
        yield from self._send_configuration()

    @asyncio.coroutine
    def async_set_fan_mode(self, fan):
        """Set the fan mode."""
        self._current_fan_mode = fan
        yield from self._send_configuration()

    @asyncio.coroutine
    def async_set_operation_mode(self, operation_mode):
        """Set operation mode."""
        self._current_operation = operation_mode
        yield from self._send_configuration()

    @property
    def current_swing_mode(self):
        """Return the current swing setting."""
        return self._current_swing_mode

    @property
    def swing_list(self):
        """List of available swing modes."""
        from miio.airconditioningcompanion import SwingMode
        return [mode.name for mode in SwingMode]

    @asyncio.coroutine
    def _send_configuration(self):
        from miio.airconditioningcompanion import \
            Power, OperationMode, FanSpeed, SwingMode, Led

        if self._air_condition_model is not None:
            yield from self._try_command(
                "Sending new air conditioner configuration failed.",
                self._device.send_configuration,
                self._air_condition_model,
                Power(int(self._state)),
                OperationMode[self._current_operation.title()],
                self._target_temperature,
                FanSpeed[self._current_fan_mode.title()],
                SwingMode[self._current_swing_mode.title()],
                Led.Off,
            )
        else:
            _LOGGER.error('Model number of the air condition unknown. '
                          'Configuration cannot be sent.')

    def _send_custom_command(self, command: str):
        if command[0:2] == "01":
            yield from self._try_command(
                "Sending new air conditioner configuration failed.",
                self._device.send_command, command)
        else:
            # Learned infrared commands has the prefix 'FE'
            yield from self._try_command(
                "Sending new air conditioner configuration failed.",
                self._device.send_ir_code, command)
