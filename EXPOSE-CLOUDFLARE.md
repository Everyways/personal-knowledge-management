# Exposer second-brain via Cloudflare Tunnel + Access

But : accéder à http://192.168.1.132:8085 depuis le laptop pro, sans rien
installer dessus, sans ouvrir de port sur la box, avec une page de login devant
(l'app n'a pas d'auth).

Remplacer `example.com` par ton domaine OVH et `sb.example.com` par le
sous-domaine choisi.

## 1. Domaine OVH → DNS chez Cloudflare (gratuit, le domaine reste chez OVH)

1. Acheter/valider le domaine chez OVH si ce n'est pas déjà fait.
2. Créer un compte sur https://dash.cloudflare.com → **Add a site** → ton
   domaine → plan **Free**. Cloudflare affiche 2 serveurs de noms
   (ex. `ana.ns.cloudflare.com`, `bob.ns.cloudflare.com`).
3. Chez OVH : Web Cloud → ton domaine → **Serveurs DNS** → *Modifier les
   serveurs DNS* → remplacer par les 2 de Cloudflare.
4. Attendre l'email Cloudflare « domain is active » (minutes à quelques
   heures). Domaine neuf = rien d'autre à vérifier.

## 2. Tunnel sur le serveur Ubuntu

1. https://one.dash.cloudflare.com → **Networks → Tunnels → Create a tunnel**
   → type *Cloudflared* → nom `second-brain`.
2. Choisir l'environnement **Debian / 64-bit** et copier la commande affichée
   (elle installe `cloudflared` + service systemd avec le token du tunnel).
   La lancer sur le serveur. Vérifier : `systemctl status cloudflared`.
3. Onglet **Public Hostname** du tunnel → *Add a public hostname* :
   - Subdomain : `sb` — Domain : `example.com`
   - Service : **HTTP** → `localhost:8085`

Aucun port à ouvrir sur la box : cloudflared fait une connexion sortante.

## 3. Login devant l'app (Cloudflare Access)

1. Zero Trust → **Access → Applications → Add an application** →
   *Self-hosted*.
   - Application domain : `sb.example.com`
   - Session duration : 1 semaine (au choix)
2. Policy : nom `moi`, action **Allow**, Include → **Emails** →
   `fauriebenoit@gmail.com`.
3. Méthode de login par défaut : **One-time PIN** (code reçu par email —
   rien à installer, marche partout).

## 4. Test depuis le laptop pro

Ouvrir `https://sb.example.com` → saisir l'email → coller le PIN reçu → l'app.

## 5. Auth de l'app (HTTP Basic, partout)

L'app exige un login/mot de passe sur toutes les routes (LAN inclus).
Identifiants : `APP_USERNAME` / `APP_PASSWORD` dans `.env` — si absents,
l'auth est désactivée (warning au démarrage). Après modification :
`sudo systemctl restart secondbrain`.

## Notes

- Ne PAS faire de redirection de port sur la box : le 8085 reste privé.
- Le token du tunnel dans la commande d'install est un secret : ne pas le
  committer.
- Si le réseau du bureau bloque le site, c'est côté proxy d'entreprise —
  Cloudflare est rarement bloqué, c'est le gros avantage vs *.ts.net.
- Pour ajouter d'autres services plus tard (ex. un 2e port) : même tunnel,
  ajouter un Public Hostname supplémentaire.
