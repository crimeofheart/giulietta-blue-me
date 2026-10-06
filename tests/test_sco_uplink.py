"""The call uplink: parsing bluealsa's PCM list, and the hold-open state machine."""

import json

from blueandme.media import sco_uplink as up

PHONE = "11:22:33:44:55:66"
DEV = "/org/bluez/hci0/dev_11_22_33_44_55_66"


def pcm(transport, mode, sampling, codec):
    return {"Device": {"type": "o", "data": DEV},
            "Transport": {"type": "s", "data": transport},
            "Mode": {"type": "s", "data": mode},
            "Sampling": {"type": "u", "data": sampling},
            "Codec": {"type": "s", "data": codec}}


def get_pcms(*entries):
    return json.dumps({"type": "a{oa{sv}}", "data": [dict(entries)]})


BASE = f"/org/bluealsa/hci0/dev_11_22_33_44_55_66"
#: The shape GetPCMs returned on the Pi with the phone connected (2026-09-24).
MUSIC = (f"{BASE}/a2dpsnk/source", pcm("A2DP-sink", "source", 44100, "SBC"))
HF_SINK = (f"{BASE}/hfphf/sink", pcm("HFP-HF", "sink", 8000, "CVSD"))
HF_SOURCE = (f"{BASE}/hfphf/source", pcm("HFP-HF", "source", 8000, "CVSD"))
WITH_HFP = get_pcms(MUSIC, HF_SINK, HF_SOURCE)
MUSIC_ONLY = get_pcms(MUSIC)


class FakePipeline:
    started: list = []

    def __init__(self, target, capture_pcm, gain_db=0.0):
        self.target, self.capture_pcm, self.gain_db = target, capture_pcm, gain_db
        self.running, self.stopped = True, False
        FakePipeline.started.append(self)

    def alive(self):
        return self.running

    def stop(self):
        self.running, self.stopped = False, True


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(pcms):
    FakePipeline.started = []
    clock = Clock()
    state = {"out": pcms}
    u = up.Uplink(pcms=lambda: state["out"], start=FakePipeline, clock=clock)
    return u, state, clock


class TestParser:
    def test_the_hands_free_sink_is_found_with_its_rate(self):
        assert up.hfp_sinks(WITH_HFP) == [up.Target(PHONE, 8000)]

    def test_music_alone_is_not_a_target(self):
        assert up.hfp_sinks(MUSIC_ONLY) == []

    def test_the_hands_free_source_is_not_a_target(self):
        assert up.hfp_sinks(get_pcms(HF_SOURCE)) == []

    def test_a_wideband_rate_is_taken_from_the_pcm(self):
        wide = (f"{BASE}/hfphf/sink", pcm("HFP-HF", "sink", 16000, "mSBC"))
        assert up.hfp_sinks(get_pcms(wide)) == [up.Target(PHONE, 16000)]

    def test_get_managed_objects_from_newer_bluealsa_is_understood(self):
        # bluealsa 4.1+ nests PCM properties under the interface name.
        objects = {"type": "a{oa{sa{sv}}}", "data": [{
            f"{BASE}/hfphf/sink": {"org.bluealsa.PCM1": HF_SINK[1]},
            f"{BASE}/hfphf/source": {"org.bluealsa.PCM1": HF_SOURCE[1]},
            f"{BASE}/rfcomm": {"org.bluealsa.RFCOMM1": {
                "Transport": {"type": "s", "data": "HFP-HF"}}},
        }]}
        assert up.hfp_sinks(json.dumps(objects)) == [up.Target(PHONE, 8000)]

    def test_a_rate_property_is_accepted_as_well_as_sampling(self):
        sink = pcm("HFP-HF", "sink", 16000, "mSBC")
        sink["Rate"] = sink.pop("Sampling")
        assert up.hfp_sinks(get_pcms((f"{BASE}/hfphf/sink", sink))) == [up.Target(PHONE, 16000)]

    def test_no_bluealsa_means_no_targets(self):
        assert up.hfp_sinks("") == []
        assert up.hfp_sinks("not json") == []


class TestUplink:
    def test_holds_the_pcm_open_as_soon_as_hands_free_is_connected(self):
        u, state, _ = make(MUSIC_ONLY)
        u.step()
        assert FakePipeline.started == []
        state["out"] = WITH_HFP
        u.step()
        u.step()
        assert len(FakePipeline.started) == 1
        assert FakePipeline.started[0].target == up.Target(PHONE, 8000)
        assert FakePipeline.started[0].capture_pcm == "default"

    def test_stops_when_the_hands_free_link_goes(self):
        u, state, _ = make(WITH_HFP)
        u.step()
        pipe = u.active
        state["out"] = MUSIC_ONLY
        u.step()
        assert pipe.stopped and u.active is None

    def test_a_dead_pipeline_is_restarted_after_the_retry_delay(self):
        u, _, clock = make(WITH_HFP)
        u.step()
        u.active.running = False
        u.step()
        assert u.active is None and len(FakePipeline.started) == 1
        u.step()
        assert len(FakePipeline.started) == 1
        clock.t += up.RETRY_S
        u.step()
        assert len(FakePipeline.started) == 2 and u.active.alive()

    def test_close_stops_a_running_pipeline(self):
        u, _, _ = make(WITH_HFP)
        u.step()
        pipe = u.active
        u.close()
        assert pipe.stopped and u.active is None


class TestRelay:
    """The backlog cap, on a real OS pipe."""

    def _pipe(self):
        import os
        r, w = os.pipe()
        os.set_blocking(w, False)
        return r, w

    def test_backlog_counts_unread_bytes_from_the_write_end(self):
        import os
        r, w = self._pipe()
        os.write(w, b"x" * 300)
        assert up.backlog(w) == 300
        os.read(r, 100)
        assert up.backlog(w) == 200

    def test_chunks_are_dropped_once_the_cap_would_be_passed(self):
        import os
        r, w = self._pipe()
        chunk = b"\0" * 320  # 20 ms at 8 kHz mono S16
        assert up.relay_chunk(chunk, w, max_backlog=640)
        assert up.relay_chunk(chunk, w, max_backlog=640)
        assert not up.relay_chunk(chunk, w, max_backlog=640)
        assert up.backlog(w) == 640
        os.read(r, 320)  # the phone takes 20 ms
        assert up.relay_chunk(chunk, w, max_backlog=640)
        assert up.backlog(w) == 640


class TestDrift:
    """Clock drift is absorbed by trimming single samples, never whole chunks."""

    def test_nothing_is_trimmed_at_or_below_the_target(self):
        assert up.trim_count(waiting=480, target=480, far=320) == 0
        assert up.trim_count(waiting=0, target=480, far=320) == 0

    def test_a_little_above_the_target_trims_a_couple_of_samples(self):
        assert up.trim_count(waiting=500, target=480, far=320) == up.TRIM_SAMPLES

    def test_far_above_the_target_trims_faster(self):
        assert up.trim_count(waiting=900, target=480, far=320) == up.TRIM_SAMPLES_FAST

    def test_trim_removes_exactly_n_samples_and_keeps_the_rest_in_order(self):
        import struct
        samples = list(range(160))
        data = struct.pack("<160h", *samples)
        out = up.trim(data, 2)
        kept = list(struct.unpack(f"<{len(out) // 2}h", out))
        assert len(kept) == 158
        assert kept == sorted(kept)                 # order preserved
        assert set(samples) - set(kept) == {53, 106}  # spread, not at the ends

    def test_trim_of_zero_or_a_tiny_chunk_is_a_no_op(self):
        data = b"\x01\x00\x02\x00"
        assert up.trim(data, 0) == data
        assert up.trim(data, 4) == data

    def test_oversampling_outpaces_a_slow_card_by_a_safe_margin(self):
        # Measured shortfall on 2026-09-24: ~9.97 s per 10 s, i.e. 0.3 %.
        assert up.OVERSAMPLE > 0.003
        # And trimming can shed more than the surplus, even at the slower rate.
        assert up.TRIM_SAMPLES / 160 > up.OVERSAMPLE


class TestNoiseFilter:
    def test_plugin_debug_and_hw_params_dumps_are_noise(self):
        for line in ("D: bluealsa-pcm.c:443: /org/bluealsa/.../sink: Closing",
                     "ACCESS:  RW_INTERLEAVED", "BUFFER_TIME: 500000", "TICK_TIME: 0"):
            assert up._NOISE.match(line), line

    def test_real_errors_are_not_noise(self):
        for line in ("E: bluealsa-pcm.c:1: Couldn't get BlueALSA PCM",
                     "aplay: pcm_write:2127: write error: No such device"):
            assert not up._NOISE.match(line), line


class TestGain:
    def test_zero_gain_is_untouched(self):
        data = bytes(range(8))
        assert up.apply_gain(data, 0) is data

    def test_six_db_roughly_doubles(self):
        import struct
        out = struct.unpack("<2h", up.apply_gain(struct.pack("<2h", 1000, -1000), 6.02))
        assert out[0] in range(1990, 2010) and out[1] in range(-2010, -1990)

    def test_gain_saturates_instead_of_wrapping(self):
        import struct
        out = struct.unpack("<2h", up.apply_gain(struct.pack("<2h", 20000, -20000), 12))
        assert out == (32767, -32768)

    def test_the_uplink_passes_its_gain_to_the_pipeline(self):
        FakePipeline.started = []
        u = up.Uplink(pcms=lambda: WITH_HFP, start=FakePipeline, gain_db=18)
        u.step()
        assert FakePipeline.started[0].gain_db == 18
