#!/usr/bin/env python3
"""
Vérification avant déploiement de The Special One.

Usage :  python3 check.py [dossier]      (dossier = . par défaut)

Rassemble en une commande tous les contrôles qui, jusqu'ici, étaient faits à
la main et de façon inégale. Chaque bug rencontré en production dans le passé
a sa contre-mesure ici :

  - </script> écrit dans un commentaire JS  -> découpe le bloc de script
  - identifiant GA4 resté en placeholder    -> aucune mesure collectée
  - URL sans www                            -> Google rejette pour redirection
  - noms de joueurs décalés d'une ligne     -> 58 lignes fausses sur Arsenal
  - numéro de maillot en double             -> deux joueurs au même numéro
  - postes incohérents entre saisons        -> même joueur, postes différents

Sortie : liste des problèmes, code de retour 1 si au moins une ERREUR.
Les AVERTISSEMENTS n'empêchent pas le déploiement.
"""
import sys, os, re, csv, json, glob, subprocess, tempfile, unicodedata
from collections import defaultdict, Counter

ROOT = sys.argv[1] if len(sys.argv) > 1 else '.'
DOMAINE = 'https://www.specialone.fr'
KU = "Nom d'usage"
COLONNES_CSV = ['Club','Saison','Numero','Joueur',KU,'Nationalite','Postes','Note']

erreurs, avertis = [], []
def err(section, msg):  erreurs.append((section, msg))
def avert(section, msg): avertis.append((section, msg))
def norm(s): return unicodedata.normalize('NFC', s.strip())


# ---------------------------------------------------------------- index.html
def check_index():
    p = os.path.join(ROOT, 'index.html')
    if not os.path.isfile(p):
        err('index.html', 'fichier introuvable'); return
    h = open(p, encoding='utf-8').read()

    # -- syntaxe des blocs de script (hors JSON-LD et hors src=)
    blocs = re.findall(r'<script(?![^>]*\bsrc=)(?![^>]*ld\+json)[^>]*>([\s\S]*?)</script>', h)
    if not blocs:
        err('index.html', 'aucun bloc <script> inline trouvé - balise coupée ?')
    for i, b in enumerate(blocs):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(b); tmp = f.name
        r = subprocess.run(['node', '--check', tmp], capture_output=True, text=True)
        os.unlink(tmp)
        if r.returncode:
            err('index.html', f"bloc <script> {i} : syntaxe JS invalide\n      {r.stderr.strip().splitlines()[0] if r.stderr else ''}")

    # -- </script> dans un commentaire : coupe le bloc en plein milieu
    for m in re.finditer(r'^\s*//.*</script', h, re.M):
        err('index.html', "un commentaire JS contient </script> : la balise sera fermée prématurément")

    # -- JSON-LD
    for b in re.findall(r'<script type="application/ld\+json">([\s\S]*?)</script>', h):
        try: json.loads(b)
        except Exception as e: err('index.html', f'JSON-LD invalide : {e}')

    # -- GA4
    ids = set(re.findall(r'G-[A-Z0-9]{10}', h))
    if 'G-XXXXXXXXXX' in ids:
        err('index.html', 'identifiant GA4 encore en placeholder : aucune mesure ne sera collectée')
    elif len(ids) > 1:
        err('index.html', f'identifiants GA4 différents dans le même fichier : {sorted(ids)}')
    elif not ids:
        avert('index.html', 'aucun identifiant GA4 trouvé')

    # -- domaine canonique
    sans_www = set(re.findall(r'https://specialone\.fr[^\s"\'<)]*', h))
    if sans_www:
        err('index.html', f'{len(sans_www)} URL sans www (le CNAME impose www) : {sorted(sans_www)[:3]}')

    # -- longueurs des meta
    cibles = [(r'<meta name="description" content="([^"]+)"', 'description', 160),
              (r'<meta property="og:description" content="([^"]+)"', 'og:description', 125),
              (r'<meta name="twitter:description" content="([^"]+)"', 'twitter:description', 125)]
    for pat, label, maxi in cibles:
        m = re.search(pat, h)
        if not m: avert('index.html', f'balise {label} absente'); continue
        n = len(m.group(1))
        if n > maxi: avert('index.html', f'{label} : {n} car. (cible ≤ {maxi}, sera tronquée)')

    # -- balises indispensables
    for besoin, label in [('rel="canonical"','canonical'), ('og:image','og:image'),
                          ('favicon.ico','favicon.ico'), ('site.webmanifest','manifeste')]:
        if besoin not in h: avert('index.html', f'{label} non déclaré')

    # -- liens internes vers /info/ : sans eux Google ne découvre pas les pages
    if 'href="/info/' not in h:
        err('index.html', 'aucun lien <a href="/info/..."> : les pages de contenu seront orphelines')
    if re.search(r'<button[^>]*class="site-footer-link"[^>]*data-info=', h):
        avert('index.html', 'des liens de pied de page sont encore des <button> (non suivis par Google)')


# --------------------------------------------------------- fichiers annexes
def check_annexes():
    for f in ['robots.txt','sitemap.xml','favicon.ico','og-image.png','site.webmanifest']:
        if not os.path.isfile(os.path.join(ROOT, f)):
            avert('fichiers', f'{f} absent de la racine')

    sm = os.path.join(ROOT, 'sitemap.xml')
    if os.path.isfile(sm):
        import xml.dom.minidom as minidom
        try: minidom.parse(sm)
        except Exception as e: err('sitemap.xml', f'XML invalide : {e}')
        c = open(sm, encoding='utf-8').read()
        mauvais = re.findall(r'<loc>https://specialone\.fr[^<]*</loc>', c)
        if mauvais: err('sitemap.xml', f'{len(mauvais)} URL sans www')
        # chaque URL /info/ doit correspondre à un fichier réel
        for loc in re.findall(r'<loc>([^<]+)</loc>', c):
            chemin = loc.replace(DOMAINE, '').strip('/')
            if chemin.startswith('info/'):
                if not os.path.isfile(os.path.join(ROOT, chemin, 'index.html')):
                    err('sitemap.xml', f'{loc} déclaré mais le fichier n\'existe pas')

    wm = os.path.join(ROOT, 'site.webmanifest')
    if os.path.isfile(wm):
        try: json.load(open(wm, encoding='utf-8'))
        except Exception as e: err('site.webmanifest', f'JSON invalide : {e}')

    for f in sorted(glob.glob(os.path.join(ROOT, 'info/*/index.html'))):
        h = open(f, encoding='utf-8').read()
        nom = f.split(os.sep)[-2]
        if 'https://specialone.fr' in h:
            err(f'info/{nom}', 'URL sans www')
        m = re.search(r'<meta name="description" content="([^"]+)"', h)
        if m and len(m.group(1)) > 160:
            avert(f'info/{nom}', f'description : {len(m.group(1))} car. (cible ≤ 160)')


# ------------------------------------------------------------------- CSV
def check_csv():
    fichiers = sorted(glob.glob(os.path.join(ROOT, '*_players.csv')))
    if not fichiers:
        avert('CSV', 'aucun fichier *_players.csv trouvé'); return
    total_l = total_j = 0

    for f in fichiers:
        nom = os.path.basename(f)
        with open(f, newline='', encoding='utf-8') as fh:
            r = csv.reader(fh); h = next(r)
            lignes = [x for x in r if x and any(c.strip() for c in x)]

        if [c.strip() for c in h] != COLONNES_CSV:
            err(nom, f'colonnes inattendues : {h}'); continue
        i = {c.strip(): n for n, c in enumerate(h)}
        iS, iN, iJ, iU, iP, iNo = i['Saison'], i['Numero'], i['Joueur'], i[KU], i['Postes'], i['Note']

        # postes : pas d'espace parasite autour des séparateurs
        for x in lignes:
            if x[iP] != x[iP].strip() or ' |' in x[iP] or '| ' in x[iP]:
                err(nom, f"espaces dans les postes : {x[iJ]} -> '{x[iP]}'"); break

        # note numérique et plausible
        for x in lignes:
            if not x[iNo].strip().isdigit():
                err(nom, f'note non numérique : {x[iJ]} ({x[iS]}) = "{x[iNo]}"'); break
            if not 40 <= int(x[iNo]) <= 99:
                avert(nom, f'note hors plage 40-99 : {x[iJ]} ({x[iS]}) = {x[iNo]}'); break

        # cohérence des postes entre saisons d'un même joueur
        g = defaultdict(list)
        for x in lignes: g[norm(x[iU]) or norm(x[iJ])].append(x)
        inc = [k for k, pr in g.items() if len(set(y[iP].strip() for y in pr)) > 1]
        if inc: err(nom, f'{len(inc)} joueur(s) aux postes incohérents entre saisons : {inc[:3]}')

        # Numéro de maillot en double sur une même saison. Simple
        # avertissement : c'est courant et légitime dans les données réelles
        # (un joueur part en janvier, un autre reprend son numéro). Ça n'est
        # un signal que sur les effectifs saisis à la main.
        doublons_num = 0
        for s in sorted(set(x[iS] for x in lignes)):
            nums = [x[iN] for x in lignes if x[iS] == s]
            if any(c > 1 for c in Counter(nums).values()): doublons_num += 1
        if doublons_num:
            avert(nom, f'{doublons_num} saison(s) avec un numéro porté par deux joueurs (transferts en cours de saison ?)')

        # même nom sous deux orthographes (accents, casse, ordre)
        def cle(n):
            n = unicodedata.normalize('NFD', n.lower())
            return frozenset(re.findall(r'[a-z]{3,}', ''.join(c for c in n if unicodedata.category(c) != 'Mn')))
        vus = {}
        for k in g:
            c = cle(k)
            if not c: continue
            if c in vus and vus[c] != k:
                avert(nom, f'doublon probable : "{vus[c]}" et "{k}"')
            vus.setdefault(c, k)

        # saisons isolées = ligne probablement attribuée au mauvais joueur
        for k, pr in g.items():
            ans = sorted(int(x[iS][:4]) for x in pr)
            trous = [(ans[j], ans[j+1]) for j in range(len(ans)-1) if ans[j+1]-ans[j] > 3]
            if trous: avert(nom, f'{k} : saisons non contiguës {ans} (retour au club ou ligne mal attribuée ?)')

        total_l += len(lignes); total_j += len(g)

    print(f"  {len(fichiers)} clubs | {total_l} lignes | {total_j} fiches joueurs")


# ------------------------------------------------------------------ sortie
def main():
    print(f"Vérification de {os.path.abspath(ROOT)}\n")
    check_index(); check_annexes(); check_csv()

    if avertis:
        print(f"\n  {len(avertis)} AVERTISSEMENT(S) - n'empêchent pas le déploiement")
        par_section = defaultdict(list)
        for s, m in avertis: par_section[s].append(m)
        for s, msgs in par_section.items():
            for m in msgs[:3]: print(f"    [{s}] {m}")
            if len(msgs) > 3: print(f"    [{s}] ... et {len(msgs)-3} autre(s)")
    if erreurs:
        print(f"\n  {len(erreurs)} ERREUR(S) - à corriger avant de déployer")
        for s, m in erreurs: print(f"    [{s}] {m}")
        print("\nÉCHEC"); return 1
    print("\nTout est bon." if not avertis else "\nAucune erreur bloquante.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
