# Guide d'installation — second-brain

Pipeline auto-hébergé : soumission d'articles/vidéos/audio → résumé par IA locale (Ollama) → notes dans ton vault Obsidian → analyse projet quotidienne → veille par email chaque matin → push GitHub chaque nuit.

⚠️ **Rappel matériel** : sur ton i3/i5 sans GPU, un résumé prend 2 à 10 min, une transcription audio bien plus. Tout est asynchrone : tu soumets, la note arrive plus tard.

## 1. Prérequis système (en SSH sur le serveur)

```bash
sudo apt update && sudo apt install -y ffmpeg git python3-venv python3-pip
```

## 2. Ollama + modèle

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b-instruct
# Test (peut être lent) :
ollama run qwen2.5:3b-instruct "Dis bonjour en une phrase"
```

Si tu n'as que 4 Go de RAM et que ça rame trop : `ollama pull llama3.2:1b` puis mets `model: llama3.2:1b` dans config.yaml (qualité moindre).

## 3. Installer l'app

Copie le dossier `second-brain` sur le serveur (depuis ton PC : `scp -r second-brain benoit@IP_SERVEUR:~/`), puis :

```bash
cd ~/second-brain
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

(faster-whisper est volumineux ; si l'installation échoue, supprime cette ligne de requirements.txt — seule la transcription audio sera indisponible.)

## 4. Configurer `config.yaml`

- `vault_path` : chemin du clone de ton vault sur le serveur (ex. `/home/benoit/obsidian-vault`)
- `smtp_password` : crée un **mot de passe d'application** Gmail sur https://myaccount.google.com/apppasswords (nécessite la validation en 2 étapes)
- ajuste `projects`, horaires, modèle si besoin

## 5. Préparer le vault

Dans Obsidian (ou sur le serveur), crée :

- `Veille/themes.md` — un thème par ligne, ex. :
  ```
  - intelligence artificielle open source
  - marché crypto trading algorithmique
  - équipement moto connecté
  ```
  Tu peux modifier ce fichier quand tu veux : la veille du lendemain suivra.
- `Projets/upsalt.md`, `Projets/motoproof.md`, `Projets/script-trading.md` — décris chaque projet en quelques lignes (objectif, état, stack). Plus c'est précis, meilleures sont les analyses.

Vérifie que `git push` fonctionne depuis le serveur dans le vault (clé SSH GitHub déjà en place puisque le vault y est cloné).

## 6. Tester

```bash
cd ~/second-brain && ./venv/bin/python app.py
```

Ouvre `http://IP_SERVEUR:8085` (ou l'IP Tailscale `100.x.x.x:8085` depuis l'extérieur), colle une URL d'article, attends quelques minutes → la note apparaît dans `Inbox/` du vault et dans le tableau "Derniers traitements".

Test des jobs sans attendre la nuit :

```bash
./venv/bin/python -c "import jobs; jobs.project_analysis()"
./venv/bin/python -c "import jobs; jobs.morning_digest()"
```

## 7. Lancer en service permanent

```bash
sudo cp secondbrain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now secondbrain
systemctl status secondbrain        # vérifier
journalctl -u secondbrain -f        # logs en direct
```

## Fonctionnement quotidien

| Heure | Action |
|-------|--------|
| 02:00 | commit + push du vault vers GitHub |
| 03:00 | analyse d'un de tes 3 projets (rotation) + détection d'opportunité de nouveau projet → note dans `Projets/Analyses/` |
| 07:30 | veille par thème (Google News, gratuit) résumée par l'IA → email + note dans `Veille/` (inclut l'analyse projet du jour) |

Soumission depuis le téléphone : ouvre la page via Tailscale, ou utilise l'API :
`curl -X POST http://100.x.x.x:8085/api/submit -H 'Content-Type: application/json' -d '{"url":"https://…"}'`

## Dépannage

- **Pas d'email** : vérifie le mot de passe d'application et `journalctl -u secondbrain`
- **Note absente** : regarde le tableau sur la page d'accueil (colonne erreur)
- **Trop lent** : modèle plus petit (`llama3.2:1b`), ou limite-toi aux articles texte
- **Vidéo YouTube sans note** : certaines vidéos n'ont ni sous-titres ni audio accessible ; la transcription audio exige faster-whisper installé
