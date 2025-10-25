# --- bot/cogs/music.py (Wavelink - Radio + Anti-repetidos + TrackStuck Fix + Spotify URL resolver) ---

import asyncio
import logging
import re
from typing import cast, Optional, Dict, Set, Tuple, List

import discord
from discord.ext import commands
import wavelink

# --- Importar helpers ---
try:
    from bot.utils.spotify_helper import (
        fetch_spotify_recommendation,
        clean_title,
        _ensure_spotify_client
    )
except ImportError:
    logging.error("¡¡ERROR!! No se pudo importar spotify_helper.")

    async def fetch_spotify_recommendation(*a, **kw):
        return None

    def clean_title(t: str, **kw) -> str:
        return t

    class DummySpotify:
        pass

    def _ensure_spotify_client():
        return None

# Importar MyBot para type hinting (asumiendo que está en __main__)
try:
    from __main__ import MyBot
except ImportError:
    class MyBot(commands.Bot):
        wavelink_ready: asyncio.Event = asyncio.Event()
    logging.warning("No se pudo importar MyBot desde __main__ para type hint.")

# Regex para links de Spotify
SPOTIFY_URL_REGEX = re.compile(r"https?://open\.spotify\.com/(?P<type>track|album|playlist)/(?P<id>[a-zA-Z0-9]+)")

# --- Categorías de Género Expandidas ---
URBANO_GENRES = {
    'reggaeton', 'trap latino', 'urbano latino', 'latin hip hop', 'trap argentino',
    'argentine hip hop', 'r&b en espanol', 'pop reggaeton', 'latin pop', 'dembow',
    'reggaeton colombiano', 'trap chileno', 'trap mexicano', 'cumbia urbana',
    'hip hop', 'rap', 'trap', 'drill', 'gangsta rap', 'conscious hip hop', 'boom bap',
    'southern hip hop', 'west coast rap', 'east coast hip hop', 'cloud rap', 'mumble rap',
    'uk hip hop', 'grime', 'uk drill', 'afro trap', 'trap soul', 'emo rap',
    'r&b', 'contemporary r&b', 'alternative r&b', 'neo soul', 'soul', 'funk',
    'quiet storm', 'urban contemporary', 'new jack swing'
}

ROCK_METAL_GENRES = {
    'rock', 'alternative rock', 'indie rock', 'classic rock', 'hard rock', 'soft rock',
    'progressive rock', 'psychedelic rock', 'garage rock', 'art rock', 'glam rock',
    'modern rock', 'post-grunge', 'grunge', 'britpop', 'madchester', 'shoegaze',
    'noise rock', 'math rock', 'post-rock', 'space rock',
    'punk', 'punk rock', 'pop punk', 'post-punk', 'hardcore punk', 'skate punk',
    'ska punk', 'horror punk', 'anarcho-punk', 'street punk',
    'metal', 'heavy metal', 'thrash metal', 'death metal', 'black metal', 'doom metal',
    'power metal', 'progressive metal', 'symphonic metal', 'folk metal', 'viking metal',
    'alternative metal', 'nu metal', 'metalcore', 'deathcore', 'djent', 'groove metal',
    'industrial metal', 'gothic metal', 'melodic death metal', 'technical death metal',
    'emo', 'screamo', 'post-hardcore', 'melodic hardcore'
}

POP_CHILL_GENRES = {
    'pop', 'dance pop', 'electropop', 'synth-pop', 'synthpop', 'indie pop', 'art pop',
    'chamber pop', 'pop rock', 'power pop', 'jangle pop', 'noise pop', 'hyperpop',
    'bubblegum pop', 'teen pop', 'post-teen pop', 'europop', 'k-pop', 'j-pop',
    'electronic', 'edm', 'house', 'deep house', 'tech house', 'progressive house',
    'electro house', 'future house', 'tropical house', 'techno', 'trance',
    'dubstep', 'drum and bass', 'future bass', 'chillstep', 'downtempo', 'ambient',
    'idm', 'glitch', 'vaporwave', 'synthwave', 'chillwave', 'lo-fi', 'lo-fi hip hop',
    'chill', 'chillout', 'chillhop', 'indie', 'indie folk', 'indie soul', 'indie pop',
    'bedroom pop', 'dream pop', 'slowcore', 'sadcore', 'lo-fi indie',
    'singer-songwriter', 'acoustic', 'folk', 'folk pop', 'chamber folk', 'freak folk',
    'anti-folk', 'stomp and holler', 'alternative', 'alt z', 'alt pop', 'indietronica',
    'folktronica', 'electronica', 'new wave', 'new romantic', 'sophisti-pop', 'yacht rock',
    'soft rock', 'adult contemporary', 'easy listening', 'lounge', 'bossa nova', 'jazz pop'
}


class MusicWavelinkCog(commands.Cog, name="Music"):
    """Cog de Música con Wavelink, Radio, Imágenes, Links Spotify y Comentarios."""

    def __init__(self, bot: commands.Bot):
        self.bot: MyBot = cast(MyBot, bot)
        self.last_text_channel: Dict[int, discord.TextChannel] = {}
        self.radio_enabled: Dict[int, bool] = {}
        self.radio_session_history: Dict[int, Set[str]] = {}
        # Retries por tema para TrackStuck
        self._stuck_retries: Dict[Tuple[int, str], int] = {}
        # Tracking de intentos de alternativas para evitar loops
        self._alternative_attempts: Dict[Tuple[int, str], int] = {}

    def build_embed(self, title: str, description: str, color=discord.Color.blurple()) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_footer(text="Cornelius Music (Lavalink)")
        return embed

    # --- Funciones Helper Internas ---
    def _is_radio_enabled(self, guild_id: int) -> bool:
        return self.radio_enabled.get(guild_id, False)

    def _get_radio_history(self, guild_id: int) -> Set[str]:
        return self.radio_session_history.setdefault(guild_id, set())

    def _add_to_radio_history(self, guild_id: int, title: str):
        if title:
            cleaned = clean_title(title, remove_artist_pattern=False).lower()
            if cleaned:
                self._get_radio_history(guild_id).add(cleaned)

    def _clear_radio_history(self, guild_id: int):
        if guild_id in self.radio_session_history:
            self.radio_session_history[guild_id].clear()
            logging.info(f"Historial radio limpiado G:{guild_id}")

    # --- Eventos Wavelink ---
    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info(f"MusicCog: Nodo '{payload.node.identifier}' listo.")

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player: wavelink.Player = payload.player
        track: wavelink.Playable = payload.track
        guild_id = player.guild.id
        logging.info(f"Start: '{track.title}' G:{guild_id}.")

        # Añadir al historial (evitar repes en radio)
        self._add_to_radio_history(guild_id, track.title)

        original_channel: Optional[discord.TextChannel] = self.last_text_channel.get(guild_id)
        if not original_channel:
            logging.warning(f"No canal G:{guild_id} track start.")
            return

        duration = f"{track.length // 1000 // 60}:{track.length // 1000 % 60:02d}"
        url = track.uri or (f"https://youtube.com/watch?v={track.identifier}" if isinstance(track, wavelink.YouTubeTrack) else None)
        desc = f"▶️ **{track.title}**" + (f" ([Link]({url}))" if url else "") + f" (`{duration}`)"

        # Comentario por género (best-effort) - DESHABILITADO: función get_spotify_genres_for_track no existe
        genre_comment = None
        try:
            # Fallback simple basado en el artista
            if "coldplay" in (track.author or "").lower():
                genre_comment = "Fidu Type music ✨🎧"
        except Exception as e:
            logging.error(f"Error procesando géneros '{track.title}': {e}")

        if genre_comment:
            desc += f"\n\n*{genre_comment}*"

        embed = self.build_embed("Reproduciendo ahora", desc)
        if track.artwork:
            embed.set_thumbnail(url=track.artwork)
        try:
            await original_channel.send(embed=embed)
        except discord.HTTPException as e:
            logging.warning(f"No se pudo enviar msg 'Reproduciendo': {e}")

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player: Optional[wavelink.Player] = payload.player
        track: Optional[wavelink.Playable] = payload.track
        # Normalizar razón
        reason_raw = payload.reason
        reason = str(reason_raw).upper() if reason_raw else "UNKNOWN"

        guild_id = player.guild.id if player and player.guild else "Unknown"
        guild_name = player.guild.name if player and player.guild else "Unknown"

        if reason not in ('REPLACED', 'STOPPED', 'FINISHED'):
            logging.info(f"End: '{track.title if track else '?'}' G:{guild_id}. Razón: {reason}")

        original_channel = self.last_text_channel.get(guild_id) if isinstance(guild_id, int) else None

        if reason in ('LOAD_FAILED', 'CLEANUP'):
            logging.error(f"Error pista {track.title if track else '?'} G:{guild_id}: {reason}")
            if original_channel:
                try:
                    await original_channel.send(embed=self.build_embed("Error", f"😞 Problema con **{track.title if track else 'pista'}**. Saltando...", color=discord.Color.red()))
                except discord.HTTPException:
                    pass

        # Si fue reemplazada, detenida o player desconectado → terminar aquí
        if reason in ('REPLACED', 'STOPPED') or not player or not player.connected:
            if reason == 'STOPPED' and isinstance(guild_id, int):
                self._clear_radio_history(guild_id)
            if not player or not player.connected:
                logging.warning(f"Player desconectado G:{guild_name} track_end.")
            return

        # --- Lógica de Cola y Radio ---
        if not player.queue.is_empty:
            next_track = player.queue.get()
            if next_track:
                try:
                    await player.play(next_track, populate=True)
                    logging.info(f"Next (Cola): {next_track.title} G:{guild_name}")
                    return
                except Exception as e:
                    logging.exception(f"Error play cola ({next_track.title}): {e}")

        logging.info(f"Cola vacía G:{guild_name} ({guild_id}).")

        if self._is_radio_enabled(guild_id if isinstance(guild_id, int) else 0) and track:
            logging.info(f"Radio: Buscando lote G:{guild_name} basada en '{track.title}'")
            current_history = self._get_radio_history(guild_id) if isinstance(guild_id, int) else set()
            history_tuples = {("", title) for title in current_history}

            recommendations_batch = await fetch_spotify_recommendation(track.title, history_tuples)

            if recommendations_batch:
                logging.info(f"Radio: Spotify recomendó {len(recommendations_batch)} canciones G:{guild_name}")
                added_radio_count = 0
                first_radio_track: Optional[wavelink.Playable] = None
                first_rec_data = None
                batch_added_titles: Set[str] = set()

                for rec_data in recommendations_batch:
                    spotify_search, _, _, spotify_cleaned_title, _, _ = rec_data
                    try:
                        # Agregar timeout a búsquedas de radio
                        found_tracks: wavelink.Search = await asyncio.wait_for(
                            wavelink.Playable.search(spotify_search),
                            timeout=15.0
                        )
                        if found_tracks and not isinstance(found_tracks, wavelink.Playlist):
                            rec_track = found_tracks[0]
                            rec_cleaned = clean_title(rec_track.title, False).lower()
                            spotify_cleaned_lower = (spotify_cleaned_title or "").lower()

                            is_duplicate = (
                                rec_cleaned in current_history or
                                spotify_cleaned_lower in current_history or
                                rec_cleaned in batch_added_titles or
                                spotify_cleaned_lower in batch_added_titles
                            )

                            if not is_duplicate:
                                self._add_to_radio_history(guild_id, spotify_cleaned_title)
                                self._add_to_radio_history(guild_id, rec_cleaned)
                                batch_added_titles.add(rec_cleaned)
                                if spotify_cleaned_lower:
                                    batch_added_titles.add(spotify_cleaned_lower)
                                await player.queue.put_wait(rec_track)
                                added_radio_count += 1
                                if first_radio_track is None:
                                    first_radio_track = rec_track
                                    first_rec_data = rec_data
                                logging.info(f"Radio: ✅ Añadido '{rec_track.title}' G:{guild_name}")
                            else:
                                if rec_cleaned in batch_added_titles or spotify_cleaned_lower in batch_added_titles:
                                    logging.info(f"Radio: ❌ Saltando '{rec_track.title}' - duplicado en lote actual")
                                else:
                                    logging.info(f"Radio: ❌ Saltando '{rec_track.title}' - ya reproducida (historial)")
                    except asyncio.TimeoutError:
                        logging.warning(f"Radio: Timeout buscando '{spotify_search}'")
                        continue
                    except Exception as e:
                        logging.error(f"Radio: Error buscando/añadiendo '{spotify_search}': {e}")

                if added_radio_count > 0 and first_radio_track and first_rec_data:
                    first_from_queue = player.queue.get()
                    logging.info(f"Radio: Añadidas {added_radio_count}. Iniciando con '{first_radio_track.title}' G:{guild_name}")
                    await player.play(first_from_queue, populate=True)

                    if original_channel:
                        _, _, _, _, first_image_url, first_release_year = first_rec_data
                        final_img = first_image_url or first_radio_track.artwork
                        embed_desc = f"Iniciando radio con **{first_radio_track.title}**"
                        if first_release_year:
                            embed_desc += f" ({first_release_year})"
                        embed = self.build_embed("📻 Modo Radio", embed_desc)
                        if final_img:
                            embed.set_thumbnail(url=final_img)
                        try:
                            await original_channel.send(embed=embed)
                        except discord.HTTPException:
                            pass
                    return

            else:
                logging.warning(f"Radio: Spotify no recomendó lote G:{guild_name}.")
            logging.info(f"Radio: No se añadió lote G:{guild_name}. Posible inactividad.")
        else:
            # Inactivo sin radio
            pass

    @commands.Cog.listener()
    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload) -> None:
        """Maneja tracks atascados - simplemente los salta para evitar problemas."""
        player: Optional[wavelink.Player] = payload.player
        track: Optional[wavelink.Playable] = payload.track
        if not player or not track:
            logging.warning("TrackStuck: payload sin player o track")
            return
        
        gid = player.guild.id if player.guild else 0
        threshold = getattr(payload, 'threshold_ms', 10000)
        
        logging.error(f"⚠️ TrackStuck detectado: '{track.title}' (threshold={threshold}ms) G:{gid}")
        
        # Estrategia simple: saltar la canción problemática
        # Intentar seek puede causar más problemas que soluciones
        try:
            guild_id = player.guild.id if player.guild else None
            if guild_id:
                original_channel = self.last_text_channel.get(guild_id)
                if original_channel:
                    try:
                        await original_channel.send(embed=self.build_embed(
                            "Canción Atascada", 
                            f"⏭️ **{track.title}** se atascó. Saltando a la siguiente...",
                            color=discord.Color.orange()
                        ))
                    except discord.HTTPException:
                        pass
            
            # Limpiar retry counter si existe
            key = (gid, getattr(track, "identifier", track.uri if hasattr(track, "uri") else track.title))
            self._stuck_retries.pop(key, None)
            
            # Saltar a la siguiente canción
            await player.stop()
            logging.info(f"TrackStuck: Canción '{track.title}' saltada G:{gid}")
            
        except Exception as e:
            logging.exception(f"TrackStuck: Error manejando stuck para '{track.title}': {e}")

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload) -> None:
        """Loguea excepciones del reproductor y busca alternativas para videos con login requerido."""
        try:
            tr = payload.track.title if payload.track else "?"
            exception_msg = str(payload.exception.get('message', '')) if isinstance(payload.exception, dict) else str(payload.exception)
            logging.error(f"Wavelink: TrackException: {tr} → {payload.exception!r}")
            
            # Detectar error de "requires login" y buscar alternativa
            if "requires login" in exception_msg.lower() or "require login" in exception_msg.lower():
                player = payload.player
                track = payload.track
                
                if player and track and player.guild:
                    guild_id = player.guild.id
                    guild_name = player.guild.name
                    original_channel = self.last_text_channel.get(guild_id)
                    
                    # Verificar intentos previos para evitar loops
                    # Usar el título normalizado como key para evitar loops con diferentes identifiers
                    normalized_title = track.title.lower().strip()
                    alt_key = (guild_id, normalized_title)
                    attempts = self._alternative_attempts.get(alt_key, 0)
                    
                    if attempts >= 1:  # Reducido a 1 intento para evitar loops
                        logging.warning(f"🔒 Video requiere login: '{tr}' - Ya se intentó buscar alternativa, saltando G:{guild_name}")
                        self._alternative_attempts.pop(alt_key, None)
                        if original_channel:
                            try:
                                await original_channel.send(embed=self.build_embed(
                                    "Video No Disponible", 
                                    f"❌ **{tr}** no está disponible (requiere login). Saltando...",
                                    color=discord.Color.red()
                                ))
                            except discord.HTTPException:
                                pass
                        await player.skip()
                        return
                    
                    self._alternative_attempts[alt_key] = attempts + 1
                    logging.warning(f"🔒 Video requiere login: '{tr}' - Buscando alternativa G:{guild_name}...")
                    
                    if original_channel and attempts == 0:
                        try:
                            await original_channel.send(embed=self.build_embed(
                                "Video Restringido", 
                                f"🔒 **{tr}** requiere login. Buscando versión alternativa...",
                                color=discord.Color.orange()
                            ))
                        except discord.HTTPException:
                            pass
                    
                    # Buscar versión alternativa con términos adicionales
                    try:
                        # Intentar búsqueda con "official audio" o "lyrics" para evitar videos con restricciones
                        search_queries = [
                            f"{track.title} official audio",
                            f"{track.title} topic",
                            f"{track.title} lyrics video"
                        ]
                        
                        alternative_found = False
                        tried_identifiers = {track.identifier}  # Evitar el video original
                        
                        for query in search_queries:
                            if alternative_found:
                                break
                                
                            try:
                                # Agregar timeout a la búsqueda
                                found_tracks: wavelink.Search = await asyncio.wait_for(
                                    wavelink.Playable.search(query),
                                    timeout=8.0
                                )
                                if found_tracks and not isinstance(found_tracks, wavelink.Playlist):
                                    # Intentar con resultados que no hayamos probado
                                    for alt_track in found_tracks[:5]:  # probar los primeros 5 resultados
                                        # Verificar que sea diferente y no lo hayamos probado
                                        if alt_track.identifier not in tried_identifiers:
                                            tried_identifiers.add(alt_track.identifier)
                                            
                                            # Intentar reproducir
                                            await player.play(alt_track, populate=True)
                                            logging.info(f"✅ Alternativa encontrada: '{alt_track.title}' (ID: {alt_track.identifier[:10]}...) G:{guild_name}")
                                            
                                            # Limpiar contador de intentos en éxito
                                            self._alternative_attempts.pop(alt_key, None)
                                            
                                            if original_channel:
                                                try:
                                                    await original_channel.send(embed=self.build_embed(
                                                        "Versión Alternativa", 
                                                        f"✅ Reproduciendo: **{alt_track.title}**",
                                                        color=discord.Color.green()
                                                    ))
                                                except discord.HTTPException:
                                                    pass
                                            
                                            alternative_found = True
                                            break
                                
                            except asyncio.TimeoutError:
                                logging.warning(f"Timeout buscando alternativa con '{query}'")
                                continue
                            except Exception as e:
                                logging.debug(f"Error buscando alternativa con '{query}': {e}")
                                continue
                        
                        if not alternative_found:
                            logging.warning(f"❌ No se encontró alternativa para '{tr}' G:{guild_name}")
                            if original_channel:
                                try:
                                    await original_channel.send(embed=self.build_embed(
                                        "Sin Alternativa", 
                                        f"😞 No se encontró versión alternativa para **{tr}**. Saltando...",
                                        color=discord.Color.red()
                                    ))
                                except discord.HTTPException:
                                    pass
                            # Continuar con la siguiente canción
                            await player.skip()
                    
                    except Exception as e:
                        logging.exception(f"Error buscando alternativa para '{tr}': {e}")
                        await player.skip()
        
        except Exception:
            logging.exception("Wavelink: TrackException sin datos.")

    @commands.Cog.listener()
    async def on_wavelink_websocket_closed(self, payload: wavelink.WebsocketClosedEventPayload):
        player: Optional[wavelink.Player] = payload.player
        guild_id: Optional[int] = None
        guild_ref: str = "?"
        if player and player.guild:
            guild_id = player.guild.id
            guild_ref = f"G:{guild_id}"
        logging.warning(f"WS cerrado {guild_ref}. Code:{payload.code}, R:{payload.reason}, Remote:{payload.by_remote}")
        if isinstance(guild_id, int):
            self.last_text_channel.pop(guild_id, None)
            self.radio_enabled.pop(guild_id, None)
            self._clear_radio_history(guild_id)
            logging.info(f"Estado limpiado G:{guild_id} tras WS close.")
        else:
            logging.warning("No Guild ID en WS Closed payload.")

    # --- Comandos ---
    async def cog_check(self, ctx: commands.Context) -> bool:
        """Verifica si Wavelink está listo antes de ejecutar comandos del Cog."""
        bot_instance = cast(MyBot, self.bot)
        if not getattr(bot_instance, 'wavelink_ready', asyncio.Event()).is_set():
            await ctx.send(embed=self.build_embed("Error", "⏳ Servidor audio no listo.", color=discord.Color.orange()))
            return False
        if not wavelink.Pool.nodes:
            await ctx.send(embed=self.build_embed("Error", "⛔ No conectado a servidor audio.", color=discord.Color.red()))
            return False
        return True

    def _update_last_channel(self, ctx: commands.Context):
        if ctx.guild:
            if isinstance(ctx.channel, discord.TextChannel):
                self.last_text_channel[ctx.guild.id] = ctx.channel
            elif isinstance(ctx.channel, discord.Thread) and isinstance(ctx.channel.parent, discord.TextChannel):
                self.last_text_channel[ctx.guild.id] = ctx.channel.parent
            else:
                logging.warning(f"No TextChannel G:{ctx.guild.id}")

    @commands.command(name="j", aliases=["join", "connect"])
    async def connect_command(self, ctx: commands.Context, *, channel: Optional[discord.VoiceChannel] = None):
        self._update_last_channel(ctx)
        if channel is None:
            player = cast(wavelink.Player, ctx.voice_client)
            if player and player.channel:
                channel = player.channel
            elif ctx.author.voice and ctx.author.voice.channel:
                channel = ctx.author.voice.channel
            else:
                await ctx.send(embed=self.build_embed("Error", "Debes estar en canal o especificar."))
                return
        if not isinstance(channel, discord.VoiceChannel):
            await ctx.send(embed=self.build_embed("Error", "Solo canales voz."))
            return
        try:
            new_player: wavelink.Player = await channel.connect(cls=wavelink.Player, self_deaf=True, self_mute=False)
            await new_player.set_volume(60)
            await ctx.send(f"✅ Conectado a {channel.mention}.")
        except asyncio.TimeoutError:
            await ctx.send(f"⏳ Timeout G:{channel.mention}.")
        except Exception as e:
            logging.exception(f"Error connect() G:{channel.name}: {e}")
            await ctx.send(embed=self.build_embed("Error", f"Error conectar G:{channel.mention}."))

    @commands.command(name="dc", aliases=["leave", "disconnect"])
    async def disconnect_command(self, ctx: commands.Context):
        self._update_last_channel(ctx)
        player = cast(wavelink.Player, ctx.voice_client)
        if not player or not player.connected:
            await ctx.send(embed=self.build_embed("Error", "No estoy conectado."))
            return
        guild_id = ctx.guild.id if ctx.guild else None
        logging.info(f"Desconectando G:{player.channel.name}.")
        if guild_id:
            self._clear_radio_history(guild_id)
            self.radio_enabled.pop(guild_id, None)
            self.last_text_channel.pop(guild_id, None)
        await player.disconnect()
        await ctx.send(embed=self.build_embed("Desconectado", "¡Hasta luego!"))

    @commands.command(name="p", aliases=["play"])
    async def play_command(self, ctx: commands.Context, *, query: str):
        """Reproduce o añade a la cola (URL YT/SC/Spotify, Búsqueda). Reinicia radio si activa."""
        self._update_last_channel(ctx)
        player = cast(wavelink.Player, ctx.voice_client)
        guild_id = ctx.guild.id if ctx.guild else None

        # Autoconectar
        if not player or not player.connected:
            if not ctx.author.voice or not ctx.author.voice.channel:
                await ctx.send(embed=self.build_embed("Error", "Conéctame primero."))
                return
            try:
                player = await ctx.author.voice.channel.connect(cls=wavelink.Player, self_deaf=True, self_mute=False)
                await player.set_volume(60)
                logging.info(f"Autoconectado G:{player.channel.name}.")
            except Exception as e:
                logging.exception(f"Error autoconectar: {e}")
                await ctx.send(embed=self.build_embed("Error", "No pude unirme."))
                return

        # Limpiar cola si radio activa (pero NO el historial)
        radio_is_on = guild_id is not None and self._is_radio_enabled(guild_id)
        if radio_is_on:
            logging.info(f"Play manual durante radio G:{guild_id}. Limpiando cola.")
            player.queue.clear()

        msg = await ctx.send(f"🔍 Procesando `{query}`...")

        # Resolver Spotify URL → queries de búsqueda (no stream directo)
        spotify_match = SPOTIFY_URL_REGEX.match(query)
        search_queries: List[str] = []
        source_description: str = ""
        is_spotify = False

        if spotify_match:
            is_spotify = True
            sp_type = spotify_match.group("type")
            sp_id = spotify_match.group("id")
            logging.info(f"Spotify: {sp_type}/{sp_id}")
            sp_client = _ensure_spotify_client()
            if not sp_client:
                await msg.edit(content="", embed=self.build_embed("Error", "No Spotify client.", color=discord.Color.red()))
                return
            loop = asyncio.get_event_loop()
            try:
                await msg.edit(content=f"🔗 Spotify ({sp_type})...")

                if sp_type == "track":
                    info = await loop.run_in_executor(None, lambda: sp_client.track(sp_id))
                    name = (info or {}).get("name")
                    arts = (info or {}).get("artists") or []
                    artist = (arts[0] or {}).get("name") if arts else ""
                    if name:
                        search_queries.append(f"{artist} {name}".strip())
                    source_description = f"Spotify track"

                elif sp_type == "album":
                    alb = await loop.run_in_executor(None, lambda: sp_client.album(sp_id))
                    alb_name = (alb or {}).get("name", "")
                    tracks_resp = await loop.run_in_executor(None, lambda: sp_client.album_tracks(sp_id, limit=50))
                    for tr in (tracks_resp or {}).get("items", []) or []:
                        tname = (tr or {}).get("name")
                        arts = (tr or {}).get("artists") or []
                        aname = (arts[0] or {}).get("name") if arts else ""
                        if tname:
                            search_queries.append(f"{aname} {tname}".strip())
                    source_description = f"Spotify álbum: **{alb_name}**" if alb_name else "Spotify álbum"

                elif sp_type == "playlist":
                    pl_meta = await loop.run_in_executor(None, lambda: sp_client.playlist(sp_id, fields='name'))
                    pl_name = (pl_meta or {}).get("name", "")
                    items = await loop.run_in_executor(None, lambda: sp_client.playlist_items(
                        sp_id,
                        fields='items(track(name,artists(name)))',
                        limit=100
                    ))
                    for it in (items or {}).get("items", []) or []:
                        tr = (it or {}).get("track") or {}
                        tname = tr.get("name")
                        arts = tr.get("artists") or []
                        aname = (arts[0] or {}).get("name") if arts else ""
                        if tname:
                            search_queries.append(f"{aname} {tname}".strip())
                    source_description = f"Spotify playlist: **{pl_name}**" if pl_name else "Spotify playlist"

            except Exception as e:
                logging.exception(f"Error Spotify: {e}")
                await msg.edit(content="", embed=self.build_embed("Error", f"Error Spotify: {e}", color=discord.Color.red()))
                return
        else:
            search_queries.append(query)
            source_description = f"`{query}`"

        if not search_queries:
            await msg.edit(content="", embed=self.build_embed("Error", "No hay resultados para la consulta.", color=discord.Color.red()))
            return

        # Buscar y armar cola
        tracks_to_add: List[wavelink.Playable] = []
        not_found_count = 0
        search_desc = f"{len(search_queries)} q" if len(search_queries) > 1 else "q"
        await msg.edit(content=f"🎵 Buscando {search_desc}...")

        for idx, sq in enumerate(search_queries):
            try:
                found: wavelink.Search = await wavelink.Playable.search(sq)
                if isinstance(found, wavelink.Playlist):
                    tracks_to_add.extend(found.tracks)
                    logging.info(f"+{len(found.tracks)} de PL: {found.name}")
                    source_description = f"PL: **{found.name}**"
                    break
                elif found:
                    tracks_to_add.append(found[0])
                else:
                    not_found_count += 1
                    logging.warning(f"No res: '{sq}'")
            except Exception as e:
                not_found_count += 1
                logging.exception(f"Error buscando '{sq}': {e}")

        if not tracks_to_add:
            await msg.edit(content="", embed=self.build_embed("Error", f"No encontré para {source_description}.", color=discord.Color.red()))
            return

        # Añadir a cola / reproducir
        try:
            start_playing = not player.playing and not player.current
            added_count = 0
            for track in tracks_to_add:
                await player.queue.put_wait(track)
                added_count += 1

            action = "Añadido"
            msg_title = "Añadido a cola"
            if added_count == 1 and len(search_queries) == 1 and not is_spotify:
                msg_text = f"✅ {action}: **{tracks_to_add[0].title}**"
            else:
                msg_text = f"➕ {action}: **{added_count}** de {source_description}."
                msg_title = "Cola actualizada"
            if not_found_count > 0:
                msg_text += f"\n*({not_found_count} no encontradas)*."
            if radio_is_on and player.playing:
                msg_text += "\n*(Radio reiniciará)*."
            elif start_playing:
                msg_text += "\nIniciando..."

            await msg.edit(content="", embed=self.build_embed(msg_title, msg_text))

            if start_playing:
                first = player.queue.get()
                if first:
                    await player.play(first, populate=True)
        except Exception as e:
            logging.exception(f"Error añadiendo/iniciando: {e}")
            await msg.edit(content="", embed=self.build_embed("Error", "Error al añadir.", color=discord.Color.red()))

    @commands.command(name="s", aliases=["skip"])
    async def skip_command(self, ctx: commands.Context):
        self._update_last_channel(ctx)
        player = cast(wavelink.Player, ctx.voice_client)
        if not player or not player.connected:
            await ctx.send(embed=self.build_embed("Error", "No conectado."))
            return
        if not player.playing and player.queue.is_empty:
            await ctx.send(embed=self.build_embed("Skip", "Nada que saltar."))
            return
        current = player.current.title if player.current else "canción"
        logging.info(f"Saltando '{current}' G:{ctx.guild.id}.")
        await player.skip(force=True)
        await ctx.send(embed=self.build_embed("Skip", f"⏭️ Saltando **{current}**..."))

    @commands.command(name="st", aliases=["stop"])
    async def stop_command(self, ctx: commands.Context):
        self._update_last_channel(ctx)
        player = cast(wavelink.Player, ctx.voice_client)
        guild_id = ctx.guild.id if ctx.guild else None
        if not player or not player.connected:
            await ctx.send(embed=self.build_embed("Error", "No conectado."))
            return
        if not player.playing and player.queue.is_empty:
            await ctx.send(embed=self.build_embed("Stop", "Nada que detener."))
            return
        radio_on = False
        if guild_id:
            if self._is_radio_enabled(guild_id):
                self.radio_enabled[guild_id] = False
                radio_on = True
                logging.info(f"Radio off por stop G:{guild_id}.")
            self._clear_radio_history(guild_id)
        player.queue.clear()
        await player.stop(force=True)
        msg = "⏹️ Detenida y cola vaciada."
        if radio_on:
            msg += "\n📻 Radio desactivado."
        await ctx.send(embed=self.build_embed("Stop", msg))

    @commands.command(name="radio")
    async def radio_command(self, ctx: commands.Context, mode: Optional[str] = None):
        """Activa o desactiva el modo radio automático."""
        if not ctx.guild:
            return

        self._update_last_channel(ctx)
        guild_id = ctx.guild.id
        current = self._is_radio_enabled(guild_id)
        if mode is None:
            new_state = not current
        else:
            new_state = mode.lower() in {"on", "true", "1", "activar", "si", "yes", "activado"}

        if new_state != current:
            self.radio_enabled[guild_id] = new_state
            status = "activado" if new_state else "desactivado"
            logging.info(f"Radio {status} G:{guild_id}.")

            # Solo limpiar historial si se DESACTIVA la radio
            if not new_state:
                self._clear_radio_history(guild_id)
                logging.info(f"Historial limpiado (radio desactivada) G:{guild_id}")

            await ctx.send(embed=self.build_embed("Modo Radio", f"📻 Modo radio **{status}**."))
            player = cast(wavelink.Player, ctx.voice_client)
            if new_state and player and not player.playing and player.queue.is_empty:
                logging.info(f"Radio activada G:{guild_id} inactivo.")
                await ctx.send(embed=self.build_embed("Modo Radio", "Reproduce una canción para iniciar."))
        else:
            status = "activado" if current else "desactivado"
            await ctx.send(embed=self.build_embed("Modo Radio", f"📻 Modo radio ya estaba **{status}**."))


# --- Función Setup ---
async def setup(bot: commands.Bot):
    await bot.add_cog(MusicWavelinkCog(bot))
    logging.info("Cog de Música (Wavelink) cargado.")
