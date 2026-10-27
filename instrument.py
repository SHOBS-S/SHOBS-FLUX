"""The camera values SHOBS-FLUX uses for a frame, each with where it came from.

Order of trust, per value: a saved camera profile (measured or typed by you) → the frame's header (EGAIN) → the
manufacturer's spec read at the frame's gain setting → a plain estimate. Every value keeps its source tag
(measured / header / spec / estimate / user / derived / none), so the Check image page can say which numbers are
measured and which are still guesses. Nothing here writes a file; starting_camera_profile() builds a profile you
can save."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import shobs_profile as sp

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BUILTIN_SPECS = os.path.join(APP_DIR, "builtin_specs")

# what the Check image page shows, in its order: key → (label, unit)
CAMERA_KEYS = {
    "gain_e_per_adu": ("Gain", "e-/ADU"),
    "read_noise_e": ("Read noise", "e-"),
    "full_well_e": ("Full well", "e-"),
    "saturation_adu": ("Saturation", "ADU"),
    "linear_limit_adu": ("Linear limit", "ADU"),
    "bias_level_adu": ("Bias level", "ADU"),
    "dark_current_e_per_px_s": ("Dark current", "e-/px/s"),
    "qe_peak": ("Peak QE", "fraction"),
}
DEFAULT_GAIN = 1.0              # e-/ADU, when nothing says otherwise
DEFAULT_READ_NOISE = 3.0        # e-
LINEAR_FRACTION = 0.8           # linear limit estimate: 80 % of saturation until a bench run measures it
DARK_DOUBLING_C = 6.0           # dark current doubles every ~6 °C (CMOS and CCD alike, roughly)


@dataclass
class CameraInUse:
    cam_id: str
    setup: dict
    records: dict = field(default_factory=dict)        # key → value record
    profile_path: str | None = None
    spec_model: str | None = None
    notes: list = field(default_factory=list)

    def value(self, key: str, default=None):
        return sp.value_of(self.records.get(key), default)

    def source(self, key: str) -> str:
        r = self.records.get(key)
        return r.get("source", "none") if isinstance(r, dict) else "none"


def _set(cam: CameraInUse, key: str, rec: dict) -> None:
    cam.records[key] = rec


def camera_in_use(setup: dict, file_max_adu: float = 65535.0, temp_c=None) -> CameraInUse:
    """The camera values for a frame with this setup key (shobs_profile.setup_key). file_max_adu = the largest value
    the file can hold (65535 for 16-bit files, less for a scaled 12/14-bit camera); temp_c = the sensor temperature
    of the frame (CCD-TEMP) for the dark-current estimate."""
    if not (isinstance(file_max_adu, (int, float)) and file_max_adu == file_max_adu and 0 < file_max_adu < 1e12):
        file_max_adu = 65535.0
    cam = CameraInUse(cam_id=setup.get("camera_id") or "unknown", setup=dict(setup))
    prof = sp.find_camera_profile(cam.cam_id)
    if prof:
        cam.profile_path = sp.profile_path("cameras", cam.cam_id)
    spec = sp.find_spec(setup.get("instrume"), (BUILTIN_SPECS,))
    if spec:
        cam.spec_model = spec.get("model")
    g_set = setup.get("gain_setting")
    today = None

    def from_profile(key):
        v, rec = sp.camera_value(prof, key)
        return rec if v is not None else None

    # gain
    rec = from_profile("gain_e_per_adu")
    if rec is None and setup.get("e_per_adu"):
        rec = sp.record(round(float(setup["e_per_adu"]), 4), "e-/ADU", "header", date=today,
                        conditions=f"EGAIN keyword, gain {g_set:g}" if g_set is not None else "EGAIN keyword")
    if rec is None:
        v = sp.spec_at(spec, "gain_e_per_adu", g_set)
        if v is not None:
            rec = sp.record(v, "e-/ADU", "spec", note=f"{spec.get('model')} at gain {g_set:g}")
    if rec is None:
        rec = sp.record(DEFAULT_GAIN, "e-/ADU", "estimate",
                        note="no EGAIN in the header and no spec for this camera: 1 e-/ADU assumed")
        cam.notes.append("⚠ The gain (e⁻ per ADU) is a plain guess: every electron count on this page is only as "
                         "good as that. A bench run (Camera page, next version) measures it.")
    _set(cam, "gain_e_per_adu", rec)
    gain = sp.value_of(rec, DEFAULT_GAIN)

    # read noise
    rec = from_profile("read_noise_e")
    if rec is None:
        v = sp.spec_at(spec, "read_noise_e", g_set)
        if v is not None:
            rec = sp.record(v, "e-", "spec", note=f"{spec.get('model')} at gain {g_set:g}")
    if rec is None:
        rec = sp.record(DEFAULT_READ_NOISE, "e-", "estimate", note="typical value; not known for this camera")
    _set(cam, "read_noise_e", rec)

    # saturation
    rec = from_profile("saturation_adu")
    if rec is None:
        rec = sp.record(float(file_max_adu), "ADU", "estimate", note="the largest value the file can hold")
    _set(cam, "saturation_adu", rec)
    sat = sp.value_of(rec, file_max_adu)

    # full well
    rec = from_profile("full_well_e")
    if rec is None:
        v = sp.spec_at(spec, "full_well_e", g_set)
        if v is not None:
            rec = sp.record(v, "e-", "spec", note=f"{spec.get('model')} at gain {g_set:g}")
    if rec is None:
        rec = sp.record(round(sat * gain), "e-", "derived", note="saturation × gain")
    _set(cam, "full_well_e", rec)

    # linear limit
    rec = from_profile("linear_limit_adu")
    if rec is None:
        rec = sp.record(round(LINEAR_FRACTION * sat), "ADU", "estimate",
                        note=f"{LINEAR_FRACTION:.0%} of saturation until a bench run measures it")
    _set(cam, "linear_limit_adu", rec)

    # bias level (from the profile only; the Check image page estimates one from the frame when it has none)
    rec = from_profile("bias_level_adu")
    _set(cam, "bias_level_adu", rec if rec is not None else sp.record(None, "ADU", "none"))

    # dark current
    rec = from_profile("dark_current_e_per_px_s")
    if rec is None and spec and isinstance((spec.get("points") or {}).get("dark_current_e_per_px_s"), dict):
        d = spec["points"]["dark_current_e_per_px_s"]
        dv, dt = sp._finite(d.get("value")), sp._finite(d.get("at_temp_c"))
        t = sp._finite(temp_c)
        if dv is not None:
            if dt is not None and t is not None:
                rec = sp.record(round(dv * 2 ** ((t - dt) / DARK_DOUBLING_C), 5), "e-/px/s", "derived",
                                conditions=f"{t:g} °C",
                                note=f"spec {dv:g} at {dt:g} °C, doubling every {DARK_DOUBLING_C:g} °C")
            else:
                rec = sp.record(dv, "e-/px/s", "spec", conditions=f"{dt:g} °C" if dt is not None else None)
    _set(cam, "dark_current_e_per_px_s", rec if rec is not None else sp.record(None, "e-/px/s", "none"))

    # peak QE
    rec = from_profile("qe_peak")
    if rec is None and spec and sp._finite((spec.get("points") or {}).get("qe_peak")) is not None:
        rec = sp.record(float(spec["points"]["qe_peak"]), "fraction", "spec")
    _set(cam, "qe_peak", rec if rec is not None else sp.record(None, "fraction", "none"))
    return cam


def starting_camera_profile(cam: CameraInUse, frame_header: dict, written_by: str) -> dict:
    """A camera profile in format v1 holding the values in use now (header, spec and estimates, labelled as such).
    Saving it gives SHOBS-P and SHOBS-SCHED something to read, and the bench run replaces each value as it measures
    it. The bias level the page estimated is not stored (an estimate from a light frame is too rough)."""
    s = cam.setup
    now = sp.now_iso()
    values = {}
    for key, (_label, unit) in CAMERA_KEYS.items():
        rec = cam.records.get(key)
        values[key] = dict(rec) if isinstance(rec, dict) else sp.record(None, unit, "none")
        if key == "bias_level_adu" and values[key].get("source") != "measured":
            values[key] = sp.record(None, unit, "none", note="not yet measured; FLUX fills it from bias frames")
    bayer = s.get("bayer_pattern")
    color = "OSC" if bayer in ("RGGB", "BGGR", "GRBG", "GBRG") else "mono"
    h = {str(k).upper(): v for k, v in (frame_header or {}).items()}
    camera = {
        "instrume": s.get("instrume"),
        "sensor": None,
        "type": None,
        "color": color,
        "bayer_pattern": bayer if color == "OSC" else None,
        "pixel_um": s.get("pixel_um"),
        "width_px": sp._finite(h.get("NAXIS1")),
        "height_px": sp._finite(h.get("NAXIS2")),
        "adc_bits": None,
        "file_bits": 16,
        "adu_step": 1,
    }
    for k in ("width_px", "height_px"):
        if camera[k] is not None:
            camera[k] = int(camera[k])
    return {
        "format": sp.CAMERA_FORMAT,
        "format_version": sp.FORMAT_VERSION,
        "profile_id": cam.cam_id,
        "revision": 1,
        "written_by": written_by,
        "created": now,
        "updated": now,
        "spec_file": None,
        "camera": camera,
        "mode": {
            "gain_setting": s.get("gain_setting"),
            "offset": s.get("offset"),
            "readout_mode": None,
            "binning": s.get("binning") or 1,
            "set_temp_c": s.get("set_temp_c"),
        },
        "values": values,
        "per_channel": ({ch: {"gain_e_per_adu": None, "read_noise_e": None, "linear_limit_adu": None}
                         for ch in ("R", "G", "B")} if color == "OSC" else
                        {"L": {"gain_e_per_adu": None, "read_noise_e": None, "linear_limit_adu": None}}),
        "linearity": {"curve_adu": [], "max_deviation_pct": None, "measured_to_fraction_of_full_scale": None,
                      "source": "none", "date": None},
    }
