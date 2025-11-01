# ======================================================
#  KPFLO – App Streamlit : interface (revenus, dépenses, budget)
#  Affiche l’UI, orchestre les interactions, s’appuie sur kpflo_core/*
# ======================================================

# --- Standard library (utilitaires légers) ---
from datetime import date  # dates simples pour l’UI et les filtres
from pathlib import Path  # chemins locaux (export/import si besoin)
import hashlib  # hachage basique (ex: clés cache, sécurité légère)

# --- Third-party (framework + data + charts) ---
import streamlit as st  # framework UI
import pandas as pd  # tables de données
import numpy as np  # calculs numériques utilitaires
import plotly.express as px  # graphiques interactifs

# --- Internal modules (kpflo_core) ---
from kpflo_core.budget import (  # calculs budget + projections + textes coach
    compute_summary,
    predict_end_of_month,
    predict_end_of_year,
    build_coach_text,
    savings_projection,
    slice_df_for_month,
    slice_df_for_year,
    compute_totals,
    build_year_timeseries,
    build_coach_text_year,
)

from kpflo_core.categories import (  # référentiels : catégories & validations
    INCOME_CATEGORIES,
    EXPENSE_CATEGORIES,
)

from kpflo_core.revenus import (  # logique revenus + callbacks UI
    get_revenus_df,
    delete_revenu,
    get_revenus_preview_summary,
    update_revenu,
    default_revenu_row,  # ligne par défaut (sans suffixe mois)
    _month_human,  # "October 2025" pour suffixer les libellés
    income_suggestions_for_category,
    rev_update_form,
)

from kpflo_core.depenses import (  # logique dépenses + ratios + callbacks UI
    get_depenses_df,
    save_depenses_edits,
    delete_depense,
    default_depense_row,  # ligne par défaut (sans suffixe mois)
    render_ratio_box,  # box d’indicateur colorée
    expense_suggestions_for_category,
    dep_update_form,
)

from kpflo_core.storage_sqlite import (  # accès DB + export/import
    init_db,
    list_users,
    create_user,
    validate_user,
    ensure_default_user,
    fetch_all_df_with_id,
    insert_transaction,
    _connect,  # usage ponctuel (ex: bulk ops)
    bulk_insert_transactions,
    build_export_or_template_csv_sqlite,
)


# ================================================
#  FONCTIONS PRINCIPALES DU TABLEAU DE BORD
#  (chargement, import et cache des données)
# ================================================


@st.cache_data(show_spinner=False)
def load_user_df(user_id: int):
    """Charge et normalise les données d’un utilisateur."""
    df = fetch_all_df_with_id(user_id)
    return normalize_df(df)


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Nettoie et harmonise les colonnes et les signes."""
    df = df.copy()
    if "type" not in df.columns and "kind" in df.columns:
        df["type"] = np.where(df["kind"].str.lower() == "revenu", "IN", "OUT")
    if "montant" not in df.columns and "amount" in df.columns:
        df["montant"] = df["amount"].astype(float)
    if "categorie" not in df.columns and "category" in df.columns:
        df["categorie"] = df["category"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df.loc[df["type"] == "OUT", "montant"] = -df.loc[
        df["type"] == "OUT", "montant"
    ].abs()
    df.loc[df["type"] == "IN", "montant"] = df.loc[df["type"] == "IN", "montant"].abs()
    return df[["date", "type", "categorie", "montant"]]


def import_unified_csv_to_sqlite(file, user_id: int, replace_all=False, chunksize=5000):
    """Importe un CSV global dans la base SQLite, avec gestion du cache."""
    import pandas as pd, io
    from datetime import datetime

    # Lecture du fichier uploadé
    if hasattr(file, "getvalue"):
        file_buf = io.BytesIO(file.getvalue())
    else:
        file_buf = file

    # Purge des données si remplacement complet
    if replace_all:
        file_buf.seek(0)
        user_ids = set()
        for chunk in pd.read_csv(file_buf, usecols=["user_id"], chunksize=chunksize):
            user_ids.update(chunk["user_id"].astype(int).unique())
        if user_ids:
            with _connect() as con:
                con.execute(
                    f"DELETE FROM transactions WHERE user_id IN ({','.join('?'*len(user_ids))})",
                    list(user_ids),
                )
                con.commit()
        file_buf.seek(0)

    # Insertion des nouvelles lignes
    imported_months = set()
    for chunk in pd.read_csv(file_buf, chunksize=chunksize):
        for col in ["user_id", "date", "kind", "category", "label", "amount"]:
            if col not in chunk.columns:
                chunk[col] = None
        chunk["user_id"] = chunk["user_id"].astype(int, errors="ignore")
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce").dt.date
        chunk["kind"] = chunk["kind"].astype(str).str.lower().str.strip()
        chunk["amount"] = pd.to_numeric(chunk["amount"], errors="coerce").fillna(0.0)

        rows = []
        now_iso = datetime.utcnow().isoformat()
        for _, r in chunk.iterrows():
            if (
                pd.isna(r["date"])
                or r["amount"] == 0.0
                or r["kind"] not in ("revenu", "depense", "epargne")
            ):
                continue
            trx_type = "IN" if r["kind"] == "revenu" else "OUT"
            rows.append(
                (
                    str(r["date"]),
                    trx_type,
                    str(r["category"] or ""),
                    str(r["label"] or ""),
                    float(r["amount"]),
                    0,
                    now_iso,
                    int(r["user_id"]) or user_id,
                )
            )
        bulk_insert_transactions(rows)

    # Rafraîchit la session
    st.session_state.update(
        {
            "last_imported_user_id": user_id,
            "_force_reload": True,
        }
    )
    return True


@st.cache_data(show_spinner=False)
def cached_revenus_df(user_id: int):
    """Charge les revenus avec mise en cache."""
    return get_revenus_df(user_id)


@st.cache_data(show_spinner=False)
def cached_depenses_df(user_id: int):
    """Charge les dépenses avec mise en cache."""
    return get_depenses_df(user_id)


# ================================================
#  CONFIGURATION INITIALE DE L'APPLICATION
# ================================================

# --- Page Streamlit ---
st.set_page_config(page_title="KPFLO", page_icon="logo_kpflo_icon.png", layout="wide")

# --- Base de données ---
init_db()  # Initialise la base SQLite

# --- État global de l'application ---
# Variables persistantes pour la session (connexion + formulaires)
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user_id = None

# Initialisation des formulaires dynamiques
st.session_state.setdefault("revenus_forms", [])
st.session_state.setdefault("depenses_forms", [])


# ================================================
#  HEADER DE L’APPLICATION
# ================================================

# --- Mise en page du header ---
left, right = st.columns([0.55, 0.45], vertical_alignment="center")

with left:
    st.image("logo_kpflo.svg", width=180)
    st.markdown(
        "<p style='font-size:20px; color:gray; margin-top:-10px;'>"
        "Gère ton argent avec style !</p>",
        unsafe_allow_html=True,
    )

# --- Gestion des utilisateurs ---
users_df = list_users()
if users_df.empty:
    ensure_default_user()
    users_df = list_users()

default_user_id = ensure_default_user()
if st.session_state.current_user_id is None:
    st.session_state.current_user_id = default_user_id


with right:
    # ================================================
    #  AVATARS + PROFIL UTILISATEUR (HEADER DROITE)
    # ================================================

    # --- Pillow optionnel (fallback initiales si absent) ---
    try:
        from PIL import Image
    except Exception:
        Image = None

    # --- Répertoire avatars ---
    AVATAR_DIR = Path("data/avatars")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)

    # --- Helpers locaux (liés au rendu header) ---
    def _avatar_path(user_id: int) -> Path:
        """Chemin fichier avatar pour un user."""
        return AVATAR_DIR / f"{int(user_id)}.png"

    def save_avatar(user_id: int, uploaded_file) -> None:
        """Enregistre un avatar carré PNG (crop + resize)."""
        if uploaded_file is None or Image is None:
            return
        img = Image.open(uploaded_file).convert("RGB")
        s = min(img.width, img.height)
        img_sq = img.crop(
            (
                (img.width - s) // 2,
                (img.height - s) // 2,
                (img.width + s) // 2,
                (img.height + s) // 2,
            )
        ).resize((256, 256))
        img_sq.save(_avatar_path(user_id), "PNG", optimize=True)

    def avatar_src(user_id: int) -> str | None:
        """Retourne le chemin de l’avatar s’il existe."""
        p = _avatar_path(user_id)
        return str(p) if p.exists() else None

    # --- Style avatars ---
    st.markdown(
        """
        <style>
        .kpflo-avatar   { width:40px;height:40px;border-radius:50%;object-fit:cover; }
        .kpflo-avatarLG { width:64px;height:64px;border-radius:50%;object-fit:cover; }
        .kpflo-initial  { width:40px;height:40px;border-radius:50%;background:#eee;display:flex;align-items:center;justify-content:center;font-weight:600; }
        .kpflo-initialLG{ width:64px;height:64px;border-radius:50%;background:#eee;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1.1rem; }
        .kpflo-row { display:flex;align-items:center;gap:.6rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- Profil courant ---
    cur_row = users_df[users_df["id"] == st.session_state.current_user_id].iloc[0]
    cur_fullname = (
        f'{cur_row.get("prenom","")} {cur_row["nom"]}'
    ).strip() or f'ID {cur_row["id"]}'
    cur_avatar = avatar_src(int(cur_row["id"]))

    # --- Bandeau avatar + popover switch ---
    cols_hdr = st.columns([0.2, 0.15, 0.65], vertical_alignment="center")
    with cols_hdr[0]:
        if cur_avatar:
            st.image(cur_avatar, width=40)
        else:
            st.markdown(
                f"<div class='kpflo-initial'>{(cur_fullname[:1] or '🙂')}</div>",
                unsafe_allow_html=True,
            )

    with cols_hdr[1]:
        with st.popover("▼"):
            st.markdown("**Profil courant**")
            c1, c2 = st.columns([0.3, 0.7], vertical_alignment="center")
            with c1:
                if cur_avatar:
                    st.image(cur_avatar, width=64)
                else:
                    st.markdown(
                        f"<div class='kpflo-initialLG'>{(cur_fullname[:1] or '🙂')}</div>",
                        unsafe_allow_html=True,
                    )
            with c2:
                st.markdown(
                    f"**{cur_fullname}**<br/><span style='opacity:.7;'>id:{cur_row['id']}</span>",
                    unsafe_allow_html=True,
                )

            st.divider()
            st.markdown("**Autres profils**")
            for _, row in users_df.iterrows():
                rid = int(row["id"])
                if rid == int(cur_row["id"]):
                    continue
                name = (f'{row.get("prenom","")} {row["nom"]}').strip() or f"ID {rid}"
                ava = avatar_src(rid)

                r1, r2, r3 = st.columns([0.25, 0.55, 0.20], vertical_alignment="center")
                with r1:
                    if ava:
                        st.image(ava, width=40)
                    else:
                        st.markdown(
                            f"<div class='kpflo-initial'>{(name[:1] or '🙂')}</div>",
                            unsafe_allow_html=True,
                        )
                with r2:
                    st.write(name)
                with r3:
                    if st.button("Basculer", key=f"switch_{rid}"):
                        st.session_state.current_user_id = rid
                        st.rerun()

            st.caption("Crée plusieurs profils (vert/rouge/limite) puis bascule ici.")

    # --- Popover authentification + création + upload avatar ---
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
            new_prenom = st.text_input(
                "Prénom", placeholder="Ex: Jean", key="new_prenom"
            )
            new_email = st.text_input(
                "Email", placeholder="Ex: jean@dupont.fr", key="new_email"
            )
            new_pw = st.text_input("Mot de passe", type="password", key="new_pw")
            new_pw_confirm = st.text_input(
                "Confirmer mot de passe", type="password", key="new_pw_confirm"
            )

            new_avatar = st.file_uploader(
                "Photo de profil (PNG/JPG, optionnel)", type=["png", "jpg", "jpeg"]
            )

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


# ================================================
#  CHARGEMENT DES DONNÉES UTILISATEUR
# ================================================
# Charge le DataFrame de l'utilisateur courant et le stocke en session.

st.session_state.df = load_user_df(st.session_state.current_user_id)

# ================================================
#  SIDEBAR : IMPORT / EXPORT CSV
# ================================================

st.sidebar.subheader("📦 Données (CSV) – Base SQLite")

# Export (ou modèle 36 mois si DB vide)
csv_bytes = build_export_or_template_csv_sqlite(months=36)
st.sidebar.download_button(
    label="⬇️ Télécharger CSV",
    data=csv_bytes,
    file_name="kpflo_finances.csv",
    mime="text/csv",
    help="Modèle ou export complet (tous utilisateurs).",
)

st.sidebar.markdown("—")
st.sidebar.markdown("**📥 Importer un CSV**")

# Formulaire d'import (anti double-import via hash)
with st.sidebar.form("csv_import_form", clear_on_submit=False):
    replace_all = st.checkbox(
        "Remplacer toutes les transactions des utilisateurs présents dans le CSV",
        value=False,
    )
    uploaded = st.file_uploader(
        "CSV (user_id,date,kind,category,label,amount)",
        type=["csv"],
        key="csv_upload",
    )
    submitted = st.form_submit_button("Importer")

if submitted:
    if uploaded is None:
        st.sidebar.error("Aucun fichier sélectionné.")
    else:
        content = uploaded.getvalue()
        file_hash = hashlib.md5(content).hexdigest()
        if st.session_state.get("last_import_hash") == file_hash:
            st.sidebar.info("Ce fichier a déjà été importé.")
        else:
            ok = import_unified_csv_to_sqlite(
                uploaded,
                st.session_state.current_user_id,
                replace_all=replace_all,
            )
            if ok:
                st.session_state["last_import_hash"] = file_hash
                st.sidebar.success("Import réussi ✅")
                st.rerun()
            else:
                st.sidebar.error("Import échoué. Vérifie les colonnes.")


# ================================================
#  INTERFACE PRINCIPALE : ONGLET REVENUS / DÉPENSES / BUDGET
# ================================================
# Crée les trois onglets principaux du tableau de bord.

tab_revenus, tab_depenses, tab_budget = st.tabs(
    ["📈 Revenus", "📉 Dépenses", "📊 Budget"]
)


# ================================================
#  SECTION : REVENUS  (Interface et style)
# ================================================
with tab_revenus:
    st.subheader("Ajoute tes revenus")

    # --- Style local de la section ---
    st.markdown(
        """
        <style>
          :root { --primary-color: #f97316; }
          #revenus-header { margin-bottom: 12px; }
          #revenus-header div[data-baseweb="select"]{
            width:220px;border:2px solid #d1d5db;border-radius:6px;background:#fff;transition:.2s;
          }
          #revenus-header div[data-baseweb="select"]:hover{
            border-color:#f97316;box-shadow:0 0 5px rgba(249,115,22,.3);
          }
          #revenus-header div[data-baseweb="select"] > div{
            font-size:.95rem;color:#333;font-weight:500;
          }
          .revenus-mini-wrap { display:flex;justify-content:flex-end; }
          .revenus-mini {
            display:inline-flex;align-items:baseline;gap:12px;padding:10px 14px;
            border:2px solid #d1d5db;border-radius:10px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.04);
          }
          .revenus-mini .mini-title{font-weight:600;color:#374151;}
          .revenus-mini .mini-total{font-variant-numeric:tabular-nums;font-weight:700;color:#111827;}
          .revenus-mini .mini-euro{opacity:.85;}
          .revenus-mini .mini-badge{margin-left:6px;font-size:12px;padding:2px 6px;border-radius:999px;background:#f3f4f6;color:#6b7280;}
          hr { border-top:1px solid #e0e0e0 !important;margin:4px 0 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- Header : sélecteur groupe + total preview ---
    st.markdown('<div id="revenus-header">', unsafe_allow_html=True)
    header_left, header_right = st.columns([1, 5], vertical_alignment="center")

    with header_left:
        group_mode = st.selectbox(
            "🗓️ Grouper par",
            ["Mois", "Année"],
            key="revenus_group_mode",
            index=0,
            label_visibility="visible",
        )

    # Recharge pour un total à jour (inclut les lignes en édition)
    st.session_state.df = load_user_df(st.session_state.current_user_id)
    total_preview = get_revenus_preview_summary(
        st.session_state.current_user_id, st.session_state.revenus_forms
    )

    with header_right:
        st.markdown(
            f"""
            <div class="revenus-mini-wrap">
              <div class="revenus-mini" title="Somme des revenus affichés (prévisualisation)">
                <span class="mini-title">Revenus enregistrés</span>
                <span class="mini-total">{total_preview:.2f}</span>
                <span class="mini-euro">€</span>
                <span class="mini-badge">Total</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    # --- Données Revenus ---
    df_revenus_all = cached_revenus_df(st.session_state.current_user_id)

    # -------- Mode "Mois" : mois -> lignes --------
    if group_mode == "Mois" and not df_revenus_all.empty:
        df = df_revenus_all.copy()
        dt = pd.to_datetime(df["date"])
        df["month_key"] = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        grouped = (
            df.groupby("month_key")
            .agg(
                total_montant=("montant", "sum"),
                month_name=("month_name", "first"),
                count=("id", "count"),
            )
            .reset_index()
            .sort_values("month_key", ascending=False)
        )

        for _, g in grouped.iterrows():
            m_key, m_name = g["month_key"], g["month_name"]
            m_total, m_count = float(g["total_montant"]), int(g["count"])
            display_name = f"Revenus_{m_name}"

            with st.expander(f"{display_name} ({m_total:.2f} €) - {m_count} lignes"):
                m_df = (
                    df[df["month_key"] == m_key]
                    .sort_values("date", ascending=False)
                    .reset_index(drop=True)
                )
                m_df["row_num"] = m_df.index + 1

                display_df = m_df[
                    ["row_num", "date", "categorie", "libelle", "montant"]
                ].copy()
                display_df["montant"] = display_df["montant"].map(
                    lambda x: f"{float(x):.2f} €"
                )
                display_df.columns = [
                    "Ligne #",
                    "Date",
                    "Catégorie",
                    "Libellé",
                    "Montant",
                ]

                st.dataframe(
                    display_df, hide_index=True, use_container_width=True, height=300
                )

                # Actions ligne existante
                st.markdown("#### Gérer une ligne existante")
                selected_rownum = st.text_input(
                    "Numéro de ligne à modifier / supprimer",
                    key=f"rev_manage_row_{m_key}",
                    placeholder="ex: 1",
                )
                action_col1, action_col2 = st.columns(2)

                with action_col1:
                    if st.button(
                        "❌ Supprimer cette ligne",
                        key=f"rev_delete_{m_key}",
                        type="secondary",
                        disabled=(selected_rownum.strip() == ""),
                    ):
                        try:
                            rownum_int = int(selected_rownum)
                            row_match = m_df[m_df["row_num"] == rownum_int]
                            if row_match.empty:
                                st.warning("Numéro de ligne invalide.")
                            else:
                                real_id = int(row_match.iloc[0]["id"])
                                delete_revenu(real_id, st.session_state.current_user_id)
                                cached_revenus_df.clear()
                                cached_depenses_df.clear()
                                load_user_df.clear()
                                st.session_state.df = load_user_df(
                                    st.session_state.current_user_id
                                )
                                st.success(f"Ligne {rownum_int} supprimée.")
                                st.rerun()
                        except Exception as e:
                            st.error(f"Erreur suppression : {e}")

                with action_col2:
                    if st.button(
                        "✏️ Charger pour édition",
                        key=f"rev_load_{m_key}",
                        disabled=(selected_rownum.strip() == ""),
                    ):
                        try:
                            rownum_int = int(selected_rownum)
                            row_match = m_df[m_df["row_num"] == rownum_int]
                            if row_match.empty:
                                st.warning("Numéro de ligne invalide.")
                            else:
                                r = row_match.iloc[0]
                                st.session_state.revenus_forms.append(
                                    {
                                        "id": int(r["id"]),
                                        "date": r["date"],
                                        "categorie": r["categorie"],
                                        "libelle": r["libelle"],
                                        "montant": float(r["montant"]),
                                    }
                                )
                                st.info(
                                    f"Ligne {rownum_int} chargée en bas pour édition."
                                )
                                st.rerun()
                        except Exception as e:
                            st.error(f"Erreur chargement : {e}")

                st.caption("⚠️ Entrez le numéro de ligne pour modifier ou supprimer.")

    # -------- Mode "Année" : année -> mois -> lignes --------
    if group_mode == "Année" and not df_revenus_all.empty:
        df = df_revenus_all.copy()
        dt = pd.to_datetime(df["date"])
        df["year"] = dt.dt.year
        df["month_key"] = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        year_groups = (
            df.groupby("year")
            .agg(total_montant=("montant", "sum"))
            .reset_index()
            .sort_values("year", ascending=False)
        )

        for _, yg in year_groups.iterrows():
            y, y_total = int(yg["year"]), float(yg["total_montant"])
            year_display_name = f"Revenus_{y}"

            with st.expander(f"{year_display_name} ({y_total:.2f} €)"):
                y_df = df[df["year"] == y]
                month_groups = (
                    y_df.groupby("month_key")
                    .agg(
                        total_montant=("montant", "sum"),
                        month_name=("month_name", "first"),
                        count=("id", "count"),
                    )
                    .reset_index()
                    .sort_values("month_key", ascending=False)
                )

                for _, mg in month_groups.iterrows():
                    m_key, m_name = mg["month_key"], mg["month_name"]
                    m_total, m_count = float(mg["total_montant"]), int(mg["count"])
                    display_name = f"Revenus_{m_name}"

                    with st.expander(
                        f"{display_name} ({m_total:.2f} €) - {m_count} lignes"
                    ):
                        m_df = (
                            y_df[y_df["month_key"] == m_key]
                            .sort_values("date", ascending=False)
                            .reset_index(drop=True)
                        )
                        m_df["row_num"] = m_df.index + 1

                        display_df = m_df[
                            ["row_num", "date", "categorie", "libelle", "montant"]
                        ].copy()
                        display_df["montant"] = display_df["montant"].map(
                            lambda x: f"{float(x):.2f} €"
                        )
                        display_df.columns = [
                            "Ligne #",
                            "Date",
                            "Catégorie",
                            "Libellé",
                            "Montant",
                        ]

                        st.dataframe(
                            display_df,
                            hide_index=True,
                            use_container_width=True,
                            height=300,
                        )

                        st.markdown("#### Gérer une ligne existante")
                        selected_rownum = st.text_input(
                            "Numéro de ligne à modifier / supprimer",
                            key=f"rev_manage_row_{y}_{m_key}",
                            placeholder="ex: 1",
                        )
                        action_col1, action_col2 = st.columns(2)

                        with action_col1:
                            if st.button(
                                "❌ Supprimer cette ligne",
                                key=f"rev_delete_{y}_{m_key}",
                                type="secondary",
                                disabled=(selected_rownum.strip() == ""),
                            ):
                                try:
                                    rownum_int = int(selected_rownum)
                                    row_match = m_df[m_df["row_num"] == rownum_int]
                                    if row_match.empty:
                                        st.warning("Numéro de ligne invalide.")
                                    else:
                                        real_id = int(row_match.iloc[0]["id"])
                                        delete_revenu(
                                            real_id, st.session_state.current_user_id
                                        )
                                        cached_revenus_df.clear()
                                        cached_depenses_df.clear()
                                        load_user_df.clear()
                                        st.session_state.df = load_user_df(
                                            st.session_state.current_user_id
                                        )
                                        st.success(f"Ligne {rownum_int} supprimée.")
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"Erreur suppression : {e}")

                        with action_col2:
                            if st.button(
                                "✏️ Charger pour édition",
                                key=f"rev_load_{y}_{m_key}",
                                disabled=(selected_rownum.strip() == ""),
                            ):
                                try:
                                    rownum_int = int(selected_rownum)
                                    row_match = m_df[m_df["row_num"] == rownum_int]
                                    if row_match.empty:
                                        st.warning("Numéro de ligne invalide.")
                                    else:
                                        r = row_match.iloc[0]
                                        st.session_state.revenus_forms.append(
                                            {
                                                "id": int(r["id"]),
                                                "date": r["date"],
                                                "categorie": r["categorie"],
                                                "libelle": r["libelle"],
                                                "montant": float(r["montant"]),
                                            }
                                        )
                                        st.info(
                                            f"Ligne {rownum_int} chargée en bas pour édition."
                                        )
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"Erreur chargement : {e}")

                        st.caption(
                            "⚠️ Entrez le numéro de ligne pour modifier ou supprimer."
                        )

    # -------- Saisie dynamique (nouvelles lignes / édition) --------
    edited_rows = []
    if st.session_state.revenus_forms:
        st.markdown("### Saisie en cours (nouvelle série ou édition)")
        header_cols = st.columns([1, 2, 2.6, 1, 0.6])
        header_cols[0].markdown("**Date**")
        header_cols[1].markdown("**Catégorie**")
        header_cols[2].markdown("**Libellé**")
        header_cols[3].markdown("**Montant (€)**")

    for i, form in enumerate(st.session_state.revenus_forms):
        key_date, key_cat = f"rev_date_{i}", f"rev_cat_{i}"
        key_lib_choice, key_lib_value = f"rev_lib_choice_{i}", f"rev_lib_value_{i}"
        key_montant = f"rev_montant_{i}"

        init_date = form.get("date", date.today())
        init_cat = form.get("categorie", INCOME_CATEGORIES[0])
        init_amt = form.get("montant", 0.0)
        init_cat_idx = (
            INCOME_CATEGORIES.index(init_cat) if init_cat in INCOME_CATEGORIES else 0
        )

        init_opts = income_suggestions_for_category(init_cat, init_date)
        init_lib = form.get("libelle") or (init_opts[0] if init_opts else "Revenu")
        init_lib_idx = init_opts.index(init_lib) if init_lib in init_opts else 0

        cols = st.columns([1, 2, 2.6, 1, 0.6])

        with cols[0]:
            st.date_input(
                "",
                value=init_date,
                key=key_date,
                on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden",
            )
        with cols[1]:
            st.selectbox(
                "",
                options=INCOME_CATEGORIES,
                index=init_cat_idx,
                key=key_cat,
                on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden",
            )
        with cols[2]:
            opts = income_suggestions_for_category(
                st.session_state[key_cat], st.session_state[key_date]
            )
            st.selectbox(
                "",
                options=opts,
                index=init_lib_idx,
                key=key_lib_choice,
                on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden",
            )
            st.session_state[key_lib_value] = st.session_state.get(
                key_lib_choice, init_lib
            )
        with cols[3]:
            st.number_input(
                "",
                min_value=0.0,
                step=10.0,
                value=float(init_amt),
                key=key_montant,
                on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden",
            )
        with cols[4]:
            if st.button(
                "❌",
                key=f"del_form_{i}",
                type="secondary",
                help="Supprimer cette ligne",
            ):
                new_forms = st.session_state.revenus_forms.copy()
                new_forms.pop(i)
                st.session_state.revenus_forms = new_forms
                st.rerun()

        edited_rows.append(
            {
                "id": form.get("id"),
                "date": st.session_state[key_date],
                "categorie": st.session_state[key_cat],
                "libelle": st.session_state[key_lib_value],
                "montant": st.session_state[key_montant],
            }
        )

    # --- Actions bas de section ---
    btn_left, btn_right = st.columns([3, 1])

    with btn_left:
        st.button(
            "Nouvelle ligne",
            key="add_revenu",
            type="primary",
            help="Ajoute une nouvelle ligne de saisie de revenu",
        )
        if st.session_state.get("add_revenu"):
            new_forms = st.session_state.revenus_forms.copy()
            today = date.today()
            base = default_revenu_row(INCOME_CATEGORIES, today)
            mois = _month_human(today)
            base["libelle"] = f"{base['libelle']}_{mois}" if mois else base["libelle"]
            new_forms.append(base)
            st.session_state.revenus_forms = new_forms
            st.rerun()

    with btn_right:
        if edited_rows and st.button(
            "Enregistrer revenus", type="primary", use_container_width=True
        ):
            try:
                for row in edited_rows:
                    if row.get("id"):
                        update_revenu(row["id"], row, st.session_state.current_user_id)
                    else:
                        insert_transaction(
                            row["date"],
                            "IN",
                            row["categorie"],
                            row["libelle"],
                            float(row["montant"]),
                            recurrent=False,
                            user_id=st.session_state.current_user_id,
                        )
                st.session_state.revenus_forms = []  # reset formulaire
                cached_revenus_df.clear()
                cached_depenses_df.clear()
                load_user_df.clear()
                st.session_state.df = load_user_df(st.session_state.current_user_id)
                st.balloons()
                st.success("Revenus enregistrés !")
            except Exception as e:
                st.error(f"Erreur : {e}")
            st.rerun()


# =======================
# SECTION : DÉPENSES 🧾
# =======================
with tab_depenses:
    st.subheader("Ajoute tes dépenses")

    # --- CSS local (style des contrôles, mini-carte, boîtes de ratio) ---
    st.markdown(
        """
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
    """,
        unsafe_allow_html=True,
    )

    # --- Header (sélecteur + mini-total) ---
    st.markdown('<div id="depenses-header">', unsafe_allow_html=True)
    header_left, header_right = st.columns([1, 5], vertical_alignment="center")

    with header_left:
        dep_group_mode = st.selectbox(
            "🗓️ Grouper par",
            ["Mois", "Année"],
            key="depenses_group_mode_v3",
            index=0,
            label_visibility="visible",
        )

    df_depenses_all = cached_depenses_df(st.session_state.current_user_id)
    total_depenses_preview = (
        float(df_depenses_all["montant"].sum()) if not df_depenses_all.empty else 0.0
    )

    with header_right:
        st.markdown(
            f"""<div class="depenses-mini-wrap">
                  <div class="depenses-mini" title="Somme des dépenses affichées (prévisualisation)">
                    <span class="mini-title">Dépenses enregistrées</span>
                    <span class="mini-total">{total_depenses_preview:.2f}</span>
                    <span class="mini-euro">€</span>
                  </div>
                </div>""",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    revenus_df_all = cached_revenus_df(st.session_state.current_user_id)

    # --- Vue Mois : ratios par catégorie + lignes du mois + actions ---
    if dep_group_mode == "Mois" and not df_depenses_all.empty:
        df = df_depenses_all.copy()
        dt = pd.to_datetime(df["date"])
        df["month_key"] = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        grouped = (
            df.groupby("month_key")
            .agg(
                total_montant=("montant", "sum"),
                month_name=("month_name", "first"),
                count=("id", "count"),
            )
            .reset_index()
            .sort_values("month_key", ascending=False)
        )

        for _, g in grouped.iterrows():
            m_key, m_name = g["month_key"], g["month_name"]
            m_total, m_count = float(g["total_montant"]), int(g["count"])
            display_name = f"Dépenses_{m_name}"

            with st.expander(
                f"{display_name} ({m_total:.2f} €) - {m_count} lignes", expanded=False
            ):
                # Ratios par catégorie (base: revenu NET du mois)
                m_df = df[df["month_key"] == m_key]
                for cat in m_df["categorie"].unique():
                    cat_total = float(m_df[m_df["categorie"] == cat]["montant"].sum())
                    st.markdown(f"<b>{cat}</b>:", unsafe_allow_html=True)
                    render_ratio_box(
                        m_df["date"].iloc[0],
                        cat,
                        cat_total,
                        revenus_df_all,
                        df_depenses_all,
                    )

                # Tableau des lignes
                m_df = (
                    df[df["month_key"] == m_key]
                    .sort_values("date", ascending=False)
                    .copy()
                ).reset_index(drop=True)
                m_df["row_num"] = m_df.index + 1

                display_df = m_df[
                    ["row_num", "date", "categorie", "libelle", "montant"]
                ].copy()
                display_df["montant"] = display_df["montant"].map(
                    lambda x: f"{float(x):.2f} €"
                )
                display_df.columns = [
                    "Ligne #",
                    "Date",
                    "Catégorie",
                    "Libellé",
                    "Montant (€)",
                ]

                st.dataframe(
                    display_df, hide_index=True, use_container_width=True, height=300
                )

                # Actions sur ligne existante (delete / charger pour édition)
                st.markdown("#### Gérer une ligne existante")
                selected_rownum = st.text_input(
                    "Numéro de ligne à modifier / supprimer",
                    key=f"dep_manage_row_{m_key}",
                    placeholder="ex: 1",
                )
                c1, c2 = st.columns(2)

                with c1:
                    if st.button(
                        "❌ Supprimer cette ligne",
                        key=f"dep_delete_{m_key}",
                        type="secondary",
                        disabled=(selected_rownum.strip() == ""),
                    ):
                        try:
                            rownum_int = int(selected_rownum)
                            row_match = m_df[m_df["row_num"] == rownum_int]
                            if row_match.empty:
                                st.warning("Numéro de ligne invalide.")
                            else:
                                real_id = int(row_match.iloc[0]["id"])
                                delete_depense(
                                    real_id, st.session_state.current_user_id
                                )
                                cached_revenus_df.clear()
                                cached_depenses_df.clear()
                                load_user_df.clear()
                                st.session_state.df = load_user_df(
                                    st.session_state.current_user_id
                                )
                                st.success(f"Ligne {rownum_int} supprimée.")
                                st.rerun()
                        except Exception as e:
                            st.error(f"Erreur suppression : {e}")

                with c2:
                    if st.button(
                        "✏️ Charger pour édition",
                        key=f"dep_load_{m_key}",
                        disabled=(selected_rownum.strip() == ""),
                    ):
                        try:
                            rownum_int = int(selected_rownum)
                            row_match = m_df[m_df["row_num"] == rownum_int]
                            if row_match.empty:
                                st.warning("Numéro de ligne invalide.")
                            else:
                                r = row_match.iloc[0]
                                st.session_state.depenses_forms.append(
                                    {
                                        "id": int(r["id"]),
                                        "date": r["date"],
                                        "categorie": r["categorie"],
                                        "libelle": r["libelle"],
                                        "montant": float(r["montant"]),
                                    }
                                )
                                st.info(
                                    f"Ligne {rownum_int} chargée en bas pour édition."
                                )
                                st.rerun()
                        except Exception as e:
                            st.error(f"Erreur chargement : {e}")

                st.caption(
                    "Vous pouvez modifier ou supprimer une ligne en indiquant son numéro."
                )

    # --- Vue Année : année -> mois -> lignes + actions ---
    if dep_group_mode == "Année" and not df_depenses_all.empty:
        df = df_depenses_all.copy()
        dt = pd.to_datetime(df["date"])
        df["year"] = dt.dt.year
        df["month_key"] = dt.dt.strftime("%Y-%m")
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
                    .agg(
                        total_montant=("montant", "sum"),
                        month_name=("month_name", "first"),
                        count=("id", "count"),
                    )
                    .reset_index()
                    .sort_values("month_key", ascending=False)
                )

                for _, mg in month_groups.iterrows():
                    m_key, m_name = mg["month_key"], mg["month_name"]
                    m_total, m_count = float(mg["total_montant"]), int(mg["count"])
                    display_name = f"Dépenses_{m_name}"

                    with st.expander(
                        f"{display_name} ({m_total:.2f} €) - {m_count} lignes",
                        expanded=False,
                    ):
                        # Ratios par catégorie
                        m_df = y_df[y_df["month_key"] == m_key]
                        for cat in m_df["categorie"].unique():
                            cat_total = float(
                                m_df[m_df["categorie"] == cat]["montant"].sum()
                            )
                            st.markdown(f"<b>{cat}</b>:", unsafe_allow_html=True)
                            render_ratio_box(
                                m_df["date"].iloc[0],
                                cat,
                                cat_total,
                                revenus_df_all,
                                df_depenses_all,
                            )

                        # Tableau des lignes
                        m_df = (
                            y_df[y_df["month_key"] == m_key]
                            .sort_values("date", ascending=False)
                            .copy()
                        ).reset_index(drop=True)
                        m_df["row_num"] = m_df.index + 1

                        display_df = m_df[
                            ["row_num", "date", "categorie", "libelle", "montant"]
                        ].copy()
                        display_df["montant"] = display_df["montant"].map(
                            lambda x: f"{float(x):.2f} €"
                        )
                        display_df.columns = [
                            "Ligne #",
                            "Date",
                            "Catégorie",
                            "Libellé",
                            "Montant (€)",
                        ]

                        st.dataframe(
                            display_df,
                            hide_index=True,
                            use_container_width=True,
                            height=300,
                        )

                        # Actions
                        st.markdown("#### Gérer une ligne existante")
                        selected_rownum = st.text_input(
                            "Numéro de ligne à modifier / supprimer",
                            key=f"dep_manage_row_{y}_{m_key}",
                            placeholder="ex: 1",
                        )
                        c1, c2 = st.columns(2)

                        with c1:
                            if st.button(
                                "❌ Supprimer cette ligne",
                                key=f"dep_delete_{y}_{m_key}",
                                type="secondary",
                                disabled=(selected_rownum.strip() == ""),
                            ):
                                try:
                                    rownum_int = int(selected_rownum)
                                    row_match = m_df[m_df["row_num"] == rownum_int]
                                    if row_match.empty:
                                        st.warning("Numéro de ligne invalide.")
                                    else:
                                        real_id = int(row_match.iloc[0]["id"])
                                        delete_depense(
                                            real_id, st.session_state.current_user_id
                                        )
                                        cached_revenus_df.clear()
                                        cached_depenses_df.clear()
                                        load_user_df.clear()
                                        st.session_state.df = load_user_df(
                                            st.session_state.current_user_id
                                        )
                                        st.success(f"Ligne {rownum_int} supprimée.")
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"Erreur suppression : {e}")

                        with c2:
                            if st.button(
                                "✏️ Charger pour édition",
                                key=f"dep_load_{y}_{m_key}",
                                disabled=(selected_rownum.strip() == ""),
                            ):
                                try:
                                    rownum_int = int(selected_rownum)
                                    row_match = m_df[m_df["row_num"] == rownum_int]
                                    if row_match.empty:
                                        st.warning("Numéro de ligne invalide.")
                                    else:
                                        r = row_match.iloc[0]
                                        st.session_state.depenses_forms.append(
                                            {
                                                "id": int(r["id"]),
                                                "date": r["date"],
                                                "categorie": r["categorie"],
                                                "libelle": r["libelle"],
                                                "montant": float(r["montant"]),
                                            }
                                        )
                                        st.info(
                                            f"Ligne {rownum_int} chargée en bas pour édition."
                                        )
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"Erreur chargement : {e}")

                        st.caption(
                            "Vous pouvez modifier ou supprimer une ligne en indiquant son numéro."
                        )

    # --- Formulaires dynamiques (édition / nouvelles lignes) ---
    edited_rows_dep = []
    for i, form in enumerate(st.session_state.depenses_forms):
        key_date = f"dep_date_{i}"
        key_cat = f"dep_cat_{i}"
        key_lib_choice = f"dep_lib_choice_{i}"
        key_lib_value = f"dep_lib_value_{i}"
        key_amt = f"dep_montant_{i}"

        # Valeurs initiales + index
        initial_date = form.get("date", date.today())
        initial_cat = form.get("categorie", EXPENSE_CATEGORIES[0])
        initial_amt = form.get("montant", 0.0)
        initial_cat_index = (
            EXPENSE_CATEGORIES.index(initial_cat)
            if initial_cat in EXPENSE_CATEGORIES
            else 0
        )

        init_opts = expense_suggestions_for_category(initial_cat, initial_date)
        initial_lib = form.get("libelle", init_opts[0] if init_opts else "Dépense")
        if initial_lib not in init_opts and init_opts:
            initial_lib = init_opts[0]
        initial_lib_index = (
            init_opts.index(initial_lib) if initial_lib in init_opts else 0
        )

        # Saisie
        cols = st.columns([1, 2, 2.6, 1.2, 1.2, 0.6])

        with cols[0]:
            st.date_input(
                "Date",
                value=initial_date,
                key=key_date,
                on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt),
            )
        with cols[1]:
            st.selectbox(
                "Catégorie",
                options=EXPENSE_CATEGORIES,
                index=initial_cat_index,
                key=key_cat,
                on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt),
            )
        with cols[2]:
            st.selectbox(
                "Libellé",
                options=init_opts,
                index=initial_lib_index,
                key=key_lib_choice,
                on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt),
            )
            st.session_state[key_lib_value] = st.session_state.get(
                key_lib_choice, initial_lib
            )
        with cols[3]:
            st.number_input(
                "Montant (€)",
                min_value=0.0,
                step=5.0,
                value=float(initial_amt),
                key=key_amt,
                on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt),
            )
        with cols[4]:
            cur_date = st.session_state.get(key_date, initial_date)
            cur_cat = st.session_state.get(key_cat, initial_cat)
            cur_amt = float(st.session_state.get(key_amt, initial_amt))
            render_ratio_box(
                cur_date, cur_cat, cur_amt, revenus_df_all, df_depenses_all
            )
        with cols[5]:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            if st.button(
                "❌",
                key=f"del_dep_form_{i}",
                type="secondary",
                help="Supprimer cette ligne",
                use_container_width=True,
            ):
                st.session_state.depenses_forms.pop(i)
                for j in range(i + 1, len(st.session_state.depenses_forms) + 1):
                    for k in [
                        f"dep_date_{j}",
                        f"dep_cat_{j}",
                        f"dep_lib_choice_{j}",
                        f"dep_lib_value_{j}",
                        f"dep_montant_{j}",
                    ]:
                        st.session_state.pop(k, None)
                st.rerun()

        edited_rows_dep.append(
            {
                "id": form.get("id"),
                "date": st.session_state.get(key_date, initial_date),
                "categorie": st.session_state.get(key_cat, initial_cat),
                "libelle": st.session_state.get(key_lib_value, initial_lib),
                "montant": st.session_state.get(key_amt, initial_amt),
            }
        )

    st.session_state.depenses_forms = edited_rows_dep

    # --- Actions bas de section (ajout / sauvegarde) ---
    btn_left, btn_right = st.columns([3, 1])

    with btn_left:
        st.button("➕ Nouvelle dépense", key="add_depense", type="primary")
        if st.session_state.get("add_depense"):
            today = date.today()
            base = default_depense_row(EXPENSE_CATEGORIES, today)
            mois = _month_human(today)
            base["libelle"] = (
                f"{base['libelle']}_{mois}"
                if mois and "_" not in base["libelle"]
                else base["libelle"]
            )
            st.session_state.depenses_forms.append(base)
            st.rerun()

    with btn_right:
        if edited_rows_dep and st.button(
            "💾 Enregistrer dépenses", type="primary", use_container_width=True
        ):
            try:
                save_depenses_edits(edited_rows_dep, st.session_state.current_user_id)
                st.session_state.depenses_forms = []
                cached_revenus_df.clear()
                cached_depenses_df.clear()
                load_user_df.clear()
                st.session_state.df = load_user_df(st.session_state.current_user_id)
                st.success("Dépenses enregistrées !")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")


# ================================================
#  BUDGET / DASHBOARD
# ================================================
with tab_budget:

    # --- Mini-agrégateur local (mois courant) ---
    def compute_month_basics(df: pd.DataFrame) -> dict:
        if df.empty:
            return {"revenus": 0.0, "depenses": 0.0, "solde": 0.0}
        revenus = float(df.loc[df["type"] == "IN", "montant"].sum())
        depenses = float(-df.loc[df["type"] == "OUT", "montant"].sum())
        return {"revenus": revenus, "depenses": depenses, "solde": revenus - depenses}

    # --- Styles (cartes, alertes, entête) ---
    st.markdown(
        """
        <style>
        .budget-card { border-radius: 10px; border: 1.5px solid #d1d5db; padding: 12px 16px; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.03); font-family: system-ui; }
        .budget-card.ok { background: #ecfdf5; border-color: #34d399; color: #065f46; }
        .budget-card.warn { background: #fff7ed; border-color: #fb923c; color: #7c2d12; }
        .budget-card.bad { background: #fef2f2; border-color: #f87171; color: #7f1d1d; }
        .budget-card .subline { font-size: 0.8rem; font-weight: 500; opacity: 0.8; margin-top: 4px; }
        .alert-row { border-radius:8px; border:1.5px solid #d1d5db; padding:8px 12px; box-shadow:0 1px 2px rgba(0,0,0,0.03); font-size:0.9rem; margin-bottom:8px; background:#fff; color:#374151; }
        .alert-row.spend { border-color:#fb923c; background:#fff7ed; color:#7c2d12; }
        .header-stat-card { border: 1.5px solid #d1d5db; background: #fff; border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.03); padding: 8px 12px; min-width: 120px; text-align: center; font-family: system-ui; }
        .header-stat-label { font-size: 0.7rem; font-weight: 600; color: #6b7280; margin-bottom: 4px; }
        .header-stat-value { font-size: 1rem; font-weight: 700; }
        .header-solde-note-inline { font-size: 0.65rem; font-weight: 500; color: #9ca3af; margin-left: 4px; white-space: nowrap; }
        .header-wrapper { display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between; row-gap:12px; column-gap:16px; margin-bottom:12px; padding-bottom:12px; border-bottom:1px solid #e5e7eb; }
        .title-nav-container { display: flex; align-items: center; justify-content: center; gap: 4px; margin-bottom: 8px; }
        .solde-green { color: #065f46; } .solde-blue { color: #111827; } .solde-orange { color: #9a3412; } .solde-red { color: #7f1d1d; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- State (vue + période sélectionnée) ---
    if "budget_view_mode" not in st.session_state:
        st.session_state.budget_view_mode = "mois"
    if "budget_selected_month_key" not in st.session_state:
        st.session_state.budget_selected_month_key = None
    if "budget_selected_year" not in st.session_state:
        st.session_state.budget_selected_year = None

    # --- Prépare les périodes disponibles ---
    df_all_dates = st.session_state.df.copy()
    df_all_dates["date"] = pd.to_datetime(df_all_dates["date"], errors="coerce")
    df_all_dates = df_all_dates.dropna(subset=["date"])

    MOIS_FR_LONG = {
        1: "Janvier",
        2: "Février",
        3: "Mars",
        4: "Avril",
        5: "Mai",
        6: "Juin",
        7: "Juillet",
        8: "Août",
        9: "Septembre",
        10: "Octobre",
        11: "Novembre",
        12: "Décembre",
    }

    df_all_dates["year"] = df_all_dates["date"].dt.year
    df_all_dates["month"] = df_all_dates["date"].dt.month
    df_all_dates["ym_key"] = df_all_dates["date"].dt.strftime("%Y-%m")

    mois_uniques = (
        df_all_dates.groupby(["ym_key", "year", "month"])
        .size()
        .reset_index()[["ym_key", "year", "month"]]
        .sort_values(["year", "month"], ascending=[False, False])
        .to_dict("records")
    )

    month_options = [
        {
            "key": f"{int(x['year'])}-{int(x['month']):02d}",
            "label": f"{MOIS_FR_LONG[int(x['month'])]} {int(x['year'])}",
            "year": int(x["year"]),
            "month": int(x["month"]),
        }
        for x in mois_uniques
    ]

    today_y, today_m = date.today().year, date.today().month
    today_key = f"{today_y}-{today_m:02d}"
    default_month_key = (
        today_key
        if any(o["key"] == today_key for o in month_options)
        else (month_options[0]["key"] if month_options else today_key)
    )
    if st.session_state.budget_selected_month_key is None:
        st.session_state.budget_selected_month_key = default_month_key

    # --- Header : sélecteurs + titre ---
    with st.container():
        st.markdown('<div class="title-nav-container">', unsafe_allow_html=True)
        title_left, title_center, title_right = st.columns([0.33, 0.34, 0.33])

        # Sélecteur période
        with title_left:
            if st.session_state.budget_view_mode == "mois":
                labels = [o["label"] for o in month_options]
                keys = [o["key"] for o in month_options]
                if not keys:
                    st.warning(
                        "⚠️ Ajoute au moins une opération datée pour activer la vue mensuelle."
                    )
                    chosen_key = None
                else:
                    cur_key = st.session_state.budget_selected_month_key
                    cur_idx = keys.index(cur_key) if cur_key in keys else 0
                    chosen_idx = st.selectbox(
                        "Période",
                        range(len(keys)),
                        index=cur_idx,
                        format_func=lambda i: labels[i],
                        key="budget_month_selector_ui",
                    )
                    chosen_key = keys[chosen_idx]
                if chosen_key is not None:
                    st.session_state.budget_selected_month_key = chosen_key
            else:
                years = sorted(
                    st.session_state.df["date"].dt.year.unique(), reverse=True
                )
                if years:
                    if (
                        st.session_state.budget_selected_year is None
                        or st.session_state.budget_selected_year not in years
                    ):
                        st.session_state.budget_selected_year = years[0]
                    sel_year = st.selectbox(
                        "Année",
                        years,
                        index=years.index(st.session_state.budget_selected_year),
                        key="budget_year_selector",
                    )
                    st.session_state.budget_selected_year = sel_year
                else:
                    st.warning("⚠️ Aucune donnée datée pour activer la vue annuelle.")

        # Titre + switch vue
        with title_center:
            if st.session_state.budget_view_mode == "mois":
                ck = st.session_state.budget_selected_month_key
                cl = next(
                    (o["label"] for o in month_options if o["key"] == ck),
                    "Mois sélectionné",
                )
                titre_dashboard = f"Situation {cl}"
            else:
                titre_dashboard = (
                    f"Situation année {st.session_state.budget_selected_year}"
                )

            st.markdown(
                f'<div style="text-align:center; font-weight:600; font-size:1rem; color:#111827;">{titre_dashboard}</div>',
                unsafe_allow_html=True,
            )
            view_choice = st.selectbox(
                "Vue",
                ["mois", "annee"],
                index=0 if st.session_state.budget_view_mode == "mois" else 1,
                format_func=lambda x: "Mensuelle" if x == "mois" else "Annuelle",
                key="budget_view_mode_selector",
            )
            if view_choice != st.session_state.budget_view_mode:
                st.session_state.budget_view_mode = view_choice
                st.rerun()

        with title_right:
            st.write("")

        st.markdown("</div>", unsafe_allow_html=True)

    # --- Période courante (df mois / df année) ---
    if st.session_state.budget_view_mode == "mois":
        y_str, m_str = st.session_state.budget_selected_month_key.split("-")
        target_year, target_month = int(y_str), int(m_str)
        df_month = slice_df_for_month(st.session_state.df, target_year, target_month)
        df_year = slice_df_for_year(st.session_state.df, target_year)
    else:
        target_year = int(st.session_state.budget_selected_year)
        df_year = slice_df_for_year(st.session_state.df, target_year)
        df_month = pd.DataFrame(columns=st.session_state.df.columns)

    # --- Calculs (mois / année) ---
    s = compute_summary(df_month)
    s_year = compute_summary(df_year)
    month_basics = compute_month_basics(df_month)
    year_totals = compute_totals(df_year)
    year_timeseries = build_year_timeseries(df_year)
    month_forecast = predict_end_of_month(df_month)
    year_forecast = predict_end_of_year(df_year)

    # --- Alertes (top 3 catégories du mois) ---
    alerts = []
    if not df_month.empty:
        dep = df_month[df_month["type"] == "OUT"].copy()
        dep["abs"] = dep["montant"].abs()
        top3 = (
            dep.groupby("categorie")["abs"].sum().sort_values(ascending=False).head(3)
        )
        for cat, val in top3.items():
            alerts.append(
                {
                    "type": "spend",
                    "text": f"{cat} pèse <b>{val:,.0f} €</b> ce mois-ci".replace(
                        ",", " "
                    ),
                }
            )

    # --- Couleur solde (mois) ---
    solde_val = float(month_basics["solde"])
    solde_class = (
        "solde-green"
        if solde_val > 2000
        else (
            "solde-blue"
            if solde_val >= 0
            else ("solde-orange" if solde_val >= -1000 else "solde-red")
        )
    )

    # ======================
    # VUE MENSUELLE
    # ======================
    if st.session_state.budget_view_mode == "mois":
        header_html = f"""
        <div class="header-wrapper" style="margin-top:8px;">
            <div class="header-stat-card">
                <div class="header-stat-label">Revenus du mois</div>
                <div class="header-stat-value normal">{month_basics['revenus']:.0f} €</div>
            </div>
            <div class="header-stat-card">
                <div class="header-stat-label">Dépenses du mois</div>
                <div class="header-stat-value normal">{month_basics['depenses']:.0f} €</div>
            </div>
            <div class="header-stat-card">
                <div class="header-stat-label">
                    Solde du mois
                    <span class="header-solde-note-inline">(au {date.today().strftime("%d/%m/%Y")})</span>
                </div>
                <div class="header-stat-value {solde_class}">{solde_val:,.0f} €</div>
            </div>
        </div>
        """.replace(
            ",", " "
        )
        st.markdown(header_html, unsafe_allow_html=True)

        left_col, right_col = st.columns([1, 1])

        # Alertes
        with left_col:
            st.markdown("#### À surveiller ce mois-ci")
            if not alerts:
                st.success("Aucune alerte prioritaire ce mois-ci. Continue comme ça")
            else:
                for a in alerts:
                    st.markdown(
                        f'<div class="alert-row spend">{a["text"]}</div>',
                        unsafe_allow_html=True,
                    )
                if len(alerts) > 1:
                    st.caption("Les montants les plus lourds apparaissent en premier.")

        # 50/30/20
        with right_col:
            st.markdown("#### Où part ton argent ? (règle 50 / 30 / 20)")
            revenu_mois = float(month_basics.get("revenus", 0.0))
            p50, p30, p20 = (
                float(s["split"].get("50", 0.0)),
                float(s["split"].get("30", 0.0)),
                float(s["split"].get("20", 0.0)),
            )
            e50, e30, e20 = (
                revenu_mois * (p50 / 100),
                revenu_mois * (p30 / 100),
                revenu_mois * (p20 / 100),
            )

            def cls_spend(p, target, soft):
                return "ok" if p <= target else ("warn" if p <= soft else "bad")

            def cls_save(p, target, warn):
                return "ok" if p >= target else ("warn" if p >= warn else "bad")

            st.markdown(
                f'<div class="budget-card {cls_spend(p50,50,55)}" style="margin-bottom:8px;"><b>Besoins</b> • <b>{p50:.1f}% • {e50:,.0f} €</b><div class="subline">Objectif 50% max</div></div>'.replace(
                    ",", " "
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="budget-card {cls_spend(p30,30,35)}" style="margin-bottom:8px;"><b>Envies</b> • <b>{p30:.1f}% • {e30:,.0f} €</b><div class="subline">Objectif 30% max</div></div>'.replace(
                    ",", " "
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="budget-card {cls_save(p20,20,10)}"><b>Épargne</b> • <b>{p20:.1f}% • {e20:,.0f} €</b><div class="subline">Objectif 20% ou plus</div></div>'.replace(
                    ",", " "
                ),
                unsafe_allow_html=True,
            )

        st.divider()

        # Graphiques mois
        st.markdown(
            f"### Dépenses {month_options[0]['label'] if month_options else 'Mois'} (vue graphique)"
        )
        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**Top postes de dépenses**")
            if not s["by_cat"].empty:
                df_cat = s["by_cat"].copy().sort_values("montant", ascending=True)
                fig_cat = px.bar(df_cat, x="montant", y="categorie", orientation="h")
                fig_cat.update_layout(
                    margin=dict(l=20, r=10, t=20, b=20),
                    xaxis_title="Montant (€)",
                    yaxis_title="",
                    showlegend=False,
                )
                st.plotly_chart(fig_cat, use_container_width=True)
            else:
                st.info("Ajoute des données pour voir la répartition.")

        with c2:
            st.markdown("**Répartition des dépenses**")
            if not df_month.empty:
                dep_mois = df_month[df_month["type"] == "OUT"].copy()
                donut_df = dep_mois.groupby("categorie")["montant"].sum().reset_index()
                donut_df["montant_abs"] = donut_df["montant"].abs()
                total_mois = donut_df["montant_abs"].sum()
                fig_donut = px.pie(
                    donut_df, names="categorie", values="montant_abs", hole=0.55
                )
                fig_donut.update_layout(
                    annotations=[
                        dict(
                            text=f"{total_mois:,.0f} €".replace(",", " "),
                            x=0.5,
                            y=0.5,
                            font=dict(size=16),
                            showarrow=False,
                        )
                    ],
                    showlegend=True,
                    margin=dict(l=20, r=20, t=20, b=20),
                )
                st.plotly_chart(fig_donut, use_container_width=True)
                st.caption("Au centre : total des dépenses du mois.")
            else:
                st.info("Aucune dépense saisie pour le mois courant.")

        st.divider()

        # Projection fin de mois (si on est sur le mois courant)
        if st.session_state.budget_selected_month_key == today_key:
            st.markdown("### Si tu continues comme ça…")
            p1, p2, p3 = st.columns(3)
            p1.metric("Fin de mois — Revenus", f"{month_forecast['revenus']:.0f} €")
            p2.metric("Fin de mois — Dépenses", f"{month_forecast['depenses']:.0f} €")
            p3.metric("Fin de mois — Solde", f"{month_forecast['solde']:.0f} €")
            st.caption("Projection basée sur ton rythme actuel.")
            st.divider()

        # Coach (mensuel)
        st.markdown("#### Ton coach")
        coach_md = build_coach_text(
            month_summary=month_basics,
            month_forecast=month_forecast,
            year_forecast=year_forecast,
            split=s["split"],
            top_alerts=[a["text"] for a in alerts][:3],
        )
        st.markdown(coach_md, unsafe_allow_html=True)
        st.divider()

    # ======================
    # VUE ANNUELLE
    # ======================
    else:
        header_year_html = f"""
        <div class="header-wrapper" style="margin-top:8px;">
            <div class="header-stat-card"><div class="header-stat-label">Revenus cumulés {target_year}</div><div class="header-stat-value">{year_totals['revenus']:.0f} €</div></div>
            <div class="header-stat-card"><div class="header-stat-label">Dépenses cumulées {target_year}</div><div class="header-stat-value">{year_totals['depenses']:.0f} €</div></div>
            <div class="header-stat-card">
                <div class="header-stat-label">Solde {target_year}</div>
                <div class="header-stat-value {'solde-green' if year_totals['solde'] >= 0 else 'solde-red'}">{year_totals['solde']:,.0f} €</div>
            </div>
        </div>
        """.replace(
            ",", " "
        )
        st.markdown(header_year_html, unsafe_allow_html=True)

        st.divider()

        st.markdown(f"### Revenus vs Dépenses {target_year} (par mois)")
        if not year_timeseries.empty:
            fig_year = px.line(
                year_timeseries,
                x="mois_label",
                y=["revenus", "depenses"],
                markers=True,
                labels={
                    "mois_label": "Mois",
                    "value": "Montant (€)",
                    "variable": "Série",
                },
            )
            fig_year.update_layout(
                legend_title_text="", margin=dict(l=20, r=20, t=20, b=20)
            )
            st.plotly_chart(fig_year, use_container_width=True)
            st.caption("Compare tes revenus et dépenses mois par mois.")
        else:
            st.info(f"Aucune donnée pour {target_year}.")

        st.divider()

        st.markdown("#### Ton coach (vision annuelle)")
        coach_year_md = build_coach_text_year(
            df_year=df_year,
            year_totals=year_totals,
            s_year_split=s_year["split"],
            year_forecast=year_forecast,
            target_year=target_year,
        )
        st.markdown(coach_year_md, unsafe_allow_html=True)
        st.divider()

    # --- Simulateur d'épargne (simple, visible partout) ---
    with st.expander("Simulation rapide d’épargne", expanded=False):
        c = st.columns(4)
        with c[0]:
            monthly = st.number_input(
                "Mensualité (€)", min_value=0.0, step=10.0, value=100.0
            )
        with c[1]:
            rate = st.number_input(
                "Taux annuel (%)", min_value=0.0, step=0.1, value=3.0
            )
        with c[2]:
            start = st.number_input(
                "Solde initial (€)", min_value=0.0, step=50.0, value=0.0
            )
        with c[3]:
            horizon = st.selectbox(
                "Horizon", [1, 3, 5, 10], index=2, format_func=lambda x: f"{x} an(s)"
            )
        v1 = savings_projection(monthly, rate, 1, start)
        vH = savings_projection(monthly, rate, int(horizon), start)
        s1, sH = st.columns(2)
        s1.metric("Dans 1 an", f"{v1:,.0f} €".replace(",", " "))
        sH.metric(f"Dans {horizon} an(s)", f"{vH:,.0f} €".replace(",", " "))
