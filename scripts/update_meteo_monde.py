#!/usr/bin/env python3
"""Met à jour les prévisions 15 jours (matin/après-midi/soir/nuit) pour
chaque pays configuré dans config/countries/*.json, via l'API gratuite
Open-Meteo (aucune clé requise, couverture mondiale).

Sortie : build/data/countries/<slug>.json + build/data/index.json
Ces fichiers sont ensuite publiés tels quels sur la branche `data` du
dépôt par le workflow GitHub Actions.
"""

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

PERIODS = {
    "matin": 9,
    "apresmidi": 15,
    "soir": 19,
    "nuit": 1,  # nuit = 01h, jour suivant conceptuellement mais on
    # reste sur le même index de jour pour simplifier la navigation
    # utilisateur (J = date affichée, la nuit qui la suit).
}

NB_JOURS = 15


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_forecast_url(villes: list) -> str:
    lats = ",".join(str(v["lat"]) for v in villes)
    lons = ",".join(str(v["lon"]) for v in villes)
    return (
        f"{OPEN_METEO_URL}?latitude={lats}&longitude={lons}"
        f"&hourly=temperature_2m,weathercode&forecast_days=16&timezone=auto"
    )


def hour_index(times: list, day_offset: int, hour: int, night: bool) -> int:
    target = datetime.now() + timedelta(days=day_offset + (1 if night else 0))
    prefix = target.strftime("%Y-%m-%dT") + f"{hour:02d}:00"
    try:
        return times.index(prefix)
    except ValueError:
        return -1


def process_country(config_path: Path) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    villes = cfg["villes"]

    data = fetch_json(build_forecast_url(villes))
    per_city = data if isinstance(data, list) else [data]

    forecast = {}
    for day in range(NB_JOURS):
        forecast[str(day)] = {}
        for period, hour in PERIODS.items():
            entry = {}
            for ville, city_data in zip(villes, per_city):
                hourly = city_data.get("hourly", {})
                times = hourly.get("time", [])
                idx = hour_index(times, day, hour, period == "nuit")
                if idx == -1:
                    continue
                entry[ville["slug"]] = {
                    "t": round(hourly["temperature_2m"][idx]),
                    "code": hourly["weathercode"][idx],
                }
            forecast[str(day)][period] = entry

    return {
        "nom": cfg["nom"],
        "slug": cfg["slug"],
        "bbox": cfg["bbox"],
        "villes": villes,
        "meilleurs_mois": cfg.get("meilleurs_mois", []),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forecast": forecast,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default="config/countries")
    parser.add_argument("--output-dir", default="build/data")
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    output_dir = Path(args.output_dir)
    countries_out = output_dir / "countries"
    countries_out.mkdir(parents=True, exist_ok=True)

    slugs = []
    for config_path in sorted(config_dir.glob("*.json")):
        print(f"Traitement : {config_path.stem}")
        try:
            country_data = process_country(config_path)
        except Exception as exc:  # noqa: BLE001
            print(f"  Échec pour {config_path.stem} : {exc}", file=sys.stderr)
            continue
        (countries_out / f"{config_path.stem}.json").write_text(
            json.dumps(country_data, ensure_ascii=False), encoding="utf-8"
        )
        slugs.append(config_path.stem)

    if not slugs:
        print("Aucun pays produit, publication annulée.", file=sys.stderr)
        return 1

    index = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "countries": slugs,
    }
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"OK : {len(slugs)} pays publiés.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
