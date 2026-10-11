# Handoff

[![PyPI](https://img.shields.io/pypi/v/ai-handoff.svg)](https://pypi.org/project/ai-handoff/)
[![Python](https://img.shields.io/pypi/pyversions/ai-handoff.svg)](https://pypi.org/project/ai-handoff/)
[![CI](https://github.com/kdev1966/Handoff-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/kdev1966/Handoff-CLI/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/kdev1966/Handoff-CLI/blob/main/LICENSE)

[English](https://github.com/kdev1966/Handoff-CLI/blob/main/README.md) · **Français**

**Handoff** donne une mémoire persistante et partagée à vos assistants IA (Claude, GitHub Copilot, Cursor, Gemini, Codex…). Quand vous passez d'un outil à un autre sur un même projet, le suivant reprend là où le précédent s'est arrêté : stack technique, état actuel, prochaines actions.

Il fonctionne sous **Windows, macOS et Linux**, via le standard [MCP](https://modelcontextprotocol.io) et une CLI.

<p align="center"><img src="https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/docs/images/fr/log.svg" alt="handoff log : les passations entre Claude Code, Copilot, Gemini CLI et Codex, en graphe" width="100%"></p>

## Fonctionnement

```text
 Claude Code / Copilot / Cursor / Gemini / Codex …
        │  MCP (stdio, local : aucun port réseau)   │  hooks : début de session, fin de tour
        ▼                                            ▼
   handoff serve                              handoff hook
        │                                            │
        └──────────────┬─────────────────────────────┘
                       ▼
        base SQLite locale (historique en ajout seul)  ◄── handoff save / show / log / stats …
                       │
                       ▼
        <projet>/.handoff/context.md   (vue générée, ignorée par git)
```

- **En début de session**, la mémoire du projet est donnée à l'assistant : automatiquement par un hook quand l'outil le permet, sinon l'assistant l'obtient avec l'outil `memory_get`, comme le lui indique la consigne posée par `handoff setup`.
- **Avant de s'arrêter**, si le code a changé depuis le début de la session ou depuis la dernière passation d'un assistant, l'assistant est invité une fois à en enregistrer une (`memory_save`).
- Le projet est identifié par son **remote git** (`origin`). La mémoire suit donc le dépôt même si vous le déplacez ou le clonez à nouveau. Sans remote, c'est le chemin du dossier qui sert d'identifiant.
- Chaque passation est **ajoutée** à l'historique : rien n'est écrasé et vous pouvez revenir à un état antérieur.

## Installation

Handoff est publié sur PyPI sous le nom [`ai-handoff`](https://pypi.org/project/ai-handoff/). Python 3.10 ou plus doit être installé. Deux façons de l'installer :

### Avec pipx ou uv

Si vous utilisez déjà un gestionnaire d'outils Python :

```bash
pipx install ai-handoff        # ou : uv tool install ai-handoff
handoff setup                  # connecte vos outils IA (mode automatique ou manuel)
handoff completion --install   # autocomplétion avec Tab (facultatif)
```

Mise à jour : `pipx upgrade ai-handoff` (ou `uv tool upgrade ai-handoff`).

### Avec le script d'installation

Une seule commande, qui installe la dernière version publiée sur PyPI, lance la configuration et sert aussi à mettre à jour :

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
  1. **Automatique** : tous les outils IA trouvés sont connectés avec les valeurs par défaut (lecture et enregistrement automatiques), l'autocomplétion avec Tab est installée, sans autre question, et un récapitulatif s'affiche ;
  2. **Manuelle** : l'assistant `handoff setup` détaillé ci-dessous, où vous choisissez les outils et les options, voyez les changements prévus et confirmez.

Lors d'une mise à jour, la question n'est pas reposée : la configuration de vos outils est conservée.

Vous pouvez lire le script avant de l'exécuter : il est court et commenté. Variables utiles :

| Variable | Effet |
|---|---|
| `HANDOFF_VERSION=0.1.0` | installe une version publiée précise (par défaut : la dernière) ; `HANDOFF_VERSION=main` installe la branche GitHub, pour essayer du code pas encore publié |
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

Avec pipx ou uv, désinstallez avec `handoff setup --remove`, `handoff completion --uninstall`, puis `pipx uninstall ai-handoff` (ou `uv tool uninstall ai-handoff`).

## Connecter vos assistants

`handoff setup` détecte les outils IA installés et propose le mode automatique ou manuel. En mode manuel, il vous laisse choisir, montre les changements prévus, puis les applique après confirmation :

```text
Outils IA trouvés sur cette machine : Claude Code, Gemini CLI, Copilot CLI, VS Code, Cline, Kiro…
  1) Automatique : tous les outils trouvés, lecture et enregistrement automatiques (recommandé)
  2) Manuelle : choisir les outils et les options
Comment voulez-vous configurer vos outils IA ? [1] 2
  1) Tous les outils trouvés
  2) Choisir outil par outil
  3) Rien pour l'instant (plus tard : handoff setup)
Que voulez-vous configurer ? [1] 1
Lire et enregistrer la mémoire automatiquement (hooks) quand l'outil le permet ? [O/n]
Installer l'autocomplétion avec Tab pour la commande handoff (zsh) ? [O/n]
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
| `handoff doctor --fix [--yes]` | répare ce qui manque dans les outils choisis, et propose les outils installés depuis ; montre les changements d'abord |
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
- Il **ne reconfigure rien en arrière-plan** : un outil installé plus tard apparaît dans `handoff doctor`, et `handoff doctor --fix` propose de l'ajouter.
- Il **retient vos choix** : les outils configurés, ceux que vous avez écartés, et si vous voulez les hooks. `doctor --fix` répare en conséquence et ne réinstalle jamais des hooks que vous avez refusés.
- Il **sauvegarde** chaque fichier avant de le modifier (dossier `backups` dans le dossier de données), **conserve** tout le reste de son contenu, et **refuse** de réécrire un fichier qu'il ne sait pas relire à l'identique.

## Outils MCP

| Outil | Rôle | Type |
|---|---|---|
| `memory_get(project_path)` | Lit la dernière passation du projet | lecture seule |
| `memory_save(project_path, summary, next_actions, stack?, agent?)` | Enregistre une nouvelle passation | ajout, non destructif |
| `memory_history(project_path, limit?)` | Liste les passations précédentes | lecture seule |

Aucun outil ne permet à une IA de supprimer des données ni d'exécuter du SQL. `memory_save` renvoie un avertissement quand le résumé est vide de sens ou que les prochaines actions manquent, pour que l'assistant complète sa passation.

## CLI

Handoff s'utilise comme `git` : on tape `handoff <commande>` dans le terminal. Ce n'est pas une session interactive avec des commandes `/` comme Claude Code.

- **`handoff`** seul affiche l'état du projet courant et toutes les commandes, regroupées par usage (Consulter, Enregistrer, Gérer, Configurer).
- **`handoff <commande> --help`** détaille les options d'une commande.
- **Touche Tab** : l'autocomplétion complète les commandes, les options et leurs valeurs (`handoff l` puis Tab propose `log` et `list` ; `--by` puis Tab propose `agent` et `branch`). Elle fonctionne avec zsh, bash et PowerShell. Elle est installée d'office en mode automatique et proposée en mode manuel ; sinon, `handoff completion --install` l'installe et `handoff completion --uninstall` la retire. Le script est écrit une fois dans le dossier de données, et votre profil shell ne fait que le charger : aucun ralentissement à l'ouverture du terminal.

| Commande | Rôle |
|---|---|
| `handoff show` | Affiche la dernière passation du projet courant, dans un panneau en Markdown |
| `handoff log [-l N] [--by agent\|branch]` | Historique en graphe, comme `git log --graph`, avec un couloir par agent ou par branche git |
| `handoff diff [ANCIEN] [NOUVEAU]` | Compare deux passations mot à mot (par défaut : les deux dernières) |
| `handoff stats` | Carte d'activité sur 12 semaines, répartition par agent et par branche |
| `handoff list` | Tous les projets, avec dernier agent, mini-courbe d'activité et statut (actif, inactif, en pause) |
| `handoff history [-l N]` | Passations précédentes en détail |
| `handoff save -s "état" -n "suite" [--stack "…"] [-a agent]` | Enregistre une passation (`-` lit l'entrée standard) ; sans `-a`, l'agent est `cli` (vous) |
| `handoff restore ID` | Rétablit une ancienne passation comme la plus récente |
| `handoff pause` / `handoff resume` | Suspend ou reprend le suivi d'un projet (travail confidentiel) |
| `handoff purge [--key KEY] [-y]` | Supprime toute la mémoire d'un projet |
| `handoff render` | Régénère `.handoff/context.md` |
| `handoff where [--json]` | Quelle mémoire ce dossier utilise et pourquoi : clé du projet, remote ou chemin, racine, nombre de passations, fichiers |
| `handoff export [--all] [-o FICHIER]` | Écrit la mémoire en JSON Lines (sauvegarde, autre poste) |
| `handoff import FICHIER [--path DOSSIER] [--dry-run]` | Ajoute les passations d'un export, jamais en double |
| `handoff completion [--install\|--uninstall]` | Autocomplétion avec Tab (zsh, bash, PowerShell) |

Toutes les commandes agissent sur le dossier courant, ou sur le dossier passé avec `--path`.

`handoff save` (comme l'outil `memory_save`) **avertit**, sans refuser, quand le résumé est très court ou ne dit rien, ou quand les prochaines actions manquent. Une passation enregistrée à la main (`handoff save` sans `--agent`) ne dispense pas l'assistant de documenter son propre travail : il sera quand même invité à enregistrer le sien. Les noms d'agents connus sont unifiés (`claude` → `claude-code`, `gemini` → `gemini-cli`…), pour qu'un même outil n'apparaisse pas sous deux noms. `show`, `log`, `history`, `stats` et `list` acceptent `--json` pour les scripts.

Chaque passation enregistre la branche git et le commit courants : ils apparaissent dans `show`, `log` et `diff`.

### Aperçu

| `handoff` | `handoff show` |
|---|---|
| <img src="https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/docs/images/fr/welcome.svg" alt="Accueil de handoff : état du projet et commandes par usage"> | <img src="https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/docs/images/fr/show.svg" alt="handoff show : la dernière passation dans un panneau"> |
| **`handoff diff`** | **`handoff stats`** |
| <img src="https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/docs/images/fr/diff.svg" alt="handoff diff : comparaison mot à mot de deux passations"> | <img src="https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/docs/images/fr/stats.svg" alt="handoff stats : carte d'activité sur 12 semaines, agents et branches"> |

Ces images sont générées à partir de données de démonstration par `scripts/screenshots.py`.

### Couleurs et langue

- **Couleurs** : chaque agent a une couleur fixe, toujours accompagnée de son nom. La palette reste lisible par les daltoniens, sur fond clair comme sur fond sombre. Les couleurs sont désactivées quand la sortie n'est pas un terminal ou quand `NO_COLOR` est défini ; `FORCE_COLOR=1` les force. Sans couleur, `diff` marque les changements comme `git diff --word-diff` : `[-supprimé-]{+ajouté+}`.
- **Consoles anciennes** : sur une console qui ne gère pas l'UTF-8, les symboles sont remplacés par des équivalents ASCII.
- **Langue** : l'interface est en français ou en anglais selon votre système (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`, puis la langue de macOS ou de Windows). `HANDOFF_LANG=fr` ou `HANDOFF_LANG=en` impose une langue. Ce que lisent les IA (outils MCP, `.handoff/context.md`) reste en anglais.

### Quelle mémoire, et pourquoi

`handoff where` explique comment le dossier courant est relié à une mémoire : le remote git qui sert de clé (identifiants retirés), ou le chemin du dossier quand il n'y a pas de remote. Il prévient quand la mémoire ne suivra pas un dossier déplacé (pas de remote), et quand vous êtes dans un sous-dossier, dont la mémoire est celle de tout le dépôt.

### Sauvegarde et synchronisation entre postes

```bash
handoff export -o api.jsonl              # ce projet (fichier lisible par vous seul)
handoff export --all -o tout.jsonl       # tous les projets
handoff import api.jsonl --dry-run       # ce qui serait ajouté
handoff import api.jsonl                 # n'ajoute jamais une passation deux fois
handoff import api.jsonl --path ~/code/api   # projet sans remote : rattaché au dossier local
```

Un export contient une ligne JSON par passation, après un en-tête versionné. L'import traite le fichier comme non fiable : chaque champ est revalidé et les secrets sont masqués à nouveau ; dates, agents, branches et commits sont conservés ; l'état « en pause » n'est pas importé.

Pour synchroniser deux postes : exportez sur l'un, transportez le fichier (dépôt git **privé**, Syncthing, clé USB…) et importez sur l'autre ; relancer l'import est sans risque. La mémoire contient le contexte de vos projets : gardez le fichier et sa destination privés.

### Assistants sans MCP

Après chaque enregistrement, Handoff génère `.handoff/context.md` à la racine du projet. Ce dossier contient son propre `.gitignore` : il n'apparaît jamais dans `git status` et votre `.gitignore` n'est pas modifié. Un assistant sans MCP peut lire ce fichier, puis enregistrer avec `handoff save`. Ne modifiez pas ce fichier à la main : il est régénéré.

Pour les outils pris en charge, `handoff setup` écrit déjà cette consigne dans leur fichier d'instructions global. Pour un autre outil, ajoutez une ligne de renvoi dans le fichier d'instructions qu'il lit (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`…), par exemple : « Lis `.handoff/context.md` en début de session. »

## Sécurité

- **Local uniquement** : le serveur MCP communique en stdio et n'ouvre aucun port réseau.
- **Hooks sans risque pour l'outil** : ils ne lancent que `handoff` (chemin absolu de son interpréteur), ne lisent que la mémoire et l'état git du projet, et en cas de problème n'affichent rien et laissent l'outil continuer.
- **Pas de pré-autorisation** : Handoff ne s'accorde jamais de permissions dans vos outils IA.
- **Surface réduite** : 3 outils au schéma typé, pas de SQL brut, pas de suppression par l'IA.
- **Chemins validés** : le chemin doit être absolu et exister. La racine du disque, le dossier personnel et ses parents sont refusés.
- **Imports non fiables par principe** : un fichier importé passe par la même validation et le même masquage des secrets qu'une nouvelle passation.
- **Entrées validées** : 16 Kio maximum par champ, caractères de contrôle supprimés, nom d'agent contrôlé.
- **Secrets masqués avant stockage** : la mémoire étant relue par d'autres fournisseurs d'IA, ces formats sont remplacés par `[REDACTED]` avant l'enregistrement (détail des expressions régulières dans [`redact.py`](https://github.com/kdev1966/Handoff-CLI/blob/main/src/ai_handoff/redact.py)) :

  | Format | Exemple reconnu |
  |---|---|
  | Clés privées PEM | `-----BEGIN … PRIVATE KEY-----` … `-----END … PRIVATE KEY-----` |
  | Identifiants dans une URL | `postgres://user:motdepasse@hôte` (l'hôte est conservé) |
  | Clés d'accès AWS | `AKIA…`, `ASIA…` |
  | Jetons GitHub | `ghp_…`, `gho_…`, `ghu_…`, `ghs_…`, `ghr_…`, `github_pat_…` |
  | Jetons GitLab | `glpat-…` |
  | Clés `sk-` (OpenAI, Anthropic…) | `sk-…`, `sk-ant-…`, `sk-proj-…` |
  | Jetons Slack | `xoxb-…`, `xoxp-…`, `xoxa-…`… |
  | Clés d'API Google | `AIza…` |
  | Clés Stripe | `sk_live_…`, `sk_test_…`, `rk_live_…` |
  | Jetons JWT | `eyJ….eyJ….…` |
  | Affectations sensibles | `password=…`, `API_KEY: …`, `client_secret = "…"`, `DB_PASSWORD='…'` (le nom est conservé) |
- **La mémoire est une donnée, pas une consigne** : les assistants sont invités à ne jamais exécuter d'instructions trouvées dans la mémoire.
- **Fichiers protégés** : base en mode `0600` dans un dossier `0700` (sous Windows, protégée par les droits du profil utilisateur). Écriture atomique, liens symboliques refusés, aucun fichier non généré par Handoff n'est écrasé.

> Le masquage repose sur des formats connus : un secret dans un format inédit peut passer. N'enregistrez pas de secrets dans la mémoire.

## Emplacement des données

| Système | Dossier |
|---|---|
| Windows | `%LOCALAPPDATA%\handoff\` |
| macOS | `~/Library/Application Support/handoff/` |
| Linux | `$XDG_DATA_HOME/handoff/` (par défaut `~/.local/share/handoff/`) |

Il contient la base `memory.db`, les sauvegardes des fichiers modifiés par `handoff setup` (`backups/`), les scripts d'autocomplétion (`completion/`), le plugin Claude Code (`claude-marketplace/`) et, en cas d'erreur d'un hook, `hooks.log`. La variable d'environnement `HANDOFF_HOME` permet de choisir un autre dossier.

Le script d'installation place Handoff lui-même dans `~/.local/share/handoff/venv` (macOS et Linux) ou `%LOCALAPPDATA%\handoff\venv` (Windows).

## Limites

- La mémoire est locale à la machine. Pour la transférer entre postes, utilisez `handoff export` et `handoff import` ; il n'y a pas de synchronisation automatique.
- Deux clones d'un même dépôt partagent la même mémoire (c'est voulu), tout comme les sous-dossiers d'un monorepo.
- Les formats de configuration des outils IA évoluent vite. `handoff doctor` signale ce qui n'est pas en place ; les erreurs des hooks sont consignées dans `hooks.log`, dans le dossier de données, sans jamais bloquer l'outil.
- L'enregistrement automatique repose sur l'état git du projet : hors dépôt git, seule la consigne demande à l'assistant d'enregistrer.
- Les messages des scripts d'installation sont en anglais ; ceux de `handoff` suivent la langue du système.

## Comparaison avec d'autres outils

Plusieurs outils aident à passer d'un assistant IA à un autre. Ils ne répondent pas au même besoin :

| | Handoff | [continues](https://github.com/yigitkonur/cli-continues) | [handoff-skill](https://github.com/klittle32/handoff-skill) |
|---|---|---|---|
| Objectif | le projet se souvient, d'un outil et d'un jour à l'autre | reprendre une conversation précise dans un autre outil, tout de suite | préparer une nouvelle session pour un objectif donné |
| Source | les passations rédigées par les assistants | les journaux de session des outils | la session en cours, à la demande |
| Déclenchement | automatique (MCP et hooks) après `handoff setup` | manuel, à chaque changement d'outil | manuel (`/handoff <objectif>`) |
| Conservé dans le temps | historique en ajout seul : log, diff, stats, export | non | non (fichier jetable) |
| Configure vos outils | MCP, hooks et consignes dans 14 outils | non | installe la compétence |

Utilisez **continues** quand vous atteignez une limite d'usage en plein travail et voulez que cette conversation précise continue ailleurs : il transmet beaucoup plus de détails (messages, commandes, fichiers touchés) et gère des outils que Handoff ne gère pas (Amp, Roo Code, Kilo Code, Crush, Kimi, Qwen Code, Factory Droid). Utilisez **Handoff** pour que chaque nouvelle session, dans n'importe quel outil, parte de l'état actuel du projet sans rien faire. Les deux sont complémentaires.

*Comparaison établie à partir du README de chaque projet, octobre 2026.*

## Développement

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows : .venv\Scripts\pip
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
python scripts/screenshots.py         # régénère les captures du README
```

La CI exécute les tests sous Windows, macOS et Linux avec Python 3.10, 3.12 et 3.14, ainsi que les scripts d'installation sur les trois systèmes. Pour publier une version : mettre à jour `__version__` dans `src/ai_handoff/__init__.py`, pousser le tag `vX.Y.Z` correspondant, puis approuver le déploiement `pypi` dans GitHub Actions. La publication sur PyPI se fait par *trusted publishing*, sans jeton stocké, et crée aussi la release GitHub. Les changements sont listés dans [CHANGELOG.md](https://github.com/kdev1966/Handoff-CLI/blob/main/CHANGELOG.md).

## Licence

MIT. Voir [LICENSE](https://github.com/kdev1966/Handoff-CLI/blob/main/LICENSE).
