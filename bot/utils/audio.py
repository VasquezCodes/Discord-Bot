# --- bot/utils/audio.py ---

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple # Import Dict
import time # Para medir tiempos

import yt_dlp

# Configuración de logging (solo para este módulo, opcional)
log = logging.getLogger(__name__)

# --- YTDL_OPTIONS (Última versión) ---
YTDL_OPTIONS = {
    # Formato más simple y recomendado
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
    # Eliminamos extractor_args por ahora
}
# ---

# --- ¡CORRECCIÓN AQUÍ! ---
# Estas deben ser strings base. El player_loop añadirá los headers dinámicamente.
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin"
FFMPEG_OPTIONS = "-vn -loglevel error"
# --- FIN DE LA CORRECCIÓN ---

# --- CONFIGURACIÓN DE COOKIES (Descomenta si las necesitas) ---
# from config.settings import get_settings
# settings = get_settings()
# if settings.ytdl_cookie_file:
#     cookie_path = Path(settings.ytdl_cookie_file).expanduser()
#     if cookie_path.is_file():
#         YTDL_OPTIONS["cookiefile"] = str(cookie_path)
#         logging.info(f"Usando archivo de cookies: {cookie_path}")
#     else:
#         logging.warning(f"Archivo de cookies especificado pero no encontrado: {cookie_path}")
# --- FIN CONFIGURACIÓN DE COOKIES ---

# Crear instancia global de YoutubeDL
try:
    log.info("Inicializando YoutubeDL...")
    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
    log.info("YoutubeDL inicializado.")
except Exception as e:
    log.exception(f"Error fatal inicializando YoutubeDL: {e}")
    ytdl = None


@dataclass
class AudioTrack:
    """Representa una pista de audio obtenida."""
    title: str
    stream_url: str  # URL directa para FFmpeg
    source_url: str  # URL de la página (YouTube, etc.)
    headers: Dict[str, str] # ¡Esto es esencial!
    # Campo opcional para guardar ID si viene de Last.fm/Spotify/etc.
    spotify_track_id: Optional[str] = None

async def search_tracks(query: str, *, limit: int = 5) -> List[AudioTrack]:
    """Busca pistas usando yt-dlp. Lanza ValueError si no encuentra nada."""
    if ytdl is None:
        log.error("search_tracks: YoutubeDL no está inicializado.")
        raise RuntimeError("YoutubeDL no está inicializado.")

    loop = asyncio.get_event_loop()
    data = None
    start_time = time.monotonic()
    log.info(f"Iniciando búsqueda yt-dlp para: '{query}' (límite: {limit})")

    try:
        # Ejecutar yt-dlp en un executor para no bloquear el loop de asyncio
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        duration = time.monotonic() - start_time
        log.info(f"Búsqueda yt-dlp para '{query}' completada en {duration:.2f} segundos.")

    except yt_dlp.utils.DownloadError as e:
        duration = time.monotonic() - start_time
        log.error(f"Error yt-dlp DownloadError tras {duration:.2f}s buscando '{query}': {e}")
        # Simplificar mensaje de error para el usuario
        error_message = f"No pude obtener resultados para: {query}"
        if "is unavailable" in str(e): error_message += " (Video no disponible)"
        elif "Private video" in str(e): error_message += " (Video privado)"
        elif "age restricted" in str(e): error_message += " (Restricción de edad)"
        raise ValueError(error_message) from e
    except Exception as e:
        duration = time.monotonic() - start_time
        log.exception(f"Error INESPERADO en yt-dlp tras {duration:.2f}s buscando '{query}'")
        raise ValueError(f"Ocurrió un error inesperado al buscar: {query}") from e

    entries = []
    if not data:
        log.error(f"yt-dlp no devolvió datos (pero no lanzó error) para: {query}")
        raise ValueError(f"yt-dlp no devolvió datos válidos para: {query}")

    # Manejar si es una lista de resultados (búsqueda) o un solo item (enlace directo)
    if "entries" in data:
        log.debug(f"Procesando {len(data.get('entries', []))} entradas para '{query}'")
        for entry in data["entries"] or []:
            if not entry: continue
            stream_url = entry.get("url")
            page_url = entry.get("webpage_url", entry.get("original_url", query))
            title = entry.get("title", "Título desconocido")
            headers = entry.get("http_headers", {}) # ¡Extrayendo headers!
            if not stream_url:
                logging.warning(f"Entrada de búsqueda sin 'url' para '{title}' (ID: {entry.get('id', 'N/A')}). Saltando.")
                continue
            entries.append(AudioTrack(title=title, stream_url=stream_url, source_url=page_url, headers=headers)) # ¡Guardando headers!
            if len(entries) >= limit:
                break
    else:
        # Es un solo resultado
        log.debug(f"Procesando resultado único para '{query}'")
        stream_url = data.get("url")
        page_url = data.get("webpage_url", data.get("original_url", query))
        title = data.get("title", "Título desconocido")
        headers = data.get("http_headers", {}) # ¡Extrayendo headers!
        if stream_url and page_url and title:
            entries.append(AudioTrack(title=title, stream_url=stream_url, source_url=page_url, headers=headers)) # ¡Guardando headers!
        else:
            logging.warning(f"Resultado único sin datos completos para '{query}'")

    if not entries:
        log.warning(f"No se extrajeron pistas VÁLIDAS de los datos de yt-dlp para: {query}")
        raise ValueError(f"No se encontraron pistas válidas o reproducibles para: {query}")

    log.info(f"Se encontraron {len(entries)} pistas válidas para '{query}'.")
    return entries


async def fetch_track(query: str) -> AudioTrack:
    """Obtiene la *primera* pista válida encontrada para una consulta."""
    log.info(f"fetch_track: Buscando la primera pista para '{query}'")
    try:
        tracks = await search_tracks(query, limit=1)
        log.info(f"fetch_track: Pista encontrada para '{query}': '{tracks[0].title}'")
        return tracks[0]
    except ValueError as e:
         log.error(f"fetch_track: Error al buscar '{query}': {e}")
         raise
    except Exception as e:
         log.exception(f"Error inesperado en fetch_track para '{query}'")
         raise ValueError(f"Error al obtener la pista: {e}") from e