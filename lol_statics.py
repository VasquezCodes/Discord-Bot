# --- bot/cogs/league.py ---

import discord
from discord.ext import commands
import logging
from typing import Optional, List, Dict
import requests
from bs4 import BeautifulSoup
import re
import asyncio

# --- Configuración (Igual que antes) ---
TOP_N_WORST_MATCHUPS = 3
DEFAULT_LANE = ""
# Añadimos alias comunes para líneas
LANE_MAP = {
    "top": "top", "superior": "top",
    "jungle": "jungle", "jg": "jungle", "jungla": "jungle",
    "mid": "mid", "middle": "mid", "medio": "mid",
    "adc": "adc", "bottom": "adc", "bot": "adc", "inferior": "adc", 
    "support": "support", "sup": "support", "soporte": "support"
}
OPGG_BASE_URL = "https://op.gg/es/lol/champions/{champion}/counters/{lane}"
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8'
}
# --- Fin Configuración ---

# --- NUEVA: Función para limpiar nombre para URL ---
def get_safe_champion_name_for_url(champion_name: str) -> str:
    """Prepara el nombre del campeón para la URL de OP.GG."""
    name = champion_name.lower()
    # Casos especiales comunes
    if name == "jarvan iv": return "jarvaniv"
    if name == "miss fortune": return "missfortune"
    if name == "dr mundo" or name == "dr. mundo": return "drmundo"
    if name == "wukong": return "monkeyking" # OP.GG usa el nombre interno
    if name == "nunu & willump" or name == "nunu": return "nunu" # Simplificado
    # Regla general: quitar apóstrofes, puntos, espacios
    name = re.sub(r"['.\s]", "", name)
    return name
# ---

# --- Función scrape_opgg_worst_matchups (Usa la nueva función de limpiar nombre) ---
def scrape_opgg_worst_matchups(champion_name: str, lane: str) -> Optional[List[Dict[str, str]]]:
    """Obtiene los peores matchups desde OP.GG de forma más flexible."""
    # --- CAMBIO AQUÍ: Usar la nueva función para limpiar ---
    safe_champion_name = get_safe_champion_name_for_url(champion_name)
    # --- FIN CAMBIO ---
    opgg_lane = LANE_MAP.get(lane.lower(), DEFAULT_LANE)
    url = OPGG_BASE_URL.format(champion=safe_champion_name, lane=opgg_lane)
    logging.info(f"[LoL Scraper] Accediendo a: {url}")

    # ... (Resto de la función de scraping: requests, BeautifulSoup, extracción flexible igual que antes) ...
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=10)
        if response.status_code in [404, 403]:
            logging.error(f"[LoL Scraper] Error {response.status_code} para {url}")
            return None
        response.raise_for_status()
        logging.info("[LoL Scraper] Página obtenida.")
    except requests.exceptions.RequestException as e:
        logging.error(f"[LoL Scraper] Error de red: {e}")
        return None
    except Exception as e:
        logging.error(f"[LoL Scraper] Error inesperado: {e}")
        return None

    soup = BeautifulSoup(response.content, 'lxml')
    potential_matchups = []
    try:
        container_selector = 'li.cursor-pointer'
        # --- Selectores Flexibles ---
        # Nombre: Buscar img y usar 'alt'
        # Win Rate: Buscar strong que contenga '%'
        # ---

        matchup_elements = soup.select(container_selector)
        if not matchup_elements:
            logging.warning(f"[LoL Scraper] No se encontraron elementos con selector: '{container_selector}' en {url}")
            return []

        for item in matchup_elements:
            champ_name: Optional[str] = None
            win_rate_str: Optional[str] = None
            win_rate_float: Optional[float] = None

            # Extraer Nombre desde img alt
            img_tag = item.find('img')
            if img_tag and img_tag.has_attr('alt'):
                alt_text = img_tag['alt']
                champ_name = re.sub(r'\s+loading.*$', '', alt_text, flags=re.IGNORECASE).strip()
            # else: # Podríamos añadir un fallback si 'alt' falla

            # Extraer Win Rate buscando '%' en <strong>
            strong_tags = item.find_all('strong')
            for strong in strong_tags:
                text = strong.get_text(strip=True)
                if '%' in text:
                    win_rate_str = text
                    try:
                        win_rate_float = float(win_rate_str.replace('%', '').strip())
                    except ValueError: win_rate_float = None
                    break
            
            # Guardado Temporal
            if champ_name and win_rate_float is not None:
                potential_matchups.append({'champion': champ_name, 'win_rate_float': win_rate_float})

    except Exception:
        logging.exception("[LoL Scraper] Error al parsear HTML.")
        return None # Error durante el parseo

    if not potential_matchups:
        logging.warning("[LoL Scraper] No se extrajeron matchups válidos.")
        return []

    # Ordenar y seleccionar
    matchups_sorted = sorted(potential_matchups, key=lambda x: x.get('win_rate_float', 101.0))
    top_worst_matchups = matchups_sorted[:TOP_N_WORST_MATCHUPS]
    
    # Formatear salida (usando el nombre limpio original para la clave)
    original_safe_name = get_safe_champion_name_for_url(champion_name) # Asegura consistencia
    final_matchups_data = [
        {'champion': m['champion'], f'{original_safe_name}_win_rate': f"{m['win_rate_float']:.2f}%"}
        for m in top_worst_matchups
    ]
    return final_matchups_data
# --- Fin función scraping ---


# --- Cog de Discord ---
class LeagueCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- COMANDO ACTUALIZADO PARA PARSEAR MEJOR ---
    @commands.command(name="counters", aliases=["c"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def get_counters(self, ctx: commands.Context, *, query: str):
        """
        Muestra los peores matchups para un campeón en una línea.
        Uso: .counters <campeon> [linea] (ej: .counters Jarvan IV jungle)
             .counters <campeon>         (usa línea por defecto: mid)
        """
        query = query.strip()
        champion_name: Optional[str] = None
        target_lane: Optional[str] = None

        # Intentar separar campeón y línea
        possible_lanes = list(LANE_MAP.keys())
        words = query.split()

        # Buscar la línea desde el final
        for i in range(len(words), 0, -1):
            potential_lane = " ".join(words[i-1:]).lower()
            if potential_lane in possible_lanes:
                target_lane = LANE_MAP[potential_lane] # Guardar el nombre OP.GG
                champion_name = " ".join(words[:i-1])
                break # Encontramos la línea

        # Si no se encontró línea, asumir que todo es el campeón y usar default
        if champion_name is None:
            champion_name = query
            target_lane = DEFAULT_LANE

        # Validar que tengamos un nombre de campeón
        if not champion_name:
             await ctx.send(f"⚠️ ¡No especificaste un campeón! Uso: `{ctx.prefix}counters <campeon> [linea]`")
             return

        logging.info(f"Comando .counters parseado: Campeón='{champion_name}', Línea='{target_lane}'")

        async with ctx.typing():
            loop = asyncio.get_event_loop()
            worst_matchups = await loop.run_in_executor(
                None,
                scrape_opgg_worst_matchups,
                champion_name, # Pasar el nombre original (la función lo limpia para URL)
                target_lane
            )

        # --- Procesar y Enviar Respuesta (Igual que antes, pero usa champion_name original) ---
        embed_title = f"Peores Matchups para {champion_name.capitalize()} ({target_lane.capitalize()})"
        # ... (resto del código para crear y enviar el embed igual, usando 'champion_name' original para el título y la clave WR) ...
        if worst_matchups is None:
             embed = discord.Embed(title=embed_title, description="❌ Error al buscar en OP.GG.", color=discord.Color.red())
        elif not worst_matchups:
             embed = discord.Embed(title=embed_title, description="❓ No se encontraron datos claros.", color=discord.Color.orange())
        else:
            description_lines = []
            # Usar el nombre limpio original para la clave del WR
            safe_champion_name_key = get_safe_champion_name_for_url(champion_name)
            wr_key = f'{safe_champion_name_key}_win_rate'
            description_lines.append(f"_(WR más bajo para **{champion_name.capitalize()}**)_")
            description_lines.append("")
            for i, matchup in enumerate(worst_matchups):
                wr_value = matchup.get(wr_key, "N/A")
                description_lines.append(f"**{i+1}. vs {matchup.get('champion', '???')}**: WR → **{wr_value}**")
            embed = discord.Embed(title=embed_title, description="\n".join(description_lines), color=discord.Color.blue())
            embed.set_footer(text="Datos de OP.GG")
        await ctx.send(embed=embed)


    @get_counters.error
    async def counters_error(self, ctx: commands.Context, error):
        # --- Actualizado para el nuevo argumento 'query' ---
        if isinstance(error, commands.MissingRequiredArgument):
             if error.param.name == 'query': # Comprobar si falta el argumento principal
                 await ctx.send(f"⚠️ ¡Especifica un campeón! Uso: `{ctx.prefix}counters <campeon> [linea]`")
             else: # Otro argumento requerido? Raro.
                  await ctx.send(f"⚠️ Faltan argumentos para el comando.")
        elif isinstance(error, commands.CommandOnCooldown):
             await ctx.send(f"⏳ Espera {error.retry_after:.1f} segundos.", delete_after=5)
        else:
            logging.error(f"Error inesperado en comando .counters: {error}")
            await ctx.send("❌ Ocurrió un error inesperado.")

# --- Función setup (Igual que antes) ---
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeagueCog(bot))
    logging.info("Cog de League of Legends cargado.")