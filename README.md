# Documentation Generator

Outil Python avec interface graphique (Tkinter) qui génère automatiquement de la documentation technique (dossier de spécifications) à partir de projets UiPath : il parcourt les fichiers de workflow XAML d'un projet, en extrait la logique métier et s'appuie sur un modèle de langage (via une API compatible Mistral) pour produire une documentation lisible, en français ou en anglais.

Le script inclut une étape d'anonymisation basique du texte envoyé au modèle (masquage des e-mails, adresses IP, mots de passe détectés) avant appel à l'API.

## Stack

- Python, Tkinter (interface graphique)
- Appel à une API de complétion type Mistral
- Parsing XML/XAML (`xml.etree.ElementTree`)

## Contenu

- `uipath_doc_gen.py` — script principal / application
- `DOSSIER_SPECIFICATIONS_TECHNIQUES.md`, `DST - *.md` — exemples de documentation générée

## Note

Les identifiants d'API présents dans le script sont des valeurs d'exemple/de développement à ne pas réutiliser telles quelles ; à remplacer par vos propres identifiants avant usage.
