"""Field-star catalogs for the "folder of lights" run (sky_folder.py): APASS DR9 and Gaia DR3 from VizieR, the
Gaia → Johnson-Cousins and Sloan relations, the VSX known-variable list, and the band helpers.

The VizieR queries are SHOBS-P's (photometry_core): ASU-TSV over HTTPS (CDS first, then the CfA mirror), answers kept
for 30 days in the SHOBS data folder's cache/ (an answer VizieR marks as cut short or failed is never kept).

Which catalog a band comes from: APASS for V, B, g′, r′, i′ (its own measured magnitudes), Gaia DR3 converted for
the stars APASS lacks and for R and z′ (Gaia only). A Gaia star within 2″ of an APASS star is the same star; APASS
wins for its bands.

Standard library + numpy (+ scipy for the position match)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request

import numpy as np

import shobs_profile as sp

HOSTS = ("https://vizier.cds.unistra.fr", "https://vizier.cfa.harvard.edu")
USER_AGENT = "SHOBS-FLUX"
CACHE_DAYS = 30.0
APASS = "II/336/apass9"
GAIA = "I/355/gaiadr3"
VSX = "B/vsx/vsx"
APASS_COLS = ("RAJ2000", "DEJ2000", "Vmag", "Bmag", "g'mag", "r'mag", "i'mag")
APASS_BANDS = {"Vmag": "V", "Bmag": "B", "g'mag": "SG", "r'mag": "SR", "i'mag": "SI"}
GAIA_COLS = ("Source", "RA_ICRS", "DE_ICRS", "Gmag", "BP-RP")
GAIA_LIMIT = 17.5             # Gmag: fainter stars are not asked for
MATCH_ARCSEC = 2.0            # an APASS and a Gaia star this close are the same star
BANDS = ("V", "B", "R", "SG", "SR", "SI", "SZ")
CATALOG_NAMES = {APASS: "APASS DR9", GAIA: "Gaia DR3", VSX: "VSX"}

# Gaia DR3 documentation, Table 5.8: G − x = Σ c_k C^k (C = BP − RP), SDSS bands, with the colour range each holds in
SLOAN_COEF = {"SG": ((0.2199, -0.6365, -0.1548, 0.0064), (0.3, 2.0)),
              "SR": ((-0.09837, 0.08592, 0.1907, -0.1701, 0.02263), (0.0, 2.0)),
              "SI": ((-0.293, 0.6404, -0.09609, -0.002104), (0.5, 2.0)),
              "SZ": ((-0.4619, 0.8992, -0.08271, 0.005029), (-0.5, 4.5))}


class CatalogError(Exception):
    """The catalog could not be asked (the message is a plain sentence)."""


# ---- band helpers --------------------------------------------------------------------------------------------------

def band_of_filter(filter_text) -> str:
    """The SHOBS band code of a FILTER header value: g′ / gp / SG / "Sloan g" / g → SG (r, i, z alike); V, B, R, I as
    they are; L, Lum, Clear, CV, none or empty → CV; anything else → CV."""
    t = str(filter_text or "").strip()
    if not t:
        return "CV"
    low = t.lower().replace("’", "'").replace("′", "'")
    low = low.replace("sloan", "").replace("sdss", "").replace("filter", "").strip(" _-")
    if low.upper() in ("SG", "SR", "SI", "SZ", "SU"):
        return low.upper()
    for letter in "griuz":
        if low in (letter, letter + "'", letter + "p", letter + "-prime", letter + " prime", letter + "_prime",
                   letter + "2", letter + "''"):
            return "S" + letter.upper()
    up = low.upper()
    if up in ("V", "B", "R", "I", "U"):
        return up
    if up in ("RC", "R_C", "COUSINS R", "JOHNSON R"):
        return "R"
    if up in ("IC", "I_C", "COUSINS I"):
        return "I"
    if up in ("JOHNSON V", "JOHNSON B", "BESSEL V", "BESSELL V", "BESSELL B"):
        return up[-1]
    return "CV"


def catalog_band(band: str) -> str:
    """The catalog band an observing band is calibrated against (onsky.BAND_CATALOG)."""
    import onsky
    return onsky.BAND_CATALOG.get(str(band or "").upper(), "V")


# ---- Gaia relations ------------------------------------------------------------------------------------------------

def _poly(c, x):
    return sum(ck * x ** k for k, ck in enumerate(c))


def gaia_v(g, c):
    """Johnson V from Gaia G and BP − RP (Riello et al. 2021, −0.5 < C < 5), or None."""
    if g is None or c is None or not -0.5 < c < 5.0:
        return None
    return g + 0.02704 - 0.01424 * c + 0.2156 * c * c - 0.01426 * c ** 3


def gaia_r(g, c):
    """Cousins R from Gaia G and BP − RP (Riello et al. 2021, −0.5 < C < 2.75), or None."""
    if g is None or c is None or not -0.5 < c < 2.75:
        return None
    return g - (-0.02275 + 0.3961 * c - 0.1243 * c * c - 0.01396 * c ** 3 + 0.003775 * c ** 4)


def gaia_sdss(g, c) -> dict:
    """SDSS g r i z from Gaia G and BP − RP (Gaia DR3 documentation Table 5.8), each only inside its colour range."""
    out = {}
    if g is None or c is None:
        return out
    for band, (coef, (lo, hi)) in SLOAN_COEF.items():
        if lo < c < hi:
            out[band] = g - _poly(coef, c)
    return out


def sdss_to_primed(m: dict) -> dict:
    """SDSS g r i z → the primed (USNO) system: g′ = g − 0.060((g−r) − 0.53), r′ = r − 0.035((r−i) − 0.21),
    i′ = i − 0.041((r−i) − 0.21), z′ = z + 0.030((i−z) − 0.09). A band whose partner colour is missing stays as is."""
    g, r, i, z = (m.get(b) for b in ("SG", "SR", "SI", "SZ"))
    out = dict(m)
    if g is not None and r is not None:
        out["SG"] = g - 0.060 * ((g - r) - 0.53)
    if r is not None and i is not None:
        out["SR"] = r - 0.035 * ((r - i) - 0.21)
        out["SI"] = i - 0.041 * ((r - i) - 0.21)
    if i is not None and z is not None:
        out["SZ"] = z + 0.030 * ((i - z) - 0.09)
    return out


def gaia_bands(g, c) -> dict:
    """Every band FLUX can calibrate against, from one Gaia star: V, R and the primed Sloan bands (None-free)."""
    out = sdss_to_primed(gaia_sdss(g, c))
    v, r = gaia_v(g, c), gaia_r(g, c)
    if v is not None:
        out["V"] = v
    if r is not None:
        out["R"] = r
    return out


# ---- VizieR --------------------------------------------------------------------------------------------------------

def query_url(host: str, source: str, ra: float, dec: float, radius_deg: float, cols, out_max: int = 5000,
              sort: str | None = None, constraints=()) -> str:
    params = [("-source", source), ("-c", f"{ra:.6f} {dec:+.6f}"), ("-c.rd", f"{radius_deg:.5f}"),
              ("-out", " ".join(cols)), ("-out.max", str(int(out_max)))]
    if sort:
        params.append(("-sort", sort))
    params.extend((str(k), str(v)) for k, v in constraints)
    return f"{host}/viz-bin/asu-tsv?" + urllib.parse.urlencode(params)


def parse_tsv(text: str) -> tuple[list[dict], str]:
    """(rows as {column: text}, why incomplete or ""). ASU-TSV: '#' comments, a header row, a units row, a row of
    dashes, then tab-separated data. A '#' line that speaks of an error, a timeout or truncation marks the answer
    incomplete."""
    rows: list[dict] = []
    incomplete = ""
    header = None
    stage = 0                       # 0 header next, 1 units next, 2 dashes next, 3 data
    for ln in text.splitlines():
        if ln.startswith("#"):
            low = ln.lower()
            if not low.startswith("#column") and any(w in low for w in ("error", "timeout", "truncated")):
                incomplete = incomplete or ln.lstrip("#").strip()
            continue
        if not ln.strip():
            continue
        cells = [c.strip() for c in ln.split("\t")]
        if stage == 0:
            header, stage = cells, 1
            continue
        if stage in (1, 2) and set(ln.replace("\t", "").strip()) <= {"-"}:
            stage = 3
            continue
        if stage == 1:
            stage = 2                   # the units row
            continue
        if stage == 2:
            stage = 3                   # no dashes row: this is data
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells)))
    return rows, incomplete


def _urlopen(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def cache_path(key: str) -> str:
    return os.path.join(sp.data_dir(), "cache", f"catalog_{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json")


def _cache_key(source, ra, dec, radius_deg, cols, out_max, sort, constraints) -> str:
    return json.dumps([source, round(float(ra), 5), round(float(dec), 5), round(float(radius_deg), 5), list(cols),
                       int(out_max), sort or "", [list(c) for c in constraints]])


def _cache_read(key: str):
    p = cache_path(key)
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        if time.time() - float(doc.get("saved", 0)) <= CACHE_DAYS * 86400.0 and isinstance(doc.get("rows"), list):
            return doc
    except (OSError, ValueError, TypeError):
        pass
    return None


def _cache_write(key: str, rows: list, host: str) -> None:
    p = cache_path(key)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"saved": time.time(), "host": host, "rows": rows}, fh)
        os.replace(tmp, p)
    except OSError:
        pass                                            # a cache that cannot be written only costs a re-query


def vizier(source: str, ra: float, dec: float, radius_deg: float, cols, constraints=(), out_max: int = 5000,
           sort: str | None = None, timeout: float = 40, fetcher=None, use_cache: bool = True) -> tuple[list, str, str]:
    """(rows, why incomplete or "", where from) for one cone query. CatalogError when no host answers."""
    key = _cache_key(source, ra, dec, radius_deg, cols, out_max, sort, constraints)
    if use_cache:
        hit = _cache_read(key)
        if hit is not None:
            return hit["rows"], "", f"{hit.get('host', 'VizieR')} (kept from an earlier run)"
    fetch = fetcher or _urlopen
    problems = []
    for host in HOSTS:
        url = query_url(host, source, ra, dec, radius_deg, cols, out_max, sort, constraints)
        try:
            raw = fetch(url, timeout)
        except Exception as exc:                        # no network, timeout, HTTP error: try the mirror
            problems.append(f"{host.split('//')[-1]}: {getattr(exc, 'reason', None) or exc}")
            continue
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        rows, incomplete = parse_tsv(text)
        if not rows and incomplete:
            problems.append(f"{host.split('//')[-1]}: {incomplete}")
            continue
        if not incomplete and use_cache:
            _cache_write(key, rows, host.split("//")[-1])
        return rows, incomplete, host.split("//")[-1]
    raise CatalogError("the star catalog (VizieR) could not be reached: " + "; ".join(problems))


# ---- parsing values ------------------------------------------------------------------------------------------------

def _num(text):
    try:
        v = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_angle(text, hours: bool = False):
    """Degrees from '123.456' or sexagesimal '12 34 56.7' / '12:34:56.7' (hours=True: × 15), or None."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text) if math.isfinite(float(text)) else None
    s = str(text).strip().replace(":", " ").replace("h", " ").replace("m", " ").replace("s", " ")
    s = s.replace("d", " ").replace("°", " ").replace("'", " ").replace('"', " ")
    parts = s.split()
    if not parts:
        return None
    if len(parts) == 1:
        v = _num(parts[0])
        return None if v is None else v * (15.0 if hours else 1.0)
    try:
        sign = -1.0 if parts[0].startswith("-") else 1.0
        vals = [abs(float(parts[0]))] + [float(p) for p in parts[1:3]]
    except ValueError:
        return None
    v = vals[0] + (vals[1] if len(vals) > 1 else 0.0) / 60.0 + (vals[2] if len(vals) > 2 else 0.0) / 3600.0
    return sign * v * (15.0 if hours else 1.0)


def apass_name(ra: float, dec: float) -> str:
    h = (ra % 360.0) / 15.0
    hh = int(h)
    mm = int((h - hh) * 60)
    ss = ((h - hh) * 60 - mm) * 60
    if ss >= 59.95:
        ss, mm = 0.0, mm + 1
        if mm == 60:
            mm, hh = 0, (hh + 1) % 24
    sign = "-" if dec < 0 else "+"
    d = abs(dec)
    dd = int(d)
    dm = int((d - dd) * 60)
    ds = int(round(((d - dd) * 60 - dm) * 60))
    if ds == 60:
        ds, dm = 0, dm + 1
        if dm == 60:
            dm, dd = 0, dd + 1
    return f"APASS J{hh:02d}{mm:02d}{ss:04.1f}{sign}{dd:02d}{dm:02d}{ds:02d}"


def _unit_vectors(ra, dec):
    ra, dec = np.radians(np.asarray(ra, dtype=float)), np.radians(np.asarray(dec, dtype=float))
    return np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


# ---- the field -----------------------------------------------------------------------------------------------------

def apass_stars(rows: list) -> list[dict]:
    out = []
    for r in rows:
        ra, dec = parse_angle(r.get("RAJ2000"), hours=" " in str(r.get("RAJ2000", "")).strip()), \
            parse_angle(r.get("DEJ2000"))
        if ra is None or dec is None:
            continue
        mags = {b: _num(r.get(c)) for c, b in APASS_BANDS.items()}
        out.append({"ra": ra, "dec": dec, "mags": {b: m for b, m in mags.items() if m is not None},
                    "name": apass_name(ra, dec)})
    return out


def gaia_stars(rows: list) -> list[dict]:
    out = []
    for r in rows:
        ra, dec, g = _num(r.get("RA_ICRS")), _num(r.get("DE_ICRS")), _num(r.get("Gmag"))
        if ra is None or dec is None or g is None:
            continue
        c = _num(r.get("BP-RP"))
        out.append({"ra": ra, "dec": dec, "G": g, "mags": gaia_bands(g, c),
                    "name": f"Gaia DR3 {str(r.get('Source') or '').strip() or apass_name(ra, dec)[6:]}"})
    return out


def merge_field(apass: list, gaia: list, cat_band: str) -> dict:
    """{"stars": [{name, ra, dec, mag, catalog, bright}] with a magnitude in cat_band, brightest first,
    "all": [{ra, dec, bright}] every star once}. APASS wins for its bands; Gaia fills in."""
    band = cat_band.upper()
    pair = {}                                           # gaia index → apass index
    if apass and gaia:
        from scipy.spatial import cKDTree
        tree = cKDTree(_unit_vectors([a["ra"] for a in apass], [a["dec"] for a in apass]))
        chord = 2.0 * math.sin(math.radians(MATCH_ARCSEC / 3600.0) / 2.0)
        d, j = tree.query(_unit_vectors([g["ra"] for g in gaia], [g["dec"] for g in gaia]), k=1,
                          distance_upper_bound=chord)
        for i, (dd, jj) in enumerate(zip(d, j)):
            if math.isfinite(dd) and jj < len(apass):
                pair[i] = int(jj)
    used_apass = set(pair.values())
    stars, allpos = [], []

    def bright_of(m: dict, g=None):
        for v in (g, m.get("V"), m.get("SG"), m.get("SR"), m.get(band)):
            if v is not None:
                return v
        return min(m.values()) if m else None

    for i, g in enumerate(gaia):
        a = apass[pair[i]] if i in pair else None
        b = bright_of(g["mags"], g["G"])
        allpos.append({"ra": g["ra"], "dec": g["dec"], "bright": b})
        if a is not None and band in a["mags"]:
            stars.append({"name": a["name"], "ra": a["ra"], "dec": a["dec"], "mag": a["mags"][band],
                          "catalog": "APASS DR9", "bright": b})
        elif band in g["mags"]:
            stars.append({"name": g["name"], "ra": g["ra"], "dec": g["dec"], "mag": g["mags"][band],
                          "catalog": "Gaia DR3", "bright": b})
    for k, a in enumerate(apass):
        if k in used_apass:
            continue
        b = bright_of(a["mags"])
        allpos.append({"ra": a["ra"], "dec": a["dec"], "bright": b})
        if band in a["mags"]:
            stars.append({"name": a["name"], "ra": a["ra"], "dec": a["dec"], "mag": a["mags"][band],
                          "catalog": "APASS DR9", "bright": b})
    stars.sort(key=lambda s: s["mag"])
    for s in stars:
        for k in ("ra", "dec", "mag", "bright"):
            if s[k] is not None:
                s[k] = round(float(s[k]), 7 if k in ("ra", "dec") else 4)
    return {"stars": stars, "all": allpos}


def fetch_field(ra_deg: float, dec_deg: float, radius_deg: float, cat_band: str, timeout: float = 40,
                fetcher=None, gaia_limit: float = GAIA_LIMIT) -> dict:
    """The catalogued stars of a field: {"stars": [{name, ra, dec, mag, catalog, bright}] (only stars with a
    magnitude in cat_band), "all": [{ra, dec, bright}] (every star, for crowding), "service": where the answer came
    from, "incomplete": why it may be incomplete, or ""}. CatalogError when no catalog answers.
    fetcher(url, timeout) → bytes replaces the network (tests)."""
    band = str(cat_band or "V").upper()
    apass_rows, gaia_rows, services, problems, inc = [], [], [], [], []
    if band in APASS_BANDS.values():
        try:
            apass_rows, why, where = vizier(APASS, ra_deg, dec_deg, radius_deg, APASS_COLS, out_max=5000,
                                            sort="Vmag", timeout=timeout, fetcher=fetcher)
            services.append(f"APASS DR9 ({where})")
            if why:
                inc.append(f"APASS: {why}")
        except CatalogError as exc:
            problems.append(f"APASS: {exc}")
    try:
        gaia_rows, why, where = vizier(GAIA, ra_deg, dec_deg, radius_deg, GAIA_COLS,
                                       constraints=[("Gmag", f"<{gaia_limit:g}")], out_max=10000, sort="Gmag",
                                       timeout=timeout, fetcher=fetcher)
        services.append(f"Gaia DR3 ({where})")
        if why:
            inc.append(f"Gaia: {why}")
    except CatalogError as exc:
        problems.append(f"Gaia: {exc}")
    if not services:
        raise CatalogError("the star catalog (VizieR) could not be reached; check the internet connection and try "
                           "again (" + "; ".join(problems) + ")")
    out = merge_field(apass_stars(apass_rows), gaia_stars(gaia_rows), band)
    out["service"] = " + ".join(services)
    out["incomplete"] = "; ".join(inc + problems)
    out["band"] = band
    return out


def fetch_variables(ra_deg: float, dec_deg: float, radius_deg: float, timeout: float = 30, fetcher=None,
                    notes: list | None = None) -> list[dict]:
    """Known variable stars (VSX) in the field: [{name, ra, dec}]. Any failure gives [] (a note is added to notes
    when given): the run goes on without it."""
    try:
        rows, why, _where = vizier(VSX, ra_deg, dec_deg, radius_deg, ("Name", "RAJ2000", "DEJ2000"), out_max=5000,
                                   timeout=timeout, fetcher=fetcher)
    except CatalogError as exc:
        if notes is not None:
            notes.append(f"The variable-star list (VSX) could not be read, so known variables are not left out "
                         f"({exc}).")
        return []
    out = []
    for r in rows:
        ra = parse_angle(r.get("RAJ2000"), hours=" " in str(r.get("RAJ2000", "")).strip())
        dec = parse_angle(r.get("DEJ2000"))
        if ra is not None and dec is not None:
            out.append({"name": str(r.get("Name") or "").strip(), "ra": ra, "dec": dec})
    if why and notes is not None:
        notes.append(f"The variable-star list (VSX) may be incomplete ({why}).")
    return out
