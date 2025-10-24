# --- bot/main.py (final corregido con audioop-lts y servidor Flask) ---

import asyncio
import logging
import os
import aiohttp

# --- Parche para Python 3.13: audioop fue removido de stdlib ---
# En Python 3.11.9, audioop está disponible nativamente
import sys
try:
    import audioop
except ImportError:
    # Solo si estamos en Python 3.13+, intentar usar audioop_lts
    try:
        import audioop_lts as audioop
        sys.modules['audioop'] = audioop
    except ImportError:
        logging.warning("⚠️ audioop no disponible. Algunas funciones de audio pueden no funcionar.")
        audioop = None

# --- Mantener Flask al inicio (para levantar servidor web en Render) ---
from flask import Flask
import threading

# --- Configurar servidor Flask para mantener activo el servicio ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot activo y ejecutándose correctamente", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Lanzar Flask en un hilo paralelo
threading.Thread(target=run_web, daemon=True).start()

# --- Cargar dotenv antes de cualquier otro import que use variables ---
from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands
import wavelink
from config.settings import get_settings

# --- Logging básico ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logging.getLogger('spotipy').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('discord').setLevel(logging.INFO)
logging.getLogger('wavelink').setLevel(logging.INFO)

settings = get_settings()

# --- Configuración Lavalink ---
LAVALINK_URI = os.getenv("LAVALINK_URI", "ws://localhost:2333")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

if not settings.discord_token:
    logging.critical("❌ DISCORD_TOKEN no está definido.")
    exit()
if not LAVALINK_PASSWORD or LAVALINK_PASSWORD == "youshallnotpass":
    logging.warning("⚠️ LAVALINK_PASSWORD no definida o usa default.")

# --- Intents ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True


# --- Clase Bot personalizada ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=settings.command_prefix,
            help_command=None,
            intents=intents
        )
        self.wavelink_connected = False
        self.wavelink_ready = asyncio.Event()

    async def setup_hook(self) -> None:
        logging.info("Ejecutando setup_hook...")
        await load_extensions(self)

    async def on_ready(self) -> None:
        logging.info(f"Conectado como {self.user} (ID: {self.user.id})")
        logging.info(f"Prefijo: {settings.command_prefix}")

        if not wavelink.Pool.nodes and not self.wavelink_connected:
            logging.info(f"Intentando conectar con Lavalink en {LAVALINK_URI}...")
            try:
                node = wavelink.Node(
                    identifier="MAIN",
                    uri=LAVALINK_URI,
                    password=LAVALINK_PASSWORD
                )
                await wavelink.Pool.connect(nodes=[node], client=self, cache_capacity=100)
                self.wavelink_connected = True
                logging.info("✅ Wavelink conectado correctamente.")
            except Exception as e:
                logging.error(f"❌ Error conectando con Lavalink ({type(e).__name__}): {e}")
                if isinstance(e, (aiohttp.ClientConnectorError, asyncio.TimeoutError)):
                    logging.warning("Verifica que Lavalink esté corriendo y accesible.")
                elif "Authorization" in str(e) or "password" in str(e).lower():
                    logging.warning("Error de contraseña o autorización con Lavalink.")
                else:
                    logging.exception("Traceback del error no manejado:")

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info(f"🎵 Nodo Lavalink '{payload.node.identifier}' listo (Session {payload.session_id}).")
        self.wavelink_ready.set()


# --- Instancia del bot ---
bot = MyBot()


# --- Manejo de errores global ---
@bot.event
async def on_command_error(ctx: commands.Context, error):
    is_music_cog_command = ctx.cog is not None and ctx.cog.qualified_name == "Music"

    if is_music_cog_command and not bot.wavelink_ready.is_set():
        music_cog = bot.get_cog("Music")
        if music_cog and hasattr(music_cog, 'build_embed'):
            await ctx.send(embed=music_cog.build_embed("Servidor Ocupado", "⏳ El servidor de audio aún no está listo.", color=discord.Color.orange()))
        else:
            await ctx.send("⏳ El servidor de audio aún no está listo.")
        return

    if isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.CommandInvokeError):
        logging.exception(f"Error ejecutando '{ctx.command}': {error.original}")
        await ctx.send(f"🤯 Ocurrió un error interno al ejecutar `{ctx.command}`.")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("🚫 No tienes permiso para usar este comando.", delete_after=10)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"🤔 Falta un argumento en `{ctx.command}`.", delete_after=15)
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ En cooldown. Intenta en {error.retry_after:.1f}s.", delete_after=10)
    else:
        logging.exception(f"Error no manejado: {error}")


# --- Cargar extensiones ---
async def load_extensions(bot_instance: commands.Bot) -> None:
    extensions = ["bot.cogs.music", "bot.cogs.league"]
    for ext in extensions:
        try:
            await bot_instance.load_extension(ext)
            logging.info(f"Extensión '{ext}' cargada.")
        except Exception as e:
            logging.exception(f"Error cargando '{ext}': {e}")


# --- Ejecutar bot ---
async def run_bot() -> None:
    async with bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logging.info("🛑 Bot detenido por el usuario.")
    except Exception as e:
        logging.exception(f"Error fatal: {e}")

# --- Import Hack (por compatibilidad) ---
try:
    from bot.cogs.music import MusicWavelinkCog
except ImportError:
    class MusicWavelinkCog:
        pass
    logging.warning("⚠️ Music cog no encontrado al iniciar.")
