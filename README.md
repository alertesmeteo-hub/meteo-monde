# Météo Monde

Prévisions 15 jours (matin / après-midi / soir / nuit) par pays, pour
alertes-meteo.com/previsions-meteo-monde/.

- **Source des prévisions** : API [Open-Meteo](https://open-meteo.com/) (gratuite, mondiale, sans clé).
- **Pipeline** : `scripts/update_meteo_monde.py`, exécuté par
  `.github/workflows/update-meteo-monde.yml` une fois par jour vers midi
  (11h UTC), publie les résultats sur la branche `data`.
- **Plugin WordPress** : `wordpress/meteo-monde/` — shortcodes
  `[meteo_pays]`, `[meteo_pays_mois]`, `[meteo_pays_villes]`. Carte via
  Leaflet + tuiles OpenStreetMap, verrouillée (pas de zoom/déplacement),
  cadrée sur la bounding box du pays.

## Ajouter un pays

Créer `config/countries/<slug>.json` :

```json
{
  "nom": "Nom du pays",
  "slug": "slug-du-pays",
  "bbox": [sud, ouest, nord, est],
  "villes": [{ "nom": "Ville", "slug": "ville", "lat": 0.0, "lon": 0.0 }],
  "meilleurs_mois": [{ "mois": "Jan.", "t_moy": 0, "pluie": 0, "t_mer": 0, "avis": 0 }]
}
```

Le pipeline se charge du reste (récupération Open-Meteo, publication).

## Statut

Version pilote : Irlande uniquement. Objectif : ~60 pays, à généraliser
une fois le rendu validé sur alertes-meteo.com.
