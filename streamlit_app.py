# streamlit_app.py — KPFLO avec CSS externe
import streamlit as st
import pandas as pd
from datetime import date
import os

from kpflo_core import budget

from kpflo_core.insee import InseeClient, compare_user_vs_cpi
from kpflo_core.categories import IPC_IDBANK_BY_CATEGORY


from kpflo_core.budget import _current_month_slice


from kpflo_core.revenus import get_revenus_df, save_revenus_edits, delete_revenu, generate_libelle, get_revenus_summary, get_revenus_preview_summary, update_revenu

from kpflo_core.categories import INCOME_CATEGORIES, EXPENSE_CATEGORIES, FIFTY, THIRTY, TWENTY
from kpflo_core.budget import compute_summary
from kpflo_core.storage_sqlite import init_db, list_users, create_user, rename_user, validate_user, export_csv, ensure_default_user, fetch_all_df_with_id, insert_transaction
from kpflo_core.revenus import (
    get_revenus_df,
    get_revenus_preview_summary,
    delete_revenu,
    update_revenu,
    suggestions_for_income_category,  # stems Revenus (UI suffixe _<Month Year>)
    default_revenu_row,               # ligne par défaut Revenus (stem, sans suffixe)
)
from kpflo_core.depenses import get_depenses_df, save_depenses_edits, delete_depense
from kpflo_core.epargne import get_epargne_stats

from kpflo_core.depenses import (
    get_depenses_df,
    save_depenses_edits,
    delete_depense,
    suggestions_for_category,     # stems Dépenses (UI suffixe _<Month Year>)
    default_depense_row,          # ligne par défaut Dépenses (stem, sans suffixe)
)

from kpflo_core.revenus import get_revenus_df, save_revenus_edits, delete_revenu, generate_libelle, get_revenus_summary, get_revenus_preview_summary, update_revenu
from kpflo_core.storage_sqlite import insert_transaction

from pathlib import Path

# --- Config interne INSEE ---
INSEE_TOKEN = ""  # pas encore utilisé
INSEE_ALERT_THRESHOLD = 2.0  # seuil d'alerte (en points de pourcentage)

# variables locales simulant celles de la sidebar
insee_token = INSEE_TOKEN
insee_pts = INSEE_ALERT_THRESHOLD


import numpy as np



# Utils: normaliser le DataFrame budgets (à appeler une seule fois au chargement)
def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Harmoniser les colonnes
    if "type" not in df.columns and "kind" in df.columns:
        df["type"] = np.where(df["kind"].str.lower() == "revenu", "IN", "OUT")
    if "montant" not in df.columns and "amount" in df.columns:
        df["montant"] = df["amount"].astype(float)
    if "categorie" not in df.columns and "category" in df.columns:
        df["categorie"] = df["category"]

    # Dates
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    # Standardiser les signes : OUT -> négatif ; IN -> positif
    # (si ton CSV OUT est déjà négatif, ça ne changera rien ; s'il est positif, on le rend négatif)
    df.loc[df["type"] == "OUT", "montant"] = -df.loc[df["type"] == "OUT", "montant"].abs()
    df.loc[df["type"] == "IN", "montant"] = df.loc[df["type"] == "IN", "montant"].abs()

    # Colonnes utiles minimales
    return df[["date", "type", "categorie", "montant"]]







# ---------- Config ----------
st.set_page_config(page_title="KPFLO 🤑", page_icon="💧", layout="wide")
init_db()

# ---------- State ----------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user_id = None
if "revenus_forms" not in st.session_state:
    st.session_state.revenus_forms = []

# 👉 AJOUT ICI : initialiser les formulaires Dépenses
if "depenses_forms" not in st.session_state or st.session_state.depenses_forms is None:
    st.session_state.depenses_forms = []

# ---------- Header ----------
left, right = st.columns([0.55, 0.45], vertical_alignment="center")
with left:
    st.title("KPFLO 🤑")
    st.caption("Gère ton argent avec style !")

users_df = list_users()
if users_df.empty:
    ensure_default_user()
    users_df = list_users()

default_user_id = ensure_default_user()
if st.session_state.current_user_id is None:
    st.session_state.current_user_id = default_user_id

with right:
    # ========= Helpers avatar (idempotents) =========
    from pathlib import Path
    try:
        from PIL import Image  # Assure-toi d'avoir Pillow installé
    except Exception:
        Image = None  # On tombera en mode "initiales" si Pillow n'est pas dispo

    AVATAR_DIR = Path("data/avatars")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)

    def _avatar_path(user_id: int) -> Path:
        return AVATAR_DIR / f"{int(user_id)}.png"

    def save_avatar(user_id: int, uploaded_file) -> None:
        """Enregistre un avatar carré PNG depuis un upload Streamlit."""
        if uploaded_file is None or Image is None:
            return
        img = Image.open(uploaded_file).convert("RGB")
        s = min(img.width, img.height)
        left = (img.width - s) // 2
        top = (img.height - s) // 2
        img_sq = img.crop((left, top, left + s, top + s)).resize((256, 256))
        img_sq.save(_avatar_path(user_id), "PNG", optimize=True)

    def avatar_src(user_id: int) -> str | None:
        p = _avatar_path(user_id)
        return str(p) if p.exists() else None

    # ========= Un peu de style pour les avatars =========
    st.markdown("""
    <style>
    .kpflo-avatar   { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; }
    .kpflo-avatarLG { width: 64px; height: 64px; border-radius: 50%; object-fit: cover; }
    .kpflo-initial  { width: 40px; height: 40px; border-radius: 50%; background:#eee;
                      display:flex; align-items:center; justify-content:center; font-weight:600; }
    .kpflo-initialLG{ width: 64px; height: 64px; border-radius: 50%; background:#eee;
                      display:flex; align-items:center; justify-content:center; font-weight:700; font-size:1.1rem; }
    .kpflo-row { display:flex; align-items:center; gap:.6rem; }
    </style>
    """, unsafe_allow_html=True)

    # ========= Récup utilisateur courant =========
    cur_row = users_df[users_df["id"] == st.session_state.current_user_id].iloc[0]
    cur_fullname = f'{cur_row.get("prenom","")} {cur_row["nom"]}'.strip() or f'ID {cur_row["id"]}'
    cur_avatar = avatar_src(int(cur_row["id"]))

    # ========= Bandeau avatar (remplace "✏️ Modifier utilisateur") =========
    # On affiche l'avatar (ou initiales) + un popover "▼" qui contient le switch
    cols_hdr = st.columns([0.2, 0.15, 0.65], vertical_alignment="center")
    with cols_hdr[0]:
        if cur_avatar:
            st.image(cur_avatar, caption=None, width=40)
        else:
            st.markdown(f"<div class='kpflo-initial'>{(cur_fullname[:1] or '🙂')}</div>", unsafe_allow_html=True)
    with cols_hdr[1]:
        with st.popover("▼"):
            st.markdown("**Profil courant**")
            c1, c2 = st.columns([0.3, 0.7], vertical_alignment="center")
            with c1:
                if cur_avatar:
                    st.image(cur_avatar, caption=None, width=64)
                else:
                    st.markdown(f"<div class='kpflo-initialLG'>{(cur_fullname[:1] or '🙂')}</div>", unsafe_allow_html=True)
            with c2:
                st.markdown(f"**{cur_fullname}**<br/><span style='opacity:.7;'>id:{cur_row['id']}</span>", unsafe_allow_html=True)

            st.divider()
            st.markdown("**Autres profils**")
            for _, row in users_df.iterrows():
                rid = int(row["id"])
                if rid == int(cur_row["id"]):
                    continue
                name = f'{row.get("prenom","")} {row["nom"]}'.strip() or f'ID {rid}'
                ava = avatar_src(rid)

                r1, r2, r3 = st.columns([0.25, 0.55, 0.20], vertical_alignment="center")
                with r1:
                    if ava:
                        st.image(ava, caption=None, width=40)
                    else:
                        st.markdown(f"<div class='kpflo-initial'>{(name[:1] or '🙂')}</div>", unsafe_allow_html=True)
                with r2:
                    st.write(name)
                with r3:
                    if st.button("Basculer", key=f"switch_{rid}"):
                        st.session_state.current_user_id = rid
                        st.rerun()

            st.caption("Crée plusieurs profils (vert/rouge/limite) puis bascule ici.")

    # ========= Popover "🔑 Se connecter / Créer" (conservé) + Upload photo =========
    with st.popover("🔑 Se connecter / Créer"):
        if st.session_state.logged_in:
            if st.button("Déconnexion"):
                st.session_state.logged_in = False
                st.session_state.current_user_id = default_user_id
                st.success("Déconnecté. Mode invité activé.")
                st.rerun()
        else:
            st.subheader("Se connecter")
            login_nom = st.text_input("Nom d'utilisateur", key="login_nom")
            login_pw = st.text_input("Mot de passe", type="password", key="login_pw")
            if st.button("Se connecter"):
                user_id = validate_user(login_nom, login_pw)
                if user_id:
                    st.session_state.logged_in = True
                    st.session_state.current_user_id = int(user_id)
                    st.success("Connecté !")
                    st.rerun()
                else:
                    st.error("Nom ou mot de passe incorrect.")

            st.divider()
            st.subheader("Créer un nouvel utilisateur")
            new_nom = st.text_input("Nom", placeholder="Ex: Dupont", key="new_nom")
            new_prenom = st.text_input("Prénom", placeholder="Ex: Jean", key="new_prenom")
            new_email = st.text_input("Email", placeholder="Ex: jean@dupont.fr", key="new_email")
            new_pw = st.text_input("Mot de passe", type="password", key="new_pw")
            new_pw_confirm = st.text_input("Confirmer mot de passe", type="password", key="new_pw_confirm")

            # 👉 Nouveau : photo de profil (optionnel)
            new_avatar = st.file_uploader("Photo de profil (PNG/JPG, optionnel)", type=["png", "jpg", "jpeg"], accept_multiple_files=False)

            if st.button("Créer"):
                if new_pw != new_pw_confirm:
                    st.error("Les mots de passe ne correspondent pas.")
                elif not new_nom or not new_pw:
                    st.error("Nom et mot de passe requis.")
                else:
                    try:
                        new_id = create_user(new_nom, new_prenom, new_email, new_pw)
                        if new_avatar is not None:
                            save_avatar(int(new_id), new_avatar)
                        st.session_state.logged_in = True
                        st.session_state.current_user_id = int(new_id)
                        st.success("Utilisateur créé et connecté !")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))


# ---------- Chargement données ----------
def refresh_df():
    st.session_state.df = fetch_all_df_with_id(st.session_state.current_user_id)
    st.session_state.df = normalize_df(st.session_state.df)  # Ajout : normalise ici

if "df" not in st.session_state:
    refresh_df()
else:
    refresh_df()

# ---------- Sidebar : Import/Export CSV (SQLite) ----------
import io, hashlib


from dateutil.relativedelta import relativedelta

from kpflo_core.storage_sqlite import (
    list_users, fetch_all_df, insert_transaction, _connect
)

st.sidebar.subheader("📦 Données (CSV) – Base SQLite")

# -- Helpers --
def month_iter(end_month: date, months: int = 36):
    end = date(end_month.year, end_month.month, 1)
    return [end - relativedelta(months=i) for i in range(months-1, -1, -1)]

def _users_map_sqlite():
    dfu = list_users()
    return {int(r["id"]): (f'{r.get("prenom","")} {r["nom"]}').strip() or f'ID {r["id"]}'
            for _, r in dfu.iterrows()}

def build_export_or_template_csv_sqlite(months=36) -> bytes:
    """
    Exporte toutes les transactions (tous users) au format unifié si base non vide.
    Sinon, génère un modèle 36 mois (revenu/depense/epargne) pour tous les users.
    Colonnes: user_id,user_name,date,kind,category,label,amount
    """
    users_map = _users_map_sqlite()
    rows, non_empty = [], False

    for uid in users_map.keys():
        df = fetch_all_df(user_id=uid)  # date,type(IN/OUT),categorie,libelle,montant,recurrent
        if df is not None and not df.empty:
            non_empty = True
            for _, r in df.iterrows():
                rows.append({
                    "user_id": uid,
                    "user_name": users_map[uid],
                    "date": pd.to_datetime(r["date"]).date(),
                    "kind": "revenu" if str(r["type"]).upper() == "IN" else "depense",
                    "category": r.get("categorie", ""),
                    "label": r.get("libelle", ""),
                    "amount": float(r.get("montant", 0) or 0),
                })

    if not non_empty:
        months_list = month_iter(date.today(), months=months)
        for uid, uname in users_map.items():
            for d in months_list:
                for kind in ("revenu", "depense", "epargne"):
                    rows.append({
                        "user_id": uid,
                        "user_name": uname,
                        "date": d,
                        "kind": kind,
                        "category": "" if kind != "epargne" else "Épargne",
                        "label": "",
                        "amount": 0.0,
                    })

    df = pd.DataFrame(rows, columns=["user_id","user_name","date","kind","category","label","amount"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

def import_unified_csv_to_sqlite(file, replace_all: bool = False):
    """
    Importe un CSV unifié et écrit en base:
    kind -> revenu=IN, depense=OUT, epargne=OUT (catégorie 'Épargne').
    Si replace_all=True, on purge d'abord les transactions des user_id présents dans le CSV.
    """
    df = pd.read_csv(file)
    required = ["user_id", "date", "kind", "amount"]
    miss = [c for c in required if c not in df.columns]
    if miss:
        st.error(f"Colonnes manquantes: {', '.join(miss)}")
        return False

    # Normalisation
    df["user_id"] = df["user_id"].astype(int)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["kind"] = df["kind"].str.lower().str.strip()
    for opt in ["category", "label"]:
        if opt not in df.columns:
            df[opt] = ""

    # (Option) Purge des utilisateurs concernés
    user_ids_in_csv = sorted(df["user_id"].unique().tolist())
    if replace_all and user_ids_in_csv:
        with _connect() as con:
            q = "DELETE FROM transactions WHERE user_id IN ({})".format(
                ",".join("?" for _ in user_ids_in_csv)
            )
            con.execute(q, user_ids_in_csv)
            con.commit()

    # Insertion
    for _, r in df.iterrows():
        kind = r["kind"]
        if kind not in ("revenu", "depense", "epargne"):
            continue
        type_val = "IN" if kind == "revenu" else "OUT"
        categorie = r.get("category") or ("Épargne" if kind == "epargne" else "")
        libelle = r.get("label") or ("Épargne" if kind == "epargne" else "")
        montant = float(r.get("amount", 0) or 0)
        if montant == 0:
            continue
        insert_transaction(
            date_val=r["date"],
            type_val=type_val,
            categorie=categorie,  # Notez que insert_transaction utilise "categorie"
            libelle=libelle,
            montant=montant,
            recurrent=False,
            user_id=int(r["user_id"])
        )

    # Patch A : Collecter les mois impactés par l'import (Épargne & placements)
    imported_months = set()
    for _, row in df[df["category"] == "Épargne & placements"].iterrows():  # Changement : "categorie" -> "category"
        try:
            d = pd.to_datetime(row["date"], errors="coerce")
            if pd.notnull(d):
                imported_months.add(d.strftime("%Y-%m"))
        except Exception:
            pass

    # Poser des flags de re-sync pour la section Épargne
    st.session_state["_force_epargne_resync"] = True
    st.session_state["_force_epargne_months"] = list(imported_months)  # ex: ["2025-10", "2025-09"]
    st.session_state["_open_tab"] = "epargne"  # Ouvre l'onglet Épargne

    return True

# -- Download (export ou modèle 36 mois)
csv_bytes = build_export_or_template_csv_sqlite(months=36)
st.sidebar.download_button(
    label="⬇️ Télécharger CSV (36 mois)",
    data=csv_bytes,
    file_name="kpflo_finances_36mois.csv",
    mime="text/csv",
    help="Modèle 3 ans (ou export DB) pour tous les utilisateurs."
)

# -- Import (FORMULAIRE + garde anti-répétition)
st.sidebar.markdown("—")
st.sidebar.markdown("**📥 Importer un CSV**")

with st.sidebar.form("csv_import_form", clear_on_submit=False):
    replace_all = st.checkbox(
        "Remplacer toutes les transactions des utilisateurs présents dans le CSV",
        value=False
    )
    uploaded = st.file_uploader(
        "CSV unifié (user_id,date,kind,category,label,amount)",
        type=["csv"],
        key="csv_upload"
    )
    submitted = st.form_submit_button("Importer")

if submitted:
    if uploaded is None:
        st.sidebar.error("Aucun fichier sélectionné.")
    else:
        content = uploaded.getvalue()
        file_hash = hashlib.md5(content).hexdigest()
        last_hash = st.session_state.get("last_import_hash")

        if last_hash == file_hash:
            st.sidebar.info("Ce fichier a déjà été importé (aucune action).")
        else:
            ok = import_unified_csv_to_sqlite(io.BytesIO(content), replace_all=replace_all)
            if ok:
                st.session_state["last_import_hash"] = file_hash
                st.sidebar.success("Import réussi ✅")
                st.rerun()
            else:
                st.sidebar.error("Import échoué. Vérifie le format des colonnes.")


    

# ---------- Onglets ----------
tab_revenus, tab_depenses, tab_epargne, tab_budget = st.tabs(
    ["📈 Revenus", "📉 Dépenses", "💰 Épargne", "📊 Budget"]
)

# =======================
# SECTION : REVENUS 💶 (Année -> Mois -> Lignes)
# =======================
with tab_revenus:
    st.subheader("Ajoute tes revenus")

    # --- CSS local de la section ---
    st.markdown("""
    <style>
      :root { --primary-color: #f97316; } /* orange pour les boutons primary */

      /* HEADER wrapper */
      #revenus-header { margin-bottom: 12px; }

      /* Styliser UNIQUEMENT le select du header (petit rectangle) */
      #revenus-header div[data-baseweb="select"]{
        width: 220px !important;
        border: 2px solid #d1d5db !important;
        border-radius: 6px !important;
        background-color: #ffffff !important;
        transition: all 0.2s ease-in-out;
      }
      #revenus-header div[data-baseweb="select"]:hover{
        border-color: #f97316 !important;
        box-shadow: 0 0 5px rgba(249,115,22,0.3) !important;
      }
      #revenus-header div[data-baseweb="select"] > div{
        font-size: 0.95rem !important;
        color: #333 !important;
        font-weight: 500 !important;
      }

      /* Mini-carte récap total à droite */
      .revenus-mini-wrap { display: flex; justify-content: flex-end; }
      .revenus-mini {
        display: inline-flex; align-items: baseline; gap: 12px;
        padding: 10px 14px;
        border: 2px solid #d1d5db;
        border-radius: 10px;
        background: #fff;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      }
      .revenus-mini .mini-title { font-weight: 600; color: #374151; }
      .revenus-mini .mini-total { font-variant-numeric: tabular-nums; font-weight: 700; color: #111827; }
      .revenus-mini .mini-euro { opacity: .85; }
      .revenus-mini .mini-badge { margin-left: 6px; font-size: 12px; padding: 2px 6px; border-radius: 999px; background: #f3f4f6; color: #6b7280; }

      hr { border-top: 1px solid #e0e0e0 !important; margin: 4px 0 !important; }
    </style>
    """, unsafe_allow_html=True)

    # ---------- HEADER : Sélecteur (gauche) + Mini-écran Total (droite) ----------
    st.markdown('<div id="revenus-header">', unsafe_allow_html=True)
    header_left, header_right = st.columns([1, 5], vertical_alignment="center")

    with header_left:
        group_mode = st.selectbox(
            "🗓️ Grouper par",
            ["Mois", "Année"],  # Semaine retiré
            key="revenus_group_mode",
            index=0,
            label_visibility="visible"
        )

    refresh_df()  # recharge les data avant le calcul du total
    total_preview = get_revenus_preview_summary(
        st.session_state.current_user_id, st.session_state.revenus_forms
    )

    with header_right:
        st.markdown(f'''
        <div class="revenus-mini-wrap">
          <div class="revenus-mini" title="Somme des revenus affichés (prévisualisation)">
            <span class="mini-title">Revenus enregistrés</span>
            <span class="mini-total">{total_preview:.2f}</span>
            <span class="mini-euro">€</span>
            <span class="mini-badge">Total</span>
          </div>
        </div>
        ''', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)  # /revenus-header

    # ---------- Données ----------
    df_revenus_all = get_revenus_df(st.session_state.current_user_id)

    # Helpers mois (local)
    def _month_key(d):  # "YYYY-MM"
        return pd.Timestamp(d).strftime("%Y-%m") if d else None

    def _month_human(d):  # "October 2025"
        try:
            return pd.Timestamp(d).strftime("%B %Y")
        except Exception:
            return ""

    # Suggesteur de libellés (suffixe mois)
    def income_suggestions_for_category(cat: str, d) -> list[str]:
        mois = _month_human(d)
        stems = suggestions_for_income_category(cat)  # depuis kpflo_core/revenus.py
        stems = stems or [cat if cat else "Revenu"]
        return [f"{s}_{mois}" if mois else s for s in stems]

    # ---------- Mode "Mois" : un seul niveau (mois -> lignes) ----------
    if group_mode == "Mois" and not df_revenus_all.empty:
        df = df_revenus_all.copy()
        dt = pd.to_datetime(df["date"])
        df["month_key"]  = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")  # ex. "October 2025"

        grouped = (
            df.groupby("month_key")
              .agg(total_montant=("montant", "sum"),
                   month_name=("month_name", "first"),
                   count=("id", "count"))
              .reset_index()
              .sort_values("month_key", ascending=False)
        )

        for _, g in grouped.iterrows():
            m_key   = g["month_key"]
            m_name  = g["month_name"]
            m_total = float(g["total_montant"])
            m_count = int(g["count"])

            rename_key = f"rename_rev_month_{m_key}"
            custom_name = st.session_state.get(rename_key, f"Revenus_{m_name}")

            with st.expander(f"{custom_name} ({m_total:.2f} €) - {m_count} lignes", expanded=False):
                # Renommer le dossier (mois)
                new_name = st.text_input("Nom du dossier", value=custom_name, key=rename_key, label_visibility="collapsed")
                if new_name != custom_name:
                    st.session_state[rename_key] = new_name
                    st.rerun()

                # Lignes du mois
                m_df = df[df["month_key"] == m_key].sort_values("date", ascending=False)
                for _, row in m_df.iterrows():
                    cols = st.columns([1, 2, 2, 1, 0.8])
                    with cols[0]: st.write(row["date"])
                    with cols[1]: st.write(row["categorie"])
                    with cols[2]: st.write(row["libelle"])
                    with cols[3]: st.write(f"{row['montant']:.2f} €")
                    with cols[4]:
                        c1, c2 = st.columns([0.5, 0.5], gap="small")
                        # ✏️ Éditer
                        with c1:
                            if st.button("✏️", key=f"edit_revenu_{row['id']}", help="Modifier"):
                                edit_form = {
                                    "id": int(row["id"]),
                                    "date": row["date"],
                                    "categorie": row["categorie"],
                                    "libelle": row["libelle"],
                                    "montant": float(row["montant"])
                                }
                                st.session_state.revenus_forms = [edit_form] + [
                                    f for f in st.session_state.revenus_forms
                                    if f.get("id") != edit_form["id"]
                                ]
                                st.rerun()
                        # ❌ Supprimer
                        with c2:
                            if st.button("❌", key=f"del_revenu_{row['id']}", type="secondary", help="Supprimer", use_container_width=True):
                                delete_revenu(row["id"], st.session_state.current_user_id)
                                refresh_df()
                                st.rerun()
                    st.markdown("<hr>", unsafe_allow_html=True)

    # ---------- Mode "Année" : deux niveaux (année -> mois -> lignes) ----------
    if group_mode == "Année" and not df_revenus_all.empty:
        df = df_revenus_all.copy()
        dt = pd.to_datetime(df["date"])
        df["year"]       = dt.dt.year
        df["month_key"]  = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        # Groupes Année
        year_groups = (
            df.groupby("year")
              .agg(total_montant=("montant", "sum"))
              .reset_index()
              .sort_values("year", ascending=False)
        )

        for _, yg in year_groups.iterrows():
            y        = int(yg["year"])
            y_total  = float(yg["total_montant"])

            year_display_name = f"Revenus_{y}"

            with st.expander(f"{year_display_name} ({y_total:.2f} €)", expanded=False):
                y_df = df[df["year"] == y]
                month_groups = (
                    y_df.groupby("month_key")
                        .agg(total_montant=("montant", "sum"),
                             month_name=("month_name", "first"),
                             count=("id", "count"))
                        .reset_index()
                        .sort_values("month_key", descending=False if False else True)  # keep desc
                )

                for _, mg in month_groups.iterrows():
                    m_key   = mg["month_key"]
                    m_name  = mg["month_name"]
                    m_total = float(mg["total_montant"])
                    m_count = int(mg["count"])

                    rename_month_key = f"rename_rev_{y}_{m_key}"
                    month_display_name = st.session_state.get(rename_month_key, f"Revenus_{m_name}")

                    with st.expander(f"{month_display_name} ({m_total:.2f} €) - {m_count} lignes", expanded=False):
                        new_m_name = st.text_input("Nom du dossier mois", value=month_display_name, key=rename_month_key, label_visibility="collapsed")
                        if new_m_name != month_display_name:
                            st.session_state[rename_month_key] = new_m_name
                            st.rerun()

                        m_df = y_df[y_df["month_key"] == m_key].sort_values("date", ascending=False)
                        for _, row in m_df.iterrows():
                            cols = st.columns([1, 2, 2, 1, 0.8])
                            with cols[0]: st.write(row["date"])
                            with cols[1]: st.write(row["categorie"])
                            with cols[2]: st.write(row["libelle"])
                            with cols[3]: st.write(f"{row['montant']:.2f} €")
                            with cols[4]:
                                c1, c2 = st.columns([0.5, 0.5], gap="small")
                                # ✏️ Éditer
                                with c1:
                                    if st.button("✏️", key=f"edit_revenu_{row['id']}", help="Modifier"):
                                        edit_form = {
                                            "id": int(row["id"]),
                                            "date": row["date"],
                                            "categorie": row["categorie"],
                                            "libelle": row["libelle"],
                                            "montant": float(row["montant"])
                                        }
                                        st.session_state.revenus_forms = [edit_form] + [
                                            f for f in st.session_state.revenus_forms
                                            if f.get("id") != edit_form["id"]
                                        ]
                                        st.rerun()
                                # ❌ Supprimer
                                with c2:
                                    if st.button("❌", key=f"del_revenu_{row['id']}", type="secondary", help="Supprimer", use_container_width=True):
                                        delete_revenu(row["id"], st.session_state.current_user_id)
                                        refresh_df()
                                        st.rerun()
                            st.markdown("<hr>", unsafe_allow_html=True)

    # ---------- Callbacks internes (sélecteur non-éditable) ----------
    def rev_update_form(index, key_date, key_cat, key_lib_choice, key_lib_value, key_montant):
        """Sync d'une ligne de formulaire quand un champ change (cat/date/lib/€)."""
        cur_date    = st.session_state.get(key_date)
        cur_cat     = st.session_state.get(key_cat)
        cur_choice  = st.session_state.get(key_lib_choice, "")
        cur_montant = st.session_state.get(key_montant, 0.0)

        # Recalcule la liste selon (catégorie, mois)
        opts = income_suggestions_for_category(cur_cat, cur_date)
        if cur_choice not in opts and opts:
            st.session_state[key_lib_choice] = opts[0]
            cur_choice = opts[0]

        # libellé = choix (non éditable)
        st.session_state[key_lib_value] = cur_choice

        # push dans la structure tampon
        if index < len(st.session_state.revenus_forms):
            st.session_state.revenus_forms[index].update({
                "date": cur_date,
                "categorie": cur_cat,
                "libelle": st.session_state[key_lib_value],
                "montant": cur_montant,
            })
        st.session_state.revenus_forms = st.session_state.revenus_forms[:]  # rerender

    # ---------- Saisie dynamique des nouvelles lignes ----------
    edited_rows = []
    if st.session_state.revenus_forms:
        st.markdown("### Saisie en cours (nouvelle série ou édition)")
        header_cols = st.columns([1, 2, 2.6, 1, 0.6])
        header_cols[0].markdown("**Date**")
        header_cols[1].markdown("**Catégorie**")
        header_cols[2].markdown("**Libellé**")
        header_cols[3].markdown("**Montant (€)**")

    for i, form in enumerate(st.session_state.revenus_forms):
        key_date       = f"rev_date_{i}"
        key_cat        = f"rev_cat_{i}"
        key_lib_choice = f"rev_lib_choice_{i}"
        key_lib_value  = f"rev_lib_value_{i}"  # valeur réelle envoyée en DB
        key_montant    = f"rev_montant_{i}"

        # init inputs
        st.session_state.setdefault(key_date,    form.get("date", date.today()))
        st.session_state.setdefault(key_cat,     form.get("categorie", INCOME_CATEGORIES[0]))
        st.session_state.setdefault(key_montant, form.get("montant", 0.0))

        init_opts = income_suggestions_for_category(st.session_state[key_cat], st.session_state[key_date])
        if key_lib_choice not in st.session_state or key_lib_value not in st.session_state:
            initial = form.get("libelle")
            if initial and initial in init_opts:
                st.session_state[key_lib_choice] = initial
                st.session_state[key_lib_value]  = initial
            elif init_opts:
                st.session_state[key_lib_choice] = init_opts[0]
                st.session_state[key_lib_value]  = init_opts[0]
            else:
                # fallback robuste
                mois = _month_human(st.session_state[key_date])
                fallback = f"Revenu_{mois}" if mois else "Revenu"
                st.session_state[key_lib_choice] = fallback
                st.session_state[key_lib_value]  = initial if initial else fallback

        # Colonnes de saisie
        cols = st.columns([1, 2, 2.6, 1, 0.6])

        # Date
        with cols[0]:
            st.date_input(
                "", key=key_date, on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden"
            )

        # Catégorie
        with cols[1]:
            st.selectbox(
                "", options=INCOME_CATEGORIES,
                index=INCOME_CATEGORIES.index(st.session_state[key_cat]) if st.session_state[key_cat] in INCOME_CATEGORIES else 0,
                key=key_cat, on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden"
            )

        # Libellé (sélecteur non-éditable, suffixé mois)
        with cols[2]:
            opts = income_suggestions_for_category(st.session_state[key_cat], st.session_state[key_date])
            try:
                idx = opts.index(st.session_state[key_lib_choice])
            except ValueError:
                idx = 0
            st.selectbox(
                "", options=opts, index=idx,
                key=key_lib_choice, on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden"
            )

        # Montant
        with cols[3]:
            st.number_input(
                "", min_value=0.0, step=10.0,
                key=key_montant, on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden"
            )

        # Supprimer la ligne
        with cols[4]:
            if st.button("❌", key=f"del_form_{i}", type="secondary", help="Supprimer cette ligne"):
                new_forms = st.session_state.revenus_forms.copy()
                new_forms.pop(i)
                st.session_state.revenus_forms = new_forms
                st.rerun()

        edited_rows.append({
            "id": form.get("id"),
            "date": st.session_state[key_date],
            "categorie": st.session_state[key_cat],
            "libelle": st.session_state[key_lib_value],  # valeur réelle
            "montant": st.session_state[key_montant]
        })

    # ---------- Boutons bas de section ----------
    btn_left, btn_right = st.columns([3, 1])

    with btn_left:
        st.button(
            "Nouvelle ligne",
            key="add_revenu",
            type="primary",
            help="Ajoute une nouvelle ligne de saisie de revenu"
        )
        if st.session_state.get("add_revenu"):
            new_forms = st.session_state.revenus_forms.copy()
            today = date.today()
            # ligne par défaut via core (non suffixée) + suffixe ici pour cohérence UI
            base = default_revenu_row(INCOME_CATEGORIES, today)
            mois = _month_human(today)
            base["libelle"] = f"{base['libelle']}_{mois}" if mois else base["libelle"]
            new_forms.append(base)
            st.session_state.revenus_forms = new_forms
            st.rerun()

    with btn_right:
        if edited_rows and st.button("Enregistrer revenus", type="primary", use_container_width=True):
            try:
                for row in edited_rows:
                    if row.get("id"):
                        update_revenu(row["id"], row, st.session_state.current_user_id)
                    else:
                        insert_transaction(
                            row["date"], "IN", row["categorie"], row["libelle"],
                            float(row["montant"]), recurrent=False,
                            user_id=st.session_state.current_user_id
                        )
                st.session_state.revenus_forms = []
                refresh_df()
                st.balloons()
                st.success("Revenus enregistrés !")
            except Exception as e:
                st.error(f"Erreur : {e}")
            st.rerun()




# =======================
# SECTION : DÉPENSES 🧾 (Année -> Mois -> Lignes)
# - Libellé via depenses.py + suffixe mois
# - "Autre" = entrée non-éditable (suffixée du mois)
# - Ratio sur revenu NET (revenus - Impôts & taxes) avec cibles/caps 50/30/20
# - Affichage court : "Seuil X%" (détail cible/cap en tooltip)
# =======================

with tab_depenses:
    st.subheader("Ajoute tes dépenses")

    # ---------- CSS local ----------
    st.markdown("""
    <style>
      :root { --primary-color: #f97316; }
      #depenses-header { margin-bottom: 12px; }
      #depenses-header div[data-baseweb="select"]{
        width: 220px !important; border: 2px solid #d1d5db !important; border-radius: 6px !important;
        background-color: #ffffff !important; transition: all 0.2s ease-in-out;
      }
      #depenses-header div[data-baseweb="select"]:hover{
        border-color: #f97316 !important; box-shadow: 0 0 5px rgba(249,115,22,0.3) !important;
      }
      .depenses-mini-wrap { display: flex; justify-content: flex-end; }
      .depenses-mini {
        display: inline-flex; align-items: baseline; gap: 12px; padding: 10px 14px;
        border: 2px solid #d1d5db; border-radius: 10px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.04);
      }
      .depenses-mini .mini-title { font-weight: 600; color: #374151; }
      .depenses-mini .mini-total { font-variant-numeric: tabular-nums; font-weight: 700; color: #111827; }
      .depenses-mini .mini-euro { opacity: .85; }
      .ratio-box {
        width:100%; padding:8px 10px; border-radius:8px; border:1.5px solid #d1d5db; background:#f9fafb;
        font-weight:600; text-align:center; line-height:1.25;
      }
      .ratio-sub { display:block; font-weight:500; font-size:12px; opacity:.8; margin-top:2px; }
      .ratio-ok{background:#ecfdf5;border-color:#34d399;color:#065f46;}
      .ratio-warn{background:#fff7ed;border-color:#fb923c;color:#7c2d12;}
      .ratio-bad{background:#fef2f2;border-color:#f87171;color:#7f1d1d;}
      .ratio-na{background:#f3f4f6;border-color:#d1d5db;color:#374151;}
      hr { border-top: 1px solid #e0e0e0 !important; margin: 4px 0 !important; }
    </style>
    """, unsafe_allow_html=True)

    # ---------- Header : sélecteur + mini-total ----------
    st.markdown('<div id="depenses-header">', unsafe_allow_html=True)
    header_left, header_right = st.columns([1, 5], vertical_alignment="center")

    with header_left:
        dep_group_mode = st.selectbox(
            "🗓️ Grouper par",
            ["Mois", "Année"],
            key="depenses_group_mode_v3",
            index=0,
            label_visibility="visible"
        )

    df_depenses_all = get_depenses_df(st.session_state.current_user_id)
    total_depenses_preview = float(df_depenses_all["montant"].sum()) if not df_depenses_all.empty else 0.0

    with header_right:
        st.markdown(
            f"""<div class="depenses-mini-wrap">
                  <div class="depenses-mini" title="Somme des dépenses affichées (prévisualisation)">
                    <span class="mini-title">Dépenses enregistrées</span>
                    <span class="mini-total">{total_depenses_preview:.2f}</span>
                    <span class="mini-euro">€</span>
                  </div>
                </div>""",
            unsafe_allow_html=True
        )
    st.markdown('</div>', unsafe_allow_html=True)

    # ---------- Helpers (mois + revenu NET du mois + règles 50/30/20) ----------
    def _month_key(d):  # "YYYY-MM"
        return pd.Timestamp(d).strftime("%Y-%m") if d else None

    def _month_human(d):  # "October 2025"
        try:
            return pd.Timestamp(d).strftime("%B %Y")
        except Exception:
            return ""

    revenus_df_all = get_revenus_df(st.session_state.current_user_id)

    @st.cache_data(show_spinner=False)
    def build_month_revenue_totals(df_revenus):
        if df_revenus is None or df_revenus.empty:
            return {}
        tmp = df_revenus.copy()
        tmp["mk"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m")
        return tmp.groupby("mk")["montant"].sum().to_dict()

    @st.cache_data(show_spinner=False)
    def build_month_impots_totals(df_depenses):
        """Somme des dépenses de la catégorie 'Impôts & taxes' par mois (pour revenu NET)."""
        if df_depenses is None or df_depenses.empty:
            return {}
        tmp = df_depenses[df_depenses["categorie"] == "Impôts & taxes"].copy()
        if tmp.empty:
            return {}
        tmp["mk"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m")
        return tmp.groupby("mk")["montant"].sum().to_dict()

    rev_month_totals_brut = build_month_revenue_totals(revenus_df_all)
    impots_month_totals   = build_month_impots_totals(df_depenses_all)

    def revenue_total_net_for_month(d):
        """Revenu NET du mois (50/30/20) = revenus - 'Impôts & taxes' du même mois."""
        mk = _month_key(d)
        if not mk:
            return 0.0
        brut = float(rev_month_totals_brut.get(mk, 0.0))
        imp  = float(impots_month_totals.get(mk, 0.0))
        return max(brut - imp, 0.0)

    # Cibles & caps par catégorie (sur % du revenu NET du mois)
    CATEGORY_BUDGET_RULES = {
        "Logement":                                  {"target": 30.0, "cap": 35.0},
        "Alimentation & boissons non alcoolisées":   {"target": 11.0, "cap": 15.0},
        "Transport":                                 {"target": 5.0,  "cap": 12.0},
        "Santé":                                     {"target": 2.0,  "cap": 6.0},
        "Communications":                            {"target": 2.0,  "cap": 5.0},
        "Ameublement & équipement ménager":          {"target": 3.0,  "cap": 6.0},
        "Habillement & chaussures":                  {"target": 3.0,  "cap": 6.0},
        "Loisirs & culture":                         {"target": 6.0,  "cap": 10.0},
        "Restaurants & hôtels":                      {"target": 3.0,  "cap": 6.0},
        "Biens & services divers":                   {"target": 3.0,  "cap": 6.0},
        "Boissons alcoolisées & tabac":              {"target": 1.0,  "cap": 2.0},
        "Enfants & famille":                         {"target": 5.0,  "cap": 10.0},
        "Animaux de compagnie":                      {"target": 1.0,  "cap": 3.0},
        "Dépenses exceptionnelles":                  {"target": 5.0,  "cap": None},  # enveloppe souple
        "Frais bancaires & services financiers":     {"target": 0.5,  "cap": 1.0},
        "Assurances":                                {"target": 2.0,  "cap": 4.0},
        "__DEFAULT__":                               {"target": 15.0, "cap": 20.0},
    }

    def budget_rule_for_category(cat: str) -> tuple[float, float | None]:
        if not cat:
            rule = CATEGORY_BUDGET_RULES["__DEFAULT__"]
        else:
            rule = CATEGORY_BUDGET_RULES.get(cat, CATEGORY_BUDGET_RULES["__DEFAULT__"])
        return rule["target"], rule["cap"]

    def ratio_status_color(pct: float, target: float, cap: float | None) -> str:
        if target <= 0:
            return "ratio-na"
        if pct <= target + 1e-9:
            return "ratio-ok"
        if cap is None:
            return "ratio-warn"  # pas de rouge si pas de cap
        if pct <= cap + 1e-9:
            return "ratio-warn"
        return "ratio-bad"

    # ------ Affichage court du ratio : "Seuil X%" (+ tooltip détail cible/cap) ------
    def render_ratio_box(date_, categorie, montant):
        """Affiche la box de ratio avec 'Seuil X%' en sous-texte (couleurs via cible/cap)."""
        rev_net = revenue_total_net_for_month(date_)
        if rev_net <= 0:
            st.markdown("<div class='ratio-box ratio-na'>n/a<span class='ratio-sub'>revenu net mensuel indisponible</span></div>", unsafe_allow_html=True)
            return

        pct = (float(montant) / rev_net) * 100.0
        target, cap = budget_rule_for_category(categorie)
        status = ratio_status_color(pct, target, cap)
        ratio_txt = f"{pct:.0f}%"
        sub_txt = f"sur {_month_human(date_)} • Seuil {target:.0f}%"
        cap_txt = "—" if cap is None else f"{cap:.0f}%"
        title_attr = f"title='Cible {target:.0f}% • Cap {cap_txt}'"

        st.markdown(
            f"<div class='ratio-box {status}' {title_attr}>{ratio_txt}"
            f"<span class='ratio-sub'>{sub_txt}</span></div>",
            unsafe_allow_html=True
        )

    # =================================================
    # Saisie dynamique (libellé via depenses.py + ratio live)
    # =================================================

    def expense_suggestions_for_category(cat: str, d) -> list[str]:
        """
        Utilise suggestions_for_category(cat) pour obtenir des 'stems',
        puis suffixe le mois (ex. 'Eau_October 2025') et ajoute 'Autre_<Month Year>'.
        """
        mois = _month_human(d)                 # ex. "October 2025"
        stems = suggestions_for_category(cat)  # ex. ["Eau", "Électricité", "Gaz"]
        stems = stems or [cat if cat else "Dépense"]
        options = [f"{s}_{mois}" if mois else s for s in stems]
        # Autre (non-éditable, sans "…")
        autre = f"Autre_{mois}" if mois else "Autre"
        if autre not in options:
            options.append(autre)
        return options

    def dep_update_form(index, key_date, key_cat, key_lib_choice, key_lib_value, key_amt):
        """
        Sync d'une ligne de formulaire :
        - recalcul des options quand (catégorie, mois) change
        - libellé = choix du select (non éditable)
        """
        cur_date   = st.session_state.get(key_date)
        cur_cat    = st.session_state.get(key_cat)
        cur_choice = st.session_state.get(key_lib_choice, "")
        cur_value  = st.session_state.get(key_lib_value, "")
        cur_amt    = st.session_state.get(key_amt, 0.0)

        opts = expense_suggestions_for_category(cur_cat, cur_date)
        if cur_choice not in opts and opts:
            st.session_state[key_lib_choice] = opts[0]
            cur_choice = opts[0]

        st.session_state[key_lib_value] = cur_choice or cur_value

        if index < len(st.session_state.depenses_forms):
            st.session_state.depenses_forms[index]["date"]      = cur_date
            st.session_state.depenses_forms[index]["categorie"] = cur_cat
            st.session_state.depenses_forms[index]["libelle"]   = st.session_state[key_lib_value]
            st.session_state.depenses_forms[index]["montant"]   = cur_amt

        st.session_state.depenses_forms = st.session_state.depenses_forms

    # ---------- Groupes (Mois / Année -> Mois -> Lignes) ----------
    if dep_group_mode == "Mois" and not df_depenses_all.empty:
        df = df_depenses_all.copy()
        dt = pd.to_datetime(df["date"])
        df["month_key"]  = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        grouped = (
            df.groupby("month_key")
              .agg(total_montant=("montant", "sum"),
                   month_name=("month_name", "first"),
                   count=("id", "count"))
              .reset_index()
              .sort_values("month_key", ascending=False)
        )

        for _, g in grouped.iterrows():
            m_key, m_name = g["month_key"], g["month_name"]
            m_total, m_count = float(g["total_montant"]), int(g["count"])
            rename_key = f"rename_dep_month_{m_key}"
            custom_name = st.session_state.get(rename_key, f"Dépenses_{m_name}")

            with st.expander(f"{custom_name} ({m_total:.2f} €) - {m_count} lignes", expanded=False):
                new_name = st.text_input("Nom du dossier", value=custom_name, key=rename_key, label_visibility="collapsed")
                if new_name != custom_name:
                    st.session_state[rename_key] = new_name
                    st.rerun()

                m_df = df[df["month_key"] == m_key].sort_values("date", ascending=False)
                for _, row in m_df.iterrows():
                    montant = float(row["montant"])

                    cols = st.columns([1, 2, 2, 1, 1.2, 0.8])
                    with cols[0]: st.write(row["date"])
                    with cols[1]: st.write(row["categorie"])
                    with cols[2]: st.write(row["libelle"])
                    with cols[3]: st.write(f"{montant:.2f} €")
                    with cols[4]:
                        render_ratio_box(row["date"], row["categorie"], row["montant"])
                    with cols[5]:
                        c1, c2 = st.columns([0.5, 0.5], gap="small")
                        with c1:
                            if st.button("✏️", key=f"edit_dep_{row['id']}", help="Modifier"):
                                edit_form = {
                                    "id": int(row["id"]),
                                    "date": row["date"],
                                    "categorie": row["categorie"],
                                    "libelle": row["libelle"],
                                    "montant": montant,
                                }
                                st.session_state.depenses_forms = [edit_form] + [
                                    f for f in st.session_state.depenses_forms
                                    if f.get("id") != edit_form["id"]
                                ]
                                st.rerun()
                        with c2:
                            if st.button("❌", key=f"del_dep_{row['id']}", type="secondary", help="Supprimer", use_container_width=True):
                                delete_depense(row["id"], st.session_state.current_user_id)
                                st.rerun()
                    st.markdown("<hr>", unsafe_allow_html=True)

    if dep_group_mode == "Année" and not df_depenses_all.empty:
        df = df_depenses_all.copy()
        dt = pd.to_datetime(df["date"])
        df["year"]       = dt.dt.year
        df["month_key"]  = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        year_groups = (
            df.groupby("year")
              .agg(total_montant=("montant", "sum"))
              .reset_index()
              .sort_values("year", ascending=False)
        )

        for _, yg in year_groups.iterrows():
            y, y_total = int(yg["year"]), float(yg["total_montant"])
            year_display_name = f"Dépenses_{y}"
            with st.expander(f"{year_display_name} ({y_total:.2f} €)", expanded=False):
                y_df = df[df["year"] == y].sort_values("date", ascending=False)
                month_groups = (
                    y_df.groupby("month_key")
                        .agg(total_montant=("montant", "sum"),
                             month_name=("month_name", "first"),
                             count=("id", "count"))
                        .reset_index()
                        .sort_values("month_key", ascending=False)
                )
                for _, mg in month_groups.iterrows():
                    m_key, m_name = mg["month_key"], mg["month_name"]
                    m_total, m_count = float(mg["total_montant"]), int(mg["count"])
                    rename_month_key = f"rename_dep_{y}_{m_key}"
                    month_display_name = st.session_state.get(rename_month_key, f"Dépenses_{m_name}")

                    with st.expander(f"{month_display_name} ({m_total:.2f} €) - {m_count} lignes", expanded=False):
                        new_m_name = st.text_input("Nom du dossier mois", value=month_display_name, key=rename_month_key, label_visibility="collapsed")
                        if new_m_name != month_display_name:
                            st.session_state[rename_month_key] = new_m_name
                            st.rerun()

                        m_df = y_df[y_df["month_key"] == m_key].sort_values("date", ascending=False)
                        for _, row in m_df.iterrows():
                            montant = float(row["montant"])

                            cols = st.columns([1, 2, 2, 1, 1.2, 0.8])
                            with cols[0]: st.write(row["date"])
                            with cols[1]: st.write(row["categorie"])
                            with cols[2]: st.write(row["libelle"])
                            with cols[3]: st.write(f"{montant:.2f} €")
                            with cols[4]:
                                render_ratio_box(row["date"], row["categorie"], row["montant"])
                            with cols[5]:
                                c1, c2 = st.columns([0.5, 0.5], gap="small")
                                with c1:
                                    if st.button("✏️", key=f"edit_dep_{row['id']}", help="Modifier"):
                                        edit_form = {
                                            "id": int(row["id"]),
                                            "date": row["date"],
                                            "categorie": row["categorie"],
                                            "libelle": row["libelle"],
                                            "montant": float(row["montant"]),
                                        }
                                        st.session_state.depenses_forms = [edit_form] + [
                                            f for f in st.session_state.depenses_forms
                                            if f.get("id") != edit_form["id"]
                                        ]
                                        st.rerun()
                                with c2:
                                    if st.button("❌", key=f"del_dep_{row['id']}", type="secondary", help="Supprimer", use_container_width=True):
                                        delete_depense(row["id"], st.session_state.current_user_id)
                                        st.rerun()
                            st.markdown("<hr>", unsafe_allow_html=True)

    # =================================================
    # Saisie dynamique (formulaires)
    # =================================================
    edited_rows_dep = []
    for i, form in enumerate(st.session_state.depenses_forms):
        key_date       = f"dep_date_{i}"
        key_cat        = f"dep_cat_{i}"
        key_lib_choice = f"dep_lib_choice_{i}"
        key_lib_value  = f"dep_lib_value_{i}"
        key_amt        = f"dep_montant_{i}"

        # colonnes de saisie
        cols = st.columns([1, 2, 2.6, 1.2, 1.2, 0.6])

        # Date : Utilise get pour value, avec default du form ou today
        with cols[0]:
            default_date = form.get("date", date.today())
            st.date_input("Date", value=st.session_state.get(key_date, default_date), key=key_date,
                          on_change=dep_update_form,
                          args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt))

        # Catégorie : Utilise get pour récupérer la catégorie, puis calcule l'index
        with cols[1]:
            default_cat = form.get("categorie", EXPENSE_CATEGORIES[0])
            default_index = EXPENSE_CATEGORIES.index(default_cat) if default_cat in EXPENSE_CATEGORIES else 0
            # Récupère la catégorie actuelle depuis session_state
            current_cat = st.session_state.get(key_cat, default_cat)
            # Calcule l'index à partir de la catégorie actuelle
            current_index = EXPENSE_CATEGORIES.index(current_cat) if current_cat in EXPENSE_CATEGORIES else default_index
            st.selectbox("Catégorie", options=EXPENSE_CATEGORIES,
                         index=current_index,
                         key=key_cat, on_change=dep_update_form,
                         args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt))

        # Libellé (toujours en select, y compris pour "Autre_<mois>")
        with cols[2]:
            # Calcul des opts basé sur la catégorie et date actuelles
            cur_cat = st.session_state.get(key_cat, form.get("categorie", EXPENSE_CATEGORIES[0]))
            cur_date = st.session_state.get(key_date, form.get("date", date.today()))
            opts = expense_suggestions_for_category(cur_cat, cur_date)

            # Default pour lib_choice : calculé si pas dans session_state
            default_lib = form.get("libelle", opts[0] if opts else "Dépense")
            if default_lib not in opts and opts:
                default_lib = opts[0]

            cur_choice = st.session_state.get(key_lib_choice, default_lib)
            try:
                idx = opts.index(cur_choice)
            except ValueError:
                idx = 0
            st.selectbox(
                "Libellé", options=opts, index=idx,
                key=key_lib_choice, on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt)
            )
            # Sync lib_value avec choice après création
            st.session_state[key_lib_value] = st.session_state.get(key_lib_choice, default_lib)

        # Montant : Utilise get pour value
        with cols[3]:
            default_amt = form.get("montant", 0.0)
            st.number_input("Montant (€)", min_value=0.0, step=5.0,
                            value=st.session_state.get(key_amt, default_amt), key=key_amt, on_change=dep_update_form,
                            args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt))

        # Ratio live (affichage court)
        with cols[4]:
            cur_date = st.session_state.get(key_date, form.get("date", date.today()))
            cur_cat  = st.session_state.get(key_cat, form.get("categorie", EXPENSE_CATEGORIES[0]))
            cur_amt  = float(st.session_state.get(key_amt, form.get("montant", 0.0)))
            render_ratio_box(cur_date, cur_cat, cur_amt)

        # Supprimer
        with cols[5]:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            if st.button("❌", key=f"del_dep_form_{i}", type="secondary", help="Supprimer cette ligne", use_container_width=True):
                st.session_state.depenses_forms.pop(i)
                # nettoie les clés suivantes
                for j in range(i + 1, len(st.session_state.depenses_forms) + 1):
                    for k in [f"dep_date_{j}", f"dep_cat_{j}", f"dep_lib_choice_{j}", f"dep_lib_value_{j}", f"dep_montant_{j}"]:
                        st.session_state.pop(k, None)
                st.rerun()

        edited_rows_dep.append({
            "id": form.get("id"),
            "date": st.session_state.get(key_date),
            "categorie": st.session_state.get(key_cat),
            "libelle": st.session_state.get(key_lib_value),
            "montant": st.session_state.get(key_amt, 0.0),
        })

    st.session_state.depenses_forms = edited_rows_dep

    # ---------- Boutons bas ----------
    btn_left, btn_right = st.columns([3, 1])

    with btn_left:
        st.button("➕ Nouvelle dépense", key="add_depense", type="primary")
        if st.session_state.get("add_depense"):
            today = date.today()
            # Base par défaut via depenses.py (catégorie + libellé cohérents)
            base = default_depense_row(EXPENSE_CATEGORIES, today)  # dict: date, categorie, libelle, montant
            # suffixe le mois sur le libellé proposé (par cohérence d'affichage)
            mois = _month_human(today)
            base["libelle"] = f"{base['libelle']}_{mois}" if mois and "_" not in base["libelle"] else base["libelle"]
            st.session_state.depenses_forms.append(base)
            st.rerun()

    with btn_right:
        if edited_rows_dep and st.button("💾 Enregistrer dépenses", type="primary", use_container_width=True):
            try:
                save_depenses_edits(edited_rows_dep, st.session_state.current_user_id)  # UPDATE si id présent, sinon INSERT
                st.session_state.depenses_forms = []
                st.success("Dépenses enregistrées !")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")





# =======================
# SECTION : ÉPARGNE 💰 (design "dossiers", sync auto bidirectionnelle)
# =======================

with tab_epargne:
    # ---------- CSS local ----------
    st.markdown("""
    <style>
      .ep-row {
        border:1px solid #e5e7eb; border-radius:10px; padding:10px 12px; background:#fff;
        box-shadow:0 1px 3px rgba(0,0,0,.04); margin-bottom:8px;
      }
      .ep-title { font-weight:700; color:#111827; }
      .muted { color:#6b7280; font-size:.9rem; }
      .cell-label { font-size:.85rem; color:#6b7280; margin-bottom:4px; }
      .hr-slim { border-top:1px solid #e5e7eb !important; margin:8px 0 !important; }
    </style>
    """, unsafe_allow_html=True)

    # ---------- Constantes ----------
    STEM_TO_ACCOUNT = {
        "Livret A / LDDS": "Livret A / LDDS",
        "Assurance-vie": "Assurance-vie (fonds €)",
        "PEL / CEL": "PEL / CEL",
        "Bourse / actions / ETF": "Bourse / ETF",
        "Cryptomonnaies": "Cryptomonnaies",
        "Autres placements financiers": "Autres placements",
    }
    ACCOUNT_TO_STEM = {v: k for k, v in STEM_TO_ACCOUNT.items()}
    ACCOUNTS_ORDER = [
        "Livret A / LDDS",
        "PEL / CEL",
        "Assurance-vie (fonds €)",
        "Bourse / ETF",
        "Cryptomonnaies",
        "Autres placements",
    ]
    FIXED_RATE_ACCOUNTS = {"Livret A / LDDS", "PEL / CEL", "Assurance-vie (fonds €)"}
    VOLATILE_ACCOUNTS = {"Bourse / ETF", "Cryptomonnaies", "Autres placements"}

    # ---------- Helpers ----------
    def _month_key(d):   # "YYYY-MM"
        return pd.Timestamp(d).strftime("%Y-%m") if d else None

    def _month_human(d): # "October 2025"
        try:
            return pd.Timestamp(d).strftime("%B %Y")
        except Exception:
            return ""

    def _stem_from_lib(lib: str) -> str:
        """Racine d'un libellé (avant suffixe _Month Year)."""
        if not isinstance(lib, str):
            return ""
        return lib.split("_")[0].strip()

    # ---------- Source : Dépenses ----------
    df_dep_all = get_depenses_df(st.session_state.current_user_id)

    # --- Patch B : Si un import CSV vient d'avoir lieu, on force une re-synchronisation ---
    force_resync = bool(st.session_state.get("_force_epargne_resync", False))
    force_months = st.session_state.get("_force_epargne_months", []) or []
    force_selected_mk = None
    if force_months:
        force_selected_mk = sorted(force_months, reverse=True)[0]  # ex: "2025-10"

    # ---- Préparation des mois disponibles ----
    mois_options = []
    if not df_dep_all.empty:
        tmp = df_dep_all[df_dep_all["categorie"] == "Épargne & placements"].copy()
        if not tmp.empty:
            tmp["date"] = pd.to_datetime(tmp["date"], errors="coerce")
            tmp = tmp.dropna(subset=["date"])
            tmp["mk"] = tmp["date"].dt.strftime("%Y-%m")
            months = (
                tmp.groupby("mk")
                   .agg(dt=("date", "max"))
                   .reset_index()
                   .sort_values("mk", ascending=False)
            )
            mois_options = [pd.to_datetime(d).date() for d in months["dt"].tolist()]

    # ---- Ligne d’en-tête compacte ----
    header_cols = st.columns([4, 1.2])
    with header_cols[0]:
        st.markdown(
            "<div style='font-size:1.7rem; font-weight:600; color:#111827; margin-top:-8px;'>Épargne & placements</div>",
            unsafe_allow_html=True
        )

    with header_cols[1]:
        st.markdown("<div style='margin-top:-20px; margin-bottom:-2px; font-size:0.85rem; color:#6b7280;'>Date de référence</div>", unsafe_allow_html=True)
        default_date = mois_options[0] if mois_options else date.today()
        selected_month_date = st.date_input(
            "",
            value=default_date,
            key="ep_month_ref",
            label_visibility="collapsed",
            format="DD/MM/YYYY",
        )

    selected_mk = pd.Timestamp(selected_month_date).strftime("%Y-%m")
    selected_mh = pd.Timestamp(selected_month_date).strftime("%B %Y")

    # --- Patch B : Ajustement du mois affiché et réinitialisation des flags ---
    if force_resync:
        if force_selected_mk:
            try:
                for d in mois_options:
                    if pd.Timestamp(d).strftime("%Y-%m") == force_selected_mk:
                        selected_month_date = d
                        selected_mk = pd.Timestamp(d).strftime("%Y-%m")
                        selected_mh = pd.Timestamp(d).strftime("%B %Y")
                        break
            except Exception:
                pass

        if "epargne_accounts" in st.session_state and "_ep_prev_auto" in st.session_state:
            for i, slot in enumerate(st.session_state.epargne_accounts):
                prev_auto = float(st.session_state["_ep_prev_auto"].get(slot["name"], 0.0))
                cur_input = float(slot.get("monthly_input", 0.0))
                manual_flag = bool(st.session_state.get(f"ep_manual_{i}", False))
                if manual_flag and abs(cur_input - prev_auto) < 0.01:
                    st.session_state[f"ep_manual_{i}"] = False
        st.session_state["_force_epargne_resync"] = False
        st.session_state["_force_epargne_months"] = []

    st.divider()

    # Reset des flags "manuel" quand on change de mois
    if st.session_state.get("_ep_last_mk") != selected_mk:
        st.session_state["_ep_last_mk"] = selected_mk
        if "epargne_accounts" in st.session_state:
            for i in range(len(st.session_state.epargne_accounts)):
                st.session_state[f"ep_manual_{i}"] = False

    # Versements "auto" du mois
    auto_contribs = {acc: 0.0 for acc in ACCOUNTS_ORDER}
    if not df_dep_all.empty:
        dep_m = df_dep_all.copy()
        dep_m["date"] = pd.to_datetime(dep_m["date"], errors="coerce")
        dep_m = dep_m.dropna(subset=["date"])
        dep_m["mk"] = dep_m["date"].dt.strftime("%Y-%m")
        dep_m = dep_m[(dep_m["mk"] == selected_mk) & (dep_m["categorie"] == "Épargne & placements")]
        for _, r in dep_m.iterrows():
            stem = _stem_from_lib(str(r.get("libelle","")))
            acc = STEM_TO_ACCOUNT.get(stem)
            if acc:
                auto_contribs[acc] = auto_contribs.get(acc, 0.0) + float(r.get("montant", 0.0))

    # ---------- State ----------
    if "_ep_prev_auto" not in st.session_state:
        st.session_state["_ep_prev_auto"] = {}

    def _default_rate_for(acc_name: str) -> float:
        if acc_name == "Livret A / LDDS":
            return 3.0
        if acc_name == "PEL / CEL":
            return 2.0
        if acc_name == "Assurance-vie (fonds €)":
            return 2.5
        return 0.0

    if "epargne_accounts" not in st.session_state:
        st.session_state.epargne_accounts = []
        for idx, acc in enumerate(ACCOUNTS_ORDER):
            auto_val = float(auto_contribs.get(acc, 0.0))
            st.session_state.epargne_accounts.append({
                "name": acc,
                "auto_monthly": auto_val,
                "monthly_input": auto_val,
                "balance": 0.0,
                "start_date": date.today(),
                "annual_rate": _default_rate_for(acc),
                "horizon_years": 5,
                "project_enabled": (acc in FIXED_RATE_ACCOUNTS),
            })
            st.session_state[f"ep_manual_{idx}"] = False  # Correction : i -> idx
            st.session_state["_ep_prev_auto"][acc] = auto_val
    else:
        for idx, slot in enumerate(st.session_state.epargne_accounts):
            acc_name = slot["name"]
            new_auto = float(auto_contribs.get(acc_name, 0.0))
            prev_auto = st.session_state["_ep_prev_auto"].get(acc_name, None)

            slot["auto_monthly"] = new_auto

            manual = bool(st.session_state.get(f"ep_manual_{idx}", False))
            if not manual:
                slot["monthly_input"] = new_auto
            else:
                if prev_auto is not None and abs(float(slot.get("monthly_input", 0.0)) - float(prev_auto)) < 0.01:
                    slot["monthly_input"] = new_auto

            st.session_state["_ep_prev_auto"][acc_name] = new_auto

    # ---------- Fonctions ----------
    def fv_monthly(balance, monthly, annual_rate_pct, years):
        r = max(annual_rate_pct, 0.0) / 100.0
        n = max(int(years * 12), 0)
        if r == 0 or n == 0:
            return float(balance + monthly * n)
        rm = r / 12.0
        growth = (1 + rm) ** n
        return float(balance * growth + monthly * (growth - 1) / rm)

    from math import isclose

    def _depenses_row_lookup(user_id: int, mk: str, libelle_full: str):
        df = get_depenses_df(user_id)
        if df is None or df.empty:
            return (None, None)
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df["mk"] = df["date"].dt.strftime("%Y-%m")
        sub = df[
            (df["mk"] == mk)
            & (df["categorie"] == "Épargne & placements")
            & (df["libelle"] == libelle_full)
        ]
        if sub.empty:
            return (None, None)
        row = sub.iloc[0]
        try:
            return (int(row["id"]), float(row["montant"]))
        except Exception:
            return (None, None)

    def _upsert_depense_from_savings(user_id: int, date_obj, libelle_full: str, montant: float, existing_id: int | None):
        if montant < 0:
            montant = 0.0
        payload = [{
            "id": existing_id,
            "date": date_obj,
            "categorie": "Épargne & placements",
            "libelle": libelle_full,
            "montant": float(montant),
        }]
        save_depenses_edits(payload, user_id)

    def autosync_monthly(idx: int, selected_month_date, selected_mh: str, selected_mk: str):
        try:
            acc = st.session_state.epargne_accounts[idx]
        except Exception:
            return
        st.session_state[f"ep_manual_{idx}"] = True

        acc_name = acc["name"]
        stem = ACCOUNT_TO_STEM.get(acc_name)
        if not stem:
            return

        current_monthly = float(st.session_state.get(f"ep_m_{idx}", acc.get("monthly_input", 0.0)))
        libelle_full = f"{stem}_{selected_mh}" if selected_mh else stem
        user_id = st.session_state.current_user_id
        existing_id, existing_amt = _depenses_row_lookup(user_id, selected_mk, libelle_full)

        if existing_id is None:
            if current_monthly > 0:
                _upsert_depense_from_savings(user_id, selected_month_date, libelle_full, current_monthly, None)
        else:
            if not isclose(current_monthly, float(existing_amt or 0.0), rel_tol=0.0, abs_tol=0.01):
                _upsert_depense_from_savings(user_id, selected_month_date, libelle_full, current_monthly, existing_id)

    # Date du jour pour l'enregistrement des écritures automatiques
    write_date = date.today()

    # ---------- Lignes "dossier" par compte (header = Nom • Mensualité • Solde) ----------
    HORIZON_LABELS = ["3 ans", "5 ans", "10 ans", "15 ans"]
    HORIZON_MAP = {"3 ans": 3, "5 ans": 5, "10 ans": 10, "15 ans": 15}

    def _fmt_eur(x: float) -> str:
        return f"{x:,.0f} €".replace(",", " ")

    edited_accounts = []
    for idx, acc in enumerate(st.session_state.epargne_accounts):
        header_text = f"{acc['name']}  •  Mensualité {_fmt_eur(acc['monthly_input'])}  •  Solde {_fmt_eur(acc['balance'])}"
        with st.expander(header_text, expanded=False):

            # === LIGNE 1 : Mensualité + Solde actuel + Depuis quand + Taux annuel ===
            row1 = st.columns([1, 1, 1, 1])
            with row1[0]:
                st.markdown("**Mensualité (projection)**")
                monthly_input = st.number_input(
                    "", min_value=0.0, step=10.0, value=float(acc["monthly_input"]),
                    key=f"ep_m_{idx}",
                    on_change=autosync_monthly,
                    args=(idx, write_date, selected_mh, selected_mk),
                    label_visibility="collapsed"
                )

            with row1[1]:
                st.markdown("**Solde actuel (€)**")
                balance = st.number_input(
                    "", min_value=0.0, step=100.0, value=float(acc["balance"]),
                    key=f"ep_b_{idx}", label_visibility="collapsed"
                )

            with row1[2]:
                st.markdown("**Depuis quand ?**")
                start_date = st.date_input(
                    "", value=acc["start_date"], key=f"ep_d_{idx}", label_visibility="collapsed"
                )

            with row1[3]:
                st.markdown("**Taux annuel (%)**")
                annual_rate = st.number_input(
                    "", min_value=0.0, max_value=25.0, step=0.1,
                    value=float(acc["annual_rate"]), key=f"ep_r_{idx}", label_visibility="collapsed"
                )

            st.markdown("<hr>", unsafe_allow_html=True)

            # === LIGNE 2 : Horizon + Projection ===
            row2 = st.columns([1, 1.2])
            with row2[0]:
                st.markdown("**Horizon**", unsafe_allow_html=True)
                st.markdown(
                    "<style>.stSelectbox {margin-bottom:-12px !important;}</style>",
                    unsafe_allow_html=True
                )
                cur_h_label = f"{int(acc.get('horizon_years', 5))} ans"
                horizon_label = st.selectbox(
                    "",
                    options=HORIZON_LABELS,
                    index=HORIZON_LABELS.index(cur_h_label) if cur_h_label in HORIZON_LABELS else 1,
                    key=f"ep_h_label_{idx}",
                    label_visibility="collapsed"
                )
                horizon_years = HORIZON_MAP[horizon_label]

                st.markdown(
                    f"<div style='margin-top:-1px; font-size:0.85rem; color:#6b7280;'>"
                    f"Capitalisation mensuelle • Taux {acc['annual_rate']:.2f}% • Horizon {horizon_label}</div>",
                    unsafe_allow_html=True
                )

            with row2[1]:
                st.markdown("**Projection**")
                fv = fv_monthly(balance, monthly_input, annual_rate, horizon_years)
                st.metric("Valeur future", _fmt_eur(fv))

            if acc["name"] in {"Bourse / ETF", "Cryptomonnaies", "Autres placements"}:
                st.markdown(
                    "<span class='muted' style='font-size:12px;'>"
                    "Actifs volatils : taux indicatif fixé à 0 %. Les performances peuvent fortement varier ; "
                    "les projections sont données à titre informatif."
                    "</span>",
                    unsafe_allow_html=True
                )

        edited_accounts.append({
            "name": acc["name"],
            "auto_monthly": float(acc["auto_monthly"]),
            "monthly_input": float(monthly_input),
            "balance": float(balance),
            "start_date": start_date,
            "annual_rate": float(annual_rate),
            "horizon_years": int(horizon_years),
        })

    st.session_state.epargne_accounts = edited_accounts
























# ====== BUDGET / DASHBOARD ======
with tab_budget:
    s = compute_summary(st.session_state.df)  # revenus/depenses/solde + by_cat + mois + split
    # --- Nouveaux résumés/projections ---
    from kpflo_core.budget import (
        compute_month_basics, predict_end_of_month, predict_end_of_year,
        build_coach_text, savings_projection
    )

    month_basics   = compute_month_basics(st.session_state.df)
    month_forecast = predict_end_of_month(st.session_state.df)
    year_forecast  = predict_end_of_year(st.session_state.df)

    # === Bandeau 3 KPI ===
    k1, k2, k3 = st.columns(3)
    k1.metric("Revenus (mois courant)", f"{month_basics['revenus']:.0f} €")
    k2.metric("Dépenses (mois courant)", f"{month_basics['depenses']:.0f} €")
    delta = ("+" if month_basics['solde'] >= 0 else "") + f"{month_basics['solde']:.0f} €"
    k3.metric("Solde (mois courant)", f"{month_basics['solde']:.0f} €", delta=None if month_basics['solde']==0 else delta)

    st.divider()

    # === 50/30/20 avec barres de progression + couleurs ===
    st.markdown("### Répartition 50 / 30 / 20")
    pb1, pb2, pb3 = st.columns(3)
    def _progress(col, title, val, target):
        with col:
            st.markdown(f"**{title}** — {val:.1f}% / cible {target}%")
            st.progress(min(int(val), 100))

    _progress(pb1, "Besoins", s['split']['50'], 50)
    _progress(pb2, "Envies",  s['split']['30'], 30)
    _progress(pb3, "Épargne", s['split']['20'], 20)

    st.caption("Astuce : les barres passent naturellement au-dessus de la cible si tu la dépasses ; combine avec les couleurs par catégorie dans l’onglet Dépenses.")

    st.divider()

    # === Graphiques récap (conserve ton esprit d'origine) ===
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Répartition par catégorie**")
        if not s["by_cat"].empty:
            st.bar_chart(s["by_cat"], x="categorie", y="montant", width="stretch")
        else:
            st.info("Ajoute des données pour voir la répartition.")
    with c2:
        st.markdown("**Évolution mensuelle**")
        if not s["mois"].empty:
            st.line_chart(s["mois"].set_index("mois"), width="stretch")
        else:
            st.info("Ajoute des données pour voir l’évolution.")

    st.divider()

    # === Prédictions (fin de mois / fin d'année) ===
    st.markdown("### 🔮 Prédictions")
    p1, p2, p3 = st.columns(3)
    p1.metric("Fin de mois — Revenus",  f"{month_forecast['revenus']:.0f} €")
    p2.metric("Fin de mois — Dépenses", f"{month_forecast['depenses']:.0f} €")
    p3.metric("Fin de mois — Solde",    f"{month_forecast['solde']:.0f} €")
    y1, y2, y3 = st.columns(3)
    y1.metric("Fin d'année — Revenus",  f"{year_forecast['revenus']:.0f} €")
    y2.metric("Fin d'année — Dépenses", f"{year_forecast['depenses']:.0f} €")
    y3.metric("Fin d'année — Solde",    f"{year_forecast['solde']:.0f} €")

    st.divider()

    # === (Option) mini simulateur d'épargne (1 an / 5 ans) ===
    with st.expander("💰 Simulation rapide d’épargne (optionnelle)", expanded=False):
        c = st.columns(4)
        with c[0]:
            monthly = st.number_input("Mensualité (€)", min_value=0.0, step=10.0, value=100.0)
        with c[1]:
            rate = st.number_input("Taux annuel (%)", min_value=0.0, step=0.1, value=3.0)
        with c[2]:
            start = st.number_input("Solde initial (€)", min_value=0.0, step=50.0, value=0.0)
        with c[3]:
            horizon = st.selectbox("Horizon", options=[1,3,5,10], index=2, format_func=lambda x: f"{x} an(s)")
        v1 = savings_projection(monthly, rate, 1, start)
        vH = savings_projection(monthly, rate, int(horizon), start)
        s1, sH = st.columns(2)
        s1.metric("Dans 1 an", f"{v1:,.0f} €".replace(",", " "))
        sH.metric(f"Dans {horizon} an(s)", f"{vH:,.0f} €".replace(",", " "))

    st.divider()

    # === Alertes (dépassements & inflation si tu branches l'API plus tard) ===
    st.markdown("### ⚠️ Alertes")
    alerts = []

    # Dépassements internes simples (top 3)
    df_month = budget._current_month_slice(st.session_state.df) if not st.session_state.df.empty else pd.DataFrame()
    if not df_month.empty:
        dep = df_month[df_month["type"] == "OUT"].copy()
        dep["abs"] = dep["montant"].abs()
        top3 = (
            dep.groupby("categorie")["abs"].sum()
            .sort_values(ascending=False)
            .head(3)
        )
        for cat, val in top3.items():
            alerts.append(
                f"**{cat}** pèse **{val:,.0f} €** ce mois-ci — vérifie ton ratio (case couleur dans l’onglet Dépenses).".replace(",", " ")
            )

    # 🔗 Alertes inflation INSEE si token présent et mapping non vide
    if insee_token and any(IPC_IDBANK_BY_CATEGORY.values()):
        try:
            client = InseeClient(token=insee_token)
            infl_alerts = compare_user_vs_cpi(
                df_user=st.session_state.df,
                client=client,
                cat_to_idbank={k: v for k, v in IPC_IDBANK_BY_CATEGORY.items() if v},
                threshold_points=float(insee_pts),
            )
            alerts.extend(infl_alerts)
        except Exception as e:
            st.warning(f"INSEE: impossible d’évaluer les alertes ({e})")

    # Affichage final
    if not alerts:
        st.success("Aucune alerte prioritaire ce mois-ci. Continue comme ça !")
    else:
        for a in alerts:
            st.warning(a)

    st.divider()

    # === Texte Coach ===
    st.markdown("### 🧠 Le coach te parle")
    coach_md = build_coach_text(
        month_summary=month_basics,
        month_forecast=month_forecast,
        year_forecast=year_forecast,
        split=s["split"],
        top_alerts=alerts
    )
    st.markdown(coach_md)
