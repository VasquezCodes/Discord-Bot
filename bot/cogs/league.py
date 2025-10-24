# --- bot/cogs/league.py ---

import discord
from discord.ext import commands
import logging
from typing import Optional, List, Dict
import requests
from bs4 import BeautifulSoup
import re
import asyncio

# --- Configuración ---
# Cuántos matchups mostrar por categoría (mejores/peores)
NUM_MATCHUPS_TO_SHOW = 5
DEFAULT_LANE = "mid"
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

# --- Función get_safe_champion_name_for_url (igual) ---
def get_safe_champion_name_for_url(champion_name: str) -> str:
    # ... (sin cambios) ...
    name = champion_name.lower()
    if name == "jarvan iv": return "jarvaniv"
    if name == "miss fortune": return "missfortune"
    if name == "dr mundo" or name == "dr. mundo": return "drmundo"
    if name == "wukong": return "monkeyking"
    if name == "nunu & willump" or name == "nunu": return "nunu"
    name = re.sub(r"['.\s]", "", name)
    return name

# --- Función scrape_opgg_matchups (MODIFICADA: devuelve TODOS los matchups) ---
def scrape_opgg_matchups(champion_name: str, lane: str) -> Optional[List[Dict]]: # Cambiado nombre y tipo de retorno
    """
    Obtiene TODOS los matchups (campeón y WR del campeón buscado) desde OP.GG.
    Devuelve una lista de diccionarios: [{'champion': str, 'win_rate_float': float}]
    """
    safe_champion_name = get_safe_champion_name_for_url(champion_name)
    opgg_lane = LANE_MAP.get(lane.lower(), DEFAULT_LANE)
    url = OPGG_BASE_URL.format(champion=safe_champion_name, lane=opgg_lane)
    logging.info(f"[LoL Scraper] Accediendo a: {url}")

    # ... (requests y manejo de errores igual) ...
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
    # --- CAMBIO: La lista ahora guarda dicts con 'win_rate_float' ---
    all_matchups = []

    try:
        container_selector = 'li.cursor-pointer'
        # --- Selectores Flexibles (iguales) ---
        # Nombre: Buscar img y usar 'alt'
        # Win Rate: Buscar strong que contenga '%'
        # ---

        matchup_elements = soup.select(container_selector)
        if not matchup_elements:
            logging.warning(f"[LoL Scraper] No se encontraron elementos con selector: '{container_selector}' en {url}")
            # Devolver lista vacía si no hay elementos en la página
            return []

        logging.info(f"Encontrados {len(matchup_elements)} elementos <li> potenciales.")

        for item in matchup_elements:
            champ_name: Optional[str] = None
            win_rate_float: Optional[float] = None # Guardamos el float directamente

            # Extraer Nombre desde img alt (igual)
            img_tag = item.find('img')
            if img_tag and img_tag.has_attr('alt'):
                alt_text = img_tag['alt']
                champ_name = re.sub(r'\s+loading.*$', '', alt_text, flags=re.IGNORECASE).strip()

            # Extraer Win Rate buscando '%' en <strong> y convertir a float (igual)
            strong_tags = item.find_all('strong')
            for strong in strong_tags:
                text = strong.get_text(strip=True)
                if '%' in text:
                    try:
                        win_rate_float = float(text.replace('%', '').strip())
                    except ValueError:
                        win_rate_float = None
                        logging.warning(f"[LoL Scraper] Error convirtiendo WR '{text}'")
                    break # Salir del bucle de strongs

            # --- CAMBIO: Guardar si tenemos ambos datos, SIN filtro de WR ---
            if champ_name and win_rate_float is not None:
                all_matchups.append({'champion': champ_name, 'win_rate_float': win_rate_float})
            # --- FIN CAMBIO ---
            # else: # Logueo de datos faltantes (opcional)
            #     missing = [] # ...

    except Exception:
        logging.exception("[LoL Scraper] Error al parsear HTML.")
        return None # Error durante el parseo

    if not all_matchups:
        logging.warning("[LoL Scraper] No se extrajeron matchups válidos.")
        # Devolver lista vacía si el parseo funcionó pero no extrajo nada
        return []

    # --- CAMBIO: La función devuelve la lista COMPLETA sin ordenar/filtrar ---
    logging.info(f"[LoL Scraper] Extraídos {len(all_matchups)} matchups válidos.")
    return all_matchups
# --- Fin función scraping ---


# --- Cog de Discord ---
class LeagueCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- COMANDO ACTUALIZADO PARA MOSTRAR MEJORES Y PEORES ---
    # Renombrado a 'matchups', mantenemos 'counters' y 'c' como alias
    @commands.command(name="matchups", aliases=["counters", "c", "m"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def get_matchups(self, ctx: commands.Context, *, query: str): # Nombre cambiado
        """
        Muestra los mejores y peores matchups para un campeón en una línea.
        Uso: .matchups <campeon> [linea] (ej: .matchups Yasuo mid)
        """
        # ... (Parsing de query para champion_name y target_lane igual que antes) ...
        query = query.strip()
        champion_name: Optional[str] = None
        target_lane: Optional[str] = None
        possible_lanes = list(LANE_MAP.keys())
        words = query.split()
        for i in range(len(words), 0, -1):
            potential_lane = " ".join(words[i-1:]).lower()
            if potential_lane in possible_lanes:
                target_lane = LANE_MAP[potential_lane]
                champion_name = " ".join(words[:i-1])
                break
        if champion_name is None:
            champion_name = query
            target_lane = DEFAULT_LANE
        if not champion_name:
             await ctx.send(f"⚠️ ¡No especificaste un campeón! Uso: `{ctx.prefix}matchups <campeon> [linea]`")
             return
        logging.info(f"Comando .matchups parseado: Campeón='{champion_name}', Línea='{target_lane}'")


        async with ctx.typing():
            loop = asyncio.get_event_loop()
            # --- CAMBIO: Llamamos a la nueva función ---
            all_matchups = await loop.run_in_executor(
                None,
                scrape_opgg_matchups, # Nueva función
                champion_name,
                target_lane
            )

        # --- Procesar y Enviar Respuesta (MODIFICADO) ---
        embed_title = f"Matchups para {champion_name.capitalize()} ({target_lane.capitalize()})"

        if all_matchups is None:
            embed = discord.Embed(title=embed_title, description="❌ Error al buscar en OP.GG.", color=discord.Color.red())
        elif not all_matchups:
            embed = discord.Embed(title=embed_title, description="❓ No se encontraron datos de matchups.", color=discord.Color.orange())
        else:
            # --- NUEVO: Ordenar, seleccionar mejores/peores y formatear ---
            # Ordenar TODOS los matchups por win_rate_float (descendente: mejores primero)
            matchups_sorted_desc = sorted(all_matchups, key=lambda x: x.get('win_rate_float', -1.0), reverse=True)

            # Seleccionar los N mejores
            best_matchups = matchups_sorted_desc[:NUM_MATCHUPS_TO_SHOW]

            # Seleccionar los N peores (los últimos N de la lista ordenada descendente)
            # O reordenar ascendente y tomar los primeros N
            matchups_sorted_asc = sorted(all_matchups, key=lambda x: x.get('win_rate_float', 101.0))
            worst_matchups = matchups_sorted_asc[:NUM_MATCHUPS_TO_SHOW]

            embed = discord.Embed(title=embed_title, color=discord.Color.blue())

            # Campo para Mejores Matchups
            best_lines = []
            if best_matchups:
                for i, matchup in enumerate(best_matchups):
                    wr_float = matchup.get('win_rate_float', 0.0)
                    best_lines.append(f"**{i+1}. vs {matchup.get('champion', '???')}**: WR → **{wr_float:.2f}%** 👍")
            else:
                best_lines.append("No se encontraron matchups favorables claros.")
            embed.add_field(name=f"✅ Top {len(best_lines)} Mejores Matchups", value="\n".join(best_lines), inline=False)

            # Campo para Peores Matchups
            worst_lines = []
            if worst_matchups:
                 for i, matchup in enumerate(worst_matchups):
                    wr_float = matchup.get('win_rate_float', 100.0)
                    worst_lines.append(f"**{i+1}. vs {matchup.get('champion', '???')}**: WR → **{wr_float:.2f}%** 👎")
            else:
                 worst_lines.append("No se encontraron matchups desfavorables claros.")
            embed.add_field(name=f"❌ Top {len(worst_lines)} Peores Matchups (Counters)", value="\n".join(worst_lines), inline=False)

            embed.set_footer(text=f"Datos de OP.GG | Total matchups encontrados: {len(all_matchups)}")
            # --- FIN NUEVO FORMATO ---

        await ctx.send(embed=embed)


    # --- Error handler (actualizar nombre de comando si es necesario) ---
    @get_matchups.error # Cambiado nombre
    async def matchups_error(self, ctx: commands.Context, error): # Cambiado nombre
        if isinstance(error, commands.MissingRequiredArgument):
             if error.param.name == 'query':
                 await ctx.send(f"⚠️ ¡Especifica un campeón! Uso: `{ctx.prefix}matchups <campeon> [linea]`") # Cambiado nombre comando
             else:
                  await ctx.send(f"⚠️ Faltan argumentos.")
        elif isinstance(error, commands.CommandOnCooldown):
             await ctx.send(f"⏳ Espera {error.retry_after:.1f} segundos.", delete_after=5)
        else:
            logging.error(f"Error inesperado en comando .matchups: {error}") # Cambiado nombre comando
            await ctx.send("❌ Ocurrió un error inesperado.")

# --- Función setup (igual) ---
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeagueCog(bot))
    logging.info("Cog de League of Legends cargado.")