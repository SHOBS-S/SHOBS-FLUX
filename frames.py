"""Reading frames for SHOBS-FLUX: pixels, header, what kind of frame it is, the ADU step, and the Bayer planes.

FITS (any HDU with an image; BZERO/BSCALE applied by astropy) and PixInsight XISF (monolithic, uncompressed or zlib;
the reader is SHOBS-P's). Pixels come back as float32 in FILE units (ADU as written). Cameras with a 12- or 14-bit
ADC usually scale their output to 16 bits, so their values step by 16 or 4: adu_step() finds that step, and every
gain FLUX reports is per FILE unit (and says the native one when the step is not 1).

Headers vary: EXPTIME or EXPOSURE, IMAGETYP or FRAME, BAYERPAT or COLORTYP. Each helper here takes what is there and
says None when nothing is."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

import numpy as np

import shobs_profile as sp

FRAME_EXTS = (".fit", ".fits", ".fts", ".xisf")
BAYER_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")


class FrameError(Exception):
    """A file FLUX cannot read as a frame (the message is a plain sentence)."""


@dataclass
class Frame:
    path: str
    data: np.ndarray                       # 2-D float32, file units
    header: dict
    kind: str = "light"                    # light / flat / bias / dark / unknown
    exptime: float | None = None
    bayer: str | None = None               # RGGB… or None for mono
    adu_step: int = 1
    bits: int | None = None                # native bits implied by the step (16 → 1, 14 → 4, 12 → 16)
    setup: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def is_color(self) -> bool:
        return self.bayer in BAYER_PATTERNS


def list_frames(folder: str) -> list[str]:
    """Frame files in a folder (not its subfolders), by name."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(os.path.join(folder, n) for n in names
                  if n.lower().endswith(FRAME_EXTS) and not n.startswith("."))


# ---- header helpers ------------------------------------------------------------------------------------------------

def hfloat(header: dict, *keys):
    for k in keys:
        v = header.get(k)
        if isinstance(v, bool) or v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f
    return None


def exptime_of(header: dict):
    """EXPTIME, EXPOSURE or EXP_TIME in seconds, or None."""
    v = hfloat(header, "EXPTIME", "EXPOSURE", "EXP_TIME")
    return v if v is not None and v >= 0 else None


def bayer_of(header: dict):
    for key in ("BAYERPAT", "COLORTYP"):
        v = header.get(key)
        if isinstance(v, str) and v.strip().upper() in BAYER_PATTERNS:
            return v.strip().upper()
    return None


def kind_of(header: dict, path: str = "") -> str:
    """light / flat / bias / dark from IMAGETYP or FRAME (any capitalisation, 'Light Frame', 'Flat Field'…), else
    from the file name, else 'unknown'."""
    for key in ("IMAGETYP", "FRAME", "FRAMETYP", "IMAGETYPE"):
        v = header.get(key)
        if isinstance(v, str) and v.strip():
            t = v.strip().lower()
            for k in ("bias", "flat", "dark", "light"):
                if k in t:
                    return k
            if "offset" in t or "zero" in t:
                return "bias"
            if "object" in t or "science" in t:
                return "light"
    base = os.path.basename(path).lower()
    for k in ("bias", "flat", "dark", "light"):
        if k in base:
            return k
    return "unknown"


def adu_step(data: np.ndarray, sample: int = 200_000) -> int:
    """The step between pixel values (1 for a true 16-bit camera, 4 for 14-bit, 16 for 12-bit scaled to 16 bits).
    Read on a sample of whole-number pixels; 1 when the values are not whole numbers (already calibrated)."""
    flat = np.asarray(data).ravel()
    if flat.size == 0:
        return 1
    stride = max(1, flat.size // sample)
    vals = flat[::stride]
    vals = vals[np.isfinite(vals)]
    if vals.size == 0 or not np.all(np.abs(vals - np.round(vals)) < 1e-6):
        return 1
    u = np.unique(np.round(vals).astype(np.int64))
    if u.size < 8:
        return 1
    g = int(np.gcd.reduce(np.diff(u)))
    if g <= 1:
        return 1
    # a step only counts when it is a power of two up to 64 (16- to 10-bit ADCs)
    return g if g in (2, 4, 8, 16, 32, 64) else 1


def native_bits(step: int) -> int | None:
    return {1: 16, 2: 15, 4: 14, 8: 13, 16: 12, 32: 11, 64: 10}.get(int(step))


# ---- reading -------------------------------------------------------------------------------------------------------

_XISF_TYPES = {"UInt8": np.uint8, "UInt16": np.uint16, "UInt32": np.uint32, "Float32": np.float32,
               "Float64": np.float64}


def _xisf(path: str, header_only: bool = False):
    import xml.etree.ElementTree as ET
    import zlib

    with open(path, "rb") as fh:
        sig = fh.read(16)
        if sig[:8] != b"XISF0100":
            raise FrameError("it is not an XISF 1.0 file")
        length = int.from_bytes(sig[8:12], "little")
        root = ET.fromstring(fh.read(length).decode("utf-8", "replace").strip("\x00").strip())
        image = next((el for el in root.iter() if el.tag.split("}")[-1] == "Image"), None)
        if image is None:
            raise FrameError("the XISF file holds no image")
        header: dict = {}
        for el in image.iter():
            if el.tag.split("}")[-1] != "FITSKeyword":
                continue
            key = (el.get("name") or "").strip().upper()
            value = (el.get("value") or "").strip()
            if not key or key in ("HISTORY", "COMMENT"):
                continue
            if value.startswith("'") and value.endswith("'") and len(value) >= 2:
                value = value[1:-1].strip()
            else:
                try:
                    value = int(value) if re.fullmatch(r"[+-]?\d+", value) else float(value)
                except ValueError:
                    if value in ("T", "F"):
                        value = value == "T"
            header.setdefault(key, value)
        geometry = [int(v) for v in (image.get("geometry") or "0:0:1").split(":")]
        width, height = geometry[0], geometry[1]
        channels = geometry[2] if len(geometry) > 2 else 1
        if header_only:
            return None, header
        fmt = image.get("sampleFormat") or "UInt16"
        if fmt not in _XISF_TYPES:
            raise FrameError(f"XISF sample format {fmt} is not supported")
        dtype = np.dtype(_XISF_TYPES[fmt]).newbyteorder(">" if image.get("byteOrder") == "big" else "<")
        location = (image.get("location") or "").split(":")
        if location[0] != "attachment" or len(location) < 3:
            raise FrameError("only XISF images stored as attachments are supported")
        fh.seek(int(location[1]))
        blob = fh.read(int(location[2]))
    comp = (image.get("compression") or "").split(":")
    if comp[0]:
        codec = comp[0].lower()
        if codec not in ("zlib", "zlib+sh"):
            raise FrameError(f"XISF compression {comp[0]} is not supported (save it uncompressed, with zlib, or "
                             "as FITS)")
        blob = zlib.decompress(blob)
        if codec.endswith("+sh"):
            item = int(comp[2]) if len(comp) > 2 else dtype.itemsize
            n = len(blob) // item
            blob = np.frombuffer(blob[:n * item], dtype=np.uint8).reshape(item, n).T.tobytes()
    if len(blob) < width * height * channels * dtype.itemsize:
        raise FrameError("the XISF file is cut short (an incomplete copy?)")
    data = np.frombuffer(blob, dtype=dtype, count=width * height * channels).astype(np.float32)
    if (image.get("pixelStorage") or "Planar").lower() == "normal" and channels > 1:
        data = np.moveaxis(data.reshape(height, width, channels), 2, 0)
    else:
        data = data.reshape(channels, height, width)
    if channels > 1:
        header["_FLUX_NOTE"] = "a color image already debayered by other software: its first channel is used"
    data = data[0]
    if fmt.startswith("Float"):
        try:
            lo, hi = (float(v) for v in (image.get("bounds") or "0:1").split(":")[:2])
        except ValueError:
            lo, hi = 0.0, 1.0
        if hi > lo:
            data = (data - lo) / (hi - lo) * 65535.0
    return data, header


def read_header(path: str) -> dict:
    """The header only (fast: no pixels)."""
    if path.lower().endswith(".xisf"):
        return _xisf(path, header_only=True)[1]
    try:
        from astropy.io import fits
        with fits.open(path, memmap=False, lazy_load_hdus=True) as hdul:
            return {str(k): v for k, v in hdul[0].header.items() if k not in ("COMMENT", "HISTORY", "")}
    except FileNotFoundError:
        raise FrameError("the file is not there") from None
    except OSError as exc:
        raise FrameError(f"it is not a readable FITS file ({exc})") from None


def read_frame(path: str) -> Frame:
    """Read one frame. FrameError (plain words) for a missing, cut-short or unreadable file."""
    if not os.path.exists(path):
        raise FrameError("the file is not there")
    notes = []
    if path.lower().endswith(".xisf"):
        data, header = _xisf(path)
    else:
        try:
            import warnings
            from astropy.io import fits
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with fits.open(path, memmap=False) as hdul:
                    hdu = next((h for h in hdul if getattr(h, "data", None) is not None and h.data.ndim >= 2), None)
                    if hdu is None:
                        raise FrameError("the FITS file holds no image (header only, or cut short)")
                    data = np.asarray(hdu.data, dtype=np.float32)
                    header = {str(k): v for k, v in hdul[0].header.items() if k not in ("COMMENT", "HISTORY", "")}
                    if hdu is not hdul[0]:
                        for k, v in hdu.header.items():
                            if k not in ("COMMENT", "HISTORY", ""):
                                header.setdefault(str(k), v)
        except FrameError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            msg = str(exc)
            if "truncated" in msg.lower() or "buffer is too small" in msg.lower() or "cannot reshape" in msg.lower():
                raise FrameError("the FITS file is cut short (an incomplete copy?)") from None
            raise FrameError(f"it is not a readable FITS file ({msg})") from None
        if data.ndim == 3:
            if data.shape[0] in (3, 4):
                notes.append("a color image already debayered by other software: its first channel is used")
            data = data[0]
        if data.ndim != 2:
            raise FrameError(f"it is not a 2-D image (shape {data.shape})")
    step = adu_step(data)
    fr = Frame(path=path, data=data, header=header, kind=kind_of(header, path), exptime=exptime_of(header),
               bayer=bayer_of(header), adu_step=step, bits=native_bits(step), setup=sp.setup_key(header),
               notes=notes)
    if step > 1:
        fr.notes.append(f"pixel values step by {step} ADU: a {fr.bits}-bit camera scaled to 16 bits; gains are "
                        f"given per file ADU (× {step} for the camera's own ADU)")
    if fr.exptime is None:
        fr.notes.append("no EXPTIME or EXPOSURE in the header: rates per second cannot be given")
    return fr


# ---- Bayer planes and superpixels ----------------------------------------------------------------------------------

def channel_offsets(pattern: str) -> dict:
    """{'R': [(dy, dx)], 'G': [(dy, dx), (dy, dx)], 'B': [(dy, dx)]} inside each 2×2 cell."""
    p = pattern.upper()
    out: dict = {"R": [], "G": [], "B": []}
    for i, ch in enumerate(p):
        out[ch].append((i // 2, i % 2))
    return out


def planes(data: np.ndarray, pattern: str | None) -> dict:
    """The raw pixels of each color: {'R': 2-D, 'G': 2-D (both greens side by side), 'B': 2-D}; {'L': data} for
    mono. No interpolation: every value is one real pixel."""
    if pattern not in BAYER_PATTERNS:
        return {"L": data}
    h, w = data.shape
    h2, w2 = h - h % 2, w - w % 2
    d = data[:h2, :w2]
    out = {}
    for ch, offs in channel_offsets(pattern).items():
        parts = [d[dy::2, dx::2] for dy, dx in offs]
        out[ch] = parts[0] if len(parts) == 1 else np.hstack(parts)
    return out


def superpixel_sum(data: np.ndarray, pattern: str | None) -> tuple[np.ndarray, int]:
    """For finding and measuring stars: color frames summed over each 2×2 Bayer cell (one superpixel = four real
    pixels), mono frames as they are. Returns (image, raw pixels per image pixel)."""
    if pattern not in BAYER_PATTERNS:
        return data, 1
    h, w = data.shape
    d = data[: h - h % 2, : w - w % 2]
    s = d[0::2, 0::2] + d[0::2, 1::2] + d[1::2, 0::2] + d[1::2, 1::2]
    return s.astype(np.float32), 4


def central_region(shape: tuple, fraction: float = 0.5) -> tuple[slice, slice]:
    """The middle part of a frame (fraction of each side), even-aligned so a Bayer cell is never split."""
    h, w = shape[:2]
    ch, cw = int(h * fraction) // 2 * 2, int(w * fraction) // 2 * 2
    y0, x0 = (h - ch) // 4 * 2, (w - cw) // 4 * 2
    return slice(y0, y0 + ch), slice(x0, x0 + cw)
