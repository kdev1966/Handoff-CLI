#!/bin/bash
set -e

GITHUB_RAW="https://githubusercontent.com"
BIN_DIR="$HOME/.local/bin"

echo "🔀 Installation de Handoff-CLI..."

# 1. Création du dossier local si absent
mkdir -p "$BIN_DIR"

# 2. Téléchargement des deux composants clés
echo "  - Téléchargement de mcp-init..."
curl -sSf -o "$BIN_DIR/mcp-init" "$GITHUB_RAW/bin/mcp-init"
chmod +x "$BIN_DIR/mcp-init"

echo "  - Téléchargement de mcp-dev-manager..."
curl -sSf -o "$BIN_DIR/mcp-dev-manager" "$GITHUB_RAW/bin/mcp-dev-manager"
chmod +x "$BIN_DIR/mcp-dev-manager"

# 3. Injection automatique du hook 'cd' dans le shell de l'utilisateur
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$rc" ]; then
        if ! grep -q "mcp-init" "$rc"; then
            echo -e "\n# Initialisation automatique de Handoff-CLI\ncd() { builtin cd \"\$@\" && [ -f package.json ] || [ -f requirements.txt ] || [ -d .git ] && ~/.local/bin/mcp-init 2>/dev/null; }" >> "$rc"
            echo "  - Hook injecté dans $(basename "$rc")"
        fi
    fi
done

echo "🎉 Handoff-CLI installé avec succès !"
echo "👉 Redémarrez votre terminal ou lancez 'source ~/.zshrc' (ou .bashrc)."
echo "🛠️  Tapez 'mcp-dev-manager' pour piloter votre mémoire de développement."
