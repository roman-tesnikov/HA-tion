from __future__ import annotations

import abc
import asyncio
import inspect
import logging
from time import localtime, strftime
from typing import Callable, final

from bleak import BleakClient
from bleak import exc
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    establish_connection,
    BleakClientWithServiceCache,
)

_LOGGER = logging.getLogger(__name__)


class MaxTriesExceededError(Exception):
    pass


def retry(retries: int = 2, delay: int = 0):
    def decor(f: Callable):
        async def wrapper(*args, **kwargs):
            last_info_exception = None
            last_warning_exception = None
            for i in range(retries + 1):
                try:
                    _LOGGER.debug(
                        "Trying %d/%d: %s(args=%s,kwargs=%s)",
                        i,
                        retries,
                        f.__name__,
                        args,
                        kwargs,
                    )
                    if inspect.iscoroutinefunction(f):
                        return await f(*args, **kwargs)
                    return f(*args, **kwargs)
                except (exc.BleakError, exc.BleakDBusError) as _e:
                    next_message = (
                        "Will try again" if i < retries else "Will not try again"
                    )
                    _LOGGER.warning("Got exception: %s. %s", str(_e), next_message)
                    last_warning_exception = _e
                    if delay > 0:
                        await asyncio.sleep(delay)

            _LOGGER.critical(
                "Retry limit (%d) exceeded for %s(%s, %s)",
                retries,
                f.__name__,
                args,
                kwargs,
            )
            if _LOGGER.level > logging.INFO and last_info_exception is not None:
                _LOGGER.critical(f"Last exception was {last_info_exception}")
            elif _LOGGER.level > logging.WARNING and last_warning_exception is not None:
                _LOGGER.critical(f"Last exception was {last_warning_exception}")

            raise MaxTriesExceededError

        return wrapper

    return decor


class TionDelegation:
    """Bounded queue for BLE notification packets."""

    MAX_QUEUE_SIZE = 64

    def __init__(self):
        self._data: asyncio.Queue[bytearray] = asyncio.Queue(
            maxsize=self.MAX_QUEUE_SIZE
        )

    def handleNotification(self, handle: object, data: bytearray) -> None:
        """Enqueue one notification without allowing an unbounded backlog."""
        packet = bytearray(data)
        if self._data.full():
            try:
                self._data.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._data.put_nowait(packet)
        _LOGGER.debug(
            "Got BLE notification from %s: bytes=%d, queued=%d",
            handle,
            len(packet),
            self._data.qsize(),
        )

    async def get(self, timeout: float) -> bytearray:
        """Wait for one notification packet."""
        return await asyncio.wait_for(self._data.get(), timeout=timeout)

    @property
    def queue_size(self) -> int:
        return self._data.qsize()

    def clear(self) -> None:
        """Drop notifications left from an earlier request or connection."""
        while True:
            try:
                self._data.get_nowait()
            except asyncio.QueueEmpty:
                return


class TionException(Exception):
    def __init__(self, expression, message):
        super().__init__(message)
        self.expression = expression
        self.message = message


class Tion:
    statuses = ["off", "on"]
    modes = [
        "recirculation",
        "mixed",
    ]  # 'recirculation', 'mixed' and 'outside', as Index exception
    uuid_notify: str = ""
    uuid_write: str = ""

    def __init__(self, mac: str | BLEDevice):
        self._mac = mac
        self._client: BleakClientWithServiceCache | None = None
        self._ble_device_callback: Callable[[], BLEDevice | None] | None = None
        self._delegation = TionDelegation()
        self._fan_speed = 0
        self._model: str = self.__class__.__name__
        self._data: bytearray = bytearray()
        """Data from breezer response at request state command"""
        # states
        self._in_temp: int = 0
        self._out_temp: int = 0
        self._heater_temp: int = 0
        self._fan_speed: int = 0
        self._mode: int = 0
        self._state: bool = False
        self._heater: bool = False
        self._sound: bool = False
        self._filter_remain: float = 0.0
        self._error_code: int = 0
        self.__notifications_enabled: bool = False
        self.have_breezer_state: bool = False
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()

    def set_ble_device_callback(self, callback: Callable[[], BLEDevice | None]) -> None:
        """Set the callback that returns a fresh BLEDevice from HA Bluetooth API.

        This must be called by the HA integration (__init__.py) before any
        connect() attempt. The callback is passed to establish_connection()
        as ble_device_callback so it can handle adapter/proxy switching.
        """
        self._ble_device_callback = callback

    @abc.abstractmethod
    async def _send_request(self, request: bytearray):
        """Send request to device

        Args:
          request : array of bytes to send to device
        Returns:
          array of bytes with device response
        """
        pass

    @abc.abstractmethod
    def _decode_response(self, response: bytearray) -> dict:
        """Decode response from device

        Args:
          response: array of bytes with data from device, taken from _send_request
        Returns:
          dictionary with device response
        """
        pass

    @abc.abstractmethod
    def _encode_request(self, request: dict) -> bytearray:
        """Encode dictionary of request to byte array

        Args:
          request: dictionary with request
        Returns:
          Byte array for sending to device
        """
        pass

    @abc.abstractmethod
    def _generate_model_specific_json(self) -> dict:
        """
        Generates dict with model-specific parameters based on class variables
        :return: dict of model specific properties
        """
        raise NotImplementedError()

    def __generate_common_json(self) -> dict:
        """
        Generates dict with common parameters based on class properties
        :return: dict of common properties
        """
        return {
            "state": self.state,
            "heater": self.heater,
            "heating": self.heating,
            "sound": self.sound,
            "mode": self.mode,
            "out_temp": self.out_temp,
            "in_temp": self.in_temp,
            "heater_temp": self._heater_temp,
            "fan_speed": self.fan_speed,
            "filter_remain": self.filter_remain,
            "time": strftime("%H:%M", localtime()),
            "request_error_code": self._error_code,
            "model": self.model,
        }

    @final
    @property
    def heating(self) -> str:
        """Tries to guess is heater working right now."""
        if self.heater == "off":
            return "off"

        if self.heater_temp - self.in_temp > 3 and self.out_temp > self.in_temp:
            return "on"

        return "off"

    @final
    async def get_state_from_breezer(self) -> None:
        """Read and decode current state in one serialized BLE transaction."""
        async with self._operation_lock:
            try:
                await self._connect()
                await self._request_state()
            finally:
                await self.reset_connection()

    async def _request_state(self) -> None:
        """Request and decode state using an already connected client."""
        self._delegation.clear()
        await self._try_write(request=self.command_getStatus)
        response = await self._get_data_from_breezer()
        self._decode_response(response)

    @final
    async def get(self, skip_update: bool = False) -> dict:
        """
        Report current breezer state
        :param skip_update: may we skip requesting data from breezer or not
        :return:
          dictionary with device state
        """
        if skip_update and self.have_breezer_state:
            _LOGGER.debug(
                "Skipping state request: skip_update=%s, have_breezer_state=%s",
                skip_update,
                self.have_breezer_state,
            )
        else:
            await self.get_state_from_breezer()
        common = self.__generate_common_json()
        model_specific_data = self._generate_model_specific_json()

        return {**common, **model_specific_data}

    @final
    def _set_internal_state_from_request(self, request: dict) -> None:
        """
        Set internal parameters based on user request
        :param request: changed breezer parameter from set request
        :return: None
        """
        for p in ["fan_speed", "heater_temp", "heater", "sound", "mode", "state"]:
            # ToDo: lite have additional parameters to set: "light" and "co2_auto_control", so we should get this
            #  list from class
            try:
                setattr(self, p, request[p])
            except KeyError:
                pass

    @final
    async def set(self, new_settings=None) -> None:
        """Set state using a fresh read and one serialized BLE transaction."""
        new_settings = dict(new_settings or {})

        try:
            if new_settings["fan_speed"] == 0:
                del new_settings["fan_speed"]
                new_settings["state"] = "off"
        except KeyError:
            pass

        async with self._operation_lock:
            try:
                await self._connect()

                # Tion SET packets contain the complete state. Refresh first so
                # a physical-button or another-controller change is not lost.
                await self._request_state()
                current_settings = self.__generate_common_json()
                current_settings.update(self._generate_model_specific_json())
                merged_settings = {**current_settings, **new_settings}

                self._delegation.clear()
                encoded_request = self._encode_request(merged_settings)
                _LOGGER.debug("Will write %s", encoded_request)
                await self._send_request(encoded_request)
                await self._get_data_from_breezer()
                self._set_internal_state_from_request(new_settings)
            finally:
                await self.reset_connection()

    @final
    @property
    def mac(self):
        return self._mac.address if isinstance(self._mac, BLEDevice) else self._mac

    @staticmethod
    def decode_temperature(raw: int) -> int:
        """Converts temperature from bytes with addition code to int
        Args:
          raw: raw temperature value from Tion
        Returns:
          Integer value for temperature
        """
        barrier = 0b10000000
        return raw if raw < barrier else -(~(raw - barrier) + barrier + 1)

    @final
    def _process_status(self, code: int) -> str:
        try:
            status = self.statuses[code]
        except IndexError:
            status = "unknown"
        return status

    @final
    @property
    def connection_status(self):
        if self._client is not None:
            status = "connected" if self._client.is_connected else "disc"
        else:
            status = "disc"
        return status

    def _disconnected_callback(self, client: BleakClient) -> None:
        """Reset state after a planned or unplanned disconnect."""
        if self._client is not client:
            _LOGGER.debug(
                "Ignoring disconnected callback from stale client "
                "(callback client != current self._client)"
            )
            return
        _LOGGER.debug("Disconnected callback fired for %s", self.mac)
        self._client = None
        self.__notifications_enabled = False
        self.have_breezer_state = False
        self._delegation.clear()

    async def _ensure_client(self) -> BleakClientWithServiceCache:
        """Return a connected BleakClient, creating one if necessary.

        Uses establish_connection() from bleak-retry-connector which is the
        HA-recommended way to manage BLE connections. It handles:
        - Automatic retries with exponential backoff
        - Bluetooth adapter/proxy switching via ble_device_callback
        - GATT service caching for faster reconnections
        """
        if self._client is not None and self._client.is_connected:
            return self._client

        if self._ble_device_callback is None:
            raise RuntimeError(
                "ble_device_callback not set. Call set_ble_device_callback() "
                "from the HA integration before using connect()."
            )

        device = self._ble_device_callback()
        if device is None:
            raise exc.BleakError(
                f"BLE device {self.mac} not found via HA Bluetooth API. "
                "Device may be out of range or Bluetooth adapter unavailable."
            )

        _LOGGER.debug(
            "Establishing BLE connection to %s via establish_connection()", self.mac
        )

        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self.mac,
            disconnected_callback=self._disconnected_callback,
            max_attempts=2,
            ble_device_callback=self._ble_device_callback,
            use_services_cache=True,
        )
        self._client = client
        _LOGGER.debug("BLE connection established to %s", self.mac)
        return client

    @final
    async def _connect(self, need_notifications: bool = True):
        """Connect to the breezer using HA-managed BLE connection."""
        _LOGGER.debug("Connecting; status=%s", self.connection_status)
        async with self._connect_lock:
            if self.connection_status != "disc":
                if need_notifications and not self.__notifications_enabled:
                    await self._enable_notifications()
                return

            try:
                await self._ensure_client()
            except exc.BleakError as e:
                _LOGGER.warning("BLE connection failed: %s", e)
                raise

            if need_notifications:
                await self._enable_notifications()
            else:
                _LOGGER.debug("Notifications were not requested")
        _LOGGER.debug("Connection ready; status=%s", self.connection_status)

    @final
    async def _disconnect(self):
        """Disconnect from the breezer."""
        client = self._client
        self._client = None
        self.__notifications_enabled = False
        self.have_breezer_state = False
        self._delegation.clear()

        _LOGGER.debug(
            "Disconnecting; client_connected=%s", bool(client and client.is_connected)
        )
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except Exception as e:
                _LOGGER.warning("Error during disconnect: %s", e)
        _LOGGER.debug("Disconnect complete; status=%s", self.connection_status)

    @final
    @retry(retries=3)
    async def _try_write(self, request: bytearray):
        if self._client is None or not self._client.is_connected:
            _LOGGER.warning("_try_write called but BLE not connected")
            raise TionException("_try_write", "BLE not connected")
        _LOGGER.debug(
            "Writing %s to %s; status=%s",
            bytes(request).hex(),
            self.uuid_write,
            self.connection_status,
        )
        return await self._client.write_gatt_char(self.uuid_write, request, False)

    @final
    async def _enable_notifications(self):
        _LOGGER.debug("Enabling notifications; status=%s", self.connection_status)
        if self._client is None or not self._client.is_connected:
            raise TionException("_enable_notifications", "BLE not connected")
        try:
            await self._client.start_notify(
                self.uuid_notify, self._delegation.handleNotification
            )
        except exc.BleakError as e:
            _LOGGER.warning("Could not enable notifications: %s", e)
            raise

        self.__notifications_enabled = True
        _LOGGER.debug("Notifications enabled")

    @final
    @property
    def fan_speed(self):
        return self._fan_speed

    @fan_speed.setter
    def fan_speed(self, new_speed: int):
        if 0 <= new_speed <= 6:
            self._fan_speed = new_speed

        else:
            _LOGGER.warning("Incorrect new fan speed. Will use 1 instead")
            self._fan_speed = 1

        # self.set({"fan_speed": new_speed})

    @final
    def _process_mode(self, mode_code: int) -> str:
        try:
            mode = self.modes[mode_code]
        except IndexError:
            mode = "outside"
        return mode

    @staticmethod
    def _decode_state(state: bool) -> str:
        return "on" if state else "off"

    @staticmethod
    def _encode_state(state: str) -> bool:
        return state == "on"

    @final
    @property
    def state(self) -> str:
        return self._decode_state(self._state)

    @final
    @state.setter
    def state(self, new_state: str):
        self._state = self._encode_state(new_state)

    @final
    @property
    def heater(self) -> str:
        return self._decode_state(self._heater)

    @final
    @heater.setter
    def heater(self, new_state: str):
        self._heater = self._encode_state(new_state)

    @final
    @property
    def heater_temp(self) -> int:
        return self._heater_temp

    @final
    @heater_temp.setter
    def heater_temp(self, new_temp: int):
        self._heater_temp = new_temp

    @final
    @property
    def target_temp(self) -> int:
        return self.heater_temp

    @final
    @target_temp.setter
    def target_temp(self, new_temp: int):
        self.heater_temp = new_temp

    @final
    @property
    def in_temp(self):
        """Income air temperature"""
        return self._in_temp

    @final
    @property
    def out_temp(self):
        """Outcome air temperature"""
        return self._out_temp

    @final
    @property
    def sound(self) -> str:
        return self._decode_state(self._sound)

    @final
    @sound.setter
    def sound(self, new_state: str):
        self._sound = self._encode_state(new_state)

    @final
    @property
    def filter_remain(self) -> float:
        return self._filter_remain

    @final
    @property
    def mode(self):
        return self._process_mode(self._mode)

    @final
    @mode.setter
    def mode(self, new_state: str):
        self._mode = self._encode_mode(new_state)

    @final
    @property
    def model(self) -> str:
        return self._model.removeprefix("Tion")

    @final
    def _encode_status(self, status: str) -> int:
        """
        Encode string status () to int
        :param status: one of:  "on", "off"
        :return: integer equivalent of state
        """
        return self.statuses.index(status) if status in self.statuses else 0

    @final
    def _encode_mode(self, mode: str) -> int:
        """
        Encode string mode to integer
        :param mode: one of self.modes + any other as outside
        :return: integer equivalent of mode
        """
        return self.modes.index(mode) if mode in self.modes else 2

    @final
    async def pair(self):
        """Pair the breezer and always release the BLE connection."""
        async with self._operation_lock:
            _LOGGER.debug("Pairing")
            try:
                await self._connect(need_notifications=False)
                if self._client is None or not self._client.is_connected:
                    raise TionException("pair", "BLE not connected after _connect()")
                await self._client.pair()
                _LOGGER.debug("Running device-specific pairing")
                await self._pair()
                _LOGGER.debug("Device pairing complete")
            except Exception as e:
                _LOGGER.error("Pairing failed with %s: %s", type(e).__name__, e)
                raise TionException("pair", f"{type(e).__name__}: {e}") from e
            finally:
                await self.reset_connection()

    @abc.abstractmethod
    async def _pair(self):
        """Perform model-specific pair steps"""

    @final
    async def connect(self):
        """Explicitly connect to the breezer."""
        async with self._operation_lock:
            await self._connect()

    @final
    async def reset_connection(self):
        """Force-disconnect and reset all connection and queue state."""
        _LOGGER.debug("reset_connection: force cleanup")
        try:
            await self._disconnect()
        except Exception as e:
            _LOGGER.warning("reset_connection: disconnect failed: %s", e)
        self._client = None
        self.__notifications_enabled = False
        self.have_breezer_state = False
        self._delegation.clear()

    @final
    async def disconnect(self):
        """Explicitly disconnect from the breezer."""
        async with self._operation_lock:
            await self.reset_connection()

    @property
    @abc.abstractmethod
    def command_getStatus(self) -> bytearray:
        raise NotImplementedError()

    @abc.abstractmethod
    def _collect_message(self, package: bytearray) -> bool:
        """
        Collects message from several package
        Must set self._data

        :param package: single package from breezer
        :return: Have we full response from breezer or not
        """
        raise NotImplementedError()

    @final
    async def _get_data_from_breezer(self) -> bytearray:
        """Get byte array with breezer response on state request

        :returns:
          breezer response
        """
        self.have_breezer_state = False

        _LOGGER.debug("Collecting BLE response")

        timeout = 5.0
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                byte_response = await self._delegation.get(remaining)
            except asyncio.TimeoutError:
                break
            if self._collect_message(byte_response):
                self.have_breezer_state = True
                return self._data

        _LOGGER.warning(
            "Timed out waiting for BLE response after %.1fs; queued=%d",
            timeout,
            self._delegation.queue_size,
        )
        raise TionException("_get_data_from_breezer", "Could not get breezer state")

    @final
    def update_btle_device(self, new_device: str | BLEDevice):
        """Update the BLE device reference.

        With establish_connection + ble_device_callback, the HA integration
        handles device updates through the callback. This method is kept for
        backward compatibility but the actual device resolution now happens
        in _ble_device_callback provided by the HA layer.
        """
        if new_device is None:
            _LOGGER.info(f"Skipping update due to {new_device= }!")
            return
        # Store for reference but actual connections use _ble_device_callback
        self._mac = new_device
