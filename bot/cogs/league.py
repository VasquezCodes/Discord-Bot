# --- bot/cogs/league.py ---
# Scraper y comando de LoL que obtiene matchups desde League of Graphs.

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

# Nota: en League of Graphs la URL de counters NO lleva rol. Aun así
# conservamos el parseo de "lane" para que el comando sea compatible
# con tu sintaxis (.matchups Yasuo mid), aunque no se use en la URL.
DEFAULT_LANE = "mid"
LANE_MAP = {
    "top": "top", "superior": "top",
    "jungle": "jungle", "jg": "jungle", "jungla": "jungle",
    "mid": "mid", "middle": "mid", "medio": "mid",
    "adc": "adc", "bottom": "adc", "bot": "adc", "inferior": "adc",
    "support": "support", "sup": "support", "soporte": "support"
}

# Base League of Graphs (sin rol)
LEAGUEGRAPHS_BASE_URL = "https://www.leagueofgraphs.com/es/champions/counters/{champion}"

REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
    'Referer': 'https://www.google.com/'
}
# --- Fin Configuración ---


# --- Normalización del nombre del campeón para League of Graphs ---
def get_safe_champion_name_for_url(champion_name: str) -> str:
    """
    Convierte el nombre recibido al slug que usa League of Graphs.
    Reglas:
      - minúsculas
      - quitar espacios, apóstrofes y puntos
      - algunos alias comunes (jarvan iv -> jarvaniv, miss fortune -> missfortune, etc.)
    """
    name = champion_name.strip().lower()

    # aliases frecuentes
    if name in {"jarvan iv", "jarvan 4", "jarvaniv"}:
        return "jarvaniv"
    if name in {"miss fortune", "missfortune"}:
        return "missfortune"
    if name in {"dr mundo", "dr. mundo", "doctor mundo", "drmundo"}:
        return "drmundo"
    if name in {"nunu & willump", "nunu y willump", "nunu-willump"}:
        return "nunu"
    # ¡OJO! En League of Graphs Wukong es "wukong" (no "monkeyking")

    # quitar apóstrofes, puntos y espacios (kha'zix -> khazix, kai'sa -> kaisa, vel'koz -> velkoz)
    name = re.sub(r"['.\s]", "", name)
    return name


# --- Scraper League of Graphs ---
def scrape_leagueofgraphs_matchups(champion_name: str, lane: str) -> Optional[List[Dict]]:
    """
    Devuelve TODOS los matchups de League of Graphs para el campeón dado.
    Retorna lista de dicts: [{'champion': str, 'win_rate_float': float}]
    - Busca secciones con h3 "gana más contra" y "pierde más contra".
    - Dentro de cada tabla, lee nombre del campeón y WinRate (progressbar[data-value] o texto con '%').
    """
    safe_name = get_safe_champion_name_for_url(champion_name)
    url = LEAGUEGRAPHS_BASE_URL.format(champion=safe_name)
    logging.info(f"[LoL Scraper] LeagueOfGraphs URL: {url}")

    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        if r.status_code in (403, 404):
            logging.error(f"[LoL Scraper] HTTP {r.status_code} para {url}")
            return None
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"[LoL Scraper] Error de red: {e}")
        return None
    except Exception as e:
        logging.error(f"[LoL Scraper] Error inesperado: {e}")
        return None

    soup = BeautifulSoup(r.content, "lxml")
    resultados: List[Dict] = []

    try:
        # Cada bloque relevante suele ser: <div class="boxContainer"><h3>...</h3><table class="data_table ...">...</table></div>
        for box in soup.select("div.boxContainer"):
            h3 = box.find("h3")
            if not h3:
                continue
            titulo = h3.get_text(" ", strip=True).lower()

            # Nos quedamos solo con los bloques de mejores/peores
            if ("gana más contra" not in titulo) and ("pierde más contra" not in titulo):
                continue

            tabla = box.select_one("table.data_table")
            if not tabla:
                logging.warning("[LoL Scraper] No se encontró table.data_table en un box válido.")
                continue

            # Recorremos filas
            for tr in tabla.select("tbody tr"):
                # --- Nombre del campeón rival ---
                nombre: Optional[str] = None

                # 1) Enlace al campeón dentro del primer td
                a = tr.select_one("td a[href*='/champions/']")
                if a:
                    nombre = a.get_text(strip=True)

                # 2) Fallback: tomar alt de img
                if not nombre:
                    img = tr.find("img")
                    if img and img.get("alt"):
                        nombre = img["alt"].strip()

                # --- WinRate de nuestro campeón vs el rival ---
                wr: Optional[float] = None

                # 1) progressbar con data-value (a veces es 0.xx o 52.xx)
                pb = tr.select_one("progressbar")
                if pb and pb.has_attr("data-value"):
                    try:
                        val = float(str(pb["data-value"]).replace(",", "."))
                        # Si es <=1 asumimos que viene normalizado [0,1]
                        wr = val * 100 if val <= 1.0 else val
                    except ValueError:
                        wr = None

                # 2) Texto con porcentaje en alguna celda
                if wr is None:
                    for td in tr.find_all("td"):
                        txt = td.get_text(" ", strip=True)
                        m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", txt)
                        if m:
                            try:
                                wr = float(m.group(1).replace(",", "."))
                                break
                            except ValueError:
                                pass

                # 3) Atributo data-sort-value (algunas columnas lo usan)
                if wr is None:
                    td_num = tr.find("td", attrs={"data-sort-value": True})
                    if td_num:
                        try:
                            val = float(str(td_num["data-sort-value"]).replace(",", "."))
                            wr = val * 100 if val <= 1.0 else val
                        except ValueError:
                            pass

                if nombre and (wr is not None):
                    resultados.append({"champion": nombre, "win_rate_float": wr})

    except Exception:
        logging.exception("[LoL Scraper] Error al parsear HTML de LeagueOfGraphs.")
        return None

    if not resultados:
        logging.warning("[LoL Scraper] No se extrajeron matchups válidos de LeagueOfGraphs.")
        return []

    logging.info(f"[LoL Scraper] Extraídos {len(resultados)} matchups.")
    return resultados


# --- Cog de Discord ---
class LeagueCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Renombrado a 'matchups', mantenemos 'counters' y 'c' como alias
    @commands.command(name="matchups", aliases=["counters", "c", "m"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def get_matchups(self, ctx: commands.Context, *, query: str):
        """
        Muestra los mejores y peores matchups para un campeón.
        Uso: .matchups <campeon> [linea]  (ej: .matchups Kai'Sa adc)
        * La línea hoy no afecta la URL de LeagueOfGraphs, se mantiene por compatibilidad.
        """
        # --- Parsing de query para champion_name y target_lane ---
        query = query.strip()
        champion_name: Optional[str] = None
        target_lane: Optional[str] = None
        posibles_lineas = list(LANE_MAP.keys())
        palabras = query.split()
        for i in range(len(palabras), 0, -1):
            posible_lane = " ".join(palabras[i - 1:]).lower()
            if posible_lane in posibles_lineas:
                target_lane = LANE_MAP[posible_lane]
                champion_name = " ".join(palabras[:i - 1])
                break
        if champion_name is None:
            champion_name = query
            target_lane = DEFAULT_LANE
        if not champion_name:
            await ctx.send(f"⚠️ ¡No especificaste un campeón! Uso: `{ctx.prefix}matchups <campeon> [linea]`")
            return

        logging.info(f"Comando .matchups => Campeón='{champion_name}', Línea='{target_lane}'")

        async with ctx.typing():
            loop = asyncio.get_event_loop()
            all_matchups = await loop.run_in_executor(
                None,
                scrape_leagueofgraphs_matchups,
                champion_name,
                target_lane  # hoy no se usa
            )

        embed_title = f"Matchups para {champion_name.capitalize()} ({target_lane.capitalize()})"

        if all_matchups is None:
            embed = discord.Embed(
                title=embed_title,
                description="❌ Error al buscar en League of Graphs.",
                color=discord.Color.red()
            )
        elif not all_matchups:
            embed = discord.Embed(
                title=embed_title,
                description="❓ No se encontraron datos de matchups.",
                color=discord.Color.orange()
            )
        else:
            # Ordenar por WR de nuestro campeón (desc: mejores primero)
            orden_desc = sorted(all_matchups, key=lambda x: x.get('win_rate_float', -1.0), reverse=True)
            mejores = orden_desc[:NUM_MATCHUPS_TO_SHOW]

            # Peores (ascendente)
            orden_asc = sorted(all_matchups, key=lambda x: x.get('win_rate_float', 101.0))
            peores = orden_asc[:NUM_MATCHUPS_TO_SHOW]

            embed = discord.Embed(title=embed_title, color=discord.Color.blue())

            # Campo: Mejores
            mejores_lines = []
            for i, m in enumerate(mejores):
                wr = m.get('win_rate_float', 0.0)
                mejores_lines.append(f"**{i+1}. vs {m.get('champion','???')}** — WR: **{wr:.2f}%** 👍")
            embed.add_field(
                name=f"✅ Top {len(mejores_lines)} Mejores Matchups",
                value="\n".join(mejores_lines) or "—",
                inline=False
            )

            # Campo: Peores
            peores_lines = []
            for i, m in enumerate(peores):
                wr = m.get('win_rate_float', 100.0)
                peores_lines.append(f"**{i+1}. vs {m.get('champion','???')}** — WR: **{wr:.2f}%** 👎")
            embed.add_field(
                name=f"❌ Top {len(peores_lines)} Peores Matchups (Counters)",
                value="\n".join(peores_lines) or "—",
                inline=False
            )

            embed.set_footer(text=f"Datos de League of Graphs | Matchups analizados: {len(all_matchups)}")

        await ctx.send(embed=embed)

    # --- Handler de errores ---
    @get_matchups.error
    async def matchups_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            if getattr(error, "param", None) and error.param.name == 'query':
                await ctx.send(f"⚠️ ¡Especifica un campeón! Uso: `{ctx.prefix}matchups <campeon> [linea]`")
            else:
                await ctx.send("⚠️ Faltan argumentos.")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Espera {error.retry_after:.1f} segundos.", delete_after=5)
        else:
            logging.error(f"Error inesperado en comando .matchups: {error}")
            await ctx.send("❌ Ocurrió un error inesperado.")


# --- Setup del COG ---
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeagueCog(bot))
    logging.info("Cog de League of Legends cargado.")
