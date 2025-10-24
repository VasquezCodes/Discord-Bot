# Discord Music Bot

## Requisitos previos

- Python 3.10+
- `ffmpeg` accesible en el PATH del sistema
- Token de bot de Discord válido

## Instalación

1. Crear entorno virtual (opcional):
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```
2. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```
3. Copiar `.env.example` a `.env` y completar `DISCORD_TOKEN`.

## Ejecución

```bash
python -m bot.main
```
