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

- **En début de session**, la mémoire du projet est donnée à l'assistant : automatiquement par un hook quand l'outil le permet, sinon l'assistant l'obtient avec l'outil `memory_get`, comme le lui indique la consigne posée par `handoff setup`.
- **Avant de s'arrêter**, si le code a changé depuis la dernière passation, l'assistant est invité une fois à en enregistrer une (`memory_save`).
- Le projet est identifié par son **remote git** (`origin`). La mémoire suit donc le dépôt même si vous le déplacez ou le clonez à nouveau. Sans remote, c'est le chemin du dossier qui sert d'identifiant.
- Chaque passage de relais est **ajouté** à l'historique : rien n'est écrasé et vous pouvez revenir à un état antérieur.

## Installation

Une seule commande, qui sert aussi à mettre à jour. Python 3.10 ou plus doit être installé.

**macOS / Linux :**

```bash
curl -fsSL https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install.sh | sh
```

**Windows (PowerShell) :**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install.ps1 | iex"
```

Le script :
- installe Handoff dans un environnement virtuel privé, sans droits administrateur ni `sudo` ;
- ajoute la commande `handoff` à votre `PATH` ;
- à la première installation, vous propose deux modes :
  1. **Automatique** : tous les outils IA trouvés sont connectés avec les valeurs par défaut (lecture et enregistrement automatiques), sans autre question, et un récapitulatif s'affiche ;
  2. **Manuelle** : l'assistant `handoff setup` détaillé ci-dessous, où vous choisissez les outils et les options, voyez les changements prévus et confirmez.

Lors d'une mise à jour, la question n'est pas reposée : la configuration de vos outils est conservée.

Vous pouvez lire le script avant de l'exécuter : il est court et commenté. Variables utiles :

| Variable | Effet |
|---|---|
| `HANDOFF_VERSION=v0.1.0` | installe une version précise (par défaut : `main`) |
| `HANDOFF_NO_SETUP=1` | ne lance pas l'assistant de configuration (vous le lancerez avec `handoff setup`) |
| `HANDOFF_SETUP_YES=1` | connecte tous les outils trouvés, hooks compris, sans poser de questions |
| `HANDOFF_NO_MODIFY_PATH=1` | ne modifie pas le `PATH` |

**Désinstallation** (votre base de mémoire est conservée) :

```bash
curl -fsSL https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install.sh | sh -s -- --uninstall
```

```powershell
powershell -ExecutionPolicy ByPass -c "$env:HANDOFF_UNINSTALL=1; irm https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install.ps1 | iex"
```

Si vous préférez un gestionnaire d'outils Python : `pipx install git+https://github.com/kdev1966/Handoff-CLI` ou `uv tool install git+https://github.com/kdev1966/Handoff-CLI`.

## Connecter vos assistants

`handoff setup` détecte les outils IA installés et propose le mode automatique ou manuel. En mode manuel, il vous laisse choisir, montre les changements prévus, puis les applique après confirmation :

```text
Comment voulez-vous configurer vos outils IA ?
  1) Automatique : tous les outils trouvés, lecture et enregistrement automatiques (recommandé)
  2) Manuelle : choisir les outils et les options
> 2
Outils IA trouvés sur cette machine : Claude Code, Gemini CLI, Copilot CLI, VS Code, Cline, Kiro…
Que voulez-vous configurer ?
  1) Tous les outils trouvés
  2) Choisir outil par outil
  3) Rien pour l'instant (plus tard : handoff setup)
Lire et enregistrer la mémoire automatiquement (hooks) quand l'outil le permet ? [O/n]
Changements prévus (chaque fichier est sauvegardé avant modification) :
  · Gemini CLI
      ~/.gemini/settings.json: mcpServers.handoff
      ~/.gemini/GEMINI.md: Handoff instructions
  …
Appliquer ? [O/n]
```

| Commande | Rôle |
|---|---|
| `handoff setup` | assistant interactif (ci-dessus) |
| `handoff setup --yes [--client NOM] [--no-hooks]` | sans questions, pour les scripts |
| `handoff setup --dry-run` | affiche les changements prévus sans rien écrire |
| `handoff setup --remove` | retire tout ce que Handoff a ajouté aux outils |
| `handoff doctor` | état de chaque outil : connecté, lecture et enregistrement automatiques, consigne |
| `handoff setup --print-config` | configuration JSON à copier pour un outil non pris en charge |

Ce que Handoff configure, selon ce que chaque outil permet :

| Outil | Connexion MCP | Lecture auto (hook) | Enregistrement auto (hook) | Consigne |
|---|---|---|---|---|
| Claude Code | ✅ `claude mcp add` | ✅ plugin `handoff@handoff` | ✅ plugin | consignes du serveur MCP |
| Codex | ✅ `codex mcp add` | ✅ ¹ | ✅ ¹ | `~/.codex/AGENTS.md` |
| Gemini CLI | ✅ | ✅ | ✅ | `~/.gemini/GEMINI.md` |
| GitHub Copilot CLI, VS Code | ✅ | ✅ `~/.copilot/hooks` | ✅ ² | `~/.copilot/instructions` |
| Cursor | ✅ | ✅ | ✅ | — |
| Junie | ✅ | — | ✅ | `~/.junie/AGENTS.md` |
| Cline, Kiro, opencode, Windsurf/Devin, Antigravity | ✅ | — | — | ✅ fichier de règles global |
| Zed | ✅ ³ | — | — | `AGENTS.md` global |
| Claude Desktop | ✅ (redémarrage requis) | — | — | — |
| Continue, JetBrains AI Assistant | à configurer à la main avec `--print-config` | | | |

¹ Codex n'exécute un nouveau hook qu'après votre approbation unique dans `/hooks`.
² Pour VS Code, le format de la réponse de fin de tour est documenté mais n'a pas été testé.
³ Seulement si `settings.json` ne contient pas de commentaires ; sinon Handoff n'y touche pas et le signale.

**Ce que Handoff ne fait pas à votre place :**
- Il **ne pré-autorise jamais** ses outils : chaque assistant vous demande la permission la première fois, et vous décidez.
- Il **ne reconfigure rien en arrière-plan** : un outil installé plus tard apparaît dans `handoff doctor`, et vous relancez `handoff setup`.
- Il **sauvegarde** chaque fichier avant de le modifier (dossier `backups` dans le dossier de données), **conserve** tout le reste de son contenu, et **refuse** de réécrire un fichier qu'il ne sait pas relire à l'identique.

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
| `handoff show` | Affiche la dernière passation du projet courant, dans un panneau en Markdown |
| `handoff log [-l N] [--by agent\|branch]` | Historique en graphe, comme `git log --graph`, avec un couloir par agent ou par branche git |
| `handoff diff [ANCIEN] [NOUVEAU]` | Compare deux passations mot à mot (par défaut : les deux dernières) |
| `handoff stats` | Carte d'activité sur 12 semaines, répartition par agent et par branche |
| `handoff list` | Tous les projets, avec dernier agent, mini-courbe d'activité et statut (actif, inactif, en pause) |
| `handoff history [-l N]` | Passations précédentes en détail |
| `handoff save -s "état" -n "suite" [--stack "…"]` | Enregistre une passation (`-` lit l'entrée standard) |
| `handoff restore ID` | Rétablit une ancienne passation comme la plus récente |
| `handoff pause` / `handoff resume` | Suspend ou reprend le suivi d'un projet (travail confidentiel) |
| `handoff purge [--key KEY] [-y]` | Supprime toute la mémoire d'un projet |
| `handoff render` | Régénère `.handoff/context.md` |
| `handoff where` | Indique où sont stockées les données |

Toutes les commandes agissent sur le dossier courant, ou sur le dossier passé avec `--path`. `show`, `log`, `history`, `stats` et `list` acceptent `--json` pour les scripts.

Chaque passation enregistre la branche git et le commit courants : ils apparaissent dans `show`, `log` et `diff`.

```text
● claude-code   ● copilot   ● gemini-cli

●      #8  claude-code  hier            main @ 116d129          Refacto du middleware
╰─╮
  ●    #6  copilot      28 sept.        main @ 116d129          Fusion de feature/auth
  ╰─╮
    ●  #5  gemini-cli   19 sept.        feature/auth @ 116d129  Tests d'intégration écrits
```

### Couleurs et langue

- **Couleurs** : chaque agent a une couleur fixe, toujours accompagnée de son nom. La palette reste lisible par les daltoniens, sur fond clair comme sur fond sombre. Les couleurs sont désactivées quand la sortie n'est pas un terminal ou quand `NO_COLOR` est défini ; `FORCE_COLOR=1` les force. Sans couleur, `diff` marque les changements comme `git diff --word-diff` : `[-supprimé-]{+ajouté+}`.
- **Consoles anciennes** : sur une console qui ne gère pas l'UTF-8, les symboles sont remplacés par des équivalents ASCII.
- **Langue** : l'interface est en français ou en anglais selon votre système (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`, puis la langue de macOS ou de Windows). `HANDOFF_LANG=fr` ou `HANDOFF_LANG=en` impose une langue. Ce que lisent les IA (outils MCP, `.handoff/context.md`) reste en anglais.

### Assistants sans MCP

Après chaque enregistrement, Handoff génère `.handoff/context.md` à la racine du projet. Ce dossier contient son propre `.gitignore` : il n'apparaît jamais dans `git status` et votre `.gitignore` n'est pas modifié. Un assistant sans MCP peut lire ce fichier, puis enregistrer avec `handoff save`. Ne modifiez pas ce fichier à la main : il est régénéré.

Pour qu'un outil le lise automatiquement, ajoutez une ligne de renvoi dans le fichier d'instructions qu'il lit déjà (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`…), par exemple : « Lis `.handoff/context.md` en début de session. »

## Sécurité

- **Local uniquement** : le serveur MCP communique en stdio et n'ouvre aucun port réseau.
- **Hooks sans risque pour l'outil** : ils ne lancent que `handoff` (chemin absolu de son interpréteur), ne lisent que l'état git du projet, et en cas de problème n'affichent rien et laissent l'outil continuer.
- **Pas de pré-autorisation** : Handoff ne s'accorde jamais de permissions dans vos outils IA.
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
- Les formats de configuration des outils IA évoluent vite. `handoff doctor` signale ce qui n'est pas en place ; les erreurs des hooks sont consignées dans `hooks.log`, dans le dossier de données, sans jamais bloquer l'outil.
- L'enregistrement automatique repose sur l'état git du projet : hors dépôt git, seule la consigne demande à l'assistant d'enregistrer.
- Les messages des scripts d'installation sont en anglais ; ceux de `handoff` suivent la langue du système.

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
