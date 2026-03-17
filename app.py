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
    """Lowercase + remove diacritics: 'Éléphant' → 'elephant'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def fmt_director(raw: str) -> str:
    """Convert 'Nom, Prénom ; Nom2, Prénom2' → 'Prénom Nom ; Prénom2 Nom2'.
    Handles particles (de, du, von…) and entries without comma unchanged."""
    parts = [p.strip() for p in raw.split(";")]
    result = []
    for part in parts:
        if "," in part:
            nom, _, prenom = part.partition(",")
            prenom = prenom.strip()
            nom = nom.strip()
            result.append(f"{prenom} {nom}".strip() if prenom else nom)
        else:
            result.append(part)
    return " ; ".join(result)


# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Chargement de l'inventaire…")
def load_data() -> pd.DataFrame:
    xl = pd.ExcelFile(EXCEL_PATH)
    frames = [xl.parse(sheet) for sheet in xl.sheet_names]
    data = pd.concat(frames, ignore_index=True)

    data["Année"] = pd.to_numeric(data["Année"], errors="coerce").astype("Int64")

    for col in ["CF", "CNC", "CT", "LTC", "Netgem - Eclair Préservation"]:
        if col in data.columns:
            data[col] = data[col].fillna("").astype(str).str.strip().str.upper() == "X"

    if "Pays" in data.columns:
        data["Pays"] = (
            data["Pays"]
            .fillna("")
            .astype(str)
            .str.replace(r"\s*;\s*", " ; ", regex=True)
            .str.replace(r"([a-zéèêëàâùûî])([A-ZÉÈÀÂ])", r"\1 ; \2", regex=True)
        )

    # Pre-compute normalised search columns (no accents, lower-case)
    data["_titre_norm"] = data["Titre"].fillna("").astype(str).apply(strip_accents)
    data["_real_norm"] = data["Réalisateur(s)"].fillna("").astype(str).apply(strip_accents)
    data["_pays_norm"] = data["Pays"].fillna("").astype(str).apply(strip_accents)

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
    """Return directors as 'Prénom Nom', sorted."""
    directors: set[str] = set()
    for val in data["Réalisateur(s)"].dropna():
        for d in str(val).split(";"):
            d = d.strip()
            if d:
                directors.add(fmt_director(d))
    return sorted(directors)


# ── Search logic ──────────────────────────────────────────────────────────────
def _tokens(q: str) -> list[str]:
    """Split query into normalised tokens, ignoring empty strings."""
    return [t for t in strip_accents(q).split() if t]


def text_mask(series_norm: pd.Series, query: str, mode: str) -> pd.Series:
    """Return boolean mask for one text column given query and mode."""
    if not query.strip():
        return pd.Series(True, index=series_norm.index)

    tokens = _tokens(query)
    if not tokens:
        return pd.Series(True, index=series_norm.index)

    if mode == "Exact":
        q_norm = strip_accents(query.strip())
        return series_norm == q_norm

    if mode == "Commence par":
        # All tokens must appear at the start of some word in the string
        mask = pd.Series(True, index=series_norm.index)
        for t in tokens:
            mask &= series_norm.str.contains(r"(?<![a-z])" + re.escape(t), regex=True)
        return mask

    # Default: "Contient" — all tokens must appear somewhere (AND logic)
    mask = pd.Series(True, index=series_norm.index)
    for t in tokens:
        mask &= series_norm.str.contains(re.escape(t), regex=True)
    return mask


def highlight_tokens(text: str, query: str) -> str:
    """Wrap matched tokens in ** for markdown bold."""
    if not query:
        return text
    for t in _tokens(query):
        text = re.sub(
            f"({re.escape(t)})",
            r"**\1**",
            text,
            flags=re.IGNORECASE,
        )
    return text


# ── Sidebar filters ───────────────────────────────────────────────────────────
def render_sidebar(data: pd.DataFrame) -> dict:
    st.sidebar.title("🔍 Filtres")

    # ── Texte ──────────────────────────────────────────────────────────────
    titre_q = st.sidebar.text_input(
        "Titre",
        placeholder="ex. nuit américaine",
        help="Multi-mots = AND. Insensible aux accents.",
    )
    search_mode = st.sidebar.radio(
        "Mode",
        ["Contient", "Commence par", "Exact"],
        horizontal=True,
    )

    all_directors = get_all_directors(data)
    director_pick = st.sidebar.selectbox(
        "Réalisateur",
        options=[""] + all_directors,
        index=0,
        placeholder="Tous les réalisateurs…",
    )

    # ── Période ────────────────────────────────────────────────────────────
    st.sidebar.divider()
    year_min = int(data["Année"].min(skipna=True))
    year_max = int(data["Année"].max(skipna=True))
    year_range = st.sidebar.slider(
        "Période",
        min_value=year_min,
        max_value=year_max,
        value=(1950, year_max),
        step=1,
    )
    include_no_year = st.sidebar.checkbox("Inclure les films sans année", value=True)

    # ── Pays ───────────────────────────────────────────────────────────────
    st.sidebar.divider()
    all_countries = get_all_countries(data)
    selected_countries = st.sidebar.multiselect(
        "Pays",
        options=all_countries,
        placeholder="Tous les pays…",
        help="Co-productions incluses.",
    )

    # ── Collections ────────────────────────────────────────────────────────
    st.sidebar.divider()
    st.sidebar.caption("Collections / Préservation")
    flag_filters: dict[str, bool] = {}
    for label, col in FLAG_COLS.items():
        if col in data.columns:
            flag_filters[col] = st.sidebar.checkbox(label)

    return {
        "titre": titre_q,
        "director_pick": director_pick,
        "search_mode": search_mode,
        "year_range": year_range,
        "include_no_year": include_no_year,
        "countries": selected_countries,
        "flags": flag_filters,
    }


# ── Filtering logic ───────────────────────────────────────────────────────────
def apply_filters(data: pd.DataFrame, filters: dict) -> pd.DataFrame:
    mask = pd.Series(True, index=data.index)
    mode = filters["search_mode"]

    if filters["titre"].strip():
        mask &= text_mask(data["_titre_norm"], filters["titre"], mode)

    if filters["director_pick"]:
        mask &= text_mask(data["_real_norm"], filters["director_pick"], "Contient")

    # Year range
    year_mask = data["Année"].between(*filters["year_range"])
    if filters["include_no_year"]:
        year_mask |= data["Année"].isna()
    mask &= year_mask

    # Countries
    if filters["countries"]:
        selected = set(filters["countries"])
        mask &= data["Pays"].apply(
            lambda v: bool({p.strip() for p in v.split(" ; ")} & selected)
        )

    # Flags
    for col, active in filters["flags"].items():
        if active:
            mask &= data[col]

    return data[mask]


# ── Active filter badges ──────────────────────────────────────────────────────
def active_filter_summary(filters: dict) -> str:
    badges = []
    if filters["titre"]:
        badges.append(f'Titre : `{filters["titre"]}` ({filters["search_mode"].lower()})')
    if filters["director_pick"]:
        badges.append(f'Réalisateur : `{filters["director_pick"]}`')
    if filters["countries"]:
        badges.append("Pays : " + ", ".join(f"`{c}`" for c in filters["countries"]))
    active_flags = [lbl for lbl, col in FLAG_COLS.items() if filters["flags"].get(col)]
    if active_flags:
        badges.append(" · ".join(active_flags))
    return "  |  ".join(badges) if badges else ""


# ── Stats ─────────────────────────────────────────────────────────────────────
def render_stats(filtered: pd.DataFrame, total: int):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Films trouvés", f"{len(filtered):,}", delta=f"{len(filtered) - total:,}" if len(filtered) != total else None)
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
            decade_data = (
                filtered["Année"].dropna().astype(int)
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
            if country_counts:
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


def render_table(filtered: pd.DataFrame, filters: dict):
    cols = [c for c in DISPLAY_COLS if c in filtered.columns]
    display = filtered[cols].copy()

    # Format director names as "Prénom Nom"
    if "Réalisateur(s)" in display.columns:
        display["Réalisateur(s)"] = display["Réalisateur(s)"].fillna("").astype(str).apply(
            lambda v: fmt_director(v) if v else ""
        )

    for col in ["LTC", "CT", "CNC", "Netgem - Eclair Préservation"]:
        if col in display.columns:
            display[col] = display[col].map({True: "✓", False: ""})
    display["Année"] = display["Année"].astype("object").where(display["Année"].notna(), other="")

    # Highlight search terms in Titre column
    if filters["titre"]:
        display["Titre"] = display["Titre"].fillna("").apply(
            lambda t: highlight_tokens(t, filters["titre"])
        )

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

    csv = filtered[cols].to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Exporter en CSV", csv, "films_selection.csv", "text/csv")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    st.title("🎬 Inventaire National des Films")

    data = load_data()
    total = len(data)
    st.caption(f"Source : Inventaire national — liste classée des films | {total:,} films au total")

    filters = render_sidebar(data)
    filtered = apply_filters(data, filters)

    # Active filter summary
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
        render_table(filtered, filters)


if __name__ == "__main__":
    main()
