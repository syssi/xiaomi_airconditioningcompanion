"""
Support for Xiaomi Mi Home Air Conditioner Companion (AC Partner)

For more details about this platform, please refer to the documentation
https://home-assistant.io/components/climate.xiaomi_miio
"""
import enum
import logging
import asyncio
from functools import partial
from datetime import timedelta
import voluptuous as vol

from homeassistant.core import callback
from homeassistant.components.climate import ClimateEntity, PLATFORM_SCHEMA
from homeassistant.const import (
    STATE_ON
)
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    DOMAIN,
    HVAC_MODES,
    HVAC_MODE_OFF,
    HVAC_MODE_HEAT,
    HVAC_MODE_COOL,
    HVAC_MODE_AUTO,
    HVAC_MODE_DRY,
    HVAC_MODE_FAN_ONLY,
    SUPPORT_SWING_MODE,
    SUPPORT_FAN_MODE,
    SUPPORT_TARGET_TEMPERATURE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME,
    CONF_HOST,
    CONF_TOKEN,
    CONF_TIMEOUT,
    TEMP_CELSIUS,
)
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.event import async_track_state_change
import homeassistant.helpers.config_validation as cv
from homeassistant.util.dt import utcnow

_LOGGER = logging.getLogger(__name__)

SUCCESS = ["ok"]

DEFAULT_NAME = "Xiaomi AC Companion"
DATA_KEY = "climate.xiaomi_miio"
TARGET_TEMPERATURE_STEP = 1

DEFAULT_TIMEOUT = 10
DEFAULT_SLOT = 30

ATTR_AIR_CONDITION_MODEL = "ac_model"
ATTR_SWING_MODE = "swing_mode"
ATTR_FAN_MODE = "fan_mode"
ATTR_LOAD_POWER = "load_power"
ATTR_LED = "led"

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE | SUPPORT_FAN_MODE | SUPPORT_SWING_MODE

CONF_SENSOR = "target_sensor"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_SLOT = "slot"
CONF_COMMAND = "command"
CONF_POWER_SENSOR = "power_sensor"

SCAN_INTERVAL = timedelta(seconds=15)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_TOKEN): vol.All(cv.string, vol.Length(min=32, max=32)),
        vol.Required(CONF_SENSOR): cv.entity_id,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_MIN_TEMP, default=16): vol.Coerce(int),
        vol.Optional(CONF_MAX_TEMP, default=30): vol.Coerce(int),
        vol.Optional(CONF_POWER_SENSOR): cv.entity_id
    }
)

SERVICE_LEARN_COMMAND = "xiaomi_miio_learn_command"
SERVICE_SEND_COMMAND = "xiaomi_miio_send_command"

SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})

SERVICE_SCHEMA_LEARN_COMMAND = SERVICE_SCHEMA.extend(
    {
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(
            int, vol.Range(min=0)
        ),
        vol.Optional(CONF_SLOT, default=DEFAULT_SLOT): vol.All(
            int, vol.Range(min=2, max=1000000)
        ),
    }
)

SERVICE_SCHEMA_SEND_COMMAND = SERVICE_SCHEMA.extend(
    {vol.Optional(CONF_COMMAND): cv.string}
)

SERVICE_TO_METHOD = {
    SERVICE_LEARN_COMMAND: {
        "method": "async_learn_command",
        "schema": SERVICE_SCHEMA_LEARN_COMMAND,
    },
    SERVICE_SEND_COMMAND: {
        "method": "async_send_command",
        "schema": SERVICE_SCHEMA_SEND_COMMAND,
    },
}


# pylint: disable=unused-argument
@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the air conditioning companion from config."""
    from miio import AirConditioningCompanion, DeviceException

    if DATA_KEY not in hass.data:
        hass.data[DATA_KEY] = {}

    host = config.get(CONF_HOST)
    name = config.get(CONF_NAME)
    token = config.get(CONF_TOKEN)
    min_temp = config.get(CONF_MIN_TEMP)
    max_temp = config.get(CONF_MAX_TEMP)
    sensor_entity_id = config.get(CONF_SENSOR)
    power_sensor_entity_id = config.get(CONF_POWER_SENSOR)

    _LOGGER.info("Initializing with host %s (token %s...)", host, token[:5])

    try:
        device = AirConditioningCompanion(host, token)
        device_info = device.info()
        model = device_info.model
        unique_id = "{}-{}".format(model, device_info.mac_address)
        _LOGGER.info(
            "%s %s %s detected",
            model,
            device_info.firmware_version,
            device_info.hardware_version,
        )
    except DeviceException as ex:
        _LOGGER.error("Device unavailable or token incorrect: %s", ex)
        raise PlatformNotReady

    air_conditioning_companion = XiaomiAirConditioningCompanion(
        hass, name, device, unique_id, sensor_entity_id, power_sensor_entity_id, min_temp, max_temp
    )
    hass.data[DATA_KEY][host] = air_conditioning_companion
    async_add_devices([air_conditioning_companion], update_before_add=True)

    async def async_service_handler(service):
        """Map services to methods on XiaomiAirConditioningCompanion."""
        method = SERVICE_TO_METHOD.get(service.service)
        params = {
            key: value for key, value in service.data.items() if key != ATTR_ENTITY_ID
        }
        entity_ids = service.data.get(ATTR_ENTITY_ID)
        if entity_ids:
            devices = [
                device
                for device in hass.data[DATA_KEY].values()
                if device.entity_id in entity_ids
            ]
        else:
            devices = hass.data[DATA_KEY].values()

        update_tasks = []
        for device in devices:
            if not hasattr(device, method["method"]):
                continue
            await getattr(device, method["method"])(**params)
            update_tasks.append(device.async_update_ha_state(True))

        if update_tasks:
            await asyncio.wait(update_tasks, loop=hass.loop)

    for service in SERVICE_TO_METHOD:
        schema = SERVICE_TO_METHOD[service].get("schema", SERVICE_SCHEMA)
        hass.services.async_register(
            DOMAIN, service, async_service_handler, schema=schema
        )


class OperationMode(enum.Enum):
    Heat = HVAC_MODE_HEAT
    Cool = HVAC_MODE_COOL
    Auto = HVAC_MODE_AUTO
    Dehumidify = HVAC_MODE_DRY
    Ventilate = HVAC_MODE_FAN_ONLY
    Off = HVAC_MODE_OFF


class XiaomiAirConditioningCompanion(ClimateEntity):
    """Representation of a Xiaomi Air Conditioning Companion."""

    def __init__(
        self, hass, name, device, unique_id, sensor_entity_id, power_sensor_entity_id, min_temp, max_temp
    ):

        """Initialize the climate device."""
        self.hass = hass
        self._name = name
        self._device = device
        self._unique_id = unique_id
        self._sensor_entity_id = sensor_entity_id
        self._power_sensor_entity_id = power_sensor_entity_id

        self._available = False
        self._state = None
        self._state_attrs = {
            ATTR_AIR_CONDITION_MODEL: None,
            ATTR_LOAD_POWER: None,
            ATTR_TEMPERATURE: None,
            ATTR_SWING_MODE: None,
            ATTR_HVAC_MODE: None,
            ATTR_LED: None,
        }

        self._max_temp = max_temp
        self._min_temp = min_temp
        self._current_temperature = None
        self._swing_mode = None
        self._last_on_operation = None
        self._hvac_mode = None
        self._fan_mode = None
        self._air_condition_model = None
        self._target_temperature = None

        if sensor_entity_id:
            async_track_state_change(hass, sensor_entity_id, self._async_sensor_changed)
            sensor_state = hass.states.get(sensor_entity_id)
            if sensor_state:
                self._async_update_temp(sensor_state)

        if power_sensor_entity_id:
            async_track_state_change(hass, power_sensor_entity_id, self._async_power_sensor_changed)
            sensor_state = hass.states.get(power_sensor_entity_id)
            if sensor_state:
                self._async_update_power_state(sensor_state)

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from sensor."""
        if state.state is None or state.state == "unknown":
            return

        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        try:
            self._current_temperature = self.hass.config.units.temperature(
                float(state.state), unit
            )
        except ValueError as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)

    @callback
    def _async_update_power_state(self, state):
        """Update thermostat with latest state from power sensor."""
        if state.state is None:
            return
        if state.state == STATE_ON:
            yield from self.async_turn_on()
        else:
            yield from self.async_turn_off()

    @asyncio.coroutine
    def _async_sensor_changed(self, entity_id, old_state, new_state):
        """Handle temperature changes."""
        if new_state is None:
            return
        self._async_update_temp(new_state)

    @asyncio.coroutine
    def _async_power_sensor_changed(self, entity_id, old_state, new_state):
        """Handle power sensor changes."""
        if new_state is None:
            return

        yield from self._async_update_power_state(new_state)

    @asyncio.coroutine
    def _try_command(self, mask_error, func, *args, **kwargs):
        """Call a AC companion command handling error messages."""
        from miio import DeviceException

        try:
            result = yield from self.hass.async_add_job(partial(func, *args, **kwargs))

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
            "Turning the miio device on failed.", self._device.on
        )

        if result:
            self._state = True

    @asyncio.coroutine
    def async_turn_off(self, **kwargs) -> None:
        """Turn the miio device off."""
        result = yield from self._try_command(
            "Turning the miio device off failed.", self._device.off
        )

        if result:
            self._state = False

    @asyncio.coroutine
    def async_update(self):
        """Update the state of this climate device."""
        from miio import DeviceException

        try:
            state = yield from self.hass.async_add_job(self._device.status)
            _LOGGER.debug("Got new state: %s", state)

            self._available = True
            self._state_attrs.update(
                {
                    ATTR_AIR_CONDITION_MODEL: state.air_condition_model.hex(),
                    ATTR_LOAD_POWER: state.load_power,
                    ATTR_TEMPERATURE: state.target_temperature,
                    ATTR_SWING_MODE: state.swing_mode.name.lower(),
                    ATTR_FAN_MODE: state.fan_speed.name.lower(),
                    ATTR_HVAC_MODE: state.mode.name.lower() if self._state else "off",
                    ATTR_LED: state.led,
                }
            )
            self._last_on_operation = OperationMode[state.mode.name].value
            if state.power == "off":
                self._hvac_mode = HVAC_MODE_OFF
                self._state = False
            else:
                self._hvac_mode = self._last_on_operation
                self._state = True
            self._target_temperature = state.target_temperature
            self._fan_mode = state.fan_speed
            self._swing_mode = state.swing_mode
            if self._air_condition_model is None:
                self._air_condition_model = state.air_condition_model.hex()

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
        return TARGET_TEMPERATURE_STEP

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def unique_id(self):
        """Return an unique ID."""
        return self._unique_id

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
        return TEMP_CELSIUS

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def hvac_mode(self):
        """Return new hvac mode ie. heat, cool, fan only."""
        return self._hvac_mode

    @property
    def last_on_operation(self):
        """Return the last operation when the AC is on (ie heat, cool, fan only)"""
        return self._last_on_operation

    @property
    def hvac_modes(self):
        """Return the list of available hvac modes."""
        return [mode.value for mode in OperationMode]

    @property
    def fan_mode(self):
        """Return the current fan mode."""
        return self._fan_mode.name.lower()

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        from miio.airconditioningcompanion import FanSpeed

        return [speed.name.lower() for speed in FanSpeed]

    @asyncio.coroutine
    def async_set_temperature(self, **kwargs):
        """Set target temperature."""
        if kwargs.get(ATTR_TEMPERATURE) is not None:
            self._target_temperature = kwargs.get(ATTR_TEMPERATURE)
        if kwargs.get(ATTR_HVAC_MODE) is not None:
            self._hvac_mode = OperationMode(kwargs.get(ATTR_HVAC_MODE))

        yield from self._send_configuration()

    @asyncio.coroutine
    def async_set_swing_mode(self, swing_mode):
        """Set the swing mode."""
        from miio.airconditioningcompanion import SwingMode

        self._swing_mode = SwingMode[swing_mode.title()]
        yield from self._send_configuration()

    @asyncio.coroutine
    def async_set_fan_mode(self, fan_mode):
        """Set the fan mode."""
        from miio.airconditioningcompanion import FanSpeed

        self._fan_mode = FanSpeed[fan_mode.title()]
        yield from self._send_configuration()

    @asyncio.coroutine
    def async_set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        if hvac_mode == OperationMode.Off.value:
            result = yield from self._try_command(
                "Turning the miio device off failed.", self._device.off
            )
            if result:
                self._state = False
                self._hvac_mode = HVAC_MODE_OFF
        else:
            self._hvac_mode = OperationMode(hvac_mode).value
            self._state = True
            yield from self._send_configuration()

    @property
    def swing_mode(self):
        """Return the current swing setting."""
        return self._swing_mode.name.lower()

    @property
    def swing_modes(self):
        """List of available swing modes."""
        from miio.airconditioningcompanion import SwingMode

        return [mode.name.lower() for mode in SwingMode]

    @asyncio.coroutine
    def _send_configuration(self):
        from miio.airconditioningcompanion import (
            Power,
            Led,
            OperationMode as MiioOperationMode,
        )

        if self._air_condition_model is not None:
            yield from self._try_command(
                "Sending new air conditioner configuration failed.",
                self._device.send_configuration,
                self._air_condition_model,
                Power(int(self._state)),
                MiioOperationMode[OperationMode(self._hvac_mode).name]
                if self._state
                else MiioOperationMode[OperationMode(self._last_on_operation).name],
                int(self._target_temperature),
                self._fan_mode,
                self._swing_mode,
                Led.Off,
            )
        else:
            _LOGGER.error(
                "Model number of the air condition unknown. "
                "Configuration cannot be sent."
            )

    @asyncio.coroutine
    def async_learn_command(self, slot, timeout):
        """Learn a infrared command."""
        yield from self.hass.async_add_job(self._device.learn, slot)

        _LOGGER.info("Press the key you want Home Assistant to learn")
        start_time = utcnow()
        while (utcnow() - start_time) < timedelta(seconds=timeout):
            message = yield from self.hass.async_add_job(self._device.learn_result)
            # FIXME: Improve python-miio here?
            message = message[0]
            _LOGGER.debug("Message received from device: '%s'", message)
            if message.startswith("FE"):
                log_msg = "Received command is: {}".format(message)
                _LOGGER.info(log_msg)
                self.hass.components.persistent_notification.async_create(
                    log_msg, title="Xiaomi Miio Remote"
                )
                yield from self.hass.async_add_job(self._device.learn_stop, slot)
                return

            yield from asyncio.sleep(1, loop=self.hass.loop)

        yield from self.hass.async_add_job(self._device.learn_stop, slot)
        _LOGGER.error("Timeout. No infrared command captured")
        self.hass.components.persistent_notification.async_create(
            "Timeout. No infrared command captured", title="Xiaomi Miio Remote"
        )

    @asyncio.coroutine
    def async_send_command(self, command):
        """Send a infrared command."""
        if command.startswith("01"):
            yield from self._try_command(
                "Sending new air conditioner configuration failed.",
                self._device.send_command,
                command,
            )
        elif command.startswith("FE"):
            if self._air_condition_model is not None:
                # Learned infrared commands has the prefix 'FE'
                yield from self._try_command(
                    "Sending custom infrared command failed.",
                    self._device.send_ir_code,
                    self._air_condition_model,
                    command,
                )
            else:
                _LOGGER.error(
                    "Model number of the air condition unknown. "
                    "IR command cannot be sent."
                )
        else:
            _LOGGER.error("Invalid IR command.")
