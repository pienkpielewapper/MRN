import unittest
from ctypes import POINTER, c_double, c_int, cast
from unittest.mock import patch

from digilent.api import DigilentApi
from digilent.dwfconstants import (
    DwfStateArmed,
    DwfStateConfig,
    DwfStateDone,
    DwfStatePrefill,
    DwfStateRunning,
    trigsrcAnalogOut1,
    trigsrcAnalogOut2,
)


class FakeDwf:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.frame = (DwfStateConfig.value, 0, 0, 0)
        self.events = []
        self.supported_triggers = (1 << trigsrcAnalogOut1.value) | (
            1 << trigsrcAnalogOut2.value
        )
        self.read_counts = {0: 0, 1: 0}

    def FDwfDeviceTriggerInfo(self, handle, supported):
        supported._obj.value = self.supported_triggers
        return 1

    def FDwfAnalogInTriggerSourceSet(self, handle, source):
        self.events.append(("source", source.value))
        return 1

    def FDwfAnalogInTriggerAutoTimeoutSet(self, handle, timeout):
        self.events.append(("auto_timeout", timeout.value))
        return 1

    def FDwfAnalogInTriggerPositionSet(self, handle, position):
        self.events.append(("position", position.value))
        return 1

    def FDwfAnalogInBufferSizeGet(self, handle, size):
        size._obj.value = 16384
        return 1

    def FDwfAnalogInConfigure(self, handle, reconfigure, start):
        self.events.append(
            ("configure", getattr(reconfigure, "value", reconfigure),
             getattr(start, "value", start))
        )
        return 1

    def FDwfAnalogInStatus(self, handle, read_data, status):
        self.frame = next(self.frames)
        status._obj.value = self.frame[0]
        self.events.append(("status", self.frame[0]))
        return 1

    def FDwfAnalogInStatusRecord(self, handle, available, lost, corrupted):
        available._obj.value = self.frame[1]
        lost._obj.value = self.frame[2]
        corrupted._obj.value = self.frame[3]
        return 1

    def FDwfAnalogInStatusData(self, handle, channel, destination, count):
        count = getattr(count, "value", count)
        samples = cast(destination, POINTER(c_double))
        for index in range(count):
            samples[index] = channel.value * 100 + self.read_counts[channel.value] + index
        self.read_counts[channel.value] += count
        self.events.append(("data", channel.value, count))
        return 1


class RecordAcquisitionTests(unittest.TestCase):
    def make_api(self, frames):
        api = object.__new__(DigilentApi)
        api.dwf = FakeDwf(frames)
        api.hdwf = c_int(1)
        api.oscilloscope_mode = "record"
        api.nSamples = 6
        api.frequencyAcquire = 20_000_000.0
        api.measuredSamples = (c_double * api.nSamples)()
        api.referenceSamples = (c_double * api.nSamples)()
        api.analogMeasurementChannel = c_int(0)
        api.analogReferenceChannel = c_int(1)
        return api

    def acquire(self, api, channel=0, callback=None, timeout=None):
        if callback is None:
            callback = lambda: api.dwf.events.append(("pulse",))
        return api.read_oscilloscope(
            timeout_seconds=timeout,
            on_acquisition_started=callback,
            trigger_output_channel=channel,
        )

    def assert_stopped(self, api):
        self.assertEqual(api.dwf.events[-1], ("configure", 0, 0))

    def test_waits_until_armed_then_fires_once_and_reads_both_channels(self):
        api = self.make_api([
            (DwfStateConfig.value, 0, 0, 0),
            (DwfStatePrefill.value, 0, 0, 0),
            (DwfStateArmed.value, 0, 0, 0),
            (DwfStateRunning.value, 3, 0, 0),
            (DwfStateDone.value, 3, 0, 0),
        ])
        result = self.acquire(api)
        self.assertTrue(result["ready"])
        self.assertIn(("source", trigsrcAnalogOut1.value), api.dwf.events)
        self.assertIn(("auto_timeout", 0.0), api.dwf.events)
        self.assertIn(("position", 0.0), api.dwf.events)
        self.assertLess(
            api.dwf.events.index(("status", DwfStateArmed.value)),
            api.dwf.events.index(("pulse",)),
        )
        self.assertLess(
            api.dwf.events.index(("pulse",)),
            api.dwf.events.index(("data", 0, 3)),
        )
        self.assertEqual(api.dwf.events.count(("pulse",)), 1)
        self.assertEqual(list(api.measuredSamples), list(range(6)))
        self.assertEqual(list(api.referenceSamples), list(range(100, 106)))
        self.assert_stopped(api)

    def test_uses_selected_second_wavegen_channel(self):
        api = self.make_api([
            (DwfStateArmed.value, 0, 0, 0),
            (DwfStateDone.value, 6, 0, 0),
        ])
        self.assertTrue(self.acquire(api, channel=1)["ready"])
        self.assertIn(("source", trigsrcAnalogOut2.value), api.dwf.events)

    def test_done_with_incomplete_final_chunk_fails_without_polling_again(self):
        api = self.make_api([
            (DwfStateRunning.value, 2, 0, 0),
            (DwfStateDone.value, 2, 0, 0),
        ])
        result = api.read_oscilloscope()
        self.assertFalse(result["ready"])
        self.assertIn("4 of 6", result["message"])
        self.assert_stopped(api)

    def test_done_without_final_data_fails_without_waiting_for_timeout(self):
        api = self.make_api([
            (DwfStateArmed.value, 0, 0, 0),
            (DwfStateRunning.value, 2, 0, 0),
            (DwfStateDone.value, 0, 0, 0),
        ])
        result = self.acquire(api)
        self.assertFalse(result["ready"])
        self.assertIn("2 of 6", result["message"])
        self.assert_stopped(api)

    def test_loss_or_corruption_is_not_success(self):
        for lost, corrupted, message in [(2, 0, "lost"), (0, 1, "corrupt")]:
            with self.subTest(lost=lost, corrupted=corrupted):
                api = self.make_api([
                    (DwfStateArmed.value, 0, 0, 0),
                    (DwfStateDone.value, 6, lost, corrupted),
                ])
                result = self.acquire(api)
                self.assertFalse(result["ready"])
                self.assertIn(message, result["message"])
                self.assertNotIn(("data", 0, 6), api.dwf.events)
                self.assert_stopped(api)

    def test_timeout_while_arming_does_not_fire(self):
        api = self.make_api([])
        with patch("digilent.api.time.monotonic", side_effect=[0.0, 2.0]):
            result = self.acquire(api, timeout=1.0)
        self.assertFalse(result["ready"])
        self.assertIn("arm", result["message"])
        self.assertNotIn(("pulse",), api.dwf.events)
        self.assert_stopped(api)

    def test_timeout_after_pulse_stops_recording(self):
        api = self.make_api([(DwfStateArmed.value, 0, 0, 0)])

        def pulse():
            api.dwf.events.append(("pulse",))
            clock.return_value = 2.0

        with patch("digilent.api.time.monotonic", return_value=0.0) as clock:
            result = self.acquire(api, callback=pulse, timeout=1.0)
        self.assertFalse(result["ready"])
        self.assertIn("timed out", result["message"])
        self.assert_stopped(api)

    def test_callback_failure_stops_recording_and_propagates(self):
        api = self.make_api([(DwfStateArmed.value, 0, 0, 0)])

        def pulse():
            raise RuntimeError("Pulse failed")

        with self.assertRaisesRegex(RuntimeError, "Pulse failed"):
            self.acquire(api, callback=pulse)
        self.assert_stopped(api)

    def test_unsupported_trigger_does_not_start_or_fire(self):
        api = self.make_api([])
        api.dwf.supported_triggers = 0
        result = self.acquire(api)
        self.assertFalse(result["ready"])
        self.assertIn("support", result["message"])
        self.assertFalse(any(event[0] == "configure" for event in api.dwf.events))
        self.assertNotIn(("pulse",), api.dwf.events)

    def test_invalid_trigger_channels_are_rejected(self):
        for channel in [-1, 4]:
            with self.subTest(channel=channel):
                api = self.make_api([])
                self.assertFalse(self.acquire(api, channel=channel)["ready"])
                self.assertEqual(api.dwf.events, [])

    def test_hardware_trigger_requires_pulse_callback(self):
        api = self.make_api([])
        result = api.read_oscilloscope(trigger_output_channel=0)
        self.assertFalse(result["ready"])
        self.assertIn("callback", result["message"])
        self.assertEqual(api.dwf.events, [])

    def test_untriggered_recording_still_works_and_resets_trigger_source(self):
        api = self.make_api([(DwfStateDone.value, 6, 0, 0)])
        result = api.read_oscilloscope()
        self.assertTrue(result["ready"])
        self.assertIn(("source", 0), api.dwf.events)
        self.assert_stopped(api)


if __name__ == "__main__":
    unittest.main()
