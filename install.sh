#!/bin/bash
# install.sh — Installerar riksdag-oppna-data MCP-servern i ~/MCP-Servers
#
# Körning:
#   chmod +x install.sh
#   ./install.sh
#
# Förutsättningar:
#   - Python 3.11 eller senare
#   - PostgreSQL med pgvector-tillägget (eller ändra DATABASE_URL till SQLite i .env)

set -e  # Avbryt vid fel

DEST=~/MCP-Servers/riksdag-oppna-data
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Installerar riksdag-oppna-data MCP-server ==="
echo ""

# 1. Skapa målkatalog
echo "Skapar $DEST ..."
mkdir -p "$DEST"

# 2. Kopiera filer (exkludera installationsskriptet och eventuell cache)
echo "Kopierar filer ..."
rsync -a --exclude='.cache' --exclude='__pycache__' --exclude='*.pyc'          --exclude='*.db' --exclude='.env'          "$SCRIPT_DIR/" "$DEST/"

# 3. Skapa virtuell miljö
echo "Skapar virtuell miljö ..."
python3 -m venv "$DEST/.venv"
source "$DEST/.venv/bin/activate"

# 4. Installera beroenden
echo "Installerar beroenden ..."
pip install --quiet --upgrade pip
pip install --quiet -r "$DEST/requirements.txt"

# 5. Skapa .env om den inte redan finns
if [ ! -f "$DEST/.env" ]; then
    cp "$DEST/config.example.env" "$DEST/.env"
    echo ""
    echo "=== Konfiguration ==="
    echo "En .env-fil har skapats i $DEST"
    echo "Öppna den och ange rätt DATABASE_URL:"
    echo ""
    echo "  PostgreSQL (rekommenderas):"
    echo "    DATABASE_URL=postgresql://user:password@localhost/riksdag_rag"
    echo ""
    echo "  SQLite (ingen server krävs):"
    echo "    DATABASE_URL=sqlite:///$DEST/riksdag_rag.db"
    echo ""
    echo "Redigera filen och kör sedan:"
    echo "  $DEST/.venv/bin/python $DEST/db/init_db.py"
    echo ""
else
    echo ".env finns redan — hoppar över."
fi

echo ""
echo "=== Nästa steg ==="
echo ""
echo "1. Redigera $DEST/.env"
echo ""
echo "2. Initiera databasen:"
echo "     $DEST/.venv/bin/python $DEST/db/init_db.py"
echo ""
echo "3. Registrera servern i din MCP-klient (exempel för Claude Desktop):"
echo '     {'
echo '       "mcpServers": {'
echo '         "riksdag": {'
echo '           "command": "'"$DEST"'/.venv/bin/python",'
echo '           "args": ["'"$DEST"'/mcp_server.py"]'
echo '         }'
echo '       }'
echo '     }'
echo ""
echo "4. Starta om din MCP-klient."
echo ""
echo "=== Installation klar ==="
