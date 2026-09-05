# Météo Monde

Prévisions 15 jours (matin / après-midi / soir / nuit) par pays, pour
alertes-meteo.com/previsions-meteo-monde/.

- **Source des prévisions** : modèle déterministe **CEP/ECMWF IFS** (données
  ouvertes ECMWF, grille mondiale 0,25°) via `scripts/update_meteo_monde_cep.py`
  — le même modèle que le module `cep` (France), mais interrogé directement
  pour n'importe quel point du globe (la grille IFS Open Data est nativement
  mondiale, aucun découpage régional n'est nécessaire pour un point
  d'extraction). `scripts/update_meteo_monde.py` (API Open-Meteo) reste
  disponible en secours si besoin.
- **Approximations du pipeline CEP** : seuls 2t/tcc/tp/sf sont téléchargés
  (pas les diagnostics orage/CAPE du module France) ; le fuseau horaire de
  chaque ville est approximé depuis sa longitude (round(lon/15), sans base
  de fuseaux) ; pour chaque créneau matin/après-midi/soir/nuit, on retient
  l'échéance IFS dont l'heure de validité est la plus proche.
- **Pipeline** : exécuté par `.github/workflows/update-meteo-monde.yml` une
  fois par jour vers midi (11h UTC), publie les résultats sur la branche
  `data`. Le format de sortie est identique à la version Open-Meteo — le
  plugin WordPress n'a rien à changer.
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
