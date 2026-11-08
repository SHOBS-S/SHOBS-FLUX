"""Copy for AstroImageJ / EXOTIC / HOPS (0.6.0, Profiles page): a camera or system profile's values in the words
those three photometry programs use, ready to type (or paste) into them.

For the chosen profile (a system profile brings its camera profile; a camera profile alone has no telescope, so no
plate scale unless exactly one system profile uses that camera): gain (e⁻/ADU), read noise (e⁻), dark current
(e⁻/pixel/s at the profile's sensor temperature), linear limit and saturation (ADU), the plate scale, and for HOPS
the star size. One block per program, in that program's own field names and order:

  AstroImageJ  "Aperture Photometry Settings" (Set_Aperture.java): "CCD gain [e-/count]", "CCD readout noise [e-]",
               "CCD dark current per sec [e-/pix/sec]", then "Saturation warning … for levels higher than" and
               "Linearity warning … for levels higher than". AstroImageJ takes the pixel scale from the plate
               solution (WCS); it is given here under a plain label.
  EXOTIC       its initialization file (inits.json). EXOTIC asks for NO gain, read noise, dark current or saturation
               (none of its keys does); it asks "Image Scale (Ex: 5.21 arcsecs/pixel)" (older files: "Pixel Scale
               (Ex: 5.21 arcsecs/pixel)", read too), "Camera Type (CCD or DSLR)" (its own guide: CCD for a CMOS),
               "Pixel Binning" and "Demosaic Format" (the Bayer pattern of a color camera).
  HOPS         "Camera gain in e/ADU" and "Saturation limit, relatively to the full-well depth" (Photometry, advanced
               settings), "Star FWHM in arcseconds (approximate)", "Binning (use 2 for coloured cameras)", and
               "Telescope Name" / "Camera Name" (observer information). HOPS asks for no read noise or dark current.

Every value says where it came from (measured / from the header / spec / typed by you / worked out / estimate). A
value the profile does not have says "not measured yet": never a made-up number."""
from __future__ import annotations

import os

import instrument
import settings
import shobs_profile as sp

NOT_YET = "not measured yet"
SOURCE = {"measured": "measured", "header": "from the FITS header", "spec": "manufacturer's spec",
          "user": "typed by you", "derived": "worked out", "estimate": "estimate, not measured yet"}
COL = 54                                     # where the values start (the field names are padded to it)
VAL = 10                                     # the values are padded to this, so the sources line up
WRAP = 110                                   # the notes above the blocks are wrapped at this


def _num(v, nd=None) -> str:
    """A value for typing into the program: plain digits, no thousands separators (AstroImageJ and HOPS read them
    as numbers)."""
    v = float(v)
    if nd is not None:
        return f"{v:.{nd}f}"
    if abs(v - round(v)) < 1e-9 and abs(v) >= 1:
        return str(int(round(v)))
    return f"{v:.4g}" if abs(v) >= 0.001 else f"{v:.3g}"


def _src(rec: dict | None, extra: str = "") -> str:
    """Where a value came from, in words, with its own note or conditions when it has one."""
    if not isinstance(rec, dict):
        return extra
    words = SOURCE.get(rec.get("source"), str(rec.get("source") or ""))
    note = rec.get("note")
    if isinstance(note, str) and note.lower().startswith("typed by"):   # says the source again
        note = note.split(";", 1)[1].strip() if ";" in note else ""
    bits = [b for b in (rec.get("conditions"), note, extra) if b]
    return words + (f" ({'; '.join(str(b) for b in bits)})" if bits else "")


def _value(rec):
    v = sp.value_of(rec) if isinstance(rec, dict) else None
    v = sp._finite(v)
    if v is None or (isinstance(rec, dict) and rec.get("source") == "none"):
        return None
    return v


def camera_for(doc: dict) -> tuple[dict | None, dict | None, str]:
    """(camera profile, system profile, words) for the chosen profile. words says what is missing."""
    if doc.get("format") == sp.SYSTEM_FORMAT:
        cid = doc.get("camera_profile") or ""
        try:
            cam = sp.find_camera_profile(cid) if cid else None
        except Exception:
            cam = None
        return cam, doc, ("" if cam else f"there is no camera profile {cid or '(none named)'} yet, so the camera "
                                         "values are not measured yet")
    if doc.get("format") == sp.CAMERA_FORMAT:
        cid = doc.get("profile_id")
        systems = []
        for p in sp.list_profiles("systems"):
            try:
                d = sp.load(p)
            except sp.ProfileError:
                continue
            if d.get("camera_profile") == cid:
                systems.append(d)
        return doc, (systems[0] if len(systems) == 1 else None), (
            "" if len(systems) == 1 else
            "a camera alone has no telescope: pick its system profile for the plate scale" if not systems else
            f"{len(systems)} telescopes use this camera: pick one's system profile for the plate scale")
    return None, None, "pick a camera or system profile"


def values(doc: dict) -> dict:
    """{key: (value or None, source words)} for the export, plus "_about" lines."""
    cam, sysd, missing = camera_for(doc)
    out: dict = {}
    mode = (cam or {}).get("mode") or {}
    camera = (cam or {}).get("camera") or {}
    temp = sp._finite(mode.get("set_temp_c"))

    def put(key, rec, extra=""):
        v = _value(rec)
        out[key] = (v, _src(rec, extra) if v is not None else NOT_YET)

    put("gain", sp.camera_value(cam, "gain_e_per_adu")[1])
    put("read_noise", sp.camera_value(cam, "read_noise_e")[1])
    # dark current at the profile's own sensor temperature (a dark run at another temperature is read there)
    drec = sp.camera_value(cam, "dark_current_e_per_px_s")[1]
    if cam and temp is not None:
        spec = None
        try:
            spec = sp.find_spec(camera.get("instrume"), (instrument.BUILTIN_SPECS,))
        except Exception:
            spec = None
        at = instrument.dark_at_temp(cam, spec, temp)
        if at is not None and (drec is None or drec.get("source") != "user"):    # a value you typed wins
            drec = at
    put("dark", drec)
    if out["dark"][0] is not None and "at most" in out["dark"][1]:
        out["dark"] = (out["dark"][0], out["dark"][1] + " — an upper limit: the real dark current is smaller")
    put("linear", sp.camera_value(cam, "linear_limit_adu")[1])
    put("saturation", sp.camera_value(cam, "saturation_adu")[1])
    bias = _value(sp.camera_value(cam, "bias_level_adu")[1])
    out["bias"] = (bias, "")
    lin, sat = out["linear"][0], out["saturation"][0]
    if lin is not None and sat is not None and sat > 0:
        b = bias or 0.0
        frac = (lin - b) / (sat - b) if sat > b else lin / sat
        out["hops_sat"] = (round(frac, 2), f"worked out: linear limit {_num(lin)} ÷ saturation {_num(sat)} ADU"
                           + (f", bias {_num(b)} ADU taken off both" if bias else ""))
    else:
        out["hops_sat"] = (None, NOT_YET + " (needs the linear limit and the saturation)")
    # plate scale (raw camera pixels) and star size, from the system profile
    ps = ((sysd or {}).get("plate_scale") or {}).get("raw_arcsec_per_px")
    if _value(ps) is not None:
        put("scale", ps, "raw camera pixels" + ("" if sysd is doc else f"; system profile {sysd.get('profile_id')}"))
    else:
        out["scale"] = (None, NOT_YET + (f" ({missing})" if missing and not sysd else ""))
    fw, fw_band = None, ""
    for band in ("TG", "CV", "V", "SG", "TR", "TB", "SR", "SI"):
        rec = (((sysd or {}).get("bands") or {}).get(band) or {}).get("fwhm_in_focus")
        if _value(rec) is not None:
            fw, fw_band = rec, band
            break
    if fw is None:
        for band, recs in sorted(((sysd or {}).get("bands") or {}).items()):
            rec = (recs or {}).get("fwhm_in_focus") if isinstance(recs, dict) else None
            if _value(rec) is not None:
                fw, fw_band = rec, band
                break
    if fw is not None:
        put("fwhm", fw, f"seeing in focus, {fw_band}")
    else:
        out["fwhm"] = (None, NOT_YET + (" (from the Sky page's nights)" if sysd else " (needs a system profile)"))
    b = sp._finite(mode.get("binning")) or 1
    out["binning"] = (int(b), "camera profile")
    bayer = camera.get("bayer_pattern")
    out["bayer"] = (bayer if bayer else None, "camera profile")
    out["camera_name"] = (camera.get("instrume") or None, "camera profile")
    out["telescope_name"] = (((sysd or {}).get("telescope") or {}).get("name") or None,
                             "system profile" if sysd else "")
    about = []
    if cam:
        cbits = [f"gain setting {_num(mode['gain_setting'])}" if sp._finite(mode.get("gain_setting")) is not None
                 else "", f"offset {_num(mode['offset'])}" if sp._finite(mode.get("offset")) is not None else "",
                 f"binning {int(b)}×{int(b)}", f"sensor at {_num(temp)} °C" if temp is not None else ""]
        about.append(f"Camera profile {cam.get('profile_id')} (revision {cam.get('revision', '?')}, updated "
                     f"{str(cam.get('updated') or '')[:10]}).")
        about.append("For raw frames from this camera at " + ", ".join(c for c in cbits if c) + ".")
    if sysd:
        tel = sysd.get("telescope") or {}
        about.append(f"System profile {sysd.get('profile_id')} (telescope {tel.get('name') or '?'}"
                     + (f", {_num(tel['focal_mm'])} mm" if sp._finite(tel.get("focal_mm")) else "") + ").")
    if missing:
        about.append(missing[0].upper() + missing[1:] + ".")
    out["_about"] = about
    return out


def _line(name: str, val, src: str, fmt=None) -> str:
    """One field: its name, the value (or "not measured yet ...") and where the value came from."""
    pad = " " * max(COL - len(name), 2)
    if val is None:
        return f"  {name}{pad}{src or NOT_YET}"
    shown = fmt(val) if fmt else _num(val) if isinstance(val, (int, float)) else str(val)
    return f"  {name}{pad}{shown:<{VAL}}  {src}".rstrip()


def text(doc: dict) -> str:
    """The whole export as plain text (what Copy puts on the clipboard and Save writes)."""
    v = values(doc)
    g, rn, dk, lin, sat, sc = (v[k] for k in ("gain", "read_noise", "dark", "linear", "saturation", "scale"))
    import textwrap
    L = [f"{settings.APP_NAME} {settings.APP_VERSION}: values for AstroImageJ, EXOTIC and HOPS",
         f"Written {sp.now_iso()[:16].replace('T', ' ')}."]
    notes = list(v["_about"])
    notes.append("Each value says where it came from: measured, from the FITS header, manufacturer's spec, typed by "
                 "you, worked out, or an estimate. “not measured yet” means the profile does not have it: nothing is "
                 "made up.")
    if v["bias"][0] is not None:
        notes.append(f"Levels in ADU are raw (bias {_num(v['bias'][0])} ADU included): for bias-subtracted frames "
                     f"take {_num(v['bias'][0])} off them.")
    for n in notes:
        L += textwrap.wrap(n, WRAP) or [""]
    L.append("")
    L.append("AstroImageJ: Aperture Photometry Settings (Multi-Aperture)")
    L.append(_line("CCD gain [e-/count]", g[0], g[1]))
    L.append(_line("CCD readout noise [e-]", rn[0], rn[1]))
    L.append(_line("CCD dark current per sec [e-/pix/sec]", dk[0], dk[1]))
    L.append(_line("Saturation warning: for levels higher than", sat[0], sat[1]))
    L.append(_line("Linearity warning: for levels higher than", lin[0], lin[1]))
    L.append(_line("Pixel scale (arcsec/pixel)*", sc[0], sc[1], fmt=lambda x: _num(x, 3)))
    L.append("  * not an AstroImageJ field: it takes the pixel scale from the plate solution.")
    L.append("")
    L.append("EXOTIC: initialization file (inits.json)")
    L.append(_line('"Image Scale (Ex: 5.21 arcsecs/pixel)"', sc[0], sc[1], fmt=lambda x: _num(x, 3)))
    L.append(_line('"Camera Type (CCD or DSLR)"', "CCD", "EXOTIC's guide: CCD for a CMOS camera too (say CMOS in "
                                                        "\"Observing Notes\")"))
    b = v["binning"][0]
    L.append(_line('"Pixel Binning"', f"{b}x{b}", v["binning"][1]))
    L.append(_line('"Demosaic Format"', v["bayer"][0] or "null", "Bayer pattern, camera profile" if v["bayer"][0]
                   else "mono camera: no color processing"))
    L.append("  EXOTIC asks for no gain, read noise, dark current or saturation. Older init files call the scale "
             "\"Pixel Scale (Ex: 5.21 arcsecs/pixel)\"; EXOTIC reads either.")
    L.append("")
    L.append("HOPS")
    L.append(_line("Camera gain in e/ADU", g[0], g[1]))
    hs = v["hops_sat"]
    L.append(_line("Saturation limit, relatively to the full-well depth", hs[0], hs[1],
                   fmt=lambda x: _num(x, 2)))
    fw = v["fwhm"]
    L.append(_line("Star FWHM in arcseconds (approximate)", fw[0], fw[1], fmt=lambda x: _num(x, 1)))
    L.append(_line("Binning (use 2 for coloured cameras)", b, v["binning"][1] + (
        "; HOPS's own advice is 2 for a color camera" if v["bayer"][0] else "")))
    L.append(_line("Telescope Name", v["telescope_name"][0], v["telescope_name"][1]))
    L.append(_line("Camera Name", v["camera_name"][0], v["camera_name"][1]))
    L.append("  HOPS asks for no read noise or dark current.")
    return "\n".join(L) + "\n"


def default_name(doc: dict) -> str:
    pid = str(doc.get("profile_id") or "profile")
    return f"SHOBS-FLUX_{pid}_for_AIJ_EXOTIC_HOPS.txt"


def downloads_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.path.expanduser("~")
