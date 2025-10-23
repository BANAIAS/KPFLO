# kpflo_core/insee.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import pandas as pd
import requests

SDMX_JSON = "application/vnd.sdmx.data+json;version=1.0"

@dataclass
class InseeClient:
    """Client minimal pour l’API INSEE BDM V1 (SDMX)."""
    token: str
    base_url: str = "https://api.insee.fr/series/BDM/V1"
    timeout: int = 20

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": SDMX_JSON,
        }

    def fetch_series(self, idbank: str, firstN: int = 60) -> pd.DataFrame:
        """
        Récupère une série par IDBANK, renvoie DataFrame [date, value] (float).
        firstN: nbre d’observations les + récentes (60 ~ 5 ans mensuel).
        """
        url = f"{self.base_url}/data/SERIES_BDM/{idbank}?firstNObservations={firstN}"
        r = requests.get(url, headers=self._headers, timeout=self.timeout)
        r.raise_for_status()
        js = r.json()

        # --- parse SDMX JSON (structure: dataSets -> series -> obs) ---
        # on prend la 1ère série (unique pour un idbank)
        try:
            series_key = next(iter(js["data"]["dataSets"][0]["series"].keys()))
            obs = js["data"]["dataSets"][0]["series"][series_key]["observations"]
            # dimension temporelle dans structure: indices -> périodes
            time_periods = js["data"]["structure"]["dimensions"]["observation"][0]["values"]
        except Exception as e:
            raise RuntimeError(f"Réponse SDMX inattendue pour {idbank}: {e}")

        rows = []
        for idx_str, arr in obs.items():
            idx = int(idx_str)
            period = time_periods[idx]["id"]  # ex "2024-12"
            val = arr[0]
            rows.append((period, float(val) if val is not None else None))

        df = pd.DataFrame(rows, columns=["date", "value"])
        # périodes mensuelles: "YYYY-MM"
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m", errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        return df

# ---------- utilitaires inflation ----------

def yoy(series: pd.Series) -> pd.Series:
    """Variation annuelle en % sur une série mensuelle."""
    return (series / series.shift(12) - 1.0) * 100.0

def latest_yoy(df: pd.DataFrame) -> float | None:
    """Renvoie le YoY de la dernière observation si possible."""
    if df.empty: 
        return None
    s = df.set_index("date")["value"].astype(float)
    y = yoy(s).dropna()
    return float(y.iloc[-1]) if not y.empty else None

def monthly_sum(df: pd.DataFrame, cat_col: str = "categorie") -> pd.DataFrame:
    """Agrège un DF transactions mensuellement (signe des OUT attendu négatif)."""
    tmp = df.copy()
    tmp["date"] = pd.to_datetime(tmp["date"])
    tmp["month"] = tmp["date"].dt.to_period("M").astype(str)
    # on veut une série positive des dépenses par catégorie
    tmp = tmp[tmp["type"] == "OUT"].copy()
    tmp["amount_pos"] = tmp["montant"].abs()
    g = tmp.groupby(["month", cat_col])["amount_pos"].sum().reset_index()
    g["date"] = pd.to_datetime(g["month"], format="%Y-%m")
    return g[["date", cat_col, "amount_pos"]].sort_values(["date", cat_col])

def user_category_yoy(df: pd.DataFrame, category: str) -> float | None:
    """YoY dépenses utilisateur pour 1 catégorie (variation annuelle %)."""
    g = monthly_sum(df)
    g = g[g["categorie"] == category]
    if g.empty:
        return None
    s = g.set_index("date")["amount_pos"].astype(float)
    y = yoy(s).dropna()
    return float(y.iloc[-1]) if not y.empty else None

def compare_user_vs_cpi(
    df_user: pd.DataFrame,
    client: InseeClient,
    cat_to_idbank: dict[str, str],
    threshold_points: float = 2.0,
) -> list[str]:
    """
    Compare YoY utilisateur vs YoY INSEE pour chaque catégorie mappée.
    Produit une liste de messages d’alerte quand l’écart dépasse 'threshold_points'
    (en points de pourcentage).
    """
    alerts = []
    for cat, idb in cat_to_idbank.items():
        if not idb:
            continue
        try:
            df_cpi = client.fetch_series(idb, firstN=60)
            yoy_cpi = latest_yoy(df_cpi)
            yoy_user = user_category_yoy(df_user, cat)
        except Exception as e:
            alerts.append(f"⚠️ INSEE indisponible pour {cat} ({e})")
            continue

        if yoy_cpi is None or yoy_user is None:
            # pas assez de données (côté INSEE ou utilisateur)
            continue

        delta = yoy_user - yoy_cpi  # points de pourcentage
        if delta > threshold_points:
            alerts.append(
                f"{cat} : +{yoy_user:.1f}% chez vous vs +{yoy_cpi:.1f}% (INSEE) — écart **+{delta:.1f} pts**."
            )
    return alerts

