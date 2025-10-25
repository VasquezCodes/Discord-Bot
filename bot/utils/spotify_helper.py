# --- bot/utils/spotify_helper.py (Radio Co-ocurrencia + Feats → Fallback por Playlist | FIX None playlist item) ---

import asyncio
import logging
import math
import os
import random
import re
from time import perf_counter
from typing import Dict, List, Optional, Set, Tuple
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

# ==============================
# Utilidades de scoring y logs
# ==============================
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(map(str.lower, a or [])), set(map(str.lower, b or []))
    if not sa or not sb: return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0

def _safe_year_from_release_date(date_str: Optional[str]) -> Optional[int]:
    if not date_str: return None
    try:
        return int(date_str.split("-")[0])
    except Exception:
        return None

# ============================================================
# Radio por "co-ocurrencia en playlists" + "feats"
# ============================================================
def _fetch_radio_cooc_sync(
    original_title: str,
    session_played_tuples_key: Tuple[Tuple[str, str], ...],
    mercado: Optional[str] = None,
    devolver: int = 5,
    max_playlists: int = 12,
    tracks_por_playlist: int = 100,
    max_coartists: int = 10,
) -> Optional[List[Tuple[str, str, str, str, Optional[str], Optional[str]]]]:

    t0 = perf_counter()
    client = _ensure_spotify_client()
    if client is None:
        logging.warning("Radio Cooc: ❌ Cliente Spotify no disponible.")
        return None

    mercado = (mercado or _get_market_default()).upper()
    sesion_clean = {item[1].lower() for item in session_played_tuples_key if len(item) > 1 and item[1]}
    logging.info(f"Radio Cooc: ▶️ start title='{original_title}' market={mercado} historial={len(sesion_clean)}")

    try:
        # 1) Semilla
        t_seed = perf_counter()
        titulo_busqueda = clean_title(original_title, False)
        artista_extraido = extract_artist_from_title(original_title)
        q = f"{artista_extraido} {titulo_busqueda}".strip() if artista_extraido else titulo_busqueda
        r = client.search(q=q, type="track", limit=1)
        items = (r.get("tracks") or {}).get("items", []) or []
        if not items:
            logging.warning(f"Radio Cooc: 🔎 sin track para q='{q}'")
            return None
        seed_track = items[0]
        seed_id = seed_track.get("id")
        seed_name = seed_track.get("name", "?")
        seed_artists = seed_track.get("artists") or []
        if not seed_id or not seed_artists:
            logging.warning("Radio Cooc: seed sin id/artistas")
            return None
        seed_artist_id = seed_artists[0].get("id")
        seed_artist_name = seed_artists[0].get("name", "?")
        seed_artist = client.artist(seed_artist_id)
        seed_genres = seed_artist.get("genres") or []
        seed_year = _safe_year_from_release_date(((seed_track.get("album") or {}).get("release_date")))
        logging.info(f"Radio Cooc: 🎯 seed='{seed_artist_name} - {seed_name}' (id={seed_id}) genres={seed_genres} t={perf_counter()-t_seed:.3f}s")

        # 2) Co-ocurrencia en playlists
        t_pls = perf_counter()
        consulta_pls = [f'"{seed_artist_name}"', seed_artist_name]
        if seed_name:
            consulta_pls.append(f'"{seed_name}"')

        playlist_ids_vistos: Set[str] = set()
        pool: Dict[str, Dict] = {}  # track_id -> data

        def _agregar_track_al_pool(t: Dict, peso: float):
            if not isinstance(t, dict) or not t.get("id"): return
            if t.get("is_local"): return
            tid = t["id"]
            title = t.get("name") or ""
            main_artist = (t.get("artists") or [{}])[0] or {}
            aid = main_artist.get("id")
            aname = main_artist.get("name", "")
            cleaned = clean_title(title, False).lower().strip()
            if not cleaned: return
            if aid == seed_artist_id:  # evitar mismo artista que la semilla para diversidad
                return
            if cleaned in sesion_clean:
                return
            data = pool.get(tid)
            if not data:
                pool[tid] = {
                    "track": t,
                    "cooc": float(peso),
                    "bonus": 0.0,
                }
            else:
                data["cooc"] += float(peso)

        total_pls = 0
        total_tracks_sumados = 0

        for query in consulta_pls:
            try:
                sr = client.search(q=query, type="playlist", limit=max_playlists)
            except SpotifyException as e:
                logging.info(f"Radio Cooc: fallo search playlists q='{query}': {e}")
                continue

            raw_pls = (sr.get("playlists") or {}).get("items", []) or []
            # 🔒 FIX: filtrar None y asegurar dict
            pls = [p for p in raw_pls if isinstance(p, dict) and p.get("id")]
            if len(pls) < len(raw_pls):
                logging.debug(f"Radio Cooc: {len(raw_pls)-len(pls)} items de playlists descartados por ser None/sin id.")

            for p in pls:
                pid = p.get("id")
                if not pid or pid in playlist_ids_vistos:
                    continue
                playlist_ids_vistos.add(pid)

                try:
                    pmeta = client.playlist(pid, fields="followers.total,name")
                    followers = ((pmeta.get("followers") or {}).get("total") or 0)
                    weight = (math.log1p(followers) / 10.0) + 1.0  # al menos 1
                except SpotifyException:
                    weight = 1.0

                offset = 0
                recogidos = 0
                while recogidos < tracks_por_playlist:
                    try:
                        page = client.playlist_items(
                            pid,
                            fields="items(track(id,name,popularity,is_local,artists(id,name),album(id,images,release_date)))",
                            limit=min(100, tracks_por_playlist - recogidos),
                            offset=offset
                        )
                    except SpotifyException as e:
                        logging.debug(f"Radio Cooc: fallo playlist_items {pid}: {e}")
                        break

                    items_page = (page or {}).get("items", []) or []
                    if not items_page:
                        break

                    for it in items_page:
                        tr = (it or {}).get("track") or {}
                        _agregar_track_al_pool(tr, peso=weight)
                        total_tracks_sumados += 1

                    recogidos += len(items_page)
                    offset += len(items_page)

                total_pls += 1

        logging.info(f"Radio Cooc: 📚 playlists_escaneadas={total_pls} candidatos_pre_bonus={len(pool)} tracks_sumados={total_tracks_sumados} t={perf_counter()-t_pls:.3f}s")
        if not pool:
            logging.warning("Radio Cooc: ❗ sin candidatos por co-ocurrencia")

        # 3) Vecindad por colaboraciones (feats) usando top-tracks
        t_feats = perf_counter()
        coartists: Set[str] = set()
        try:
            tops = client.artist_top_tracks(seed_artist_id, country=mercado).get("tracks", []) or []
            for t in tops:
                for a in (t.get("artists") or []) or []:
                    aid = (a or {}).get("id")
                    if aid and aid != seed_artist_id:
                        coartists.add(aid)
        except SpotifyException as e:
            logging.debug(f"Radio Cooc: fallo artist_top_tracks seed: {e}")

        FEAT_BONUS = 0.6
        coartists = set(list(coartists)[:max_coartists])

        co_tracks_added = 0
        for aid in coartists:
            try:
                tt = client.artist_top_tracks(aid, country=mercado).get("tracks", []) or []
                for t in tt:
                    _agregar_track_al_pool(t, peso=FEAT_BONUS)
                    if isinstance(t, dict) and t.get("id") in pool:
                        pool[t["id"]]["bonus"] += FEAT_BONUS
                        co_tracks_added += 1
            except SpotifyException:
                continue

        logging.info(f"Radio Cooc: 🤝 coartists={len(coartists)} tracks_from_feats={co_tracks_added} pool_total={len(pool)} t={perf_counter()-t_feats:.3f}s")
        if not pool:
            logging.warning("Radio Cooc: ❗ sin candidatos tras co-ocurrencia+feats")
            return None

        # 4) Enriquecer artistas candidatos (géneros/popularidad)
        t_enrich = perf_counter()
        cand_artist_ids: List[str] = []
        for data in pool.values():
            tr = data["track"]
            main = (tr.get("artists") or [{}])[0] or {}
            aid = main.get("id")
            if aid:
                cand_artist_ids.append(aid)
        cand_artist_ids = list({x for x in cand_artist_ids if x})
        id2genres: Dict[str, List[str]] = {}
        id2artistpop: Dict[str, int] = {}
        for i in range(0, len(cand_artist_ids), 50):
            chunk = cand_artist_ids[i:i+50]
            try:
                arts = client.artists(chunk).get("artists", []) or []
                for a in arts:
                    if not isinstance(a, dict): continue
                    aid = a.get("id")
                    if not aid: continue
                    id2genres[aid] = a.get("genres", []) or []
                    id2artistpop[aid] = int(a.get("popularity", 0) or 0)
            except SpotifyException as e:
                logging.debug(f"Radio Cooc: fallo artists batch: {e}")
                continue
        logging.info(f"Radio Cooc: 🧩 enriquecidos artists={len(id2genres)} t={perf_counter()-t_enrich:.3f}s")

        # 5) Scoring
        coocs = [d["cooc"] + d["bonus"] for d in pool.values()]
        c_min = min(coocs) if coocs else 0.0
        c_max = max(coocs) if coocs else 1.0
        c_range = (c_max - c_min) or 1.0
        def _norm_cooc(v: float) -> float: return _clamp((v - c_min) / c_range, 0.0, 1.0)

        W_COOC, W_GENRE, W_POP, W_REC = 0.50, 0.25, 0.15, 0.10

        scored: List[Tuple[float, Dict]] = []
        years_seen: List[int] = []
        for data in pool.values():
            tr = data["track"]
            year = _safe_year_from_release_date((tr.get("album") or {}).get("release_date"))
            if year: years_seen.append(year)
        y_min = min(years_seen) if years_seen else None
        y_max = max(years_seen) if years_seen else None
        y_span = (y_max - y_min) if (y_min is not None and y_max is not None) else None

        def _recency_score(y: Optional[int]) -> float:
            if y is None: return 0.5
            if seed_year: return _clamp(1.0 - (abs(y - seed_year) / 10.0), 0.0, 1.0)
            if y_span and y_span > 0: return _clamp((y - y_min) / y_span, 0.0, 1.0)
            return 0.5

        for tid, data in pool.items():
            tr = data["track"]
            main_artist = (tr.get("artists") or [{}])[0] or {}
            aid = main_artist.get("id")
            aname = main_artist.get("name", "")
            title = tr.get("name", "")
            cleaned = clean_title(title, False).lower().strip()
            if not cleaned:
                continue
            S_cooc = _norm_cooc(data["cooc"] + data["bonus"])
            genres_cand = id2genres.get(aid, [])
            S_genre = _jaccard(seed_genres, genres_cand)
            S_pop = (tr.get("popularity", 0) or 0) / 100.0
            year = _safe_year_from_release_date((tr.get("album") or {}).get("release_date"))
            S_rec = _recency_score(year)
            score = (W_COOC * S_cooc) + (W_GENRE * S_genre) + (W_POP * S_pop) + (W_REC * S_rec)
            scored.append((score, tr))

        scored.sort(key=lambda x: x[0], reverse=True)
        muestra = ", ".join([f"{(s[1].get('name','?'))}:{s[0]:.2f}" for s in scored[:5]])
        logging.debug(f"Radio Cooc: 🧭 top5_scores=[{muestra}]")

        # 6) Selección final y formateo
        elegidos: List[Tuple[str, str, str, str, Optional[str], Optional[str]]] = []
        vistos_batch: Set[str] = set()
        for score, t in scored:
            if len(elegidos) >= devolver: break
            titulo = t.get("name", "")
            artista_nombre = ((t.get("artists") or [{}])[0] or {}).get("name", "")
            artista_id = ((t.get("artists") or [{}])[0] or {}).get("id")
            cleaned = clean_title(titulo, False).lower().strip()
            clave = re.sub(r"\s+", " ", f"{artista_nombre.lower().strip()} {cleaned}")
            if cleaned and cleaned not in sesion_clean and clave not in vistos_batch:
                vistos_batch.add(cleaned); vistos_batch.add(clave)
                album = t.get("album") or {}
                images = album.get("images") or []
                image_url = images[1].get("url") if len(images) > 1 else (images[0].get("url") if images else None)
                release_year = None
                rd = album.get("release_date")
                if isinstance(rd, str) and rd:
                    release_year = rd.split("-")[0]
                track_id = t.get("id")
                if all([artista_nombre, artista_id, titulo, track_id]):
                    elegidos.append((f"{artista_nombre} - {titulo}", artista_id, track_id, cleaned, image_url, release_year))
                    logging.info(f"Radio Cooc: ✅ elegido '{artista_nombre} - {titulo}' score={score:.3f}")

        if elegidos:
            logging.info(f"Radio Cooc: 🏁 devolviendo {len(elegidos)} temas Ttotal={perf_counter()-t0:.3f}s")
            return elegidos

        logging.warning(f"Radio Cooc: ❌ sin elegidos finales tras filtros Ttotal={perf_counter()-t0:.3f}s")
        return None

    except SpotifyException as exc:
        logging.exception(f"Radio Cooc: 💥 error API ({getattr(exc, 'http_status','?')}) Ttotal={perf_counter()-t0:.3f}s")
        return None
    except Exception:
        logging.exception(f"Radio Cooc: 💥 error inesperado Ttotal={perf_counter()-t0:.3f}s")
        return None

# ==================================================
# Fallback: LÓGICA DE RECOMENDACIÓN (AÑO + PLAYLIST)
# ==================================================
def _fetch_recommendation_playlist_search_sync(
    original_title: str,
    session_played_tuples_key: Tuple[Tuple[str, str], ...],
) -> Optional[List[Tuple[str, str, str, str, Optional[str], Optional[str]]]]:
    """Fallback simple: buscar en playlists relacionadas."""
    client = _ensure_spotify_client()
    if client is None:
        logging.warning("Radio Fallback: ❌ Cliente Spotify no disponible.")
        return None
    
    sesion_clean = {item[1].lower() for item in session_played_tuples_key if len(item) > 1 and item[1]}
    logging.info(f"Radio Fallback: ▶️ start title='{original_title}' historial={len(sesion_clean)}")
    
    try:
        # Buscar el track original
        titulo_busqueda = clean_title(original_title, False)
        artista_extraido = extract_artist_from_title(original_title)
        q = f"{artista_extraido} {titulo_busqueda}".strip() if artista_extraido else titulo_busqueda
        
        r = client.search(q=q, type="track", limit=1)
        items = (r.get("tracks") or {}).get("items", []) or []
        if not items:
            logging.warning(f"Radio Fallback: 🔎 sin track para q='{q}'")
            return None
        
        seed_track = items[0]
        seed_name = seed_track.get("name", "?")
        seed_artists = seed_track.get("artists") or []
        if not seed_artists:
            return None
        seed_artist_name = seed_artists[0].get("name", "?")
        
        logging.info(f"Radio Fallback: 🎯 seed='{seed_artist_name} - {seed_name}'")
        
        # Buscar playlists relacionadas
        playlist_query = f'"{seed_artist_name}"'
        sr = client.search(q=playlist_query, type="playlist", limit=5)
        playlists = (sr.get("playlists") or {}).get("items", []) or []
        playlists = [p for p in playlists if isinstance(p, dict) and p.get("id")]
        
        if not playlists:
            logging.warning("Radio Fallback: ❌ sin playlists")
            return None
        
        # Recoger tracks de las playlists
        candidates: List[Tuple[str, str, str, str, Optional[str], Optional[str]]] = []
        for playlist in playlists[:3]:  # Solo las primeras 3 playlists
            pid = playlist.get("id")
            if not pid:
                continue
            
            try:
                page = client.playlist_items(
                    pid,
                    fields="items(track(id,name,artists(id,name),album(images,release_date)))",
                    limit=20
                )
                items_page = (page or {}).get("items", []) or []
                
                for it in items_page:
                    tr = (it or {}).get("track") or {}
                    if not isinstance(tr, dict) or not tr.get("id") or tr.get("is_local"):
                        continue
                    
                    titulo = tr.get("name", "")
                    main_artist = (tr.get("artists") or [{}])[0] or {}
                    artista_nombre = main_artist.get("name", "")
                    artista_id = main_artist.get("id")
                    track_id = tr.get("id")
                    cleaned = clean_title(titulo, False).lower().strip()
                    
                    if not cleaned or cleaned in sesion_clean:
                        continue
                    
                    album = tr.get("album") or {}
                    images = album.get("images") or []
                    image_url = images[1].get("url") if len(images) > 1 else (images[0].get("url") if images else None)
                    release_year = None
                    rd = album.get("release_date")
                    if isinstance(rd, str) and rd:
                        release_year = rd.split("-")[0]
                    
                    if all([artista_nombre, artista_id, titulo, track_id]):
                        candidates.append((f"{artista_nombre} - {titulo}", artista_id, track_id, cleaned, image_url, release_year))
                        
                        if len(candidates) >= 5:
                            break
            except SpotifyException as e:
                logging.debug(f"Radio Fallback: error en playlist {pid}: {e}")
                continue
            
            if len(candidates) >= 5:
                break
        
        if candidates:
            logging.info(f"Radio Fallback: ✅ devolviendo {len(candidates)} temas")
            return candidates
        
        logging.warning("Radio Fallback: ❌ sin candidatos finales")
        return None
        
    except Exception:
        logging.exception("Radio Fallback: 💥 error inesperado")
        return None
    

# --- Wrapper async ---
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
    logging.info(f"Radio Engine: 🎚️ Estrategia=Cooc+Feats→Fallback seed='{original_title}' historial={len(history_key)}")
    try:
        vecinos = await loop.run_in_executor(None, lambda: _fetch_radio_cooc_sync(original_title, history_key))
        if vecinos:
            logging.info(f"Radio Engine: ✅ Cooc+Feats produjo {len(vecinos)} temas")
            return vecinos
        logging.info("Radio Engine: ↩️ Cooc+Feats no produjo resultados, aplicando Fallback Playlist…")
        fallback = await loop.run_in_executor(None, lambda: _fetch_recommendation_playlist_search_sync(original_title, history_key))
        if fallback:
            logging.info(f"Radio Engine: ✅ Fallback produjo {len(fallback)} temas")
        else:
            logging.warning("Radio Engine: ❌ Fallback tampoco devolvió resultados")
        return fallback
    except Exception:
        logging.exception("Radio Engine: 💥 Error en fetch_spotify_recommendation")
        return None
