# --- bot/utils/spotify_helper.py (Radio por Playlist Año + Fallback - Lote de 5 - Corregido Syntax Error) ---

import asyncio
import logging
import random
import re
from typing import List, Optional, Set, Tuple, Dict
from datetime import datetime

# --- Imports y Configuración Inicial ---
try:
    import spotipy
    from spotipy import Spotify
    from spotipy.exceptions import SpotifyException
    from spotipy.oauth2 import SpotifyClientCredentials
except ImportError: # pragma: no cover
    spotipy = None; Spotify = None; SpotifyClientCredentials = None # type: ignore
    class SpotifyException(Exception): pass
    logging.error("SPOTIPY NO INSTALADO. 'pip install spotipy'")

try: from config.settings import get_settings
except ImportError:
    import os
    class MockSettings:
        spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    def get_settings(): return MockSettings()
    logging.warning("No se encontró config.settings, usando os.getenv para Spotify.")

_SPOTIFY_CLIENT: Optional[Spotify] = None
_SPOTIFY_CREDENTIALS_WARNING_EMITTED = False

# --- Regex, clean_title, extract_artist_from_title ---
_BRACKET_PATTERN = re.compile(r"\s*[\(\[\{].*"); _EXTRA_SEP_PATTERN = re.compile(r"\s*(?:\||//|★|☆).*")
_EXTRA_WORDS_PATTERN = re.compile(r"""\s+\b(official|video|audio|lyric|lyrics|visualizer|remaster(?:ed)?|hd|4k|oficial|live|acústico|acoustic|explicit|version|edit|mix|remix|radio|original|extended|deluxe|club|instrumental|karaoke|performance|session|cover)\b""", re.IGNORECASE | re.VERBOSE)
_ARTIST_TITLE_SEP = re.compile(r"^(.*?)\s+-\s+(.+)$"); _ARTIST_SEP = re.compile(r"\s+(?:x|&|,|(?:vs|feat|ft)\.?)+\s+", re.IGNORECASE)
_ALT_ARTIST_SEP = re.compile(r"^(.*?)\s+\|{1,2}\s+.+$"); _CLEANUP_PATTERN = re.compile(r"\s+#\d+$|&[a-zA-Z]+;")

def clean_title(title: str, remove_artist_pattern: bool = True) -> str:
    if not title: return ""
    text = title.strip()
    if remove_artist_pattern:
        hyphen_match = _ARTIST_TITLE_SEP.match(text)
        if hyphen_match: text = hyphen_match.group(2).strip()
        elif "|" in text: text = text.split("|", 1)[-1].strip()
    text = _CLEANUP_PATTERN.sub("", text); text = _BRACKET_PATTERN.sub("", text)
    text = _EXTRA_SEP_PATTERN.sub("", text); text = _EXTRA_WORDS_PATTERN.sub("", text)
    text = text.replace("_", " "); text = re.sub(r"\s+", " ", text); text = text.strip(" -|/")
    return text.strip()

def extract_artist_from_title(title: str) -> Optional[str]:
    if not title: return None
    text = title.strip()
    hyphen_match = _ARTIST_TITLE_SEP.match(text)
    if hyphen_match: artist = hyphen_match.group(1).strip()
    else:
        alt_match = _ALT_ARTIST_SEP.match(text)
        if not alt_match: return None
        artist = alt_match.group(1).strip()
    artist = _CLEANUP_PATTERN.sub("", artist); artist = re.sub(r"\s+", " ", artist); artist = artist.strip(" -|/")
    if not artist: return None
    primary = _ARTIST_SEP.split(artist)[0].strip(); return primary or None

# --- Función _ensure_spotify_client (CORREGIDA) ---
def _ensure_spotify_client() -> Optional[Spotify]:
    global _SPOTIFY_CLIENT, _SPOTIFY_CREDENTIALS_WARNING_EMITTED
    if _SPOTIFY_CLIENT is not None: return _SPOTIFY_CLIENT
    if spotipy is None:
        if not _SPOTIFY_CREDENTIALS_WARNING_EMITTED: logging.warning("Radio Spotify: spotipy no instalado."); _SPOTIFY_CREDENTIALS_WARNING_EMITTED = True
        return None
    settings = get_settings(); client_id = getattr(settings, "spotify_client_id", None); client_secret = getattr(settings, "spotify_client_secret", None)
    if not client_id or not client_secret:
        if not _SPOTIFY_CREDENTIALS_WARNING_EMITTED: logging.warning("Radio Spotify: Credenciales faltantes."); _SPOTIFY_CREDENTIALS_WARNING_EMITTED = True
        return None
    # --- !! CORRECCIÓN AQUÍ: Añadir bloque try...except !! ---
    try:
        auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        _SPOTIFY_CLIENT = spotipy.Spotify(auth_manager=auth_manager, requests_timeout=10, retries=2)
        _SPOTIFY_CREDENTIALS_WARNING_EMITTED = False; logging.info("Radio Spotify: Cliente inicializado.")
    except Exception as exc: # Capturar cualquier excepción durante la inicialización
        logging.exception(f"Radio Spotify: No se pudo iniciar cliente: {exc}")
        _SPOTIFY_CLIENT = None # Asegurarse que sea None si falla
    # --- FIN CORRECCIÓN ---
    return _SPOTIFY_CLIENT


# --- LÓGICA DE RECOMENDACIÓN (AÑO + FALLBACK - LOTE) ---
def _fetch_recommendation_playlist_search_sync(
    original_title: str,
    session_played_tuples_key: Tuple[Tuple[str, str], ...],
) -> Optional[List[Tuple[str, str, str, str, Optional[str], Optional[str]]]]:
    # ... (código igual que la versión anterior completa) ...
    client = _ensure_spotify_client();
    if client is None: return None
    session_played_tuples = tuple(session_played_tuples_key or ());
    session_played_cleaned_titles = {item[1].lower() for item in session_played_tuples if len(item) > 1 and item[1]};
    genres: List[str] = []; original_artist_name: Optional[str] = None
    try:
        logging.debug(f"Radio Spotify (Batch): Buscando '{original_title}'")
        search_title = clean_title(original_title, False); search_artist = extract_artist_from_title(original_title)
        query = f"{search_artist} {search_title}".strip() if search_artist else search_title
        results = client.search(q=query, type="track", limit=1); items = results.get("tracks", {}).get("items", [])
        if not items: logging.warning(f"Radio Spotify: No track '{query}'."); return None
        artists = items[0].get("artists") or []
        if not artists: logging.warning(f"Radio Spotify: Track '{query}' sin artista."); return None
        original_artist = artists[0]; artist_id = original_artist.get("id"); original_artist_name = original_artist.get("name")
        if not artist_id or not original_artist_name: logging.warning("Radio Spotify: Artista sin ID/nombre."); return None
        base_search_term = ""
        try:
            info = client.artist(artist_id); genres = (info.get("genres") or [])
            if genres: base_search_term = genres[0]
            else: logging.warning(f"Radio Spotify: {original_artist_name} sin géneros."); base_search_term = original_artist_name
        except SpotifyException: logging.warning(f"Radio Spotify: Falló API géneros {artist_id}."); base_search_term = original_artist_name
        if not base_search_term: logging.error("Radio Spotify: No término base."); return None
        current_year = datetime.now().year; playlist_items = []; search_term_used = ""
        search_term_year = f"{base_search_term} {current_year}"; logging.info(f"Radio Spotify: Buscando playlists '{search_term_year}'.")
        try:
            res_year = client.search(q=search_term_year, type='playlist', limit=5); items_year = res_year.get("playlists", {}).get("items", [])
            playlist_items = [p for p in items_year if isinstance(p, dict) and p.get("id")]
            if playlist_items: search_term_used = search_term_year; logging.info(f"Radio Spotify: Playlists con año.")
            else: logging.warning(f"Radio Spotify: No playlists '{search_term_year}'.")
        except SpotifyException as e: logging.error(f"Radio Spotify: Falló búsqueda '{search_term_year}': {e}"); playlist_items = []
        if not playlist_items:
            logging.info(f"Radio Spotify: Fallback '{base_search_term}'.")
            try:
                res_base = client.search(q=base_search_term, type='playlist', limit=5); items_base = res_base.get("playlists", {}).get("items", [])
                playlist_items = [p for p in items_base if isinstance(p, dict) and p.get("id")]
                if playlist_items: search_term_used = base_search_term; logging.info(f"Radio Spotify: Playlists base.")
                else: logging.warning(f"Radio Spotify: No playlists para '{base_search_term}'."); return None
            except SpotifyException as e: logging.error(f"Radio Spotify: Falló fallback '{base_search_term}': {e}"); return None
        chosen_playlist = playlist_items[0]; playlist_id = chosen_playlist["id"]; playlist_name = chosen_playlist.get("name", "?")
        logging.info(f"Radio Spotify: Usando playlist '{playlist_name}' ({playlist_id}) encontrada con '{search_term_used}'")
        try: tracks_data = client.playlist_items(playlist_id, fields='items(track(id, name, artists(id, name), album(images,release_date)))', limit=50)
        except SpotifyException as e: logging.error(f"Radio Spotify: Falló obtener tracks '{playlist_name}': {e}"); return None
        playlist_tracks = tracks_data.get("items", [])
        if not playlist_tracks: logging.warning(f"Radio Spotify: Playlist '{playlist_name}' vacía."); return None
        possible_tracks_data: List[Dict] = []
        seen_in_batch: Set[str] = set()  # Para evitar duplicados dentro del lote
        
        for item in playlist_tracks:
            track_data = item.get("track")
            if track_data and track_data.get("id"):
                 title = track_data.get("name", "")
                 artist = track_data.get("artists", [{}])[0].get("name", "")
                 
                 # Limpiar título para comparación
                 cleaned = clean_title(title, False).lower().strip()
                 # Normalizar artista (remover espacios extras, lowercase)
                 artist_normalized = re.sub(r'\s+', ' ', artist.lower().strip())
                 # Crear clave única con artista + título para mejor detección
                 unique_key = f"{artist_normalized} {cleaned}".strip()
                 # Normalizar la clave única (remover espacios múltiples)
                 unique_key = re.sub(r'\s+', ' ', unique_key)
                 
                 # Verificar que no esté en historial Y no sea duplicado en el lote actual
                 # Verificar AMBOS: unique_key (artista+título) Y cleaned (solo título)
                 if cleaned and cleaned not in session_played_cleaned_titles and unique_key not in seen_in_batch and cleaned not in seen_in_batch:
                     possible_tracks_data.append(track_data)
                     seen_in_batch.add(unique_key)  # Marcar como visto
                     seen_in_batch.add(cleaned)  # También añadir solo el título
                     logging.info(f"Radio Spotify: ✅ Aceptando '{artist} - {title}' (key: '{unique_key}')")
                     
                     # Obtener hasta 30 candidatos para tener un pool variado
                     if len(possible_tracks_data) >= 30:
                         break
                 else:
                     # Logging cuando se detecta duplicado
                     if not cleaned:
                         logging.warning(f"Radio Spotify: ⚠️ Saltando '{artist} - {title}' - título vacío después de limpiar")
                     elif cleaned in session_played_cleaned_titles:
                         logging.info(f"Radio Spotify: ❌ Saltando '{artist} - {title}' - ya en historial de sesión")
                     elif cleaned in seen_in_batch:
                         logging.info(f"Radio Spotify: ❌ Saltando '{artist} - {title}' - título duplicado en lote actual")
                     elif unique_key in seen_in_batch:
                         logging.info(f"Radio Spotify: ❌ Saltando '{artist} - {title}' - unique_key duplicado en lote (key: '{unique_key}')")
        
        if not possible_tracks_data: logging.warning(f"Radio Spotify: No tracks válidos/nuevos en '{playlist_name}'."); return None
        
        # Seleccionar 5 canciones aleatorias del pool (hasta 30 candidatos)
        num_to_select = min(5, len(possible_tracks_data))
        tracks_to_recommend = random.sample(possible_tracks_data, num_to_select)
        
        logging.info(f"Radio Spotify: Seleccionadas {num_to_select} canciones aleatorias de un pool de {len(possible_tracks_data)} candidatos.")
        recommendations_list = []
        for track_data in tracks_to_recommend:
            artist_name = track_data.get("artists", [{}])[0].get("name"); artist_id = track_data.get("artists", [{}])[0].get("id")
            title = track_data.get("name"); track_id = track_data.get("id")
            cleaned_title = clean_title(title, False).lower() if title else ""
            image_url: Optional[str] = None; release_year: Optional[str] = None
            album = track_data.get("album")
            if isinstance(album, dict):
                images = album.get("images");
                if isinstance(images, list) and images: idx = 1 if len(images) > 1 else 0; image_url = images[idx].get("url")
                release_date = album.get("release_date");
                if isinstance(release_date, str) and release_date: release_year = release_date.split('-')[0]
            if all([artist_name, artist_id, title, track_id]):
                recommendations_list.append((f"{artist_name} - {title}", artist_id, track_id, cleaned_title, image_url, release_year))
                logging.info(f"Radio Spotify: 📋 Recomendación #{len(recommendations_list)}: '{artist_name} - {title}'")
            else: logging.warning(f"Radio Spotify: Track incompleto omitido: {track_data.get('name')}")
        if not recommendations_list: logging.warning("Radio Spotify: No se generaron recomendaciones."); return None
        logging.info(f"Radio Spotify: 🎵 Devolviendo {len(recommendations_list)} recomendaciones finales")
        return recommendations_list
    except SpotifyException as exc: logging.exception(f"Radio Spotify: Falló ({getattr(exc, 'http_status', '?')}...)"); return None
    except Exception: logging.exception(f"Radio Spotify: Error inesperado '{original_title}'"); return None

# --- Función wrapper async fetch_spotify_recommendation ---
async def fetch_spotify_recommendation(
    original_title: str,
    session_played_tuples: Set[Tuple[str, str]],
) -> Optional[List[Tuple[str, str, str, str, Optional[str], Optional[str]]]]:
    # ... (código igual) ...
    cleaned = clean_title(original_title, False);
    if not cleaned: return None
    loop = asyncio.get_event_loop(); history_key = tuple(sorted(list(session_played_tuples)))
    try:
        result = await loop.run_in_executor(None, lambda: _fetch_recommendation_playlist_search_sync(original_title, history_key))
        return result
    except Exception: logging.exception("Error en run_in_executor (playlist search - batch)"); return None

# --- Función get_spotify_genres_for_track (sin cambios) ---
async def get_spotify_genres_for_track(track_title: Optional[str]) -> Optional[List[str]]:
    # ... (código igual) ...
    if not track_title: return None
    client = _ensure_spotify_client();
    if not client: return None
    try:
        artist = extract_artist_from_title(track_title); title_cleaned = clean_title(track_title, True)
        query = f"artist:{artist} track:{title_cleaned}" if artist else f"track:{title_cleaned}"
        logging.debug(f"Spotify Genre Check: Buscando '{query}'")
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: client.search(q=query, type="track", limit=1))
        items = results.get("tracks", {}).get("items", [])
        if not items:
            logging.debug(f"Spotify Genre Check: Fallback '{track_title}'")
            results = await loop.run_in_executor(None, lambda: client.search(q=track_title, type="track", limit=1))
            items = results.get("tracks", {}).get("items", [])
            if not items: logging.warning(f"Spotify Genre Check: No track '{track_title}'."); return None
        artists = items[0].get("artists");
        if not artists: logging.warning(f"Spotify Genre Check: Track '{track_title}' sin artistas."); return None
        artist_id = artists[0].get("id");
        if not artist_id: logging.warning("Spotify Genre Check: Artista sin ID."); return None
        artist_info = await loop.run_in_executor(None, lambda: client.artist(artist_id))
        genres = artist_info.get("genres"); logging.debug(f"Spotify Genre Check: Géneros para '{artists[0].get('name')}': {genres}")
        return genres if isinstance(genres, list) else None
    except SpotifyException as e:
        if hasattr(e, 'http_status') and e.http_status == 404: logging.warning(f"Spotify Genre Check: Not found (404) '{track_title}'.")
        else: logging.error(f"Spotify Genre Check: Error API: {e}")
        return None
    except Exception: logging.exception(f"Spotify Genre Check: Error inesperado '{track_title}'."); return None