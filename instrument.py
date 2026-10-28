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


def camera_in_use(setup: dict, file_max_adu: float = 65535.0, temp_c=None,
                  header_saturation: tuple | None = None) -> CameraInUse:
    """The camera values for a frame with this setup key (shobs_profile.setup_key). file_max_adu = the largest value
    the file can hold (65535 for 16-bit files, less for a scaled 12/14-bit camera); temp_c = the sensor temperature
    of the frame (CCD-TEMP) for the dark-current estimate; header_saturation = (ADU, keyword) when the frame's header
    says where the camera saturates (SATURATE, MAXADU or DATAMAX), used when no saved profile gives it."""
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
                         "good as that. A bench run (Camera page) measures it.")
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
    if rec is None and header_saturation:
        hv, hk = header_saturation
        rec = sp.record(float(hv), "ADU", "header", note=f"{hk} keyword in the frame's header")
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
    curve = (spec or {}).get("dark_current_curve") or {}
    t_now = sp._finite(temp_c)
    if rec is not None and t_now is not None and rec.get("source") == "measured":
        rec = dark_at_temp(prof, spec, t_now) or rec
    if rec is None and t_now is not None and isinstance(curve.get("points"), list) and len(curve["points"]) >= 2:
        v = dark_from_curve(curve["points"], t_now)
        if v is not None:
            rec = sp.record(round(v, 6), "e-/px/s", "spec", conditions=f"{t_now:g} °C",
                            note=f"{spec.get('model')} dark-current curve at the frame's temperature")
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


def dark_at_temp(profile: dict | None, spec: dict | None, temp_c: float) -> dict | None:
    """A camera profile's measured dark current read at another sensor temperature, as a 'derived' record, or None
    when the profile holds it at (within 1 °C of) that temperature already. Two or more measured temperatures:
    straight lines in log(dark current) between them. One: scaled along the spec's dark-current curve, or doubling
    every DARK_DOUBLING_C °C when there is no curve. An upper limit stays an upper limit."""
    import math
    block = (profile or {}).get("dark") if isinstance((profile or {}).get("dark"), dict) else {}
    pts = [p for p in block.get("by_temp") or [] if isinstance(p, dict) and sp._finite(p.get("temp_c")) is not None
           and sp._finite(p.get("value")) is not None and float(p["value"]) > 0]
    if not pts:
        return None
    near = min(pts, key=lambda p: abs(float(p["temp_c"]) - temp_c))
    if abs(float(near["temp_c"]) - temp_c) <= 1.0:
        # measured at (about) this temperature: that measurement, whichever temperature the profile's headline is
        ul = bool(near.get("upper_limit"))
        return sp.record(float(near["value"]), "e-/px/s", "measured",
                         sigma=None if ul else sp._finite(near.get("sigma")), date=block.get("date"),
                         conditions=f"{float(near['temp_c']):g} °C", run=block.get("run"),
                         note=("at most (2σ upper limit); " if ul else "") +
                         f"measured at {float(near['temp_c']):g} °C")
    firm = [p for p in pts if not p.get("upper_limit")]
    temps = [float(p["temp_c"]) for p in firm]
    at_most = False
    if len(firm) >= 2 and max(temps) - min(temps) >= 4.0:
        v = dark_from_curve([[p["temp_c"], p["value"]] for p in firm], temp_c)
        outside = temp_c < min(temps) or temp_c > max(temps)
        how = ("measured at " + " and ".join(f"{t:g}" for t in sorted(temps)) + " °C" +
               (", carried on past them" if outside else ""))
    else:
        p0 = near
        t0, d0 = float(p0["temp_c"]), float(p0["value"])
        at_most = bool(p0.get("upper_limit"))
        curve = ((spec or {}).get("dark_current_curve") or {}).get("points")
        s0 = dark_from_curve(curve, t0) if isinstance(curve, list) and len(curve) >= 2 else None
        s1 = dark_from_curve(curve, temp_c) if s0 else None
        if s0 and s1:
            v = d0 * s1 / s0
            how = f"measured {d0:g} at {t0:g} °C, scaled along the spec's curve"
        else:
            v = d0 * 2 ** ((temp_c - t0) / DARK_DOUBLING_C)
            how = f"measured {d0:g} at {t0:g} °C, doubling every {DARK_DOUBLING_C:g} °C"
    if v is None or not math.isfinite(v):
        return None
    return sp.record(round(v, 7), "e-/px/s", "derived", conditions=f"{temp_c:g} °C",
                     note=("at most: " if at_most else "") + how + f", read at the frame's {temp_c:g} °C")


def dark_from_curve(points, temp_c: float):
    """A dark-current curve [[°C, e-/px/s], ...] read at temp_c: straight lines in log(dark current), so the
    doubling every few degrees is followed; beyond the ends the end segment's doubling rate is carried on."""
    import math
    pts = sorted((float(t), float(v)) for t, v in points if sp._finite(t) is not None and sp._finite(v)
                 and float(v) > 0)
    if len(pts) < 2:
        return None
    if temp_c <= pts[0][0]:
        (t0, v0), (t1, v1) = pts[0], pts[1]
    elif temp_c >= pts[-1][0]:
        (t0, v0), (t1, v1) = pts[-2], pts[-1]
    else:
        i = next(k for k in range(len(pts) - 1) if pts[k][0] <= temp_c <= pts[k + 1][0])
        (t0, v0), (t1, v1) = pts[i], pts[i + 1]
    if t1 == t0:
        return v0
    return math.exp(math.log(v0) + (math.log(v1) - math.log(v0)) * (temp_c - t0) / (t1 - t0))


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
