"""Log parsing and the three capture tools, exercised on real Doblo traces."""

import json
from pathlib import Path

import pytest

from blueandme.tools import decode as decode_tool
from blueandme.tools import diff as diff_tool
from blueandme.tools import protocol_map as map_tool
from blueandme.tools import status as status_tool
from blueandme.tools.logfmt import CaptureMeta, LogFormatError, parse_line, read, write

FIXTURES = Path(__file__).parent / "fixtures"
TRACE = FIXTURES / "doblo-03-radio-play-mute-menu-esc.log"
RADIO_TRACE = FIXTURES / "doblo-01-radio-unit.log"
#: fmntf/fiatcan publishes its traces without a licence, so they are fetched
#: (tests/fixtures/fetch.sh), not shipped.
needs_traces = pytest.mark.skipif(
    not TRACE.exists(), reason="Doblo traces not fetched: run tests/fixtures/fetch.sh")


class TestLogFormat:
    def test_parses_a_29_bit_line(self):
        f = parse_line("(1517155764.191254) can0 06314000#4000000000000000")
        assert f.can_id == 0x06314000 and f.extended
        assert f.data.hex().upper() == "4000000000000000"

    def test_parses_an_11_bit_line(self):
        f = parse_line("(1.0) can0 3C4#8000")
        assert f.can_id == 0x3C4 and not f.extended

    def test_id_width_comes_from_the_printed_nibble_count(self):
        """This is the only place a candump log records 11-bit vs 29-bit, and
        resolving that is decision gate D1, so it must not be guessed."""
        assert not parse_line("(1.0) can0 021#00").extended
        assert parse_line("(1.0) can0 00000021#00").extended

    def test_blank_lines_and_comments_are_skipped(self):
        assert parse_line("") is None and parse_line("# note") is None

    def test_garbage_is_rejected(self):
        with pytest.raises(LogFormatError):
            parse_line("this is not a candump line")

    @needs_traces
    def test_round_trips(self, tmp_path):
        original = list(read(TRACE))[:50]
        out = tmp_path / "rt.log"
        assert write(out, original) == 50
        assert list(read(out)) == original

    @needs_traces
    def test_reads_a_real_trace(self):
        frames = list(read(TRACE))
        assert len(frames) == 2000
        assert all(f.extended for f in frames)

    def test_meta_round_trips(self, tmp_path):
        log = tmp_path / "c.log"
        log.write_text("(1.0) can0 3C4#8000\n")
        meta = CaptureMeta(label="test", scenario="A", id_widths={"11-bit": 1})
        path = meta.save(log)
        assert json.loads(path.read_text())["label"] == "test"
        assert CaptureMeta.load(log).id_widths == {"11-bit": 1}

    def test_meta_absent_returns_none(self, tmp_path):
        log = tmp_path / "c.log"
        log.write_text("")
        assert CaptureMeta.load(log) is None


@needs_traces
class TestDecode:
    def test_annotations_match_fmntfs_published_decoding(self, capsys):
        """Regression proof that the decoder reads real traffic the way the
        person who reverse-engineered it did."""
        decode_tool.main([str(TRACE), "--profile", "doblo263", "--limit", "40"])
        out = capsys.readouterr().out
        for expected in (
            "BC: nodes, please wake up",
            "BC: nodes, what is your status?",
            "Blue&Me: going to sleep",
            "instrument panel: going to sleep",
            "track position 00:00",
            "FM tuner is off",
            "Blue&Me watchdog",
            "no button pressed",
            "radio unit powered down",
        ):
            assert expected in out, expected

    def test_every_annotation_carries_its_evidence_level(self, capsys):
        decode_tool.main([str(TRACE), "--profile", "doblo263", "--limit", "40"])
        for line in capsys.readouterr().out.splitlines():
            if "\t" in line and line.split("\t", 1)[1].strip():
                assert "[OTHER_VEHICLE" in line, line

    def test_giulietta_profile_recognises_only_what_this_car_showed(self, capsys):
        """In a Doblo trace the Giulietta profile recognises the frames the two
        cars share, ID and all, and labels them with this car's evidence."""
        from blueandme.protocol.registry import get

        ids = {spec.hex_id for spec in get("giulietta940")}
        decode_tool.main([str(TRACE), "--limit", "200"])
        out, err = capsys.readouterr()
        assert "27 recognised" in err
        for line in out.splitlines():
            if "\t" in line and line.split("\t", 1)[1].strip():
                assert line.split("can0 ", 1)[1][:8] in ids, line
                # The text frame keeps its real evidence: the Doblo's, sent on
                # the owner's decision; everything else was seen on this car.
                if "0A394021#" in line:
                    assert "[OTHER_VEHICLE]" in line, line
                else:
                    assert "[CONFIRMED_940]" in line, line

    def test_summary_lists_the_research_queue(self, capsys):
        decode_tool.main([str(TRACE), "--profile", "doblo263",
                          "--summary", "--limit", "300"])
        err = capsys.readouterr().err
        assert "unrecognised IDs (research queue)" in err

    def test_missing_file_is_reported(self, capsys):
        assert decode_tool.main(["/nonexistent.log"]) == 2


@needs_traces
class TestDiff:
    def test_finds_ids_unique_to_the_candidate(self, capsys):
        assert diff_tool.main([
            str(RADIO_TRACE), str(TRACE), "--profile", "doblo263", "--min-count", "3",
        ]) == 0
        out = capsys.readouterr().out
        # The menu-press capture contains display text frames the radio-only
        # capture does not. This is exactly the signal capture H must produce.
        assert "0A394021" in out
        assert "bm_text_message" in out

    def test_reports_changing_bit_positions(self, capsys):
        diff_tool.main([str(RADIO_TRACE), str(TRACE), "--by", "bits",
                        "--profile", "doblo263"])
        out = capsys.readouterr().out
        assert "volatile bits" in out

    def test_identical_captures_differ_in_nothing(self, capsys):
        diff_tool.main([str(TRACE), str(TRACE), "--profile", "doblo263"])
        out = capsys.readouterr().out
        assert "0 new IDs, 0 disappeared" in out


class TestProtocolMap:
    def test_giulietta_section_shows_frames_with_provenance(self, capsys):
        map_tool.main(["--profile", "giulietta940"])
        out = capsys.readouterr().out
        assert "steering_wheel_buttons" in out
        assert "captures/C-buttons.log" in out
        # The 29-bit width appears with both citations: capture A and the
        # alfa-blue-me author who reported it first.
        assert "29-bit extended" in out
        assert "CONFIRMED_940" in out
        assert "captures/A-first-contact.log" in out
        assert "768146196" in out

    def test_reference_profiles_are_labelled(self, capsys):
        map_tool.main(["--profile", "doblo263"])
        assert "REFERENCE ONLY" in capsys.readouterr().out


class TestStatusAudio:
    """The audio check reads a fake /proc/asound laid out like the real one."""

    @staticmethod
    def _proc(tmp_path, usbid="0d8c:0014"):
        (tmp_path / "cards").write_text(
            " 0 [Device         ]: USB-Audio - USB Audio Device\n"
            "                      C-Media Electronics Inc. USB Audio Device at usb-1, full speed\n"
        )
        if usbid is not None:
            card = tmp_path / "card0"
            card.mkdir()
            (card / "usbid").write_text(usbid + "\n")
            (tmp_path / "Device").symlink_to("card0")
        return tmp_path

    @staticmethod
    def _rows(proc, monkeypatch, loopback="off", agc="off"):
        def fake_amixer(cmd):
            value = agc if cmd[-1] == "name=Auto Gain Control" else loopback
            return f"numid=3,iface=MIXER,{cmd[-1]}\n  : values={value}\n"
        monkeypatch.setattr(status_tool, "_run", fake_amixer)
        rep = status_tool.Report()
        status_tool.check_audio(rep, None, proc=proc)
        return {label: (st, detail) for st, label, detail in rep.rows}

    def test_the_hs100b_is_recognised_by_name_and_usb_id(self, tmp_path, monkeypatch):
        rows = self._rows(self._proc(tmp_path), monkeypatch)
        assert rows["USB sound card"] == (status_tool.OK, "Device (0d8c:0014)")
        assert rows["mic loopback to output"] == (status_tool.OK, "off")
        assert rows["mic auto gain"] == (status_tool.OK, "off")

    def test_a_missing_card_is_flagged(self, tmp_path, monkeypatch):
        rows = self._rows(self._proc(tmp_path, usbid=None), monkeypatch)
        assert rows["USB sound card"][0] == status_tool.WARN
        assert "OTG" in rows["USB sound card"][1]

    def test_a_different_card_under_the_same_name_is_flagged(self, tmp_path, monkeypatch):
        rows = self._rows(self._proc(tmp_path, usbid="1234:5678"), monkeypatch)
        assert rows["USB sound card"][0] == status_tool.WARN

    def test_mic_loopback_on_is_flagged(self, tmp_path, monkeypatch):
        rows = self._rows(self._proc(tmp_path), monkeypatch, loopback="on")
        assert rows["mic loopback to output"][0] == status_tool.WARN

    def test_mic_auto_gain_on_is_flagged(self, tmp_path, monkeypatch):
        rows = self._rows(self._proc(tmp_path), monkeypatch, agc="on")
        assert rows["mic auto gain"][0] == status_tool.WARN
        assert "Auto Gain Control" in rows["mic auto gain"][1]


class TestStatusPlayers:
    """journalctl -u bluealsa-aplay -o cat, as on the Pi (2026-09-25)."""

    STARTED = "Started bluealsa-aplay.service - BlueALSA player service.\n"
    DEAF = "bluealsa-aplay: W: Couldn't open ALSA playback PCM: Open PCM: No such device\n"
    MIXER = "bluealsa-aplay: W: Couldn't open ALSA mixer: Mixer element not found\n"

    def test_a_player_that_cannot_open_the_card_is_caught(self):
        assert status_tool.player_cannot_open_card(self.STARTED + self.DEAF * 3)

    def test_a_restart_clears_it(self):
        log = self.STARTED + self.DEAF * 9 + self.STARTED + self.MIXER
        assert not status_tool.player_cannot_open_card(log)

    def test_the_missing_mixer_warning_is_not_a_failure(self):
        assert not status_tool.player_cannot_open_card(self.STARTED + self.MIXER)

    def test_no_journal_access_is_not_a_failure(self):
        assert not status_tool.player_cannot_open_card("")


class TestStatusCan:
    """ip -details -statistics link show can0, on the Pi (2026-09-25)."""

    IP = (
        "2: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP mode DEFAULT group default qlen 128\n"
        "    link/can  promiscuity 0  allmulti 0 minmtu 0 maxmtu 0 \n"
        "    can <LISTEN-ONLY> state ERROR-ACTIVE restart-ms 0 \n"
        "\t  bitrate 50000 sample-point 0.875\n"
        "\t  clock 4000000 \n"
        "\t  re-started bus-errors arbit-lost error-warn error-pass bus-off\n"
        "\t  {counts}         numtxqueues 1 gso_max_size 65536 parentbus spi parentdev spi0.0 \n"
        "    RX:  bytes packets errors dropped  missed   mcast           \n"
        "             0       0      0       0       0       0 \n"
    )
    CLEAN = "0          0          0          0          0          0"

    def test_the_controller_state_is_not_the_link_state(self):
        assert status_tool.can_controller_state(self.IP.format(counts=self.CLEAN)) == "ERROR-ACTIVE"

    def test_the_state_without_a_ctrlmode(self):
        out = self.IP.format(counts=self.CLEAN).replace("can <LISTEN-ONLY> state", "can state")
        assert status_tool.can_controller_state(out) == "ERROR-ACTIVE"

    def test_error_counters_are_read_from_the_table(self):
        counters = status_tool.can_error_counters(
            self.IP.format(counts="2          17         0          1          0          0"))
        assert counters["re-started"] == 2
        assert counters["bus-errors"] == 17
        assert counters["error-warn"] == 1

    def test_no_table_means_no_counters(self):
        assert status_tool.can_error_counters("can state ERROR-ACTIVE\n") == {}
