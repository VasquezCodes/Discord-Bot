# --- bot/main.py (Usando Pool.set_client() y Corrigiendo Excepciones) ---

import asyncio
import logging
import os
import aiohttp # Importar para manejo de excepciones de conexión

# IMPORTANTE: Cargar dotenv ANTES de importar cualquier cosa que use get_settings
from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands
import wavelink # <-- Importar wavelink

# Ahora sí importar settings
from config.settings import get_settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')

# --- !! AÑADIR ESTAS LÍNEAS PARA SILENCIAR SPOTIPY y URLLIB3 !! ---
# Poner los loggers específicos de spotipy y urllib3 en nivel WARNING o superior
logging.getLogger('spotipy').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
# --- FIN DEL AÑADIDO ---

# Mantener discord y wavelink en INFO (o WARNING si prefieres menos logs aún)
logging.getLogger('discord').setLevel(logging.INFO)
logging.getLogger('wavelink').setLevel(logging.INFO)


settings = get_settings() # Leer configuración

# --- Configuración de Lavalink ---
LAVALINK_URI = os.getenv("LAVALINK_URI", "ws://localhost:2333") # ¡Mantener ws://!
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass") # ¡Debe coincidir con application.yml!

# Validar token de Discord y contraseña de Lavalink
if not settings.discord_token:
    logging.critical("¡¡ERROR FATAL: DISCORD_TOKEN no está definido!!")
    exit()
if not LAVALINK_PASSWORD or LAVALINK_PASSWORD == "youshallnotpass":
     logging.warning("LAVALINK_PASSWORD no definida o usa default. ¡Verifica que coincida con application.yml!")
# --- Fin Configuración Lavalink ---


# Configurar Intents del bot
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# --- Usar una subclase de commands.Bot ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=settings.command_prefix,
            help_command=None,
            intents=intents
        )
        self.wavelink_connected = False # Flag para saber si se intentó conectar
        self.wavelink_ready = asyncio.Event() # Evento para saber si el nodo está LISTO

    async def setup_hook(self) -> None:
        """Código que se ejecuta una vez antes de on_ready."""
        logging.info("Ejecutando setup_hook...")
        # Cargar extensiones (Cogs) aquí está bien
        await load_extensions(self)

    async def on_ready(self) -> None:
        """Se ejecuta cuando el bot está listo y conectado a Discord."""
        logging.info(f"Conectado a Discord como {self.user} (ID: {self.user.id})")
        logging.info(f"Prefijo de comando: {settings.command_prefix}")

        # --- CONECTAR WAVELINK AQUÍ (CORREGIDO) ---
        if not wavelink.Pool.nodes and not self.wavelink_connected:
            logging.info(f"on_ready: Intentando conectar con Lavalink en {LAVALINK_URI}...")
            try:
                # 1. Crear el nodo CON identificador
                node = wavelink.Node(
                    identifier="MAIN",  # Agregar identificador explícito
                    uri=LAVALINK_URI, 
                    password=LAVALINK_PASSWORD
                )
                
                # 2. Conectar el nodo al Pool CON el cliente desde el inicio
                logging.debug(f"DEBUG: Llamando a Pool.connect con cliente: {type(self)}")
                await wavelink.Pool.connect(nodes=[node], client=self, cache_capacity=100)
                
                logging.info("on_ready: Wavelink conectado exitosamente. Esperando NodeReadyEvent...")
                # 3. Marcar que se intentó la conexión
                self.wavelink_connected = True

            except Exception as e:
                # --- MANEJO DE EXCEPCIONES CORREGIDO ---
                logging.error(f"FALLO AL CONECTAR NODO LAVALINK desde on_ready ({LAVALINK_URI}):")
                logging.error(f"  Tipo de error: {type(e).__name__}")
                logging.error(f"  Mensaje: {e}")
                
                # Manejo de errores de conexión comunes
                if isinstance(e, (aiohttp.ClientConnectorError, asyncio.TimeoutError)):
                    logging.warning(f"Error de conexión: {type(e).__name__}")
                    logging.warning("Asegúrate de que Lavalink esté ejecutándose en localhost:2333")
                elif isinstance(e, wavelink.InvalidNodeException):
                    logging.warning("Nodo inválido - verifica la configuración")
                elif "Authorization" in str(e) or "password" in str(e).lower():
                    logging.warning("Error de autorización - verifica LAVALINK_PASSWORD")
                else:
                    logging.exception("Traceback del error de conexión inesperado:")
                # --- FIN CORRECCIÓN EXCEPCIONES ---
                
        elif wavelink.Pool.nodes:
            logging.info("on_ready: Wavelink ya parece tener nodos conectados.")
        elif self.wavelink_connected:
            logging.info("on_ready: Ya se intentó conectar Wavelink previamente.")

    # --- Listener on_wavelink_node_ready (SIMPLIFICADO) ---
    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        """Se dispara cuando un nodo Lavalink está listo."""
        logging.info(f"✅ EVENTO: Wavelink Nodo '{payload.node.identifier}' está listo (Sesión {payload.session_id}).")
        # El cliente ya fue establecido en Pool.connect, solo marcamos como listo
        self.wavelink_ready.set() # Señalizar que Wavelink está completamente listo
        logging.info("🎵 Sistema de audio completamente inicializado.")


# --- Crear instancia del Bot ---
bot = MyBot()

# --- Manejador de Errores Global ---
@bot.event

async def on_command_error(ctx: commands.Context, error):
    # --- !! CORRECCIÓN NameError AQUÍ !! ---
    # Verificar si el comando pertenece al Cog de Música comparando nombres
    is_music_cog_command = ctx.cog is not None and ctx.cog.qualified_name == "Music" # Asume que el name="Music" en la clase del Cog

    # Si es comando de música y Wavelink no está listo, enviar mensaje y retornar
    if is_music_cog_command and not bot.wavelink_ready.is_set():
        # Intentar obtener el Cog para usar su build_embed
        music_cog = bot.get_cog("Music")
        if music_cog and hasattr(music_cog, 'build_embed'):
            await ctx.send(embed=music_cog.build_embed("Servidor Ocupado", "⏳ El servidor de audio aún no está listo. Intenta de nuevo en unos segundos.", color=discord.Color.orange()))
        else:
            await ctx.send("⏳ El servidor de audio aún no está listo. Intenta de nuevo en unos segundos.")
        return

    # Manejo de errores específicos
    if isinstance(error, commands.CommandNotFound): 
        pass
    elif isinstance(error, commands.CommandInvokeError):
        logging.exception(f"Error ejecutando comando '{ctx.command}': {error.original}")
        await ctx.send(f"🤯 ¡Ups! Ocurrió un error interno al ejecutar el comando `{ctx.command}`.")
    elif isinstance(error, commands.CheckFailure):
        logging.warning(f"Check fallido para '{ctx.command}' por {ctx.author}: {error}")
        await ctx.send("🚫 No tienes permiso para usar este comando.", delete_after=10)
    elif isinstance(error, commands.MissingRequiredArgument):
        logging.warning(f"Argumento faltante en comando '{ctx.command}': {error}")
        if ctx.command and ctx.command.help: 
            await ctx.send(f"🤔 Te falta un argumento. Uso: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`\n{ctx.command.help}", delete_after=20)
        else: 
            await ctx.send(f"🤔 Te falta un argumento para el comando `{ctx.command}`.", delete_after=15)
    elif isinstance(error, commands.UserInputError):
        logging.warning(f"Error de entrada en comando '{ctx.command}': {error}")
        await ctx.send(f"🤔 Hubo un error con los argumentos. Revisa `{ctx.prefix}help {ctx.command}` si necesitas ayuda.", delete_after=15)
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Comando en cooldown. Intenta en {error.retry_after:.1f} segundos.", delete_after=10)
    else:
        logging.exception(f"Error de comando no manejado: {error}")

# --- Carga de Extensiones ---
async def load_extensions(bot_instance: commands.Bot) -> None:
    extensions = ["bot.cogs.music", "bot.cogs.league"]
    for extension in extensions:
        try:
            await bot_instance.load_extension(extension)
            logging.info(f"Extensión '{extension}' cargada.")
        except Exception as e:
            logging.exception(f"Error cargando extensión '{extension}': {e}")

# --- run_bot y Punto de Entrada ---
async def run_bot() -> None:
    async with bot:
        await bot.start(settings.discord_token)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logging.info("Proceso terminado por el usuario (Ctrl+C).")
    except Exception as e:
        logging.exception(f"Error fatal en el nivel principal: {e}")

# --- Import Hack ---
try:
    from bot.cogs.music import MusicWavelinkCog
except ImportError:
    class MusicWavelinkCog: 
        pass
    logging.warning("Music cog not found for on_command_error check.")