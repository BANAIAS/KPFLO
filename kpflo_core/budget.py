import pandas as pd
from .categories import FIFTY, THIRTY, TWENTY

from datetime import date, datetime
import numpy as np



def compute_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "revenus": 0.0,
            "depenses": 0.0,
            "solde": 0.0,
            "by_cat": pd.DataFrame(columns=["categorie","montant"]),
            "mois": pd.DataFrame(columns=["mois","montant"]),
            "split": {"50":0.0,"30":0.0,"20":0.0},
        }
    revenus = df.loc[df["type"]=="IN","montant"].sum()
    depenses = df.loc[df["type"] == "OUT", "montant"].abs().sum()  # valeurs négatives -> dépense positive
    solde = revenus - depenses

    by = df[df["type"] == "OUT"].copy()
    by["abs"] = by["montant"].abs()
    by_cat = by.groupby("categorie", dropna=False)["abs"].sum().sort_values(ascending=False).reset_index()
    by_cat = by_cat.rename(columns={"abs": "montant"})

    tmp = df.copy()
    tmp["mois"] = tmp["date"].dt.to_period("M").astype(str)
    mois = tmp.groupby("mois")["montant"].sum().reset_index()

    dep = df[df["type"]=="OUT"].copy()
    dep["abs"] = dep["montant"].abs()
    total_dep = dep["abs"].sum() or 1.0
    f = dep[dep["categorie"].isin(FIFTY)]["abs"].sum()
    t = dep[dep["categorie"].isin(THIRTY)]["abs"].sum()
    w = dep[dep["categorie"].isin(TWENTY)]["abs"].sum()
    split = {
        "50": round(100*f/total_dep,1),
        "30": round(100*t/total_dep,1),
        "20": round(100*w/total_dep,1),
    }
    return {"revenus":revenus, "depenses":depenses, "solde":solde, "by_cat":by_cat, "mois":mois, "split":split}

# --- 🎯 Ajouts dans kpflo_core/budget.py ---



def _month_key(dt: pd.Timestamp | date) -> str:
    return pd.Timestamp(dt).strftime("%Y-%m")

def _current_month_slice(df: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    today = today or date.today()
    mk = _month_key(today)
    tmp = df.copy()
    tmp["mk"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m")
    return tmp[tmp["mk"] == mk]

def compute_month_basics(df: pd.DataFrame, today: date | None = None) -> dict:
    """Totaux revenus/dépenses/solde pour le mois courant."""
    month_df = _current_month_slice(df, today)
    if month_df.empty:
        return {"revenus":0.0, "depenses":0.0, "solde":0.0}
    revenus  = float(month_df.loc[month_df["type"]=="IN", "montant"].sum())
    depenses = float(-month_df.loc[month_df["type"]=="OUT", "montant"].sum())
    return {"revenus":revenus, "depenses":depenses, "solde":revenus-depenses}

def predict_end_of_month(df: pd.DataFrame, today: date | None = None) -> dict:
    """
    Projection ultra-robuste (sans ML) :
    - revenus du mois = somme actuelle (hypothèse stable) ;
    - dépenses du mois = (dépenses cumulées / jour_du_mois) * nb_jours_mois.
    """
    if df.empty:
        return {"revenus":0.0,"depenses":0.0,"solde":0.0}
    today = today or date.today()
    month_df = _current_month_slice(df, today)
    # revenus
    rev = float(month_df.loc[month_df["type"]=="IN","montant"].sum())
    # dépenses
    dep_spent = float(-month_df.loc[month_df["type"]=="OUT","montant"].sum())
    d = pd.Timestamp(today)
    days_passed = d.day
    days_in_month = (d + pd.offsets.MonthEnd(0)).day
    dep_forecast = dep_spent if days_passed<=0 else dep_spent / days_passed * days_in_month
    solde = rev - dep_forecast
    return {"revenus": round(rev,2), "depenses": round(dep_forecast,2), "solde": round(solde,2)}

def predict_end_of_year(
    df: pd.DataFrame,
    today: date | None = None,
    eom_override: dict | None = None,   # optionnel: {"revenus":..., "depenses":..., "solde":...}
    use_last_n: int = 3
) -> dict:
    """
    Projection fin d'année COHÉRENTE avec la projection de fin de mois.
    - YTD exclut le mois courant (évite le double comptage).
    - Le mois courant prend la projection de fin de mois (override si fourni).
    - Les mois restants après le mois courant utilisent la moyenne des mois complétés (last N).
    Retourne revenus/depenses/solde projetés pour l'année.
    """
    if df.empty:
        return {"revenus": 0.0, "depenses": 0.0, "solde": 0.0}

    import pandas as pd
    today = today or date.today()
    cur_ts = pd.Timestamp(today)
    cur_mk = cur_ts.strftime("%Y-%m")
    year_str = str(cur_ts.year)

    tmp = df.copy()
    tmp["date"] = pd.to_datetime(tmp["date"], errors="coerce")
    tmp["mk"] = tmp["date"].dt.strftime("%Y-%m")
    tmp["year"] = tmp["date"].dt.year.astype(str)

    # Données de l'année en cours
    ydf = tmp[tmp["year"] == year_str]
    if ydf.empty:
        return {"revenus": 0.0, "depenses": 0.0, "solde": 0.0}

    # Agrégats par mois (revenus positifs, dépenses positives) — robustes si IN/OUT manquent
    g = ydf.groupby(["mk", "type"])["montant"].sum().unstack(fill_value=0.0)
    g = g.reset_index()  # mk devient une colonne

    # Garantir des Series même si la colonne manque
    rev_series = g["IN"]  if "IN"  in g.columns else pd.Series(0.0, index=g.index)
    out_series = g["OUT"] if "OUT" in g.columns else pd.Series(0.0, index=g.index)

    # Revenus = IN ; Dépenses = -OUT (positif)
    g["revenus"]  = rev_series
    g["depenses"] = (-out_series).clip(lower=0.0)
    g = g[["mk", "revenus", "depenses"]].sort_values("mk")

    # Séparation mois complétés vs mois courant
    completed = g[g["mk"] < cur_mk].copy()
    # current   = g[g["mk"] == cur_mk].copy()  # non utilisé ici mais gardable si besoin

    # Moyennes sur les mois complétés (prendre les 'use_last_n' derniers si possible)
    def _avg_last_n(s: pd.Series, n: int) -> float:
        if s.empty:
            return 0.0
        return float(s.tail(n).mean() if len(s) >= n else s.mean())

    avg_rev_completed = _avg_last_n(completed["revenus"], use_last_n)
    avg_dep_completed = _avg_last_n(completed["depenses"], use_last_n)

    # YTD EXCLUANT le mois courant
    ytd_rev_excl_cur = float(completed["revenus"].sum())
    ytd_dep_excl_cur = float(completed["depenses"].sum())

    # Projection fin de mois (courant)
    if eom_override is not None:
        eom_rev = float(eom_override.get("revenus", 0.0))
        eom_dep = float(eom_override.get("depenses", 0.0))
    else:
        # Par défaut : cohérent avec ta fonction actuelle `predict_end_of_month`
        month_forecast = predict_end_of_month(df, today)  # réutilise ta logique actuelle
        eom_rev = float(month_forecast.get("revenus", 0.0))
        eom_dep = float(month_forecast.get("depenses", 0.0))

    # Mois restants APRÈS le mois courant
    months_left_after_current = 12 - cur_ts.month

    # Projection fin d'année
    year_rev = ytd_rev_excl_cur + eom_rev + months_left_after_current * avg_rev_completed
    year_dep = ytd_dep_excl_cur + eom_dep + months_left_after_current * avg_dep_completed
    return {
        "revenus": round(year_rev, 2),
        "depenses": round(year_dep, 2),
        "solde": round(year_rev - year_dep, 2),
    }



def savings_projection(monthly: float, annual_rate_pct: float, years: int, start_balance: float = 0.0) -> float:
    """Valeur future avec capitalisation mensuelle."""
    r = max(annual_rate_pct, 0.0)/100.0
    n = max(int(years*12), 0)
    if r == 0 or n == 0:
        return float(start_balance + monthly*n)
    rm = r/12.0
    growth = (1+rm)**n
    return float(start_balance*growth + monthly*(growth-1)/rm)

def build_coach_text(month_summary: dict, month_forecast: dict, year_forecast: dict, split: dict, top_alerts: list[str] | None = None) -> str:
    """
    Produit un texte court et motivant.
    - month_summary : dict revenus/depenses/solde (réalisé)
    - month_forecast : dict revenus/depenses/solde (prévu fin de mois)
    - year_forecast : dict revenus/depenses/solde (projection fin d’année)
    - split : {"50":x,"30":y,"20":z}
    - top_alerts : liste de messages d’alerte (dépassements, inflation)
    """
    rev = month_summary.get("revenus",0.0); dep = month_summary.get("depenses",0.0); sol = month_summary.get("solde",0.0)
    frev = month_forecast.get("revenus",0.0); fdep = month_forecast.get("depenses",0.0); fsol = month_forecast.get("solde",0.0)
    ysol = year_forecast.get("solde",0.0)

    lines = []
    lines.append(f"Ce mois-ci, tu as enregistré **{rev:,.0f} €** de revenus et **{dep:,.0f} €** de dépenses (solde **{sol:,.0f} €**).".replace(",", " "))
    lines.append(f"À ce rythme, ton **solde de fin de mois** sera **{fsol:,.0f} €** ({frev:,.0f} € de revenus / {fdep:,.0f} € de dépenses).".replace(",", " "))
    lines.append(f"Projection **fin d’année** : **{ysol:,.0f} €** de solde cumulé si tu maintiens la tendance.".replace(",", " "))

    lines.append(f"Répartition 50/30/20 → **Besoins {split.get('50',0)} %** • **Envies {split.get('30',0)} %** • **Épargne  {split.get('20',0)} %**.")

    if top_alerts:
        lines.append("")
        lines.append("**Alertes prioritaires :**")
        for a in top_alerts[:3]:
            lines.append(f"- {a}")

    lines.append("")
    lines.append("💡 *Conseil : verrouille une épargne automatique (virement permanent) juste après la paie pour sécuriser la part « 20 % ».*")
    return "\n".join(lines)

