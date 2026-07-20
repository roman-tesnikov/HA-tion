"""Regression tests for the bundled Tion BLE connection lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


LIB_DIR = Path(__file__).parents[1] / "custom_components" / "ha_tion_btle" / "lib"
sys.path.insert(0, str(LIB_DIR))

from tion_btle.lite import TionLite  # noqa: E402
from tion_btle.s4 import TionS4  # noqa: E402
from tion_btle.tion import Tion, TionDelegation  # noqa: E402


class FakeClient:
    """Small BleakClient stand-in which immediately sends a response."""

    def __init__(self, responses: list[int], on_disconnect) -> None:
        self.is_connected = True
        self._responses = iter(responses)
        self._notification_callback = None
        self._on_disconnect = on_disconnect
        self.start_notify_calls = 0

    async def start_notify(self, _uuid, callback) -> None:
        self.start_notify_calls += 1
        self._notification_callback = callback

    async def write_gatt_char(self, _uuid, _request, _response) -> None:
        assert self._notification_callback is not None
        self._notification_callback("fake", bytearray([next(self._responses)]))

    async def disconnect(self) -> None:
        if self.is_connected:
            self.is_connected = False
            self._on_disconnect(self)

    async def pair(self) -> None:
        return None


class PacketClient(FakeClient):
    """Fake client which emits a complete multi-packet response."""

    def __init__(self, packets: list[bytearray], on_disconnect) -> None:
        super().__init__([], on_disconnect)
        self._packets = packets

    async def write_gatt_char(self, _uuid, _request, _response) -> None:
        assert self._notification_callback is not None
        for packet in self._packets:
            self._notification_callback("fake", packet)


class FakeTion(Tion):
    """Protocol-minimal Tion implementation for lifecycle tests."""

    uuid_notify = "notify"
    uuid_write = "write"

    def __init__(self) -> None:
        super().__init__("AA:BB:CC:DD:EE:FF")
        self.encoded_request = None

    @property
    def command_getStatus(self) -> bytearray:
        return bytearray([1])

    async def _send_request(self, request: bytearray):
        await self._try_write(request)

    def _decode_response(self, response: bytearray) -> dict:
        self._fan_speed = response[0]
        return {}

    def _encode_request(self, request: dict) -> bytearray:
        self.encoded_request = request
        return bytearray([2])

    def _generate_model_specific_json(self) -> dict:
        return {}

    def _collect_message(self, package: bytearray) -> bool:
        self._data = package
        return True

    async def _pair(self):
        return None


class TionDelegationTest(unittest.IsolatedAsyncioTestCase):
    async def test_queue_is_bounded_and_drops_oldest_packets(self) -> None:
        delegation = TionDelegation()

        for value in range(TionDelegation.MAX_QUEUE_SIZE + 10):
            delegation.handleNotification("fake", bytearray([value]))

        self.assertEqual(delegation.queue_size, TionDelegation.MAX_QUEUE_SIZE)
        self.assertEqual(
            await delegation.get(0.1),
            bytearray([10]),
        )


class TionLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_reads_are_serialized_and_reuse_connection(self) -> None:
        tion = FakeTion()
        tion.set_ble_device_callback(lambda: object())
        active_clients = 0
        max_active_clients = 0
        clients: list[FakeClient] = []

        def disconnected(_client) -> None:
            nonlocal active_clients
            active_clients -= 1

        async def establish(*_args, **_kwargs):
            nonlocal active_clients, max_active_clients
            active_clients += 1
            max_active_clients = max(max_active_clients, active_clients)
            client = FakeClient([3, 4], disconnected)
            clients.append(client)
            return client

        with patch(
            "tion_btle.tion.establish_connection", new=AsyncMock(side_effect=establish)
        ):
            first, second = await asyncio.gather(tion.get(), tion.get())

        self.assertIn(first["fan_speed"], (3, 4))
        self.assertIn(second["fan_speed"], (3, 4))
        self.assertEqual(len(clients), 1)
        self.assertEqual(max_active_clients, 1)
        self.assertEqual(active_clients, 1)
        self.assertTrue(clients[0].is_connected)

        await tion.disconnect()

        self.assertEqual(active_clients, 0)
        self.assertFalse(clients[0].is_connected)

    async def test_set_refreshes_state_before_encoding_full_packet(self) -> None:
        tion = FakeTion()
        tion.set_ble_device_callback(lambda: object())
        client = FakeClient([4, 5, 6, 7], lambda _client: None)
        establish_mock = AsyncMock(return_value=client)

        with patch(
            "tion_btle.tion.establish_connection",
            new=establish_mock,
        ):
            await tion.set({"heater": "on"})

            self.assertIsNotNone(tion.encoded_request)
            self.assertEqual(tion.encoded_request["fan_speed"], 4)
            self.assertEqual(tion.encoded_request["heater"], "on")

            await tion.set({"fan_speed": 2})

        self.assertEqual(establish_mock.await_count, 1)
        self.assertEqual(client.start_notify_calls, 1)
        self.assertEqual(tion.encoded_request["fan_speed"], 2)
        self.assertTrue(client.is_connected)

    async def test_poll_does_not_reconnect_after_disconnect(self) -> None:
        tion = FakeTion()
        tion.set_ble_device_callback(lambda: object())
        clients: list[FakeClient] = []

        async def establish(*_args, **kwargs):
            client = FakeClient(
                [4, 5] if not clients else [6, 7],
                kwargs["disconnected_callback"],
            )
            clients.append(client)
            return client

        establish_mock = AsyncMock(side_effect=establish)
        with patch("tion_btle.tion.establish_connection", new=establish_mock):
            await tion.set({"heater": "on"})
            await clients[0].disconnect()

            cached = await tion.get(connect_if_needed=False)

            self.assertEqual(establish_mock.await_count, 1)
            self.assertEqual(cached["fan_speed"], 4)
            self.assertFalse(tion.is_connected)

            await tion.set({"heater": "off"})

        self.assertEqual(establish_mock.await_count, 2)
        self.assertEqual(len(clients), 2)
        self.assertTrue(tion.is_connected)

    async def test_pair_releases_connection(self) -> None:
        tion = FakeTion()
        tion.set_ble_device_callback(lambda: object())
        client = FakeClient([], lambda _client: None)

        with patch(
            "tion_btle.tion.establish_connection",
            new=AsyncMock(return_value=client),
        ):
            await tion.pair()

        self.assertFalse(client.is_connected)

    async def test_lite_family_multi_packet_response(self) -> None:
        for tion_class in (TionLite, TionS4):
            with self.subTest(model=tion_class.__name__):
                tion = tion_class("AA:BB:CC:DD:EE:FF")
                tion.set_ble_device_callback(lambda: object())
                client = PacketClient(tion._packages, lambda _client: None)

                with patch(
                    "tion_btle.tion.establish_connection",
                    new=AsyncMock(return_value=client),
                ):
                    result = await tion.get()

                self.assertEqual(
                    result["model"], tion_class.__name__.removeprefix("Tion")
                )
                self.assertTrue(client.is_connected)
                await tion.disconnect()
                self.assertFalse(client.is_connected)


if __name__ == "__main__":
    unittest.main()
