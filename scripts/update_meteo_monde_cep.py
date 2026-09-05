#!/usr/bin/env python3
"""CEP/ECMWF mondial pour meteo-monde.

Remplace la source Open-Meteo par le modèle déterministe CEP/IFS
directement (données ouvertes ECMWF, résolution 0,25°, grille MONDIALE
1440x721 — le même modèle que le module `cep` (France), mais interrogé ici
pour n'importe quel point du globe sans découpage régional : la grille
IFS Open Data est nativement mondiale, aucune carte cartopy n'est requise
pour un simple point d'extraction ville par ville.

Approximations assumées pour rester réalisable en un run quotidien léger :
- Seuls 2t (température 2 m), tcc (nébulosité totale), tp (précipitations
  cumulées), sf (chutes de neige cumulées) et 10fg (rafales à 10 m) sont
  téléchargés — pas les diagnostics orage/CAPE détaillés du module France.
- Le décalage horaire local de chaque ville est approximé depuis sa
  longitude (round(lon / 15)), sans base de données de fuseaux — suffisant
  pour positionner matin/après-midi/soir/nuit à l'échéance IFS la plus
  proche (le pas GRIB est de toute façon 3 h puis 6 h, donc plus fin que
  cette approximation n'a de sens de l'être).
- Pour chaque créneau demandé, on prend l'échéance IFS dont l'heure de
  validité est la plus proche de l'heure locale visée.

Sortie : build/data/countries/<slug>.json (même format que la version
Open-Meteo — le plugin WordPress n'a donc rien à changer).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from ecmwf.opendata import Client
from eccodes import (
    codes_get,
    codes_get_double_elements,
    codes_grib_new_from_file,
    codes_release,
)

LOGGER = logging.getLogger("meteo-monde.cep")

# Grille mondiale régulière IFS Open Data 0,25° (identique au module cep).
CEP_NI = 1440
CEP_NJ = 721
CEP_LAT_FIRST = 90.0
CEP_LON_FIRST = -180.0
CEP_STEP = 0.25

PERIOD_HOURS = {"matin": 9, "apresmidi": 15, "soir": 19, "nuit": 1}
NB_JOURS = 15
IFS_PARAMETERS = ["2t", "tcc", "tp", "sf", "10fg"]
SHORT_NAME_FIELD = {"2t": "t2m", "tcc": "tcc", "tp": "tp", "sf": "sf", "10fg": "gust", "fg10": "gust"}


def grid_index(latitude: float, longitude: float) -> int:
    row = int(round((CEP_LAT_FIRST - latitude) / CEP_STEP))
    column = int(round(((longitude - CEP_LON_FIRST) % 360.0) / CEP_STEP)) % CEP_NI
    row = max(0, min(CEP_NJ - 1, row))
    column = max(0, min(CEP_NI - 1, column))
    return row * CEP_NI + column


def forecast_steps(forecast_hours: int) -> list[int]:
    """Échéances déterministes IFS : 3 h jusqu'à 144 h, puis 6 h."""
    first = list(range(0, min(forecast_hours, 144) + 1, 3))
    if forecast_hours <= 144:
        return first
    return first + list(range(150, forecast_hours + 1, 6))


def safe_get(gid: int, key: str, default: Any = None) -> Any:
    try:
        return codes_get(gid, key)
    except Exception:  # noqa: BLE001
        return default


def mask_missing(values: np.ndarray, missing_value: Any) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64)
    invalid = ~np.isfinite(result) | (np.abs(result) > 1.0e20)
    try:
        missing = float(missing_value)
    except (TypeError, ValueError):
        missing = float("nan")
    if np.isfinite(missing):
        invalid |= np.isclose(result, missing, rtol=0.0, atol=1.0e-9)
    result[invalid] = np.nan
    return result


def utc_offset_hours(longitude: float) -> int:
    return int(round(longitude / 15.0))


def condition_code(cloud_pct: float, precip_mm: float, snow_mm: float) -> int:
    """Code de condition compatible avec l'échelle WMO déjà utilisée côté
    JavaScript (wmoEmoji), pour ne rien changer au plugin WordPress."""
    if np.isfinite(snow_mm) and snow_mm >= 0.2:
        return 75 if snow_mm >= 2.0 else 71
    if np.isfinite(precip_mm) and precip_mm >= 0.2:
        if precip_mm >= 4.0:
            return 65
        if precip_mm >= 1.0:
            return 63
        return 61
    if not np.isfinite(cloud_pct):
        return 3
    if cloud_pct < 15:
        return 0
    if cloud_pct < 50:
        return 1
    if cloud_pct < 80:
        return 2
    return 3


def uv_index_estimate(latitude: float, local_date, hour: int, cloud_pct: float) -> int:
    """Indice UV approximatif (pas de champ UV dans les données ouvertes
    ECMWF) : élévation solaire depuis la date/heure locale et la latitude,
    puis atténuation par la nébulosité. Ordre de grandeur seulement — pas
    une prévision UV certifiée."""
    day_of_year = local_date.timetuple().tm_yday
    declination = math.radians(23.44) * math.sin(math.radians(360.0 / 365.0 * (day_of_year - 81)))
    hour_angle = math.radians(15.0 * (hour - 12))
    lat_rad = math.radians(latitude)
    sin_elevation = (
        math.sin(lat_rad) * math.sin(declination)
        + math.cos(lat_rad) * math.cos(declination) * math.cos(hour_angle)
    )
    if sin_elevation <= 0:
        return 0
    clear_sky_uv = 12.0 * (sin_elevation ** 1.2)
    cloud_fraction = cloud_pct / 100.0 if np.isfinite(cloud_pct) else 0.0
    attenuation = 1.0 - 0.75 * cloud_fraction
    uv = clear_sky_uv * max(attenuation, 0.15)
    return max(0, int(round(uv)))


def load_points(config_dir: Path) -> list[dict]:
    points = []
    for path in sorted(config_dir.glob("*.json")):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        for ville in cfg["villes"]:
            points.append({
                "country_slug": cfg["slug"],
                "city_slug": ville["slug"],
                "lat": float(ville["lat"]),
                "lon": float(ville["lon"]),
            })
    return points


def download_series(points: list[dict], forecast_hours: int) -> tuple[dict, dict, datetime]:
    """Télécharge les échéances IFS et renvoie les séries par champ et par
    point (indexées par échéance), ainsi que l'heure de validité par
    échéance et l'heure du run."""
    unique_indexes = sorted({grid_index(p["lat"], p["lon"]) for p in points})
    position_of_index = {idx: pos for pos, idx in enumerate(unique_indexes)}
    for point in points:
        point["position"] = position_of_index[grid_index(point["lat"], point["lon"])]

    client = Client(source="ecmwf", model="ifs", resol="0p25", infer_stream_keyword=False)
    run_hint = client.latest(stream="oper", type="fc", step=forecast_hours, param="2t")
    if run_hint.tzinfo is None:
        run_hint = run_hint.replace(tzinfo=timezone.utc)
    LOGGER.info("Run CEP/IFS mondial sélectionné : %s", run_hint.isoformat())

    steps = forecast_steps(forecast_hours)
    series: dict[str, dict[int, np.ndarray]] = {name: {} for name in SHORT_NAME_FIELD.values()}
    valid_times: dict[int, datetime] = {}

    with tempfile.TemporaryDirectory(prefix="meteo-monde-cep-") as tmp:
        tmp_path = Path(tmp)
        for step_number, lead in enumerate(steps):
            destination = tmp_path / f"ifs-{lead:03d}h.grib2"
            LOGGER.info("Téléchargement IFS +%03d h (%d/%d)", lead, step_number + 1, len(steps))
            client.retrieve(
                date=run_hint.strftime("%Y%m%d"),
                time=run_hint.hour,
                stream="oper",
                type="fc",
                step=lead,
                param=IFS_PARAMETERS,
                target=str(destination),
            )
            valid_times[lead] = run_hint + timedelta(hours=lead)
            with destination.open("rb") as handle:
                while True:
                    gid = codes_grib_new_from_file(handle)
                    if gid is None:
                        break
                    try:
                        short_name = str(safe_get(gid, "shortName", ""))
                        field = SHORT_NAME_FIELD.get(short_name)
                        if field is None:
                            continue
                        raw = codes_get_double_elements(gid, "values", unique_indexes)
                        values = mask_missing(raw, safe_get(gid, "missingValue"))
                        if field in ("tp", "sf"):
                            values = values * 1000.0  # m -> mm
                        elif field == "tcc":
                            values = values * 100.0  # fraction -> %
                        elif field == "t2m":
                            values = values - 273.15  # K -> °C
                        elif field == "gust":
                            values = values * 3.6  # m/s -> km/h
                        series[field][lead] = values
                    finally:
                        codes_release(gid)
            destination.unlink(missing_ok=True)

    return series, valid_times, run_hint


def nearest_lead(valid_times: dict[int, datetime], target: datetime) -> int:
    return min(valid_times, key=lambda lead: abs((valid_times[lead] - target).total_seconds()))


def build_forecast_for_point(
    point: dict,
    series: dict[str, dict[int, np.ndarray]],
    valid_times: dict[int, datetime],
) -> dict[str, dict[str, dict]]:
    offset = utc_offset_hours(point["lon"])
    now_utc = datetime.now(timezone.utc)
    local_now_date = (now_utc + timedelta(hours=offset)).date()
    position = point["position"]

    forecast: dict[str, dict[str, dict]] = {}
    for day in range(NB_JOURS):
        forecast[str(day)] = {}
        for period, hour in PERIOD_HOURS.items():
            night_shift = 1 if period == "nuit" else 0
            local_date = local_now_date + timedelta(days=day + night_shift)
            local_wall = datetime(local_date.year, local_date.month, local_date.day, hour)
            target_utc = local_wall - timedelta(hours=offset)
            target_utc = target_utc.replace(tzinfo=timezone.utc)

            lead = nearest_lead(valid_times, target_utc)
            temp = series["t2m"].get(lead)
            cloud = series["tcc"].get(lead)
            precip = series["tp"].get(lead)
            snow = series["sf"].get(lead)
            gust = series["gust"].get(lead)
            if temp is None:
                continue

            t_value = temp[position]
            cloud_value = cloud[position] if cloud is not None else float("nan")
            precip_value = precip[position] if precip is not None else float("nan")
            snow_value = snow[position] if snow is not None else float("nan")
            gust_value = gust[position] if gust is not None else float("nan")
            uv_value = uv_index_estimate(point["lat"], local_date, hour, cloud_value)

            forecast[str(day)][period] = {
                "t": int(round(t_value)) if np.isfinite(t_value) else None,
                "code": condition_code(cloud_value, precip_value, snow_value),
                "rafales": int(round(gust_value)) if np.isfinite(gust_value) else None,
                "uv": uv_value,
            }
    return forecast


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default="config/countries")
    parser.add_argument("--output-dir", default="build/data")
    parser.add_argument("--forecast-hours", type=int, default=360)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )

    config_dir = Path(args.config_dir)
    output_dir = Path(args.output_dir)
    countries_out = output_dir / "countries"
    countries_out.mkdir(parents=True, exist_ok=True)

    points = load_points(config_dir)
    if not points:
        LOGGER.error("Aucune ville trouvée dans %s", config_dir)
        return 1
    LOGGER.info("%d villes à traiter (CEP mondial, grille 0,25°)", len(points))

    series, valid_times, run_hint = download_series(points, args.forecast_hours)

    by_country: dict[str, dict] = {}
    for point in points:
        forecast = build_forecast_for_point(point, series, valid_times)
        by_country.setdefault(point["country_slug"], {})[point["city_slug"]] = forecast

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slugs = []
    for config_path in sorted(config_dir.glob("*.json")):
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        slug = cfg["slug"]
        city_forecasts = by_country.get(slug, {})
        if not city_forecasts:
            LOGGER.warning("Aucune prévision produite pour %s, ignoré", slug)
            continue

        merged_forecast: dict[str, dict[str, dict]] = {
            str(day): {period: {} for period in PERIOD_HOURS} for day in range(NB_JOURS)
        }
        for city_slug, forecast in city_forecasts.items():
            for day, periods in forecast.items():
                for period, value in periods.items():
                    merged_forecast[day][period][city_slug] = value

        output = {
            "nom": cfg["nom"],
            "slug": slug,
            "bbox": cfg["bbox"],
            "villes": cfg["villes"],
            "meilleurs_mois": cfg.get("meilleurs_mois", []),
            "generated_at": generated_at,
            "model_run": run_hint.isoformat(),
            "source": "cep-ecmwf-ifs-0p25",
            "forecast": merged_forecast,
        }
        (countries_out / f"{slug}.json").write_text(
            json.dumps(output, ensure_ascii=False), encoding="utf-8"
        )
        slugs.append(slug)

    if not slugs:
        LOGGER.error("Aucun pays publié, arrêt.")
        return 1

    index = {"generated_at": generated_at, "countries": slugs, "source": "cep-ecmwf-ifs-0p25"}
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    LOGGER.info("OK : %d pays publiés (CEP mondial).", len(slugs))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        LOGGER.exception("Échec de la mise à jour Météo Monde (CEP)")
        raise SystemExit(1)
