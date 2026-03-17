import unicodedata
import re
import streamlit as st
import pandas as pd
from pathlib import Path

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Inventaire National des Films",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

EXCEL_PATH = Path(__file__).parent / "Inventaire national - liste classée des films.xlsx"

FLAG_COLS = {
    "LTC": "LTC",
    "CT": "CT",
    "CNC": "CNC",
    "Netgem / Eclair Préservation": "Netgem - Eclair Préservation",
}


# ── Text normalization ────────────────────────────────────────────────────────
def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def fmt_director(raw: str) -> str:
    """'Nom, Prénom ; Nom2, Prénom2' → 'Prénom Nom ; Prénom2 Nom2'."""
    parts = [p.strip() for p in raw.split(";")]
    result = []
    for part in parts:
        if "," in part:
            nom, _, prenom = part.partition(",")
            prenom, nom = prenom.strip(), nom.strip()
            result.append(f"{prenom} {nom}".strip() if prenom else nom)
        else:
            result.append(part)
    return " ; ".join(result)


# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Chargement de l'inventaire…")
def load_data() -> pd.DataFrame:
    xl = pd.ExcelFile(EXCEL_PATH)
    data = pd.concat([xl.parse(s) for s in xl.sheet_names], ignore_index=True)

    data["Année"] = pd.to_numeric(data["Année"], errors="coerce").astype("Int64")

    for col in ["CF", "CNC", "CT", "LTC", "Netgem - Eclair Préservation"]:
        if col in data.columns:
            data[col] = data[col].fillna("").astype(str).str.strip().str.upper() == "X"

    if "Pays" in data.columns:
        data["Pays"] = (
            data["Pays"]
            .fillna("").astype(str)
            .str.replace(r"\s*;\s*", " ; ", regex=True)
            .str.replace(r"([a-zéèêëàâùûî])([A-ZÉÈÀÂ])", r"\1 ; \2", regex=True)
        )

    data["_titre_norm"] = data["Titre"].fillna("").astype(str).apply(strip_accents)
    data["_real_norm"] = data["Réalisateur(s)"].fillna("").astype(str).apply(strip_accents)

    return data


@st.cache_data(show_spinner=False)
def get_all_countries(data: pd.DataFrame) -> list[str]:
    countries: set[str] = set()
    for val in data["Pays"]:
        for c in val.split(" ; "):
            c = c.strip()
            if c and c.lower() != "nan":
                countries.add(c)
    return sorted(countries)


@st.cache_data(show_spinner=False)
def get_all_directors(data: pd.DataFrame) -> list[str]:
    directors: set[str] = set()
    for val in data["Réalisateur(s)"].dropna():
        for d in str(val).split(";"):
            d = d.strip()
            if d:
                directors.add(fmt_director(d))
    return sorted(directors)


# ── Search logic ──────────────────────────────────────────────────────────────
def _tokens(q: str) -> list[str]:
    return [t for t in strip_accents(q).split() if t]


def text_mask(series_norm: pd.Series, query: str, mode: str) -> pd.Series:
    if not query.strip():
        return pd.Series(True, index=series_norm.index)
    tokens = _tokens(query)
    if not tokens:
        return pd.Series(True, index=series_norm.index)

    if mode == "Exact":
        return series_norm == strip_accents(query.strip())

    if mode == "Commence par":
        mask = pd.Series(True, index=series_norm.index)
        for t in tokens:
            mask &= series_norm.str.contains(r"(?<![a-z])" + re.escape(t), regex=True)
        return mask

    # Contient — all tokens must appear (AND)
    mask = pd.Series(True, index=series_norm.index)
    for t in tokens:
        mask &= series_norm.str.contains(re.escape(t), regex=True)
    return mask


# ── Reset ─────────────────────────────────────────────────────────────────────
def reset_filters():
    for key in ["f_titre", "f_directors", "f_mode", "f_years", "f_no_year", "f_countries",
                *[f"f_flag_{col}" for col in FLAG_COLS.values()]]:
        st.session_state.pop(key, None)


# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar(data: pd.DataFrame) -> dict:
    st.sidebar.title("🔍 Filtres")
    st.sidebar.button("↺ Réinitialiser tous les filtres", on_click=reset_filters, use_container_width=True)

    year_min = int(data["Année"].min(skipna=True))
    year_max = int(data["Année"].max(skipna=True))

    # ── Titre ──────────────────────────────────────────────────────────────
    st.sidebar.divider()
    titre_q = st.sidebar.text_input(
        "Titre",
        placeholder="ex. nuit américaine",
        help="Plusieurs mots = tous doivent être présents. Insensible aux accents.",
        key="f_titre",
    )
    search_mode = st.sidebar.radio(
        "Mode de correspondance (titre)",
        ["Contient", "Commence par", "Exact"],
        horizontal=True,
        key="f_mode",
    )

    # ── Réalisateur ────────────────────────────────────────────────────────
    st.sidebar.divider()
    all_directors = get_all_directors(data)
    director_picks = st.sidebar.multiselect(
        "Réalisateur",
        options=all_directors,
        placeholder="Tous les réalisateurs…",
        key="f_directors",
    )

    # ── Période ────────────────────────────────────────────────────────────
    st.sidebar.divider()
    year_range = st.sidebar.slider(
        "Période",
        min_value=year_min,
        max_value=year_max,
        value=(year_min, year_max),
        step=1,
        key="f_years",
    )
    include_no_year = st.sidebar.checkbox(
        "Inclure les films sans année", value=True, key="f_no_year"
    )

    # ── Pays ───────────────────────────────────────────────────────────────
    st.sidebar.divider()
    all_countries = get_all_countries(data)
    selected_countries = st.sidebar.multiselect(
        "Pays",
        options=all_countries,
        placeholder="Tous les pays…",
        help="Co-productions incluses : sélectionner un pays retourne tous les films où il apparaît.",
        key="f_countries",
    )

    # ── Collections ────────────────────────────────────────────────────────
    st.sidebar.divider()
    st.sidebar.caption("Collections / Préservation")
    flag_filters: dict[str, bool] = {}
    for label, col in FLAG_COLS.items():
        if col in data.columns:
            flag_filters[col] = st.sidebar.checkbox(label, key=f"f_flag_{col}")

    return {
        "titre": titre_q,
        "directors": director_picks,
        "search_mode": search_mode,
        "year_range": year_range,
        "year_min": year_min,
        "year_max": year_max,
        "include_no_year": include_no_year,
        "countries": selected_countries,
        "flags": flag_filters,
    }


# ── Filtering ─────────────────────────────────────────────────────────────────
def apply_filters(data: pd.DataFrame, filters: dict) -> pd.DataFrame:
    mask = pd.Series(True, index=data.index)
    mode = filters["search_mode"]

    if filters["titre"].strip():
        mask &= text_mask(data["_titre_norm"], filters["titre"], mode)

    if filters["directors"]:
        dir_mask = pd.Series(False, index=data.index)
        for d in filters["directors"]:
            dir_mask |= text_mask(data["_real_norm"], d, "Contient")
        mask &= dir_mask

    year_mask = data["Année"].between(*filters["year_range"])
    if filters["include_no_year"]:
        year_mask |= data["Année"].isna()
    mask &= year_mask

    if filters["countries"]:
        selected = set(filters["countries"])
        mask &= data["Pays"].apply(
            lambda v: bool({p.strip() for p in v.split(" ; ")} & selected)
        )

    for col, active in filters["flags"].items():
        if active:
            mask &= data[col]

    return data[mask]


# ── Active filter summary ─────────────────────────────────────────────────────
def active_filter_summary(filters: dict) -> str:
    badges = []
    if filters["titre"]:
        badges.append(f'Titre : `{filters["titre"]}` ({filters["search_mode"].lower()})')
    if filters["directors"]:
        badges.append("Réalisateur : " + ", ".join(f"`{d}`" for d in filters["directors"]))
    yr_min, yr_max = filters["year_range"]
    if yr_min != filters["year_min"] or yr_max != filters["year_max"]:
        badges.append(f"Période : `{yr_min} – {yr_max}`")
    if not filters["include_no_year"]:
        badges.append("sans année exclus")
    if filters["countries"]:
        badges.append("Pays : " + ", ".join(f"`{c}`" for c in filters["countries"]))
    active_flags = [lbl for lbl, col in FLAG_COLS.items() if filters["flags"].get(col)]
    if active_flags:
        badges.append(" · ".join(active_flags))
    return "  |  ".join(badges) if badges else ""


# ── Stats ─────────────────────────────────────────────────────────────────────
def render_stats(filtered: pd.DataFrame, total: int):
    c1, c2, c3, c4 = st.columns(4)

    pct = f"{len(filtered) / total * 100:.0f} % du catalogue" if len(filtered) != total else "catalogue complet"
    c1.metric("Films trouvés", f"{len(filtered):,}", delta=pct, delta_color="off")

    with_year = filtered["Année"].dropna()
    c2.metric("Année médiane", int(with_year.median()) if len(with_year) else "—")
    c3.metric("LTC", f"{filtered['LTC'].sum():,}" if "LTC" in filtered.columns else "—")
    c4.metric(
        "Netgem / Eclair",
        f"{filtered['Netgem - Eclair Préservation'].sum():,}"
        if "Netgem - Eclair Préservation" in filtered.columns else "—",
    )


# ── Charts ────────────────────────────────────────────────────────────────────
def render_charts(filtered: pd.DataFrame):
    with st.expander("📊 Visualisations", expanded=False):
        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**Films par décennie**")
            years = filtered["Année"].dropna()
            if years.empty:
                st.caption("Aucune donnée d'année disponible.")
            else:
                decade_data = (
                    years.astype(int)
                    .apply(lambda y: f"{y // 10 * 10}s")
                    .value_counts().sort_index().reset_index()
                )
                decade_data.columns = ["Décennie", "Nombre"]
                st.bar_chart(decade_data.set_index("Décennie"))

        with c2:
            st.markdown("**Top 15 pays**")
            country_counts: dict[str, int] = {}
            for val in filtered["Pays"]:
                for c in val.split(" ; "):
                    c = c.strip()
                    if c and c.lower() != "nan":
                        country_counts[c] = country_counts.get(c, 0) + 1
            if not country_counts:
                st.caption("Aucune donnée de pays disponible.")
            else:
                top = (
                    pd.Series(country_counts).sort_values(ascending=False)
                    .head(15).reset_index()
                )
                top.columns = ["Pays", "Nombre"]
                st.bar_chart(top.set_index("Pays"))


# ── Results table ─────────────────────────────────────────────────────────────
DISPLAY_COLS = [
    "N° Œuvre", "Titre", "Réalisateur(s)", "Année", "Pays",
    "LTC", "CT", "CNC", "Netgem - Eclair Préservation",
]


def render_table(filtered: pd.DataFrame):
    cols = [c for c in DISPLAY_COLS if c in filtered.columns]
    display = filtered[cols].copy()

    if "Réalisateur(s)" in display.columns:
        display["Réalisateur(s)"] = (
            display["Réalisateur(s)"].fillna("").astype(str)
            .apply(lambda v: fmt_director(v) if v else "")
        )

    for col in ["LTC", "CT", "CNC", "Netgem - Eclair Préservation"]:
        if col in display.columns:
            display[col] = display[col].map({True: "✓", False: ""})

    # Année : Int64 → plain int string (avoids "1970.0" in export)
    display["Année"] = display["Année"].astype(object).where(display["Année"].notna(), other="")

    st.dataframe(
        display,
        use_container_width=True,
        height=540,
        column_config={
            "N° Œuvre": st.column_config.NumberColumn("N° Œuvre", format="%d"),
            "Titre": st.column_config.TextColumn("Titre", width="large"),
            "Réalisateur(s)": st.column_config.TextColumn("Réalisateur(s)", width="medium"),
            "Année": st.column_config.TextColumn("Année", width="small"),
            "Pays": st.column_config.TextColumn("Pays", width="medium"),
            "LTC": st.column_config.TextColumn("LTC", width="small"),
            "CT": st.column_config.TextColumn("CT", width="small"),
            "CNC": st.column_config.TextColumn("CNC", width="small"),
            "Netgem - Eclair Préservation": st.column_config.TextColumn("Netgem / Eclair", width="small"),
        },
    )

    # CSV export : use display (already cleaned) except re-add raw Année for correctness
    csv = display.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Exporter en CSV", csv, "films_selection.csv", "text/csv")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    st.title("🎬 Inventaire National des Films")

    data = load_data()
    total = len(data)
    st.caption(f"Source : Inventaire national — liste classée des films  ·  {total:,} films au total")

    filters = render_sidebar(data)
    filtered = apply_filters(data, filters)

    summary = active_filter_summary(filters)
    if summary:
        st.info(f"Filtres actifs : {summary}")

    render_stats(filtered, total)
    st.divider()
    render_charts(filtered)
    st.divider()
    st.subheader(f"Résultats — {len(filtered):,} film{'s' if len(filtered) != 1 else ''}")

    if filtered.empty:
        st.warning("Aucun film ne correspond aux critères sélectionnés.")
    else:
        render_table(filtered)


if __name__ == "__main__":
    main()
