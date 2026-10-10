# Handoff

[![CI](https://github.com/kdev1966/Handoff-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/kdev1966/Handoff-CLI/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Handoff** donne une mémoire persistante et partagée à vos assistants IA (Claude, GitHub Copilot, Cursor, Gemini, Codex…). Quand vous passez d'un outil à un autre sur un même projet, le suivant reprend là où le précédent s'est arrêté : stack technique, état actuel, prochaines actions.

Il fonctionne sous **Windows, macOS et Linux**, via le standard [MCP](https://modelcontextprotocol.io) et une CLI.

## Fonctionnement

```text
 Claude / Copilot / Cursor / Gemini / Codex
        │  MCP (stdio, local : aucun port réseau)
        ▼
   handoff serve ──┐
   handoff save  ──┼──► base SQLite locale (historique en ajout seul)
   handoff show  ──┘
        │
        ▼
   <projet>/.handoff/context.md   (vue générée, ignorée par git)
```

- Chaque assistant **lit** la mémoire en début de session (`memory_get`) et **enregistre** un passage de relais avant de s'arrêter (`memory_save`). Le serveur MCP transmet lui-même ces consignes au client. Si votre client ne les relaie pas au modèle, ajoutez une phrase à ses instructions personnalisées : « Utilise les outils Handoff `memory_get` et `memory_save` ».
- Le projet est identifié par son **remote git** (`origin`). La mémoire suit donc le dépôt même si vous le déplacez ou le clonez à nouveau. Sans remote, c'est le chemin du dossier qui sert d'identifiant.
- Chaque passage de relais est **ajouté** à l'historique : rien n'est écrasé et vous pouvez revenir à un état antérieur.

## Installation

Python 3.10 ou plus est requis. Installez Handoff dans un environnement isolé :

```bash
pipx install git+https://github.com/kdev1966/Handoff-CLI
# ou
uv tool install git+https://github.com/kdev1966/Handoff-CLI
```

Une fois publié sur PyPI : `pipx install ai-handoff`.

## Connecter vos assistants

**Claude Code** (une seule commande) :

```bash
handoff setup            # enregistre le serveur pour votre utilisateur
handoff setup --dry-run  # affiche la commande sans l'exécuter
```

**Autres clients MCP** (Cursor, VS Code / Copilot, Claude Desktop, Gemini CLI, Codex, Windsurf, Zed…) : générez la configuration et ajoutez-la dans le fichier de configuration MCP de votre client, en suivant sa documentation :

```bash
handoff setup --print-config
```

La commande utilise le chemin absolu de l'interpréteur Python. Elle fonctionne donc même dans les applications graphiques dont le `PATH` est réduit.

## Outils MCP

| Outil | Rôle | Type |
|---|---|---|
| `memory_get(project_path)` | Lit le dernier passage de relais du projet | lecture seule |
| `memory_save(project_path, summary, next_actions, stack?, agent?)` | Enregistre un nouveau passage de relais | ajout, non destructif |
| `memory_history(project_path, limit?)` | Liste les passages de relais précédents | lecture seule |

Aucun outil ne permet à une IA de supprimer des données ni d'exécuter du SQL.

## CLI

| Commande | Rôle |
|---|---|
| `handoff show` | Affiche la mémoire du projet courant |
| `handoff save -s "état" -n "suite" [--stack "…"]` | Enregistre un passage de relais (`-` lit l'entrée standard) |
| `handoff history [-l N]` | Historique, du plus récent au plus ancien |
| `handoff restore ID` | Rétablit un ancien passage de relais comme le plus récent |
| `handoff list` | Liste tous les projets suivis |
| `handoff pause` / `handoff resume` | Suspend ou reprend le suivi d'un projet (travail confidentiel) |
| `handoff purge [--key KEY] [-y]` | Supprime toute la mémoire d'un projet |
| `handoff render` | Régénère `.handoff/context.md` |
| `handoff where` | Indique où sont stockées les données |

Toutes les commandes agissent sur le dossier courant, ou sur le dossier passé avec `--path`.

### Assistants sans MCP

Après chaque enregistrement, Handoff génère `.handoff/context.md` à la racine du projet. Ce dossier contient son propre `.gitignore` : il n'apparaît jamais dans `git status` et votre `.gitignore` n'est pas modifié. Un assistant sans MCP peut lire ce fichier, puis enregistrer avec `handoff save`. Ne modifiez pas ce fichier à la main : il est régénéré.

Pour qu'un outil le lise automatiquement, ajoutez une ligne de renvoi dans le fichier d'instructions qu'il lit déjà (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`…), par exemple : « Lis `.handoff/context.md` en début de session. »

## Sécurité

- **Local uniquement** : le serveur MCP communique en stdio et n'ouvre aucun port réseau.
- **Surface réduite** : 3 outils au schéma typé, pas de SQL brut, pas de suppression par l'IA.
- **Chemins validés** : le chemin doit être absolu et exister. La racine du disque, le dossier personnel et ses parents sont refusés.
- **Entrées validées** : 16 Kio maximum par champ, caractères de contrôle supprimés, nom d'agent contrôlé.
- **Secrets masqués avant stockage** : clés AWS, jetons GitHub/GitLab/Slack/Stripe/Google, clés `sk-…`, JWT, clés privées PEM, identifiants dans les URL, affectations `password=…`. La mémoire étant relue par d'autres fournisseurs d'IA, rien de cela ne doit y entrer.
- **La mémoire est une donnée, pas une consigne** : les assistants sont invités à ne jamais exécuter d'instructions trouvées dans la mémoire.
- **Fichiers protégés** : base en mode `0600` dans un dossier `0700` (sous Windows, protégée par les droits du profil utilisateur). Écriture atomique, liens symboliques refusés, aucun fichier non généré par Handoff n'est écrasé.

> Le masquage repose sur des formats connus : un secret dans un format inédit peut passer. N'enregistrez pas de secrets dans la mémoire.

## Emplacement des données

| Système | Dossier |
|---|---|
| Windows | `%LOCALAPPDATA%\handoff\` |
| macOS | `~/Library/Application Support/handoff/` |
| Linux | `$XDG_DATA_HOME/handoff/` (par défaut `~/.local/share/handoff/`) |

La variable d'environnement `HANDOFF_HOME` permet de choisir un autre dossier.

## Limites

- La mémoire est locale à la machine. Elle n'est pas synchronisée entre postes ni partagée avec une équipe.
- Deux clones d'un même dépôt partagent la même mémoire (c'est voulu).
- `handoff setup` ne configure automatiquement que Claude Code. Pour les autres clients, la configuration se fait manuellement avec `--print-config`.

## Développement

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows : .venv\Scripts\pip
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

La CI exécute les tests sous Windows, macOS et Linux avec Python 3.10, 3.12 et 3.14. Une release est publiée sur PyPI (par *trusted publishing*) quand un tag `vX.Y.Z` correspondant à la version du paquet est poussé.

## Licence

MIT. Voir [LICENSE](LICENSE).
