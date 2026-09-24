---

# **Dossier de Spécifications Techniques (DST) – Maintenance RPA**
**Projet :** *FR MKT Comercial Cooperation Contract V2*

---

## **0. 📝 Résumé Exécutif du Projet**
Ce robot RPA automatise la **gestion des contrats commerciaux et des factures** dans le cadre d’une collaboration marketing, en intégrant les systèmes **Mediaplan HQ** et **Microsoft Office 365**. Il orchestré :
- Le **téléchargement/upload de fichiers comptables** (format 577) et de répertoires fournisseurs.
- La **sélection et modification de statuts de contrats** via des filtres dynamiques (plans médias, organisations, formats).
- L’**envoi d’emails aux fournisseurs** en cas d’absence de contrats ou de données manquantes.
- Une **gestion centralisée des logs** pour éviter les doublons et tracer les opérations.

Le processus est **linéaire et transactionnel**, avec une forte dépendance aux interactions UI (UiPath UIAutomation) et aux fichiers Excel/CSV. La robustesse repose sur des mécanismes de *retry* et une architecture modulaire (sous-workflows réutilisables).

---

## **1. 🆔 Fiche d'identité du Processus**
- **Nom :** FR MKT Comercial Cooperation Contract V2
- **Type :**
  - **Linéaire** (exécution séquentielle via *Main.xaml*).
  - **Transactionnel** (traitement par lots de contrats/factures sans file d’attente Orchestrator).
- **Framework :** UiPath (Studio 2023+).
- **Langage :** XAML (activités .NET).

### **Dépendances NuGet (versions exactes)**
| Package                          | Version          |
|----------------------------------|------------------|
| Application.UI.Library           | 1.0.1-alpha.25   |
| UiPath.Excel.Activities          | 3.2.1            |
| UiPath.Mail.Activities           | 2.4.10           |
| UiPath.MicrosoftOffice365.Activities | 3.4.10       |
| UiPath.System.Activities         | 25.10.2          |
| UiPath.Testing.Activities        | 25.10.0          |
| UiPath.UIAutomation.Activities   | 25.10.19         |

---

## **2. 🏗 Architecture et Logique**
### **Flux Global (*Main.xaml*)**
Le workflow principal (*Main.xaml*) suit une **séquence opérationnelle en 5 phases** :
1. **Préparation de l’environnement** :
   - Fermeture forcée de Chrome (`Kill Process`).
   - Création d’un dossier temporaire (*Create Temporary Folder.xaml*).
   - Copie des logs (*Copy log file.xaml*) et lecture de la configuration (*Read Configuration file.xaml*).
2. **Téléchargement des données** :
   - Récupération du fichier **577** (*Download 577.xaml*).
   - Connexion à **Mediaplan HQ** (*Mediaplan Connection.xaml*).
3. **Traitement des contrats** :
   - Réinitialisation des filtres (*Reset filters.xaml*).
   - Ajout de **plans médias**, **organisations**, et **formats** (*Adding media plans.xaml*, *Adding organizations.xaml*).
   - Sélection des statuts de contrats (*Select contracts statuts.xaml*).
4. **Gestion des fichiers et logs** :
   - Téléchargement des contrats (*Download Contract.xaml*).
   - Insertion de données dans le fichier 577 (*Insert Data 577.xaml*).
   - Upload du fichier modifié (*Upload 577 file.xaml*).
5. **Finalisation** :
   - Envoi d’emails aux fournisseurs (*Sending Email Suppliers.xaml*).
   - Nettoyage (*Delete Temporary folder.xaml*).

### **Logique d’Appels**
- **Modularité forte** : 90% des fonctionnalités sont externalisées dans des sous-workflows (*Addons/*).
- **Gestion d’état** : Chaque étape valide son résultat avant de passer à la suivante (ex : vérification de l’existence d’un fichier avant téléchargement).
- **Points de contrôle** : Utilisation de *Message Box* pour validation manuelle en cas d’anomalie.

---

## **3. ⚙️ Configuration (Config.xlsx)**
### **Assets Orchestrator**
*Aucun asset référencé* dans les workflows (les données sont gérées via fichiers locaux ou variables en mémoire).

### **Queues Orchestrator**
*Aucune queue utilisée* : le processus est **sans file d’attente**, basé sur des fichiers et interactions UI.

---
## **4. 📥 Entrées / Sorties (I/O)**
### **Inputs Globaux**
| Type               | Source                          | Format          | Exemple                          |
|--------------------|---------------------------------|-----------------|----------------------------------|
| Fichier 577        | Dossier partagé/SharePoint       | Excel/CSV       | `577_202405.xlsx`                |
| Configuration      | Fichier local                   | Excel           | `Config.xlsx` (chemins, URLs)     |
| Logs               | Dossier de logs                 | CSV             | `LogContract_202405.csv`         |
| Répertoire fournisseur | URL externe ou SharePoint   | ZIP/Excel       | `Repertoire_Fournisseurs.zip`   |

### **Outputs Globaux**
| Type               | Destination                     | Format          | Exemple                          |
|--------------------|---------------------------------|-----------------|----------------------------------|
| Fichier 577 mis à jour | Dossier de sortie          | Excel           | `577_Updated_202405.xlsx`        |
| Logs mis à jour    | Dossier de logs                 | CSV             | `LogContract_202405_updated.csv` |
| Emails envoyés     | Boîte mail (SMTP/Office 365)   | Email           | Objet : "Contrats manquants"     |
| Fichiers temporaires | Dossier `Temp/`             | Divers          | `Assistant_Folder/`              |

---

## **5. 🔧 Gestion des Erreurs & Known Issues**
### **Stratégie de Retry**
| Workflow                     | Mécanisme de Retry                          | Nombre de tentatives |
|------------------------------|---------------------------------------------|----------------------|
| *Download 577.xaml*           | Retry Scope (téléchargement)                | 3 (par défaut)       |
| *Upload 577 file.xaml*        | Retry Scope (upload)                        | 3                    |
| *Sending Email Suppliers.xaml*| Retry Scope (envoi email)                   | 3                    |
| *Mediaplan Connection.xaml*  | Check App States (champ *username*)         | 1 (avec fallback)    |

### **Restartabilité**
- **Idempotence** : Les workflows vérifient l’existence de logs/fichiers avant action (ex : *Download Contract.xaml* évite les doublons).
- **Points de reprise** : Le *Main.xaml* peut redémarrer après un échec en relançant depuis le dernier sous-workflow réussi (ex : après *Download 577.xaml*).
- **Known Issues** :
  - **Dépendance aux sélecteurs UI** : Sensible aux mises à jour de l’interface Mediaplan HQ.
  - **Gestion des timeouts** : Certains *Loading Circle.xaml* peuvent bloquer si le chargement dépasse 60s (à configurer).

---

## **6. 🚀 Guide de Déploiement**
### **Pré-requis Techniques**
| Composant               | Version/Configuration                     |
|--------------------------|------------------------------------------|
| **UiPath Robot**         | 2023.10+ (compatibilité activités 25.10) |
| **Système d’exploitation** | Windows 10/11 (64-bit)                |
| **.NET Framework**       | 4.8+                                    |
| **Navigateur**           | Chrome (pour Mediaplan HQ)               |
| **Excel**                | Microsoft 365 (pour *UiPath.Excel*)     |
| **Permissions**          | Accès aux dossiers partagés + SMTP        |
| **Mémoire**              | 8 Go RAM minimum (traitement de gros fichiers Excel) |

### **Étapes de Déploiement**
1. **Installer les dépendances NuGet** via UiPath Studio (versions exactes ci-dessus).
2. **Configurer les chemins** dans `Config.xlsx` :
   - Dossiers de logs, temporaires, et URLs (Mediaplan HQ, répertoire fournisseur).
3. **Tester les sélecteurs UI** dans Mediaplan HQ (valider avec *UIElementCheck.xaml*).
4. **Exécuter en mode Debug** pour vérifier :
   - La connexion à Mediaplan HQ (*Mediaplan Connection.xaml*).
   - Le téléchargement du fichier 577 (*Download 577.xaml*).
5. **Planifier via Orchestrator** :
   - Trigger : Fichier 577 disponible dans le dossier d’entrée.
   - Environnement : Machine virtuelle dédiée (éviter les conflits avec d’autres robots).

---
## **7. 📂 Dictionnaire des Workflows (Détail)**

### **Workflows Principaux**
| Nom du Fichier                     | Rôle                                                                 |
|------------------------------------|----------------------------------------------------------------------|
| **Main.xaml**                      | Orchestre l’ensemble du processus (téléchargement, traitement, upload, emails). |
| **GlobalHandler.xaml**             | Gestionnaire global d’exceptions (log et actions correctives par type d’erreur). |

### **Gestion des Fichiers & Logs**
| Nom du Fichier                     | Rôle                                                                 |
|------------------------------------|----------------------------------------------------------------------|
| **Copy log file.xaml**             | Copie un fichier de log vers un dossier de téléchargement.          |
| **Read logs.xaml**                 | Lit un fichier CSV de logs et retourne un `DataTable`.               |
| **Read Configuration file.xaml**   | Charge les paramètres depuis un fichier Excel (chemins, URLs).       |
| **Create Temporary Folder.xaml**   | Crée un dossier temporaire si inexistant.                            |
| **Delete Temporary folder.xaml**   | Supprime un dossier temporaire après vérification d’existence.       |

### **Connexion & Déconnexion**
| Nom du Fichier                     | Rôle                                                                 |
|------------------------------------|----------------------------------------------------------------------|
| **Mediaplan Connection.xaml**      | Authentification à Mediaplan HQ (récupération credentials + UI).   |
| **Logout.xaml**                    | Déconnexion de Mediaplan HQ (clics sur profil + bouton Logout).       |

### **Traitement des Contrats & Factures**
| Nom du Fichier                     | Rôle                                                                 |
|------------------------------------|----------------------------------------------------------------------|
| **Download 577.xaml**              | Télécharge le fichier 577 avec retry et validation post-téléchargement. |
| **Upload 577 file.xaml**           |