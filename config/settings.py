from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Settings:
    discord_token: str
    command_prefix: str

    spotify_client_id: Optional[str] = None
    spotify_client_secret: Optional[str] = None

    lastfm_api_key: Optional[str] = None
    lastfm_api_secret: Optional[str] = None
    youtube_api_key: Optional[str] = None
    ytdl_cookie_file: Optional[str] = None

def get_settings() -> Settings:
    from os import getenv

    token = getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN no definido en el entorno")

    prefix = getenv("COMMAND_PREFIX", "!")
    return Settings(
        discord_token=token,
        command_prefix=prefix,
        spotify_client_id=getenv("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=getenv("SPOTIFY_CLIENT_SECRET"),
        lastfm_api_key=getenv("LASTFM_API_KEY"),
        youtube_api_key=getenv("YOUTUBE_API_KEY"),
        lastfm_api_secret=getenv("LASTFM_API_SECRET"),
        ytdl_cookie_file=getenv("YTDL_COOKIE_FILE"),
    )