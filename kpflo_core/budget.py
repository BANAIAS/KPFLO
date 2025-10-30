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
            "by_cat": pd.DataFrame(columns=["categorie", "montant"]),
            "mois": pd.DataFrame(columns=["mois", "montant"]),
            "split": {"50": 0.0, "30": 0.0, "20": 0.0},
        }
    revenus = df.loc[df["type"] == "IN", "montant"].sum()
    depenses = (
        df.loc[df["type"] == "OUT", "montant"].abs().sum()
    )  # valeurs négatives -> dépense positive
    solde = revenus - depenses

    by = df[df["type"] == "OUT"].copy()
    by["abs"] = by["montant"].abs()
    by_cat = (
        by.groupby("categorie", dropna=False)["abs"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    by_cat = by_cat.rename(columns={"abs": "montant"})

    tmp = df.copy()
    tmp["mois"] = tmp["date"].dt.to_period("M").astype(str)
    mois = tmp.groupby("mois")["montant"].sum().reset_index()

    dep = df[df["type"] == "OUT"].copy()
    dep["abs"] = dep["montant"].abs()
    total_dep = dep["abs"].sum() or 1.0
    f = dep[dep["categorie"].isin(FIFTY)]["abs"].sum()
    t = dep[dep["categorie"].isin(THIRTY)]["abs"].sum()
    w = dep[dep["categorie"].isin(TWENTY)]["abs"].sum()
    split = {
        "50": round(100 * f / total_dep, 1),
        "30": round(100 * t / total_dep, 1),
        "20": round(100 * w / total_dep, 1),
    }
    return {
        "revenus": revenus,
        "depenses": depenses,
        "solde": solde,
        "by_cat": by_cat,
        "mois": mois,
        "split": split,
    }


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
        return {"revenus": 0.0, "depenses": 0.0, "solde": 0.0}
    revenus = float(month_df.loc[month_df["type"] == "IN", "montant"].sum())
    depenses = float(-month_df.loc[month_df["type"] == "OUT", "montant"].sum())
    return {"revenus": revenus, "depenses": depenses, "solde": revenus - depenses}


def predict_end_of_month(df: pd.DataFrame, today: date | None = None) -> dict:
    """
    Projection ultra-robuste (sans ML) :
    - revenus du mois = somme actuelle (hypothèse stable) ;
    - dépenses du mois = (dépenses cumulées / jour_du_mois) * nb_jours_mois.
    """
    if df.empty:
        return {"revenus": 0.0, "depenses": 0.0, "solde": 0.0}
    today = today or date.today()
    month_df = _current_month_slice(df, today)
    # revenus
    rev = float(month_df.loc[month_df["type"] == "IN", "montant"].sum())
    # dépenses
    dep_spent = float(-month_df.loc[month_df["type"] == "OUT", "montant"].sum())
    d = pd.Timestamp(today)
    days_passed = d.day
    days_in_month = (d + pd.offsets.MonthEnd(0)).day
    dep_forecast = (
        dep_spent if days_passed <= 0 else dep_spent / days_passed * days_in_month
    )
    solde = rev - dep_forecast
    return {
        "revenus": round(rev, 2),
        "depenses": round(dep_forecast, 2),
        "solde": round(solde, 2),
    }


def predict_end_of_year(
    df: pd.DataFrame,
    today: date | None = None,
    eom_override: (
        dict | None
    ) = None,  # optionnel: {"revenus":..., "depenses":..., "solde":...}
    use_last_n: int = 3,
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
    rev_series = g["IN"] if "IN" in g.columns else pd.Series(0.0, index=g.index)
    out_series = g["OUT"] if "OUT" in g.columns else pd.Series(0.0, index=g.index)

    # Revenus = IN ; Dépenses = -OUT (positif)
    g["revenus"] = rev_series
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
        month_forecast = predict_end_of_month(
            df, today
        )  # réutilise ta logique actuelle
        eom_rev = float(month_forecast.get("revenus", 0.0))
        eom_dep = float(month_forecast.get("depenses", 0.0))

    # Mois restants APRÈS le mois courant
    months_left_after_current = 12 - cur_ts.month

    # Projection fin d'année
    year_rev = (
        ytd_rev_excl_cur + eom_rev + months_left_after_current * avg_rev_completed
    )
    year_dep = (
        ytd_dep_excl_cur + eom_dep + months_left_after_current * avg_dep_completed
    )
    return {
        "revenus": round(year_rev, 2),
        "depenses": round(year_dep, 2),
        "solde": round(year_rev - year_dep, 2),
    }


def savings_projection(
    monthly: float, annual_rate_pct: float, years: int, start_balance: float = 0.0
) -> float:
    """Valeur future avec capitalisation mensuelle."""
    r = max(annual_rate_pct, 0.0) / 100.0
    n = max(int(years * 12), 0)
    if r == 0 or n == 0:
        return float(start_balance + monthly * n)
    rm = r / 12.0
    growth = (1 + rm) ** n
    return float(start_balance * growth + monthly * (growth - 1) / rm)


def _solde_color_class(val: float) -> str:
    """Retourne la classe CSS pour colorer un solde selon sa valeur."""
    if val > 2000:
        return "solde-green"
    elif val >= 0:
        return "solde-blue"
    elif val >= -1000:
        return "solde-orange"
    else:
        return "solde-red"


def _format_euro(val: float) -> str:
    """Formate un montant en euros avec espace insécable (ex: '2 750 €')."""
    return f"{val:,.0f} €".replace(",", " ")


def build_coach_text(
    month_summary,
    month_forecast,
    year_forecast,
    split,
    top_alerts=None,
):
    """
    Retourne du HTML/Markdown pour la zone 'Ton coach 👇'
    - month_summary: dict avec 'revenus', 'depenses', 'solde' (observé à date)
    - month_forecast: dict avec 'revenus','depenses','solde' (projection fin de mois)
    - year_forecast: (non utilisé dans le texte mais gardé pour compatibilité)
    - split: dict style {"50": pct_besoins, "30": pct_envies, "20": pct_epargne}
    - top_alerts: liste de strings pour les alertes principales
    """
    # --- chiffres actuels ---
    rev_now = float(month_summary.get("revenus", 0.0))
    dep_now = float(month_summary.get("depenses", 0.0))
    solde_now = float(month_summary.get("solde", 0.0))

    # --- projection fin de mois ---
    rev_fore = float(month_forecast.get("revenus", 0.0)) if month_forecast else 0.0
    dep_fore = float(month_forecast.get("depenses", 0.0)) if month_forecast else 0.0
    solde_fore = float(month_forecast.get("solde", 0.0)) if month_forecast else 0.0

    # --- couleurs dynamiques pour les soldes ---
    solde_now_html = f'<span class="{_solde_color_class(solde_now)}">{_format_euro(solde_now)}</span>'
    solde_fore_html = f'<span class="{_solde_color_class(solde_fore)}">{_format_euro(solde_fore)}</span>'

    # 1. État actuel
    line_now = (
        "Ce mois-ci tu as enregistré "
        f"{_format_euro(rev_now)} de revenus et {_format_euro(dep_now)} de dépenses "
        f"(solde {solde_now_html})."
    )

    # 2. Projection fin de mois
    line_fore = (
        "À ce rythme ton solde de fin de mois sera "
        f"{solde_fore_html} "
        f"({_format_euro(rev_fore)} de revenus / {_format_euro(dep_fore)} de dépenses)."
    )

    # 3. Règle 50 / 30 / 20
    pct_besoins = float(split.get("50", 0.0))
    pct_envies = float(split.get("30", 0.0))
    pct_epargne = float(split.get("20", 0.0))

    def status_besoins(p):
        if p <= 50:
            return "ok"
        elif p <= 55:
            return "warn"
        else:
            return "bad"

    def status_envies(p):
        if p <= 30:
            return "ok"
        elif p <= 35:
            return "warn"
        else:
            return "bad"

    def status_epargne(p):
        if p >= 20:
            return "ok"
        elif p >= 10:
            return "warn"
        else:
            return "bad"

    sb = status_besoins(pct_besoins)
    se = status_envies(pct_envies)
    ss = status_epargne(pct_epargne)

    # Choix du texte 50/30/20
    if sb == "ok" and se == "ok" and ss == "ok":
        rule_line = (
            "Ta répartition est solide 👍 : "
            f"Besoins {pct_besoins:.1f} %, Envies {pct_envies:.1f} %, "
            f"Épargne {pct_epargne:.1f} %. Tu restes dans les zones cibles."
        )
    elif sb == "bad" and se == "bad" and ss == "bad":
        rule_line = (
            "Là tu pousses fort 😬 : "
            f"les Besoins ({pct_besoins:.1f} %) et les Envies ({pct_envies:.1f} %) "
            "sont hauts, et l’Épargne est sous le niveau souhaité "
            f"({pct_epargne:.1f} %). On va devoir calmer le rythme."
        )
    else:
        parts_high = []
        parts_ok = []

        if sb in ("warn", "bad"):
            parts_high.append(f"Besoins {pct_besoins:.1f} %")
        else:
            parts_ok.append("Besoins OK")

        if se in ("warn", "bad"):
            parts_high.append(f"Envies {pct_envies:.1f} %")
        else:
            parts_ok.append("Envies OK")

        if ss in ("warn", "bad"):
            parts_high.append(f"Épargne {pct_epargne:.1f} %")
        else:
            parts_ok.append("Épargne OK")

        alert_bit = ""
        ok_bit = ""

        if parts_high:
            alert_bit = "À surveiller : " + " / ".join(parts_high) + ". "
        if parts_ok:
            ok_bit = "Plutôt bien : " + " / ".join(parts_ok) + "."

        rule_line = alert_bit + ok_bit

    # 4. Top alertes (restos, etc.)
    alerts_block = ""
    if top_alerts:
        alerts_block = (
            "<br><br><strong>Ce qui pèse le plus :</strong><br>• "
            + "<br>• ".join(top_alerts)
        )

    # Assemblage final
    coach_html = (
        line_now + "<br><br>" + line_fore + "<br><br>" + rule_line + alerts_block
    )

    return coach_html


import pandas as pd
from datetime import date
from calendar import monthrange


# 🔹 Extrait les lignes correspondant à un mois donné (inclusivement)
def slice_df_for_month(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    if df.empty:
        return df.iloc[0:0]
    start = date(year, month, 1)
    last_day = monthrange(year, month)[1]
    end = date(year, month, last_day)
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    mask = (d["date"].dt.date >= start) & (d["date"].dt.date <= end)
    return d.loc[mask].copy()


# 🔹 Extrait les lignes correspondant à une année donnée
def slice_df_for_year(df: pd.DataFrame, year: int) -> pd.DataFrame:
    if df.empty:
        return df.iloc[0:0]
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    mask = d["date"].dt.year == year
    return d.loc[mask].copy()


# 🔹 Calcule les totaux (revenus, dépenses, solde) d’un DataFrame
def compute_totals(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"revenus": 0.0, "depenses": 0.0, "solde": 0.0}
    revenus = float(df.loc[df["type"] == "IN", "montant"].sum())
    depenses = float(-df.loc[df["type"] == "OUT", "montant"].sum())
    return {"revenus": revenus, "depenses": depenses, "solde": revenus - depenses}


# 🔹 Construit une série mensuelle (revenus + dépenses) prête pour les graphiques
def build_year_timeseries(df_year: pd.DataFrame) -> pd.DataFrame:
    if df_year.empty:
        return pd.DataFrame(columns=["mois", "revenus", "depenses"])
    d = df_year.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["mois"] = d["date"].dt.month

    revenus_by_month = (
        d[d["type"] == "IN"].groupby("mois")["montant"].sum().rename("revenus")
    )
    depenses_by_month = (
        d[d["type"] == "OUT"]
        .assign(montant_abs=lambda x: x["montant"].abs())
        .groupby("mois")["montant_abs"]
        .sum()
        .rename("depenses")
    )

    merged = (
        pd.concat([revenus_by_month, depenses_by_month], axis=1)
        .fillna(0.0)
        .reset_index()
    )
    mois_labels = {
        1: "Jan",
        2: "Fév",
        3: "Mar",
        4: "Avr",
        5: "Mai",
        6: "Juin",
        7: "Juil",
        8: "Août",
        9: "Sept",
        10: "Oct",
        11: "Nov",
        12: "Déc",
    }
    merged["mois_label"] = merged["mois"].map(mois_labels)
    return merged


# 🔹 Génère un texte “coach annuel” (bilan + conseils personnalisés)
def build_coach_text_year(
    df_year: pd.DataFrame,
    year_totals: dict,
    s_year_split: dict,
    year_forecast: dict,
    target_year: int,
) -> str:
    revenus_ytd = year_totals["revenus"]
    depenses_ytd = year_totals["depenses"]
    solde_ytd = year_totals["solde"]

    worst_month_name = None
    worst_month_value = None
    if not df_year.empty:
        d = df_year.copy()
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        d["mois_num"] = d["date"].dt.month
        d["mois_label"] = d["date"].dt.strftime("%B")

        depmois = (
            d[d["type"] == "OUT"]
            .assign(absval=lambda x: x["montant"].abs())
            .groupby(["mois_num", "mois_label"], as_index=False)["absval"]
            .sum()
            .sort_values("absval", ascending=False)
        )

        if not depmois.empty:
            row0 = depmois.iloc[0]
            mois_lbl = row0["mois_label"]
            worst_month_value = row0["absval"]
            MONTHS_FR = {
                "January": "janvier",
                "February": "février",
                "March": "mars",
                "April": "avril",
                "May": "mai",
                "June": "juin",
                "July": "juillet",
                "August": "août",
                "September": "septembre",
                "October": "octobre",
                "November": "novembre",
                "December": "décembre",
            }
            worst_month_name = MONTHS_FR.get(mois_lbl, mois_lbl)

    pct_besoins = float(s_year_split.get("50", 0.0))
    pct_envies = float(s_year_split.get("30", 0.0))
    pct_epargne = float(s_year_split.get("20", 0.0))

    def badge_envies(p):
        if p <= 30:
            return "OK"
        if p <= 35:
            return "un peu élevé"
        return "à maîtriser"

    def badge_epargne(p):
        if p >= 20:
            return "très bien"
        if p >= 10:
            return "peut mieux faire"
        return "insuffisant"

    envies_comment = badge_envies(pct_envies)
    epargne_comment = badge_epargne(pct_epargne)

    proj_revenus = year_forecast.get("revenus", 0.0)
    proj_depenses = year_forecast.get("depenses", 0.0)
    proj_solde = year_forecast.get("solde", 0.0)

    parts = []
    parts.append(
        f"Depuis le début de {target_year}, tu as encaissé <b>{revenus_ytd:,.0f} €</b> "
        f"et dépensé <b>{depenses_ytd:,.0f} €</b>, soit un solde actuel de "
        f"<b>{solde_ytd:,.0f} €</b>."
    )
    if worst_month_name:
        parts.append(
            f"Ton mois le plus coûteux est <b>{worst_month_name}</b> "
            f"avec environ <b>{worst_month_value:,.0f} €</b> de sorties."
        )
    parts.append(
        "Sur l'année, ta répartition ressemble à : "
        f"<b>Besoins {pct_besoins:.1f}%</b>, "
        f"<b>Envies {pct_envies:.1f}%</b> ({envies_comment}), "
        f"<b>Épargne {pct_epargne:.1f}%</b> ({epargne_comment})."
    )
    parts.append(
        f"Si tu gardes ce rythme, la fin {target_year} ressemble à "
        f"<b>{proj_solde:,.0f} €</b> de solde annuel "
        f"({proj_revenus:,.0f} € de revenus / {proj_depenses:,.0f} € de dépenses)."
    )
    if pct_epargne >= 20:
        parts.append(
            "Très bon signal : tu dégages une vraie capacité d'épargne sur l'année."
        )
    elif pct_envies > 35:
        parts.append(
            "Ton point d'attention principal reste les dépenses plaisir / envies. Tu peux viser < 30%."
        )
    else:
        parts.append(
            "Tu es globalement sur une trajectoire équilibrée. L'idée maintenant : tenir jusqu'à décembre."
        )

    return "<br><br>".join(parts).replace(",", " ")
