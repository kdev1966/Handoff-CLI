# 🔀 Handoff-CLI

[![License: MIT](https://shields.io)](https://opensource.org)
[![Standard: MCP](https://shields.io)](https://modelcontextprotocol.io)
[![Language: Python/Bash](https://shields.io)]()

**Handoff-CLI** est un protocole de mémoire universel et agnostique conçu pour les développeurs qui utilisent plusieurs assistants IA (*Claude, GitHub Copilot, Gemini, Codex, etc.*) au sein d'un même projet. 

Il élimine la perte de contexte technique lors du passage d'un outil ou d'un IDE à un autre en centralisant la mémoire dans une base SQLite globale, synchronisée à la vitesse de l'éclair à chaque fois que vous naviguez dans votre terminal.

---

## ✨ Fonctionnalités clés

- **Agnostique et Universel :** Fonctionne avec n'importe quelle stack technique, architecture logicielle, et tous les IDE connus (VS Code, Cursor, Windsurf, Zed, JetBrains).
- **Zéro Pollution de Commit :** La base de données est globale (`~/.mcp_memory`). Seul un fichier éphémère `.ai_context.md` est généré localement pour guider les IA.
- **Transparence Totale :** Interception automatique et invisible via le terminal lors de vos commandes `cd` habituelles.
- **Contrôle Exclusif CLI :** Un gestionnaire interactif complet en Python pour mettre en pause, purger ou désinstaller l'outil en une commande.

---

## 🛠️ Architecture du Système

```text
  [ Terminal : cd mon-projet ]
               │
               ▼
       [ bin/mcp-init ] ──(Lit/Écrit)──► [ Base SQLite Globale (~/.mcp_memory) ]
               │
               ▼ (Génère à la racine du projet)
      [ .ai_context.md ] ◄──(Indexé automatiquement)──► [ Vos Assistants IA ]
                                                    (Claude, Copilot, Gemini)
```

---

## 🚀 Installation Rapide

Installez l'intégralité de l'écosystème en une seule ligne de commande dans votre terminal :

```bash
curl -sSf https://githubusercontent.com | sh
```

> 💡 **Note :** Pensez à redémarrer votre terminal ou à exécuter `source ~/.zshrc` (ou `source ~/.bashrc`) après l'installation pour activer le hook de suivi.

---

## 🧠 Le Prompt Système Magique

Pour que vos extensions IA utilisent cette mémoire de manière autonome, ajoutez simplement cette consigne dans les **Instructions Personnalisées** (*Custom Instructions* ou *System Prompt*) de vos outils (Copilot Chat, Claude Desktop, Cursor, etc.) :

> "[MCP MEMORY PROTOCOL] Tu as accès au serveur MCP global 'global-memory'. Ce projet est identifié par ton dossier de travail actuel (cwd). Au début de la session, fais un SELECT dans la table 'project_memory' avec le chemin absolu du dossier pour charger l'historique technique. Avant de t'arrêter ou lors d'un tournant majeur, fais un INSERT/UPDATE pour documenter la stack, le résumé de l'état actuel et les prochaines actions pour l'IA qui te succédera. Ne réinvente pas la roue."

---

## 🕹️ Utilisation du Gestionnaire (`mcp-dev-manager`)

Pour piloter l'outil, utilisez le CLI interactif dédié mis à votre disposition :

```bash
mcp-dev-manager
```

Ce panneau de contrôle vous permet de :
1. **Lister** tous vos projets actuellement indexés sous contrôle.
2. **Purger** la mémoire d'un espace de travail spécifique (et supprimer son fichier `.ai_context.md`).
3. **Désinstaller** proprement et intégralement l'outil de votre machine de développement.

### ⏸️ Mettre en pause temporairement
Si vous travaillez sur un projet confidentiel et souhaitez désactiver le suivi sans désinstaller l'outil, tapez :
```bash
mcp-toggle
```

---

## 📂 Structure du Projet

```text
handoff-cli/
├── bin/
│   ├── mcp-init            # Script Bash d'interception et génération Markdown
│   └── mcp-dev-manager     # Utilitaire CLI interactif Python d'administration
├── install.sh              # Script d'installation automatique distant
├── README.md               # Vous êtes ici
└── LICENSE                 # Licence MIT
```

---

## 📄 Licence

Ce projet est distribué sous licence MIT. Voir le fichier [LICENSE](LICENSE) pour plus de détails.
