# --- bot/cogs/music.py (Wavelink - Mensajes/Comentarios Mejorados - Repetición Check) ---

import asyncio
import logging
import re
import discord
from discord.ext import commands
import wavelink
from typing import cast, Optional, Dict, Set, Tuple, List

# --- Importar helpers ---
try:
    from bot.utils.spotify_helper import (
        fetch_spotify_recommendation,
        clean_title,
        _ensure_spotify_client,
        get_spotify_genres_for_track # <-- Importar
    )
except ImportError:
    logging.error("¡¡ERROR!! No se pudo importar spotify_helper.");
    # Funciones dummy para evitar errores si falla la importación
    async def fetch_spotify_recommendation(*a, **kw) -> Optional[List[Tuple[str, str, str, str, Optional[str], Optional[str]]]]: 
        return None
    
    def clean_title(t: str, **kw) -> str: 
        return t
    
    class DummySpotify: 
        pass
    
    def _ensure_spotify_client() -> Optional[DummySpotify]: 
        return None
    
    async def get_spotify_genres_for_track(*a, **kw) -> Optional[List[str]]: 
        return None

# Importar MyBot para type hinting (asumiendo que está en __main__)
try: from __main__ import MyBot
except ImportError:
    # Placeholder si no se puede importar
    class MyBot(commands.Bot): wavelink_ready: asyncio.Event = asyncio.Event()
    logging.warning("No se pudo importar MyBot desde __main__ para type hint.")

# Regex para links de Spotify
SPOTIFY_URL_REGEX = re.compile(r"https?://open\.spotify\.com/(?P<type>track|album|playlist)/(?P<id>[a-zA-Z0-9]+)")

# --- Categorías de Género Expandidas ---
URBANO_GENRES = {
    # Reggaeton & Urbano Latino
    'reggaeton', 'trap latino', 'urbano latino', 'latin hip hop', 'trap argentino', 
    'argentine hip hop', 'r&b en espanol', 'pop reggaeton', 'latin pop', 'dembow',
    'reggaeton colombiano', 'trap chileno', 'trap mexicano', 'cumbia urbana',
    # Hip Hop & Rap
    'hip hop', 'rap', 'trap', 'drill', 'gangsta rap', 'conscious hip hop', 'boom bap',
    'southern hip hop', 'west coast rap', 'east coast hip hop', 'cloud rap', 'mumble rap',
    'uk hip hop', 'grime', 'uk drill', 'afro trap', 'trap soul', 'emo rap',
    # R&B & Soul
    'r&b', 'contemporary r&b', 'alternative r&b', 'neo soul', 'soul', 'funk',
    'quiet storm', 'urban contemporary', 'new jack swing'
}

ROCK_METAL_GENRES = {
    # Rock Clásico & Alternativo
    'rock', 'alternative rock', 'indie rock', 'classic rock', 'hard rock', 'soft rock',
    'progressive rock', 'psychedelic rock', 'garage rock', 'art rock', 'glam rock',
    # Rock Moderno
    'modern rock', 'post-grunge', 'grunge', 'britpop', 'madchester', 'shoegaze',
    'noise rock', 'math rock', 'post-rock', 'space rock',
    # Punk & Derivados
    'punk', 'punk rock', 'pop punk', 'post-punk', 'hardcore punk', 'skate punk',
    'ska punk', 'horror punk', 'anarcho-punk', 'street punk',
    # Metal & Subgéneros
    'metal', 'heavy metal', 'thrash metal', 'death metal', 'black metal', 'doom metal',
    'power metal', 'progressive metal', 'symphonic metal', 'folk metal', 'viking metal',
    'alternative metal', 'nu metal', 'metalcore', 'deathcore', 'djent', 'groove metal',
    'industrial metal', 'gothic metal', 'melodic death metal', 'technical death metal',
    # Emo & Screamo
    'emo', 'emo rap', 'screamo', 'post-hardcore', 'melodic hardcore'
}

POP_CHILL_GENRES = {
    # Pop Principal
    'pop', 'dance pop', 'electropop', 'synth-pop', 'synthpop', 'indie pop', 'art pop',
    'chamber pop', 'pop rock', 'power pop', 'jangle pop', 'noise pop', 'hyperpop',
    'bubblegum pop', 'teen pop', 'post-teen pop', 'europop', 'k-pop', 'j-pop',
    # Electrónica & Dance
    'electronic', 'edm', 'house', 'deep house', 'tech house', 'progressive house',
    'electro house', 'future house', 'tropical house', 'techno', 'trance',
    'dubstep', 'drum and bass', 'future bass', 'chillstep', 'downtempo', 'ambient',
    'idm', 'glitch', 'vaporwave', 'synthwave', 'chillwave', 'lo-fi', 'lo-fi hip hop',
    # Chill & Indie
    'chill', 'chillout', 'chillhop', 'indie', 'indie folk', 'indie soul', 'indie pop',
    'bedroom pop', 'dream pop', 'slowcore', 'sadcore', 'lo-fi indie',
    # Singer-Songwriter & Acústico
    'singer-songwriter', 'acoustic', 'folk', 'folk pop', 'chamber folk', 'freak folk',
    'anti-folk', 'indie folk', 'stomp and holler',
    # Alternativo Suave
    'alternative', 'alt z', 'alt pop', 'indietronica', 'folktronica', 'electronica',
    # Otros
    'new wave', 'new romantic', 'sophisti-pop', 'yacht rock', 'soft rock',
    'adult contemporary', 'easy listening', 'lounge', 'bossa nova', 'jazz pop'
}

class MusicWavelinkCog(commands.Cog, name="Music"):
    """Cog de Música con Wavelink, Radio, Imágenes, Links Spotify y Comentarios."""

    def __init__(self, bot: commands.Bot):
        # Asegurarse que bot es MyBot o tiene wavelink_ready
        self.bot: MyBot = cast(MyBot, bot)
        # Diccionarios para estado por servidor
        self.last_text_channel: Dict[int, discord.TextChannel] = {}
        self.radio_enabled: Dict[int, bool] = {}
        self.radio_session_history: Dict[int, Set[str]] = {}

    def build_embed(self, title: str, description: str, color=discord.Color.blurple()) -> discord.Embed:
        """Crea un embed estándar para los mensajes del bot."""
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_footer(text="Cornelius Music (Lavalink)")
        return embed

    # --- Funciones Helper Internas ---
    def _is_radio_enabled(self, guild_id: int) -> bool:
        """Verifica si la radio está activa para un servidor."""
        return self.radio_enabled.get(guild_id, False)

    def _get_radio_history(self, guild_id: int) -> Set[str]:
        """Obtiene (o crea) el set de historial de radio para un servidor."""
        return self.radio_session_history.setdefault(guild_id, set())

    def _add_to_radio_history(self, guild_id: int, title: str):
        """Añade un título limpio (con artista) al historial de radio."""
        if title:
            cleaned = clean_title(title, remove_artist_pattern=False).lower()
            if cleaned:
                self._get_radio_history(guild_id).add(cleaned)

    def _clear_radio_history(self, guild_id: int):
        """Limpia el historial de radio para un servidor."""
        if guild_id in self.radio_session_history:
            self.radio_session_history[guild_id].clear()
            logging.info(f"Historial radio limpiado G:{guild_id}")

    # --- Eventos Wavelink ---
    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        """Se activa cuando un nodo Lavalink está listo."""
        logging.info(f"MusicCog: Nodo '{payload.node.identifier}' listo.")

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        """Se activa cuando una canción empieza a sonar."""
        player: wavelink.Player = payload.player; track: wavelink.Playable = payload.track; guild_id = player.guild.id
        logging.info(f"Start: '{track.title}' G:{guild_id}.")

        # Añadir SIEMPRE al historial (para evitar repeticiones en radio)
        self._add_to_radio_history(guild_id, track.title) # Usa título completo

        original_channel: Optional[discord.TextChannel] = self.last_text_channel.get(guild_id)
        if not original_channel:
            logging.warning(f"No canal G:{guild_id} track start.")
            return

        # Formatear info básica
        duration = f"{track.length // 1000 // 60}:{track.length // 1000 % 60:02d}"
        url = track.uri or (f"https://youtube.com/watch?v={track.identifier}" if isinstance(track, wavelink.YouTubeTrack) else None)
        desc = f"▶️ **{track.title}**" + (f" ([Link]({url}))" if url else "") + f" (`{duration}`)"

        # Obtener Géneros y Añadir Comentario
        genre_comment = None
        try:
            # Construir título completo para búsqueda de géneros
            search_title_for_genre = track.title
            if track.author and track.author.lower() not in track.title.lower():
                 search_title_for_genre = f"{track.author} {track.title}"

            genres: Optional[List[str]] = await get_spotify_genres_for_track(search_title_for_genre)

            if genres:
                genres_set = {g.lower() for g in genres}
                if genres_set.intersection(URBANO_GENRES): genre_comment = "¡Qué palo bro! 🎶🔥"
                elif genres_set.intersection(ROCK_METAL_GENRES): genre_comment = "Música de comegato 🤘🐱"
                elif genres_set.intersection(POP_CHILL_GENRES): genre_comment = "Fidu Type music ✨🎧"
                elif "coldplay" in track.author.lower() and not genre_comment: genre_comment = "Fidu Type music ✨🎧" # Fallback Coldplay
            elif "coldplay" in track.author.lower(): genre_comment = "Fidu Type music ✨🎧" # Fallback si no hay géneros
        except Exception as e:
            logging.error(f"Error get/proc géneros '{track.title}': {e}") # No detener por error aquí

        if genre_comment:
            desc += f"\n\n*{genre_comment}*" # Añadir comentario

        # Enviar Embed
        embed = self.build_embed("Reproduciendo ahora", desc)
        if track.artwork: embed.set_thumbnail(url=track.artwork) # Añadir miniatura
        try:
            await original_channel.send(embed=embed)
        except discord.HTTPException as e:
            logging.warning(f"No se pudo enviar msg 'Reproduciendo': {e}")

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        """Se activa cuando una canción termina o falla."""
        player: Optional[wavelink.Player] = payload.player
        track: Optional[wavelink.Playable] = payload.track
        reason: str = payload.reason

        # Logs y comprobaciones iniciales
        guild_id = player.guild.id if player and player.guild else "Unknown"
        guild_name = player.guild.name if player and player.guild else "Unknown"
        if reason not in ('REPLACED', 'STOPPED', 'FINISHED'): logging.info(f"End: '{track.title if track else '?'}' G:{guild_id}. Razón: {reason}")
        original_channel = self.last_text_channel.get(guild_id) if isinstance(guild_id, int) else None

        # Notificar errores de carga
        if reason in ('LOAD_FAILED', 'CLEANUP'):
             logging.error(f"Error pista {track.title if track else '?'} G:{guild_id}: {reason}")
             if original_channel:
                 try: await original_channel.send(embed=self.build_embed("Error", f"😞 Problema con **{track.title if track else 'pista'}**. Saltando...", color=discord.Color.red()))
                 except discord.HTTPException: pass

        # Si fue reemplazada, detenida, o el player ya no existe/está conectado, no continuar
        if reason in ('REPLACED', 'STOPPED') or not player or not player.connected:
            if reason == 'STOPPED' and isinstance(guild_id, int): self._clear_radio_history(guild_id) # Limpiar historial si se detiene
            if not player or not player.connected: logging.warning(f"Player desconectado G:{guild_name} track_end.")
            return

        # --- Lógica de Cola y Radio ---
        # Primero intentar sacar algo de la cola
        if not player.queue.is_empty:
             next_track = player.queue.get()
             if next_track:
                 try: await player.play(next_track, populate=True); logging.info(f"Next (Cola): {next_track.title} G:{guild_name}"); return
                 except Exception as e: logging.exception(f"Error play cola ({next_track.title}): {e}")
                 # Si falla al reproducir de la cola, podríamos intentar el siguiente o pasar a radio

        # Si llegamos aquí, la cola está vacía
        logging.info(f"Cola vacía G:{guild_name} ({guild_id}).")

        # Intentar Radio si activa y hubo track anterior
        if self._is_radio_enabled(guild_id) and track:
            logging.info(f"Radio: Buscando lote G:{guild_name} basada en '{track.title}'")
            current_history = self._get_radio_history(guild_id); history_tuples = {("", title) for title in current_history}

            recommendations_batch = await fetch_spotify_recommendation(track.title, history_tuples)

            if recommendations_batch:
                logging.info(f"Radio: Spotify recomendó {len(recommendations_batch)} canciones G:{guild_name}")
                added_radio_count = 0
                first_radio_track: Optional[wavelink.Playable] = None
                first_rec_data = None # Guardar datos del primero para el embed
                batch_added_titles: Set[str] = set()  # Para evitar duplicados dentro del lote actual

                for rec_data in recommendations_batch:
                    spotify_search, _, _, spotify_cleaned_title, _, _ = rec_data # Ya no necesitamos imagen/año aquí directamente
                    try:
                        found_tracks: wavelink.Search = await wavelink.Playable.search(spotify_search)
                        if found_tracks and not isinstance(found_tracks, wavelink.Playlist):
                            rec_track = found_tracks[0]
                            # Limpiar título de YouTube/Lavalink para comparación
                            rec_cleaned = clean_title(rec_track.title, False).lower()
                            # También limpiar el título de Spotify para comparación más robusta
                            spotify_cleaned_lower = spotify_cleaned_title.lower() if spotify_cleaned_title else ""
                            
                            # Verificar si ya está en historial O en el lote actual (comparar ambas versiones)
                            is_duplicate = (rec_cleaned in current_history or 
                                          spotify_cleaned_lower in current_history or
                                          rec_cleaned in batch_added_titles or
                                          spotify_cleaned_lower in batch_added_titles)
                            
                            if not is_duplicate:
                                # Añadir ambas versiones al historial para máxima cobertura
                                self._add_to_radio_history(guild_id, spotify_cleaned_title)
                                self._add_to_radio_history(guild_id, rec_cleaned)
                                # Añadir al set del lote actual
                                batch_added_titles.add(rec_cleaned)
                                batch_added_titles.add(spotify_cleaned_lower)
                                await player.queue.put_wait(rec_track); added_radio_count += 1
                                if first_radio_track is None: first_radio_track = rec_track; first_rec_data = rec_data # Guardar el primero
                                logging.info(f"Radio: ✅ Añadido '{rec_track.title}' G:{guild_name}")
                            else:
                                if rec_cleaned in batch_added_titles or spotify_cleaned_lower in batch_added_titles:
                                    logging.info(f"Radio: ❌ Saltando '{rec_track.title}' - duplicado en lote actual")
                                else:
                                    logging.info(f"Radio: ❌ Saltando '{rec_track.title}' - ya reproducida (historial)")
                        # else: logging.warning(f"Radio: No resultado/playlist '{spotify_search}'.") # Opcional
                    except Exception as e: logging.exception(f"Radio: Error buscando/añadiendo '{spotify_search}': {e}")

                # Si se añadieron canciones y hay una primera
                if added_radio_count > 0 and first_radio_track and first_rec_data:
                    # Sacar la primera de la cola antes de reproducirla (para evitar duplicados)
                    first_from_queue = player.queue.get()
                    logging.info(f"Radio: Añadidas {added_radio_count}. Iniciando con '{first_radio_track.title}' G:{guild_name}")
                    await player.play(first_from_queue, populate=True) # Iniciar reproducción con la que sacamos de la cola

                    # Enviar mensaje de radio (solo info de la primera)
                    if original_channel:
                         _, _, _, _, first_image_url, first_release_year = first_rec_data # Desempaquetar datos guardados
                         final_img = first_image_url or first_radio_track.artwork
                         embed_desc = f"Iniciando radio con **{first_radio_track.title}**"
                         if first_release_year: embed_desc += f" ({first_release_year})"
                         embed = self.build_embed("📻 Modo Radio", embed_desc)
                         if final_img: embed.set_thumbnail(url=final_img)
                         try: await original_channel.send(embed=embed)
                         except discord.HTTPException: pass
                    return # Salir

            else: logging.warning(f"Radio: Spotify no recomendó lote G:{guild_name}.")
            logging.info(f"Radio: No se añadió lote G:{guild_name}. Posible inactividad.")
            # await self.start_inactive_timer(player) # Implementar si se desea
        else: # Cola vacía Y (radio apagado O no hubo track anterior)
            # await self.start_inactive_timer(player)
            pass


    @commands.Cog.listener()
    async def on_wavelink_websocket_closed(self, payload: wavelink.WebsocketClosedEventPayload):
         player: Optional[wavelink.Player] = payload.player; guild_id: Optional[int] = None; guild_ref: str = "?"
         if player and player.guild: guild_id = player.guild.id; guild_ref = f"G:{guild_id}"
         logging.warning(f"WS cerrado {guild_ref}. Code:{payload.code}, R:{payload.reason}, Remote:{payload.by_remote}")
         if isinstance(guild_id, int):
              self.last_text_channel.pop(guild_id, None); self.radio_enabled.pop(guild_id, None); self._clear_radio_history(guild_id)
              logging.info(f"Estado limpiado G:{guild_id} tras WS close.")
         else: logging.warning("No Guild ID en WS Closed payload.")


    # --- Comandos ---
    async def cog_check(self, ctx: commands.Context) -> bool:
        """Verifica si Wavelink está listo antes de ejecutar comandos del Cog."""
        bot_instance = cast(MyBot, self.bot)
        # Usar getattr para evitar AttributeError si wavelink_ready no existe por alguna razón
        if not getattr(bot_instance, 'wavelink_ready', asyncio.Event()).is_set():
             await ctx.send(embed=self.build_embed("Error","⏳ Servidor audio no listo.",color=discord.Color.orange()))
             return False
        if not wavelink.Pool.nodes:
             await ctx.send(embed=self.build_embed("Error","⛔ No conectado a servidor audio.",color=discord.Color.red()))
             return False
        return True

    def _update_last_channel(self, ctx: commands.Context):
        """Guarda el canal de texto donde se usó el comando."""
        if ctx.guild:
             # Priorizar canal de texto normal
             if isinstance(ctx.channel, discord.TextChannel): self.last_text_channel[ctx.guild.id] = ctx.channel
             # Si es un hilo, intentar usar el canal padre
             elif isinstance(ctx.channel, discord.Thread) and isinstance(ctx.channel.parent, discord.TextChannel):
                  self.last_text_channel[ctx.guild.id] = ctx.channel.parent
             else: logging.warning(f"No TextChannel G:{ctx.guild.id}")

    # --- Comandos (j, dc, p, s, st, radio - sin cambios funcionales mayores) ---
    @commands.command(name="j", aliases=["join", "connect"])
    async def connect_command(self, ctx: commands.Context, *, channel: Optional[discord.VoiceChannel] = None):
        """Conecta al bot a tu canal de voz o a uno especificado."""
        self._update_last_channel(ctx)
        if channel is None:
            player = cast(wavelink.Player, ctx.voice_client)
            if player and player.channel: channel = player.channel # Usar canal actual si ya está
            elif ctx.author.voice and ctx.author.voice.channel: channel = ctx.author.voice.channel # Usar canal del autor
            else: await ctx.send(embed=self.build_embed("Error", "Debes estar en canal o especificar.")); return
        if not isinstance(channel, discord.VoiceChannel): await ctx.send(embed=self.build_embed("Error", "Solo canales voz.")); return
        try:
            new_player: wavelink.Player = await channel.connect(cls=wavelink.Player, self_deaf=True, self_mute=False)
            await new_player.set_volume(60); await ctx.send(f"✅ Conectado a {channel.mention}.")
        except asyncio.TimeoutError: await ctx.send(f"⏳ Timeout G:{channel.mention}.")
        except Exception as e: logging.exception(f"Error connect() G:{channel.name}: {e}"); await ctx.send(embed=self.build_embed("Error", f"Error conectar G:{channel.mention}."))

    @commands.command(name="dc", aliases=["leave", "disconnect"])
    async def disconnect_command(self, ctx: commands.Context):
        """Desconecta el bot del canal de voz."""
        self._update_last_channel(ctx); player = cast(wavelink.Player, ctx.voice_client)
        if not player or not player.connected: await ctx.send(embed=self.build_embed("Error", "No estoy conectado.")); return
        guild_id = ctx.guild.id if ctx.guild else None; logging.info(f"Desconectando G:{player.channel.name}.")
        if guild_id: self._clear_radio_history(guild_id); self.radio_enabled.pop(guild_id, None); self.last_text_channel.pop(guild_id, None)
        await player.disconnect(); await ctx.send(embed=self.build_embed("Desconectado", "¡Hasta luego!"))

    @commands.command(name="p", aliases=["play"])
    async def play_command(self, ctx: commands.Context, *, query: str):
        """Reproduce o añade a la cola (URL YT/SC/Spotify, Búsqueda). Reinicia radio si activa."""
        self._update_last_channel(ctx); player = cast(wavelink.Player, ctx.voice_client); guild_id = ctx.guild.id if ctx.guild else None
        # Autoconectar
        if not player or not player.connected:
            if not ctx.author.voice or not ctx.author.voice.channel: await ctx.send(embed=self.build_embed("Error", "Conéctame primero.")); return
            try: player = await ctx.author.voice.channel.connect(cls=wavelink.Player, self_deaf=True, self_mute=False); await player.set_volume(60); logging.info(f"Autoconectado G:{player.channel.name}.")
            except Exception as e: logging.exception(f"Error autoconectar: {e}"); await ctx.send(embed=self.build_embed("Error", "No pude unirme.")); return
        # Limpiar cola si radio activa (pero mantener historial para evitar repeticiones)
        radio_is_on = guild_id is not None and self._is_radio_enabled(guild_id)
        if radio_is_on: 
            logging.info(f"Play manual durante radio G:{guild_id}. Limpiando cola.")
            player.queue.clear()  # Solo limpiar cola, NO el historial
        msg = await ctx.send(f"🔍 Procesando `{query}`...");
        # Lógica Spotify/Búsqueda (sin cambios)
        spotify_match = SPOTIFY_URL_REGEX.match(query); search_queries: List[str] = []; source_description: str = ""; is_spotify = False; spotify_fetch_failed = False
        if spotify_match:
            is_spotify = True; spotify_type = spotify_match.group("type"); spotify_id = spotify_match.group("id"); logging.info(f"Spotify: {spotify_type}/{spotify_id}"); sp_client = _ensure_spotify_client()
            if not sp_client: await msg.edit(content="", embed=self.build_embed("Error", "No Spotify client.", color=discord.Color.red())); return
            try:
                loop = asyncio.get_event_loop(); await msg.edit(content=f"🔗 Spotify ({spotify_type})...")
                # ... (resto código Spotify) ...
                if spotify_type == "track": info = await loop.run_in_executor(None, lambda: sp_client.track(spotify_id)); ...
                elif spotify_type == "album": info = await loop.run_in_executor(None, lambda: sp_client.album(spotify_id)); ...
                elif spotify_type == "playlist": info = await loop.run_in_executor(None, lambda: sp_client.playlist(spotify_id, fields='name,tracks.total')); ...
            except Exception as e: logging.exception(f"Error Spotify: {e}"); await msg.edit(content="", embed=self.build_embed("Error", f"Error Spotify: {e}", color=discord.Color.red())); spotify_fetch_failed = True
        else: search_queries.append(query); source_description = f"`{query}`"
        if spotify_fetch_failed or not search_queries: return
        tracks_to_add: List[wavelink.Playable] = []; not_found_count = 0; search_desc = f"{len(search_queries)} q" if len(search_queries) > 1 else "q"; await msg.edit(content=f"🎵 Buscando {search_desc}...")
        for idx, sq in enumerate(search_queries):
            try:
                found: wavelink.Search = await wavelink.Playable.search(sq)
                if isinstance(found, wavelink.Playlist): tracks_to_add.extend(found.tracks); logging.info(f"+{len(found.tracks)} de PL: {found.name}"); source_description = f"PL: **{found.name}**"; break
                elif found: tracks_to_add.append(found[0]); # if (idx + 1) % 10 == 0: logging.info(f"Proc {idx+1}/{len(search_queries)}...")
                else: not_found_count += 1; logging.warning(f"No res: '{sq}'")
            except Exception as e: not_found_count += 1; logging.exception(f"Error buscando '{sq}': {e}")
        if not tracks_to_add: await msg.edit(content="", embed=self.build_embed("Error", f"No encontré para {source_description}.", color=discord.Color.red())); return
        # Añadir a Cola (sin cambios)
        try:
            start_playing = not player.playing and not player.current; added_count = 0
            for track in tracks_to_add: await player.queue.put_wait(track); added_count += 1
            action = "Añadido"; msg_title = "Añadido a cola"
            if added_count == 1 and len(search_queries) == 1 and not is_spotify: msg_text = f"✅ {action}: **{tracks_to_add[0].title}**"
            else: msg_text = f"➕ {action}: **{added_count}** de {source_description}."; msg_title = "Cola actualizada"
            if not_found_count > 0: msg_text += f"\n*({not_found_count} no encontradas)*."
            if radio_is_on and player.playing: msg_text += "\n*(Radio reiniciará)*."
            elif start_playing: msg_text += "\nIniciando..."
            await msg.edit(content="", embed=self.build_embed(msg_title, msg_text))
            if start_playing: 
                first = player.queue.get()
                if first: 
                    await player.play(first, populate=True)
        except Exception as e: logging.exception(f"Error añadiendo/iniciando: {e}"); await msg.edit(content="", embed=self.build_embed("Error", "Error al añadir.", color=discord.Color.red()))

    @commands.command(name="s", aliases=["skip"])
    async def skip_command(self, ctx: commands.Context):
        # ... (código skip igual) ...
        self._update_last_channel(ctx); player = cast(wavelink.Player, ctx.voice_client)
        if not player or not player.connected: await ctx.send(embed=self.build_embed("Error", "No conectado.")); return
        if not player.playing and player.queue.is_empty: await ctx.send(embed=self.build_embed("Skip", "Nada que saltar.")); return
        current = player.current.title if player.current else "canción"; logging.info(f"Saltando '{current}' G:{ctx.guild.id}.")
        await player.skip(force=True); await ctx.send(embed=self.build_embed("Skip", f"⏭️ Saltando **{current}**..."))

    @commands.command(name="st", aliases=["stop"])
    async def stop_command(self, ctx: commands.Context):
        # ... (código stop igual) ...
        self._update_last_channel(ctx); player = cast(wavelink.Player, ctx.voice_client); guild_id = ctx.guild.id if ctx.guild else None
        if not player or not player.connected: await ctx.send(embed=self.build_embed("Error", "No conectado.")); return
        if not player.playing and player.queue.is_empty: await ctx.send(embed=self.build_embed("Stop", "Nada que detener.")); return
        radio_on = False
        if guild_id:
             if self._is_radio_enabled(guild_id): self.radio_enabled[guild_id] = False; radio_on = True; logging.info(f"Radio off por stop G:{guild_id}.")
             self._clear_radio_history(guild_id)
        player.queue.clear(); await player.stop(force=True)
        msg = "⏹️ Detenida y cola vaciada.";
        if radio_on: msg += "\n📻 Radio desactivado."
        await ctx.send(embed=self.build_embed("Stop", msg))

    @commands.command(name="radio")
    async def radio_command(self, ctx: commands.Context, mode: Optional[str] = None):
        """Activa o desactiva el modo radio automático."""
        if not ctx.guild: 
            return
        
        self._update_last_channel(ctx)
        guild_id = ctx.guild.id
        current = self._is_radio_enabled(guild_id); new_state: bool
        if mode is None: new_state = not current
        else: new_state = mode.lower() in {"on", "true", "1", "activar", "si", "yes", "activado"}
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
        else: status = "activado" if current else "desactivado"; await ctx.send(embed=self.build_embed("Modo Radio", f"📻 Modo radio ya estaba **{status}**."))


# --- Función Setup ---
async def setup(bot: commands.Bot):
    await bot.add_cog(MusicWavelinkCog(bot))
    logging.info("Cog de Música (Wavelink) cargado.")