#!/usr/bin/env bash
# Sync Obsidian 2nd_brain vault to GitHub
set -euo pipefail

VAULT="/home/everyways/2nd_brain_obsidian/2nd brain"
LOG="/home/everyways/second-brain/sync-vault.log"
LOCK="/home/everyways/second-brain/vault-sync.lock"
DATE=$(date '+%Y-%m-%d %H:%M')

# Même fichier de verrou que jobs.py (fcntl.flock) : évite qu'un push déclenché
# par l'app et ce timer touchent le repo git en même temps (conflit .git/index.lock).
exec 200>"$LOCK"
if ! flock -w 120 200; then
    echo "$DATE — sync déjà en cours (verrou non obtenu), abandon" >> "$LOG"
    exit 0
fi

cd "$VAULT"

# Verrou git périmé (crash d'un git précédent) : on détient déjà le flock,
# donc aucun autre job ne tourne — un index.lock vieux de +60 min est mort.
find "$(git rev-parse --git-dir)/index.lock" -mmin +60 -delete 2>/dev/null || true

git config user.email "fauriebenoit@gmail.com"
git config user.name "Benoit Faurie"

git pull --rebase --autostash >> "$LOG" 2>&1

# add AVANT le test : `git diff` ignore les fichiers non suivis, or les
# nouvelles notes sont précisément des fichiers non suivis (bug du 01/07).
git add -A
if git diff --staged --quiet; then
    echo "$DATE — nothing to commit" >> "$LOG"
    exit 0
fi

git commit -m "vault sync $DATE" >> "$LOG" 2>&1
git push >> "$LOG" 2>&1
echo "$DATE — pushed ok" >> "$LOG"
