# --- bot/utils/spotify_helper.py (Radio Vecinos + Fallback Playlist Año | Logs + Fix 403 AF) ---

import asyncio
import logging
import random
import re
import os
from time import perf_counter
from typing import List, Optional, Set, Tuple, Dict
from datetime import datetime

# --- Imports y Configuración Inicial ---
try:
    import spotipy
    from spotipy import Spotify
    from spotipy.exceptions import SpotifyException
    from spotipy.oauth2 import SpotifyClientCredentials
except ImportError:  # pragma: no cover
    spotipy = None; Spotify = None; SpotifyClientCredentials = None  # type: ignore
    class SpotifyException(Exception): pass
    logging.error("SPOTIPY NO INSTALADO. 'pip install spotipy'")

try:
    from config.settings import get_settings
except ImportError:
    class MockSettings:
        spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        spotify_market = os.getenv("SPOTIFY_MARKET")
        spotify_disable_audio_features = os.getenv("SPOTIFY_DISABLE_AUDIO_FEATURES")
    def get_settings(): return MockSettings()
    logging.warning("No se encontró config.settings, usando os.getenv para Spotify.")

_SPOTIFY_CLIENT: Optional[Spotify] = None
_SPOTIFY_CREDENTIALS_WARNING_EMITTED = False

# --- Regex, clean_title, extract_artist_from_title ---
_BRACKET_PATTERN = re.compile(r"\s*[\(\[\{].*")
_EXTRA_SEP_PATTERN = re.compile(r"\s*(?:\||//|★|☆).*")
_EXTRA_WORDS_PATTERN = re.compile(r"""
    \s+\b(official|video|audio|lyric|lyrics|visualizer|remaster(?:ed)?|hd|4k|
    oficial|live|acústico|acoustic|explicit|version|edit|mix|remix|radio|
    original|extended|deluxe|club|instrumental|karaoke|performance|session|cover)\b
""", re.IGNORECASE | re.VERBOSE)
_ARTIST_TITLE_SEP = re.compile(r"^(.*?)\s+-\s+(.+)$")
_ARTIST_SEP = re.compile(r"\s+(?:x|&|,|(?:vs|feat|ft)\.?)+\s+", re.IGNORECASE)
_ALT_ARTIST_SEP = re.compile(r"^(.*?)\s+\|{1,2}\s+.+$")
_CLEANUP_PATTERN = re.compile(r"\s+#\d+$|&[a-zA-Z]+;")

def clean_title(title: str, remove_artist_pattern: bool = True) -> str:
    if not title: return ""
    text = title.strip()
    if remove_artist_pattern:
        hyphen_match = _ARTIST_TITLE_SEP.match(text)
        if hyphen_match:
            text = hyphen_match.group(2).strip()
        elif "|" in text:
            text = text.split("|", 1)[-1].strip()
    text = _CLEANUP_PATTERN.sub("", text)
    text = _BRACKET_PATTERN.sub("", text)
    text = _EXTRA_SEP_PATTERN.sub("", text)
    text = _EXTRA_WORDS_PATTERN.sub("", text)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" -|/")
    return text.strip()

def extract_artist_from_title(title: str) -> Optional[str]:
    if not title: return None
    text = title.strip()
    hyphen_match = _ARTIST_TITLE_SEP.match(text)
    if hyphen_match:
        artist = hyphen_match.group(1).strip()
    else:
        alt_match = _ALT_ARTIST_SEP.match(text)
        if not alt_match: return None
        artist = alt_match.group(1).strip()
    artist = _CLEANUP_PATTERN.sub("", artist)
    artist = re.sub(r"\s+", " ", artist)
    artist = artist.strip(" -|/")
    if not artist: return None
    primary = _ARTIST_SEP.split(artist)[0].strip()
    return primary or None

# --- Cliente Spotify ---
def _ensure_spotify_client() -> Optional[Spotify]:
    global _SPOTIFY_CLIENT, _SPOTIFY_CREDENTIALS_WARNING_EMITTED
    if _SPOTIFY_CLIENT is not None:
        return _SPOTIFY_CLIENT
    if spotipy is None:
        if not _SPOTIFY_CREDENTIALS_WARNING_EMITTED:
            logging.warning("Radio Spotify: spotipy no instalado.")
            _SPOTIFY_CREDENTIALS_WARNING_EMITTED = True
        return None
    settings = get_settings()
    client_id = getattr(settings, "spotify_client_id", None)
    client_secret = getattr(settings, "spotify_client_secret", None)
    if not client_id or not client_secret:
        if not _SPOTIFY_CREDENTIALS_WARNING_EMITTED:
            logging.warning("Radio Spotify: Credenciales faltantes.")
            _SPOTIFY_CREDENTIALS_WARNING_EMITTED = True
        return None
    try:
        auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        _SPOTIFY_CLIENT = spotipy.Spotify(auth_manager=auth_manager, requests_timeout=10, retries=2)
        _SPOTIFY_CREDENTIALS_WARNING_EMITTED = False
        logging.info("Radio Spotify: ✅ Cliente inicializado.")
    except Exception as exc:
        logging.exception(f"Radio Spotify: ❌ No se pudo iniciar cliente: {exc}")
        _SPOTIFY_CLIENT = None
    return _SPOTIFY_CLIENT

def _get_market_default() -> str:
    try:
        settings = get_settings()
        return (getattr(settings, "spotify_market", None) or os.getenv("SPOTIFY_MARKET") or "AR").upper()
    except Exception:
        return os.getenv("SPOTIFY_MARKET", "AR").upper()

def _audio_features_desactivadas() -> bool:
    try:
        settings = get_settings()
        v = getattr(settings, "spotify_disable_audio_features", None) or os.getenv("SPOTIFY_DISABLE_AUDIO_FEATURES")
        return str(v).lower() in ("1", "true", "yes", "on")
    except Exception:
        return False

# =========================
# NUEVO: Radio por Vecinos
# =========================
_FEATURE_KEYS = ["danceability","energy","speechiness","acousticness","instrumentalness","liveness","valence","tempo"]
_WEIGHTS = {
    "danceability": 1.0,
    "energy": 1.0,
    "valence": 0.9,
    "tempo": 0.6,
    "speechiness": 0.3,
    "acousticness": 0.5,
    "instrumentalness": 0.4,
    "liveness": 0.3,
}

def _vector_audio_features(af: dict) -> Optional[Dict[str, float]]:
    if not isinstance(af, dict): return None
    vec: Dict[str, float] = {}
    for k in _FEATURE_KEYS:
        v = af.get(k)
        if v is None: return None
        if k == "tempo":
            v = max(0.0, min(1.0, float(v) / 250.0))  # normalizar BPM a [0..1]
        else:
            v = float(v)
        vec[k] = v
    return vec

def _distancia_audio(a: Dict[str, float], b: Dict[str, float]) -> float:
    d = 0.0
    for k in _FEATURE_KEYS:
        w = _WEIGHTS.get(k, 0.5)
        d += w * abs(a[k] - b[k])
    return d

def _fmt_vec_corto(vec: Optional[Dict[str, float]]) -> str:
    if not vec: return "sin_features"
    keys = ("danceability","energy","valence","tempo")
    parts = []
    for k in keys:
        v = vec.get(k, 0.0)
        if k == "tempo":
            parts.append(f"{k[:3]}={int(round(v*250))}bpm")
        else:
            parts.append(f"{k[:3]}={v:.2f}")
    return ",".join(parts)

# --- Helpers robustos para audio-features (manejo 403 y fallback single) ---
def _get_seed_features_seguro(client: Spotify, seed_id: str) -> Optional[Dict[str, float]]:
    if _audio_features_desactivadas():
        logging.info("Radio Vecinos: ⛔ audio-features desactivadas por configuración (SPOTIFY_DISABLE_AUDIO_FEATURES).")
        return None
    try:
        # Intento batch normal
        af_list = client.audio_features([seed_id]) or []
        af = af_list[0] if af_list else None
        vec = _vector_audio_features(af) if af else None
        if vec: return vec
        logging.warning("Radio Vecinos: seed sin vector de features (None).")
    except SpotifyException as e:
        if getattr(e, "http_status", None) == 403:
            logging.warning("Radio Vecinos: 403 en audio-features (seed). Reintentando endpoint single…")
            try:
                # Forzar endpoint single
                af_single = client._get(f"audio-features/{seed_id}")
                vec = _vector_audio_features(af_single)
                if vec:
                    return vec
                logging.warning("Radio Vecinos: endpoint single devolvió features vacíos/None para seed.")
            except Exception as e2:
                logging.info(f"Radio Vecinos: fallo single seed AF: {e2}. Continuamos sin features.")
        else:
            logging.info(f"Radio Vecinos: fallo audio-features seed ({e}). Continuamos sin features.")
    except Exception as ex:
        logging.info(f"Radio Vecinos: excepción inesperada en seed AF: {ex}. Continuamos sin features.")
    return None

def _get_batch_features_seguro(client: Spotify, ids: List[str]) -> Dict[str, Dict[str, float]]:
    id2vec: Dict[str, Dict[str, float]] = {}
    if not ids or _audio_features_desactivadas():
        return id2vec
    # Particionar en chunks de 100 (límite API)
    def _chunks(lst, n): 
        for i in range(0, len(lst), n): 
            yield lst[i:i+n]
    for chunk in _chunks(ids, 100):
        try:
            feats = client.audio_features(chunk) or []
            for af in feats:
                if not af or not af.get("id"): 
                    continue
                vec = _vector_audio_features(af)
                if vec:
                    id2vec[af["id"]] = vec
        except SpotifyException as e:
            if getattr(e, "http_status", None) == 403:
                logging.warning("Radio Vecinos: 403 en audio-features batch. Reintentando por cada id (single)…")
                # Intentar uno por uno para este chunk
                for tid in chunk:
                    try:
                        af_single = client._get(f"audio-features/{tid}")
                        vec = _vector_audio_features(af_single)
                        if vec:
                            id2vec[tid] = vec
                    except Exception as e2:
                        logging.debug(f"Radio Vecinos: single AF falló para {tid}: {e2}")
                # seguimos con el resto de chunks
            else:
                logging.info(f"Radio Vecinos: fallo batch AF ({e}). Seguimos sin features para este chunk.")
        except Exception as ex:
            logging.info(f"Radio Vecinos: excepción batch AF ({ex}). Seguimos sin features para este chunk.")
    return id2vec

def _fetch_radio_vecinos_sync(
    original_title: str,
    session_played_tuples_key: Tuple[Tuple[str, str], ...],
    mercado: Optional[str] = None,
    max_artistas_rel: int = 8,
    top_tracks_por_artista: int = 5,
    max_candidatos: int = 60,
    devolver: int = 5,
) -> Optional[List[Tuple[str, str, str, str, Optional[str], Optional[str]]]]:
    """
    Estrategia sin /recommendations:
      1) Buscar track semilla (search track)
      2) audio_features(seed) (tolerante a 403)
      3) related_artists(seed.artist)
      4) artist_top_tracks(related_i, market)
      5) audio_features(batch candidatos) (tolerante a 403)
      6) Rank por distancia (si hay features) o por popularidad (fallback)
    """
    t0 = perf_counter()
    client = _ensure_spotify_client()
    if client is None:
        logging.warning("Radio Vecinos: ❌ Cliente Spotify no disponible.")
        return None

    mercado = (mercado or _get_market_default()).upper()
    sesion_clean = {item[1].lower() for item in session_played_tuples_key if len(item) > 1 and item[1]}
    logging.info(f"Radio Vecinos: ▶️ start title='{original_title}' market={mercado} historial={len(sesion_clean)}")

    try:
        # 1) Resolver semilla
        t_seed = perf_counter()
        titulo_busqueda = clean_title(original_title, False)
        artista_extraido = extract_artist_from_title(original_title)
        q = f"{artista_extraido} {titulo_busqueda}".strip() if artista_extraido else titulo_busqueda
        r = client.search(q=q, type="track", limit=1)
        item_track = (r.get("tracks") or {}).get("items", [])
        if not item_track:
            logging.warning(f"Radio Vecinos: 🔎 sin track para q='{q}'")
            return None
        seed_track = item_track[0]
        seed_id = seed_track.get("id")
        seed_name = seed_track.get("name", "?")
        seed_artistas = seed_track.get("artists") or []
        if not seed_id or not seed_artistas:
            logging.warning("Radio Vecinos: seed sin id/artistas")
            return None
        seed_artista_id = seed_artistas[0].get("id")
        seed_artista_nombre = seed_artistas[0].get("name", "?")
        logging.info(f"Radio Vecinos: 🎯 seed='{seed_artista_nombre} - {seed_name}' (id={seed_id}) t={perf_counter()-t_seed:.3f}s")

        # 2) Audio features de la semilla (robusto)
        t_feat_seed = perf_counter()
        vec_seed = _get_seed_features_seguro(client, seed_id)
        logging.info(f"Radio Vecinos: 🎛️ features_seed={_fmt_vec_corto(vec_seed)} t={perf_counter()-t_feat_seed:.3f}s")

        # 3) Artistas relacionados
        t_rel = perf_counter()
        rel = client.artist_related_artists(seed_artista_id)
        artistas_rel = (rel or {}).get("artists", [])
        if not artistas_rel:
            logging.warning(f"Radio Vecinos: 👥 sin relacionados para '{seed_artista_nombre}'")
            return None
        artistas_rel = artistas_rel[:max_artistas_rel]
        nombres_rel = ", ".join([a.get("name","?") for a in artistas_rel])
        logging.info(f"Radio Vecinos: 👥 relacionados={len(artistas_rel)} [{nombres_rel}] t={perf_counter()-t_rel:.3f}s")

        # 4) Candidatos: top tracks de cada artista relacionado
        t_cands = perf_counter()
        candidatos_tracks: List[Dict] = []
        total_top_tracks_llamadas = 0
        for a in artistas_rel:
            aid = a.get("id")
            aname = a.get("name", "?")
            if not aid: continue
            try:
                tt = client.artist_top_tracks(aid, market=mercado)
                tracks = (tt or {}).get("tracks", [])[:top_tracks_por_artista]
                total_top_tracks_llamadas += 1
                logging.debug(f"Radio Vecinos: ↪︎ top_tracks '{aname}' -> {len(tracks)}")
                for t in tracks:
                    if not t or not t.get("id") or not t.get("name"): continue
                    titulo = t.get("name", "")
                    cleaned = clean_title(titulo, False).lower().strip()
                    if cleaned and cleaned not in sesion_clean:
                        candidatos_tracks.append(t)
                        if len(candidatos_tracks) >= max_candidatos:
                            break
                if len(candidatos_tracks) >= max_candidatos:
                    break
            except SpotifyException as e:
                logging.info(f"Radio Vecinos: ⚠️ fallo top_tracks artista {aid}: {e}")
                continue

        logging.info(
            f"Radio Vecinos: 📦 candidatos_pre_features={len(candidatos_tracks)} "
            f"(llamadas_top_tracks={total_top_tracks_llamadas}) t={perf_counter()-t_cands:.3f}s"
        )
        if not candidatos_tracks:
            logging.warning("Radio Vecinos: ❗ no hay candidatos")
            return None

        # 5) Audio features batch para candidatos (robusto)
        t_feat_batch = perf_counter()
        ids = [t["id"] for t in candidatos_tracks if t.get("id")]
        id2vec = _get_batch_features_seguro(client, ids)
        logging.info(
            f"Radio Vecinos: 🧪 features_batch={len(id2vec)}/{len(ids)} "
            f"t={perf_counter()-t_feat_batch:.3f}s"
        )

        # 6) Rank y selección
        t_rank = perf_counter()
        scored: List[Tuple[float, Dict]] = []
        for t in candidatos_tracks:
            tid = t.get("id")
            if not tid: continue
            if vec_seed and tid in id2vec:
                dist = _distancia_audio(vec_seed, id2vec[tid])
            else:
                # Fallback: priorizar popularidad (menor distancia = mejor)
                dist = 9.99 - float(t.get("popularity", 0)) / 100.0
            scored.append((dist, t))
        scored.sort(key=lambda x: x[0])

        muestra = ", ".join([f"{(s[1].get('name','?'))}:{s[0]:.2f}" for s in scored[:5]])
        logging.debug(f"Radio Vecinos: 🧭 top5_distancias=[{muestra}] t={perf_counter()-t_rank:.3f}s")

        elegidos: List[Tuple[str, str, str, str, Optional[str], Optional[str]]] = []
        vistos_batch: Set[str] = set()

        for dist, t in scored:
            if len(elegidos) >= devolver: break
            titulo = t.get("name", "")
            artista_nombre = (t.get("artists") or [{}])[0].get("name", "")
            cleaned = clean_title(titulo, False).lower().strip()
            clave = re.sub(r'\s+', ' ', f"{artista_nombre.lower().strip()} {cleaned}")
            if cleaned and cleaned not in sesion_clean and clave not in vistos_batch:
                vistos_batch.add(cleaned); vistos_batch.add(clave)
                album = t.get("album") or {}
                images = album.get("images") or []
                image_url = images[1].get("url") if len(images) > 1 else (images[0].get("url") if images else None)
                release_year = None
                rd = album.get("release_date")
                if isinstance(rd, str) and rd:
                    release_year = rd.split("-")[0]
                artista_id = (t.get("artists") or [{}])[0].get("id")
                track_id = t.get("id")
                if all([artista_nombre, artista_id, titulo, track_id]):
                    elegidos.append((f"{artista_nombre} - {titulo}", artista_id, track_id, cleaned, image_url, release_year))
                    logging.info(f"Radio Vecinos: ✅ elegido '{artista_nombre} - {titulo}' dist={dist:.3f}")

        if elegidos:
            logging.info(f"Radio Vecinos: 🏁 devolviendo {len(elegidos)} temas (vecinos) Ttotal={perf_counter()-t0:.3f}s")
            return elegidos

        logging.warning(f"Radio Vecinos: ❌ sin elegidos finales tras filtros Ttotal={perf_counter()-t0:.3f}s")
        return None

    except SpotifyException as exc:
        logging.exception(f"Radio Vecinos: 💥 error API ({getattr(exc, 'http_status','?')}) Ttotal={perf_counter()-t0:.3f}s")
        return None
    except Exception:
        logging.exception(f"Radio Vecinos: 💥 error inesperado Ttotal={perf_counter()-t0:.3f}s")
        return None

# ==================================================
# Fallback: LÓGICA DE RECOMENDACIÓN (AÑO + PLAYLIST)
# ==================================================
def _fetch_recommendation_playlist_search_sync(
    original_title: str,
    session_played_tuples_key: Tuple[Tuple[str, str], ...],
) -> Optional[List[Tuple[str, str, str, str, Optional[str], Optional[str]]]]:
    t0 = perf_counter()
    client = _ensure_spotify_client()
    if client is None:
        logging.warning("Radio Spotify: ❌ Cliente Spotify no disponible (fallback).")
        return None
    session_played_tuples = tuple(session_played_tuples_key or ())
    session_played_cleaned_titles = {item[1].lower() for item in session_played_tuples if len(item) > 1 and item[1]}
    genres: List[str] = []; original_artist_name: Optional[str] = None
    try:
        logging.info(f"Radio Spotify: ↩️ Fallback playlists para '{original_title}'")
        search_title = clean_title(original_title, False); search_artist = extract_artist_from_title(original_title)
        query = f"{search_artist} {search_title}".strip() if search_artist else search_title
        results = client.search(q=query, type="track", limit=1); items = results.get("tracks", {}).get("items", [])
        if not items:
            logging.warning(f"Radio Spotify: No track '{query}'. T={perf_counter()-t0:.3f}s"); return None
        artists = items[0].get("artists") or []
        if not artists:
            logging.warning(f"Radio Spotify: Track '{query}' sin artista. T={perf_counter()-t0:.3f}s"); return None
        original_artist = artists[0]; artist_id = original_artist.get("id"); original_artist_name = original_artist.get("name")
        if not artist_id or not original_artist_name:
            logging.warning("Radio Spotify: Artista sin ID/nombre. T={:.3f}s".format(perf_counter()-t0)); return None

        base_search_term = ""
        try:
            info = client.artist(artist_id); genres = (info.get("genres") or [])
            if genres: base_search_term = genres[0]
            else:
                logging.warning(f"Radio Spotify: {original_artist_name} sin géneros."); base_search_term = original_artist_name
        except SpotifyException:
            logging.warning(f"Radio Spotify: Falló API géneros {artist_id}."); base_search_term = original_artist_name
        if not base_search_term:
            logging.error("Radio Spotify: No término base. T={:.3f}s".format(perf_counter()-t0)); return None

        current_year = datetime.now().year; playlist_items = []; search_term_used = ""
        search_term_year = f"{base_search_term} {current_year}"
        logging.info(f"Radio Spotify: 🔎 buscando playlists '{search_term_year}'")

        try:
            res_year = client.search(q=search_term_year, type='playlist', limit=5); items_year = res_year.get("playlists", {}).get("items", [])
            playlist_items = [p for p in items_year if isinstance(p, dict) and p.get("id")]
            if playlist_items:
                search_term_used = search_term_year; logging.info("Radio Spotify: ✅ Playlists con año.")
            else:
                logging.info("Radio Spotify: ⚠️ sin playlists por año.")
        except SpotifyException as e:
            logging.error(f"Radio Spotify: Falló búsqueda '{search_term_year}': {e}"); playlist_items = []

        if not playlist_items:
            logging.info(f"Radio Spotify: Fallback 2: término base '{base_search_term}'")
            try:
                res_base = client.search(q=base_search_term, type='playlist', limit=5); items_base = res_base.get("playlists", {}).get("items", [])
                playlist_items = [p for p in items_base if isinstance(p, dict) and p.get("id")]
                if playlist_items:
                    search_term_used = base_search_term; logging.info("Radio Spotify: ✅ Playlists base.")
                else:
                    logging.warning(f"Radio Spotify: ❗ Sin playlists para '{base_search_term}'. T={perf_counter()-t0:.3f}s"); return None
            except SpotifyException as e:
                logging.error(f"Radio Spotify: Falló fallback '{base_search_term}': {e}"); return None

        chosen_playlist = playlist_items[0]; playlist_id = chosen_playlist["id"]; playlist_name = chosen_playlist.get("name", "?")
        logging.info(f"Radio Spotify: ▶️ usando playlist '{playlist_name}' ({playlist_id}) term='{search_term_used}'")
        try:
            tracks_data = client.playlist_items(
                playlist_id,
                fields='items(track(id, name, artists(id, name), album(images,release_date)))',
                limit=50
            )
        except SpotifyException as e:
            logging.error(f"Radio Spotify: Falló obtener tracks '{playlist_name}': {e}"); return None

        playlist_tracks = tracks_data.get("items", [])
        if not playlist_tracks:
            logging.warning(f"Radio Spotify: Playlist '{playlist_name}' vacía. T={perf_counter()-t0:.3f}s"); return None

        possible_tracks_data: List[Dict] = []
        seen_in_batch: Set[str] = set()  # Para evitar duplicados dentro del lote

        for item in playlist_tracks:
            track_data = item.get("track")
            if track_data and track_data.get("id"):
                title = track_data.get("name", "")
                artist = track_data.get("artists", [{}])[0].get("name", "")

                cleaned = clean_title(title, False).lower().strip()
                artist_normalized = re.sub(r'\s+', ' ', artist.lower().strip())
                unique_key = f"{artist_normalized} {cleaned}".strip()
                unique_key = re.sub(r'\s+', ' ', unique_key)

                if cleaned and cleaned not in session_played_cleaned_titles and unique_key not in seen_in_batch and cleaned not in seen_in_batch:
                    possible_tracks_data.append(track_data)
                    seen_in_batch.add(unique_key)
                    seen_in_batch.add(cleaned)
                    logging.debug(f"Radio Spotify: + '{artist} - {title}' (key='{unique_key}')")

                    if len(possible_tracks_data) >= 30:
                        break
                else:
                    if not cleaned:
                        logging.debug(f"Radio Spotify: skip vacío '{artist} - {title}'")
                    elif cleaned in session_played_cleaned_titles:
                        logging.debug(f"Radio Spotify: skip historial '{artist} - {title}'")
                    elif cleaned in seen_in_batch or unique_key in seen_in_batch:
                        logging.debug(f"Radio Spotify: skip duplicado '{artist} - {title}'")

        if not possible_tracks_data:
            logging.warning(f"Radio Spotify: ❗ No tracks válidos/nuevos en '{playlist_name}'. T={perf_counter()-t0:.3f}s"); return None

        num_to_select = min(5, len(possible_tracks_data))
        tracks_to_recommend = random.sample(possible_tracks_data, num_to_select)

        logging.info(
            f"Radio Spotify: 🎲 seleccionadas {num_to_select} de {len(possible_tracks_data)} candidatos. "
            f"T={perf_counter()-t0:.3f}s"
        )
        recommendations_list: List[Tuple[str, str, str, str, Optional[str], Optional[str]]] = []
        for track_data in tracks_to_recommend:
            artist_name = track_data.get("artists", [{}])[0].get("name")
            artist_id = track_data.get("artists", [{}])[0].get("id")
            title = track_data.get("name")
            track_id = track_data.get("id")
            cleaned_title = clean_title(title, False).lower() if title else ""
            image_url: Optional[str] = None; release_year: Optional[str] = None
            album = track_data.get("album")
            if isinstance(album, dict):
                images = album.get("images")
                if isinstance(images, list) and images:
                    idx = 1 if len(images) > 1 else 0
                    image_url = images[idx].get("url")
                release_date = album.get("release_date")
                if isinstance(release_date, str) and release_date:
                    release_year = release_date.split('-')[0]
            if all([artist_name, artist_id, title, track_id]):
                recommendations_list.append((f"{artist_name} - {title}", artist_id, track_id, cleaned_title, image_url, release_year))
                logging.info(f"Radio Spotify: 📋 elegido '{artist_name} - {title}'")
            else:
                logging.debug(f"Radio Spotify: omito track incompleto: {track_data.get('name')}")

        if not recommendations_list:
            logging.warning("Radio Spotify: ❌ No se generaron recomendaciones (fallback)."); return None
        logging.info(f"Radio Spotify: ✅ Devolviendo {len(recommendations_list)} recomendaciones finales (fallback)")
        return recommendations_list
    except SpotifyException as exc:
        logging.exception(f"Radio Spotify: 💥 Falló ({getattr(exc, 'http_status', '?')}) en fallback")
        return None
    except Exception:
        logging.exception(f"Radio Spotify: 💥 Error inesperado en fallback")
        return None

# --- Wrapper async: intenta Vecinos primero y luego Fallback por Playlist ---
async def fetch_spotify_recommendation(
    original_title: str,
    session_played_tuples: Set[Tuple[str, str]],
) -> Optional[List[Tuple[str, str, str, str, Optional[str], Optional[str]]]]:
    cleaned = clean_title(original_title, False)
    if not cleaned:
        logging.warning("fetch_spotify_recommendation: título semilla vacío tras limpiar.")
        return None
    loop = asyncio.get_event_loop()
    history_key = tuple(sorted(list(session_played_tuples)))
    logging.info(f"Radio Engine: 🎚️ Estrategia=Vecinos→Fallback seed='{original_title}' historial={len(history_key)}")
    try:
        # 1) Intento principal: Vecinos por artista + audio-features (tolerante a 403)
        vecinos = await loop.run_in_executor(None, lambda: _fetch_radio_vecinos_sync(original_title, history_key))
        if vecinos:
            logging.info(f"Radio Engine: ✅ Vecinos produjo {len(vecinos)} temas")
            return vecinos

        logging.info("Radio Engine: ↩️ Vecinos no produjo resultados, aplicando Fallback Playlist…")
        # 2) Fallback: Playlist por año / base (tu lógica previa)
        fallback = await loop.run_in_executor(None, lambda: _fetch_recommendation_playlist_search_sync(original_title, history_key))
        if fallback:
            logging.info(f"Radio Engine: ✅ Fallback produjo {len(fallback)} temas")
        else:
            logging.warning("Radio Engine: ❌ Fallback tampoco devolvió resultados")
        return fallback
    except Exception:
        logging.exception("Radio Engine: 💥 Error en fetch_spotify_recommendation")
        return None

# --- Función get_spotify_genres_for_track (sin cambios) ---
async def get_spotify_genres_for_track(track_title: Optional[str]) -> Optional[List[str]]:
    if not track_title: return None
    client = _ensure_spotify_client()
    if not client: return None
    try:
        artist = extract_artist_from_title(track_title)
        title_cleaned = clean_title(track_title, True)
        query = f"artist:{artist} track:{title_cleaned}" if artist else f"track:{title_cleaned}"
        logging.debug(f"Spotify Genre Check: Buscando '{query}'")
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: client.search(q=query, type="track", limit=1))
        items = results.get("tracks", {}).get("items", [])
        if not items:
            logging.debug(f"Spotify Genre Check: Fallback '{track_title}'")
            results = await loop.run_in_executor(None, lambda: client.search(q=track_title, type="track", limit=1))
            items = results.get("tracks", {}).get("items", [])
            if not items:
                logging.warning(f"Spotify Genre Check: No track '{track_title}'."); return None
        artists = items[0].get("artists")
        if not artists:
            logging.warning(f"Spotify Genre Check: Track '{track_title}' sin artistas."); return None
        artist_id = artists[0].get("id")
        if not artist_id:
            logging.warning("Spotify Genre Check: Artista sin ID."); return None
        artist_info = await loop.run_in_executor(None, lambda: client.artist(artist_id))
        genres = artist_info.get("genres")
        logging.debug(f"Spotify Genre Check: Géneros para '{artists[0].get('name')}': {genres}")
        return genres if isinstance(genres, list) else None
    except SpotifyException as e:
        if hasattr(e, 'http_status') and e.http_status == 404:
            logging.warning(f"Spotify Genre Check: Not found (404) '{track_title}'.")
        else:
            logging.error(f"Spotify Genre Check: Error API: {e}")
        return None
    except Exception:
        logging.exception(f"Spotify Genre Check: Error inesperado '{track_title}'.")
        return None
