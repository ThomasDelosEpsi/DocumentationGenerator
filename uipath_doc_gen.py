import os
import json
import re
import requests
import xml.etree.ElementTree as ET
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, font
from collections import Counter, OrderedDict
from datetime import datetime
import urllib3

# Désactive les avertissements de sécurité
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION ---
DEFAULT_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
LYRECO_PROXY_URL = "http://proxy.lyreco.com:8080" 

AVAILABLE_MODELS = [
    "mistral-tiny", 
    "mistral-small-latest",
    "mistral-large-latest",
    "codestral-latest"
]

LANGUAGES = {
    "Français": "fr",
    "English": "en"
}

NAMESPACES = {
    'x': 'http://schemas.microsoft.com/winfx/2006/xaml',
    'ui': 'http://schemas.uipath.com/workflow/activities',
    'sap2010': 'http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation'
}

IGNORED_FOLDERS = {'.local', '.screenshots', '.settings', 'backup', 'mocks', '.git', 'coverage', '.entities'}

# --- MOTEUR LOGIQUE ---
class LogicEngine:
    def __init__(self, api_key, proxy, model_name, language="fr", verify_ssl=True):
        self.api_key = api_key
        self.proxy = proxy
        self.model = model_name
        self.language = language
        self.verify_ssl = verify_ssl
        self._setup_proxy()

    def _setup_proxy(self):
        if self.proxy and self.proxy.strip():
            os.environ["HTTP_PROXY"] = self.proxy
            os.environ["HTTPS_PROXY"] = self.proxy

    def sanitize_text(self, text):
        if not text: return ""
        text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL_HIDDEN]', text)
        text = re.sub(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', '[IP_HIDDEN]', text)
        text = re.sub(r'(?i)(password|pwd|mdp|secret)\s*[:=]\s*\S+', r'\1 [SECRET]', text)
        return text

    def load_project_json(self, path):
        json_path = os.path.join(path, "project.json")
        if not os.path.exists(json_path): return None
        with open(json_path, 'r', encoding='utf-8') as f: return json.load(f)

    def get_activity_name(self, elem):
        tag = elem.tag
        if '}' in tag: return tag.split('}')[1]
        return tag

    def detect_workflow_type(self, root):
        for elem in root.iter():
            tag = self.get_activity_name(elem)
            if "StateMachine" in tag: return "StateMachine"
            if "Flowchart" in tag: return "Flowchart"
        return "Sequence" 

    def parse_vbs(self, file_path):
        try:
            with open(file_path, 'r', encoding='latin-1') as f: 
                content = f.readlines()
            functions = []
            comments = []
            for line in content:
                line = line.strip()
                if not line: continue
                if line.startswith("'"): comments.append(line[1:].strip())
                if re.match(r'(?i)^(Function|Sub)\s+', line):
                    func_name = re.split(r'[\s\(]', line)[1]
                    functions.append(func_name)
            return {
                "name": os.path.basename(file_path),
                "type": "VBS",
                "workflow_type": "Script VBS",
                "full_path": file_path,
                "functions": functions,
                "comments_preview": comments[:5],
                "logic_summary": [f"Function: {f}" for f in functions],
                "description": " ".join(comments[:2]) if comments else "" 
            }
        except: return None

    def parse_xaml(self, file_path):
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
        except ET.ParseError:
            return None

        wf_type = self.detect_workflow_type(root)
        annotation = root.get(f'{{{NAMESPACES["sap2010"]}}}Annotation.AnnotationText')
        
        file_data = {
            "name": os.path.basename(file_path),
            "type": "XAML",
            "workflow_type": wf_type,
            "full_path": file_path,
            "arguments": [],
            "invokes": [],
            "assets_detected": [],
            "queues_detected": [],
            "activity_stats": Counter(),
            "logic_summary": [],
            "description": annotation if annotation else "" 
        }

        for member in root.findall('.//x:Member', NAMESPACES):
            arg_type = member.get('Type', '')
            direction = "In" if "InArgument" in arg_type else "Out" if "OutArgument" in arg_type else "InOut"
            file_data["arguments"].append({
                "name": member.get('Name'),
                "direction": direction,
                "type": arg_type.split('(')[-1].replace(')', '')
            })

        for elem in root.iter():
            act_name = self.get_activity_name(elem)
            if "http://schemas.uipath.com/workflow/activities" in elem.tag:
                file_data["activity_stats"][act_name] += 1
            
            if act_name == "GetRobotAsset":
                val = elem.get("AssetName")
                if val and not any(char in val for char in ["{", "}", "[", "]", "+", "(", ")"]):
                    file_data["assets_detected"].append(val)
            
            if act_name in ["AddTransactionItem", "GetTransactionItem"]:
                val = elem.get("QueueName")
                if val and not any(char in val for char in ["{", "}", "[", "]", "+", "(", ")"]):
                    file_data["queues_detected"].append(val)
            
            if act_name == "InvokeWorkflowFile":
                wf = elem.get('WorkflowFileName')
                if wf: file_data["invokes"].append(wf)
            
            display_name = elem.get('DisplayName')
            if display_name and display_name not in ["Body", "Do", "Sequence", "Try Catch", "Then", "Else", "If", "Assign"]:
                if act_name not in ["Assign", "LogMessage", "WriteLine"]:
                    file_data["logic_summary"].append(display_name)
        
        return file_data

    def call_mistral(self, prompt, max_tokens=2000):
        if not self.api_key: return None
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1, 
            "max_tokens": max_tokens
        }
        
        wait = 2
        for i in range(5):
            try:
                resp = requests.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=data, timeout=90, verify=self.verify_ssl)
                if resp.status_code == 200:
                    return resp.json()['choices'][0]['message']['content'].strip()
                elif resp.status_code == 429:
                    print(f"⏳ Quota (429). Pause {wait}s...")
                    time.sleep(wait)
                    wait *= 2
                else:
                    print(f"⚠️ Erreur API {resp.status_code}: {resp.text}")
                    return None
            except Exception as e:
                print(f"⚠️ Exception: {e}")
                time.sleep(1)
        return None

    def ask_mistral_batch_summaries(self, batch_list):
        files_desc = ""
        for d in batch_list:
            existing_desc_marker = f"[ANNOTATION EXISTANTE: {d['description']}]" if d['description'] else "[PAS D'ANNOTATION]"
            type_info = f"({d.get('workflow_type', 'Sequence')})"
            if d['type'] == 'VBS':
                desc_str = f"SCRIPTS: {', '.join(d['functions'])}"
            else:
                steps = d['logic_summary'][:20]
                logic = " -> ".join(steps)
                desc_str = f"FLUX ACTIVITÉS: {logic}"
            files_desc += f"- FICHIER {type_info} '{d['name']}' {existing_desc_marker} : \n  Données techniques: {desc_str}\n\n"
        
        lang_instruction = "en français" if self.language == "fr" else "in English"
        prompt = (
            f"RÔLE : Expert Technique UiPath.\n"
            f"TÂCHE : Générer un résumé technique concis (1 phrase) pour chaque fichier.\n"
            f"RÈGLE D'OR : Si le marqueur [ANNOTATION EXISTANTE] est présent, utilise CETTE description comme base de vérité absolue. Sinon, déduis ce que fait le fichier d'après les activités. N'invente RIEN.\n"
            f"LANGUE : {lang_instruction}.\n"
            f"FORMAT RÉPONSE : NomFichier.ext : Résumé.\n\n"
            f"FICHIERS :\n{files_desc}"
        )
        
        response = self.call_mistral(self.sanitize_text(prompt))
        results = {}
        if response:
            for line in response.splitlines():
                if ":" in line:
                    parts = line.split(":", 1)
                    results[parts[0].strip()] = parts[1].strip()
        return results

    def ask_section_content(self, section_title, instruction, project_context, use_mermaid=False):
        lang_prompt = "Réponds en Français." if self.language == "fr" else "Answer in English."
        
        mermaid_instruction = ""
        if use_mermaid:
            mermaid_instruction = (
                "\n\n--- INSTRUCTION DIAGRAMME MERMAID OBLIGATOIRE ---\n"
                "Génère un diagramme Mermaid pour illustrer l'architecture globale.\n"
                "Utilise strictement la syntaxe `graph TD`.\n"
                "Applique les styles suivants pour la lisibilité :\n"
                "```mermaid\n"
                "graph TD\n"
                "classDef startend fill:#f96,stroke:#333,stroke-width:2px;\n"
                "classDef proc fill:#61dafb,stroke:#333,stroke-width:2px;\n"
                "classDef decision fill:#f9f,stroke:#333,stroke-width:2px;\n"
                "Start((Début)):::startend --> Processus[Processus Principal]:::proc\n"
                "```\n"
                "Adapte ce graphe aux Invokes réels listés dans le contexte JSON.\n"
            )

        prompt = (
            f"RÔLE : Auditeur Technique Senior RPA.\n"
            f"CONTEXTE : Rédaction d'une Documentation Technique (DSD) pour un projet UiPath.\n"
            f"LANGUE : {lang_prompt}\n\n"
            f"DONNÉES PROJET (JSON) : \n{json.dumps(project_context, indent=2)}\n\n"
            f"TA MISSION : Rédiger le chapitre '{section_title}'.\n\n"
            f"RÈGLES ABSOLUES DE RÉDACTION :\n"
            f"1. **STYLE DESCRIPTIF** : Ne dis jamais 'Il faut configurer' ou 'L'utilisateur doit'. Écris 'Le processus est configuré pour...', 'L'architecture utilise...'.\n"
            f"2. **PAS DE VIDE** : Si une info (Queues, Assets) est vide, ignore-la. Ne crée pas de section vide.\n"
            f"3. **VOCABULAIRE EXACT** : Arguments, Assets, Config Dictionary. Pas de 'variables globales'.\n"
            f"4. **INCERTITUDE** : Si l'info manque, n'écris rien.\n"
            f"5. **MISE EN FORME** : Tableaux Markdown pour les listes.\n"
            f"6. **PAS D'EXEMPLES** : Interdiction totale des exemples fictifs.\n"
            f"\nCONSIGNE SPÉCIFIQUE : {instruction}\n"
            f"{mermaid_instruction}"
        )
        
        content = self.call_mistral(self.sanitize_text(prompt), max_tokens=3000)
        
        if content:
            content = re.sub(r'^```markdown\s*', '', content)
            content = re.sub(r'^```\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
            content = re.sub(r'^#+\s*.*?\n', '', content.strip(), count=1)
            
            if len(content) < 20 and ("aucun" in content.lower() or "none" in content.lower()):
                return ""
                
        return content

    def rewrite_section(self, section_content, instruction, is_full_doc=False):
        """Réécrit une section ou cherche dans tout le document."""
        context_type = "CONTENU COMPLET DU DOCUMENT" if is_full_doc else "SECTION SÉLECTIONNÉE"
        
        prompt = (
            f"RÔLE : Assistant de rédaction technique expert.\n"
            f"CONTEXTE : {context_type}.\n"
            f"TÂCHE : Appliquer la modification demandée par l'utilisateur.\n"
            f"CONTENU SOURCE :\n{section_content}\n\n"
            f"INSTRUCTION UTILISATEUR : {instruction}\n\n"
            f"IMPORTANT : Renvoie le texte modifié au format Markdown. "
        )
        
        if is_full_doc:
            prompt += "Comme aucun texte n'était sélectionné, repère le passage pertinent dans tout le document, modifie-le, et RENVOIE L'INTÉGRALITÉ du document mis à jour."
        else:
            prompt += "Renvoie uniquement la section réécrite."

        return self.call_mistral(self.sanitize_text(prompt), max_tokens=3000 if not is_full_doc else 8000)

# --- INTERFACE MODERNE ---
class ModernApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("UIPath Generator // V11 // CyberLink")
        self.geometry("1200x900")
        
        # Palette Cyberpunk / Dark
        self.colors = {
            "bg": "#0a0a0a",           # Noir profond
            "fg": "#e0e0e0",           # Gris clair
            "accent": "#00d4ff",       # Cyan Néon
            "secondary": "#141414",    # Gris très sombre
            "input_bg": "#1f1f1f",     # Gris input
            "success": "#00ff9d",      # Vert Matrix
            "danger": "#ff3b30",       # Rouge alerte
            "border": "#333333",       # Bordures subtiles
            "text_hl": "#00d4ff"       # Highlight
        }
        self.configure(bg=self.colors["bg"])
        
        self.project_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.api_key = tk.StringVar(value=DEFAULT_API_KEY)
        self.batch_size = 1 # Forcé à 1
        self.disable_ssl = tk.BooleanVar(value=False)
        self.enable_mermaid = tk.BooleanVar(value=True)
        self.selected_model = tk.StringVar(value=AVAILABLE_MODELS[0])
        self.selected_lang = tk.StringVar(value="Français") 
        
        # Selection des chapitres à générer
        self.chapters_to_gen = {
            "resume": tk.BooleanVar(value=True),
            "identity": tk.BooleanVar(value=True),
            "architecture": tk.BooleanVar(value=True),
            "config": tk.BooleanVar(value=True),
            "errors": tk.BooleanVar(value=True),
            "deployment": tk.BooleanVar(value=True),
            "dictionary": tk.BooleanVar(value=True)
        }

        # Retouche variables
        self.edit_file_path = ""
        self.doc_sections = OrderedDict() 
        
        self._setup_styles()
        self._build_layout()
        
    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        
        # Configuration générique
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["fg"], font=("Segoe UI", 10))
        
        # Boutons futuristes
        style.configure("TButton", 
            font=("Segoe UI", 9, "bold"), 
            background=self.colors["secondary"], 
            foreground=self.colors["accent"], 
            borderwidth=1,
            focusthickness=3,
            focuscolor=self.colors["accent"]
        )
        style.map("TButton", 
            background=[("active", self.colors["accent"])], 
            foreground=[("active", "black")]
        )
        
        # Onglets
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", 
            background=self.colors["secondary"], 
            foreground="gray", 
            padding=[15, 8], 
            font=("Segoe UI", 10, "bold")
        )
        style.map("TNotebook.Tab", 
            background=[("selected", self.colors["bg"])], 
            foreground=[("selected", self.colors["accent"])],
            bordercolor=[("selected", self.colors["accent"])]
        )
        
        # Checkbox
        style.configure("TCheckbutton", 
            background=self.colors["bg"], 
            foreground=self.colors["fg"], 
            font=("Segoe UI", 10),
            indicatorcolor=self.colors["bg"],
            indicatorrelief="flat",
            indicatormargin=5
        )
        style.map("TCheckbutton", 
            indicatorcolor=[("selected", self.colors["accent"])],
            background=[("active", self.colors["bg"])]
        )

        # Combobox LISIBLE (Fond clair si besoin ou contrasté)
        style.configure("TCombobox", 
            fieldbackground=self.colors["input_bg"], 
            background=self.colors["secondary"], 
            foreground="white", # Texte blanc
            arrowcolor=self.colors["accent"],
            borderwidth=1
        )
        # Hack pour forcer la couleur du texte dans la liste déroulante sur certains OS
        self.option_add('*TCombobox*Listbox.background', self.colors["secondary"])
        self.option_add('*TCombobox*Listbox.foreground', "white")
        self.option_add('*TCombobox*Listbox.selectBackground', self.colors["accent"])
        self.option_add('*TCombobox*Listbox.selectForeground', "black")

    def _build_layout(self):
        # En-tête futuriste
        header = tk.Frame(self, bg=self.colors["bg"], height=60, highlightthickness=1, highlightbackground=self.colors["secondary"])
        header.pack(fill="x", padx=1, pady=1)
        
        title_lbl = tk.Label(header, text="PROJECT // DOC_GEN_V11", bg=self.colors["bg"], fg=self.colors["accent"], font=("Consolas", 18, "bold"))
        title_lbl.pack(side="left", padx=20, pady=15)
        
        status_lbl = tk.Label(header, text="SYSTEM: READY", bg=self.colors["bg"], fg=self.colors["success"], font=("Consolas", 10))
        status_lbl.pack(side="right", padx=20)
        
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.tab_params = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_params, text="CONFIGURATION")
        self._build_params_tab()
        
        self.tab_console = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_console, text="TERMINAL")
        self._build_console_tab()
        
        self.tab_preview = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_preview, text="VISUALISATION")
        self._build_preview_tab()
        
        self.tab_edit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_edit, text="RETOUCHE IA")
        self._build_edit_tab()

    def _build_params_tab(self):
        container = tk.Frame(self.tab_params, bg=self.colors["bg"])
        container.pack(fill="both", expand=True, padx=40, pady=20)
        
        # Inputs Paths
        self._create_path_input(container, "DOSSIER PROJET (.JSON)", self.project_path)
        self._create_path_input(container, "DOSSIER SORTIE", self.output_path)
        self._create_text_input(container, "CLÉ API MISTRAL", self.api_key, is_password=True)
        
        # Grid settings
        grid_frame = tk.Frame(container, bg=self.colors["bg"])
        grid_frame.pack(fill="x", pady=20)
        
        # Modèle & Langue (Style corrigé)
        tk.Label(grid_frame, text="MODÈLE IA", bg=self.colors["bg"], fg=self.colors["accent"], font=("Consolas", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(0,10))
        ttk.Combobox(grid_frame, textvariable=self.selected_model, values=AVAILABLE_MODELS, state="readonly", width=35).grid(row=1, column=0, sticky="w", padx=(0,20))
        
        tk.Label(grid_frame, text="LANGUE", bg=self.colors["bg"], fg=self.colors["accent"], font=("Consolas", 10, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Combobox(grid_frame, textvariable=self.selected_lang, values=list(LANGUAGES.keys()), state="readonly", width=20).grid(row=1, column=1, sticky="w")

        # Séparateur
        tk.Frame(container, bg=self.colors["secondary"], height=2).pack(fill="x", pady=20)

        # Colonnes Options & Chapitres
        cols = tk.Frame(container, bg=self.colors["bg"])
        cols.pack(fill="x")
        
        col_left = tk.Frame(cols, bg=self.colors["bg"])
        col_left.pack(side="left", fill="both", expand=True)
        
        col_right = tk.Frame(cols, bg=self.colors["bg"])
        col_right.pack(side="left", fill="both", expand=True, padx=20)
        
        # Options Techniques
        tk.Label(col_left, text="OPTIONS SYSTÈME", fg=self.colors["fg"], font=("Consolas", 12, "bold")).pack(anchor="w", pady=(0,10))
        ttk.Checkbutton(col_left, text="Activer Diagrammes Mermaid", variable=self.enable_mermaid, style="TCheckbutton").pack(anchor="w", pady=5)
        ttk.Checkbutton(col_left, text="Bypass SSL (Proxy Fix)", variable=self.disable_ssl, style="TCheckbutton").pack(anchor="w", pady=5)
        
        # Sélection des chapitres
        tk.Label(col_right, text="CHAPITRES À GÉNÉRER", fg=self.colors["fg"], font=("Consolas", 12, "bold")).pack(anchor="w", pady=(0,10))
        chap_map = [
            ("Résumé Exécutif", "resume"),
            ("Fiche d'identité", "identity"),
            ("Architecture Globale", "architecture"),
            ("Config & Environnement", "config"),
            ("Gestion des Erreurs", "errors"),
            ("Guide de Déploiement", "deployment"),
            ("Dictionnaire Composants", "dictionary"),
        ]
        
        for label, key in chap_map:
            ttk.Checkbutton(col_right, text=label, variable=self.chapters_to_gen[key], style="TCheckbutton").pack(anchor="w", pady=2)

        # Bouton Action
        btn_run = tk.Button(container, text="INITIALISER LA GÉNÉRATION", bg=self.colors["accent"], fg="black", font=("Consolas", 14, "bold"), relief="flat", padx=20, pady=15, command=self.start_generation)
        btn_run.pack(pady=40, fill="x")

    def _create_path_input(self, parent, label, var):
        tk.Label(parent, text=label, bg=self.colors["bg"], fg=self.colors["fg"], font=("Consolas", 10)).pack(anchor="w", pady=(10, 5))
        frame = tk.Frame(parent, bg=self.colors["bg"], highlightbackground=self.colors["secondary"], highlightthickness=1)
        frame.pack(fill="x")
        tk.Entry(frame, textvariable=var, bg=self.colors["input_bg"], fg="white", relief="flat", insertbackground="white", font=("Consolas", 10)).pack(side="left", fill="x", expand=True, ipady=8, padx=5)
        cmd = self.browse_project if "PROJET" in label else self.browse_output
        tk.Button(frame, text="...", bg=self.colors["secondary"], fg="white", command=cmd, relief="flat", width=4).pack(side="right", fill="y")

    def _create_text_input(self, parent, label, var, is_password=False):
        tk.Label(parent, text=label, bg=self.colors["bg"], fg=self.colors["fg"], font=("Consolas", 10)).pack(anchor="w", pady=(10, 5))
        entry = tk.Entry(parent, textvariable=var, show="*" if is_password else "", bg=self.colors["input_bg"], fg="white", relief="flat", insertbackground="white", font=("Consolas", 10))
        entry.pack(fill="x", ipady=8, padx=1, pady=1)

    def _build_console_tab(self):
        self.log_text = scrolledtext.ScrolledText(self.tab_console, bg="black", fg=self.colors["success"], font=("Consolas", 10), state="disabled", borderwidth=0)
        self.log_text.pack(fill="both", expand=True, padx=2, pady=2)

    def _build_preview_tab(self):
        toolbar = tk.Frame(self.tab_preview, bg=self.colors["secondary"], height=40)
        toolbar.pack(fill="x")
        tk.Button(toolbar, text="SAUVEGARDER FICHIER", bg=self.colors["accent"], fg="black", relief="flat", command=self.save_manual).pack(side="right", padx=10, pady=5)
        
        self.preview_text = scrolledtext.ScrolledText(self.tab_preview, bg="#f0f0f0", fg="#1a1a1a", font=("Segoe UI", 11), borderwidth=0, padx=40, pady=40)
        self.preview_text.pack(fill="both", expand=True, padx=0, pady=0)
        self.preview_text.tag_config("h1", font=("Segoe UI", 20, "bold"), foreground="#007acc", spacing3=15)
        self.preview_text.tag_config("h2", font=("Segoe UI", 16, "bold"), foreground="#333333", spacing3=10)
        self.preview_text.tag_config("bold", font=("Segoe UI", 11, "bold"))

    def _build_edit_tab(self):
        # Top Bar
        top_frame = tk.Frame(self.tab_edit, bg=self.colors["bg"], pady=10)
        top_frame.pack(fill="x", padx=10)
        
        tk.Button(top_frame, text="CHARGER DOCUMENT", bg=self.colors["secondary"], fg=self.colors["accent"], command=self.load_markdown_for_edit).pack(side="left")
        self.lbl_edit_file = tk.Label(top_frame, text="NO FILE LOADED", bg=self.colors["bg"], fg="gray", font=("Consolas", 10))
        self.lbl_edit_file.pack(side="left", padx=15)
        
        tk.Button(top_frame, text="SAUVEGARDER MODIFICATIONS", bg=self.colors["success"], fg="black", command=self.save_edited_doc).pack(side="right")

        # Layout Paned
        paned = tk.PanedWindow(self.tab_edit, orient="horizontal", bg=self.colors["bg"], sashwidth=4, sashrelief="flat")
        paned.pack(fill="both", expand=True, padx=10, pady=10)
        
        # --- Gauche : Structure ---
        left_frame = tk.Frame(paned, bg=self.colors["secondary"])
        tk.Label(left_frame, text="STRUCTURE", bg=self.colors["secondary"], fg="white", font=("Consolas", 10, "bold"), pady=5).pack(anchor="w", padx=5)
        
        self.section_list = tk.Listbox(left_frame, bg="#1a1a1a", fg="white", borderwidth=0, highlightthickness=0, selectbackground=self.colors["accent"], selectforeground="black", font=("Segoe UI", 10))
        self.section_list.pack(fill="both", expand=True, padx=1, pady=1)
        self.section_list.bind("<<ListboxSelect>>", self.on_section_select)
        
        # Bouton suppression section
        btn_del = tk.Button(left_frame, text="SUPPRIMER SECTION", bg=self.colors["danger"], fg="white", command=self.delete_section)
        btn_del.pack(fill="x", pady=5, padx=5)
        
        paned.add(left_frame, width=300)
        
        # --- Droite : Éditeur & IA ---
        right_frame = tk.Frame(paned, bg=self.colors["bg"])
        
        tk.Label(right_frame, text="CONTENU (Sélectionné)", bg=self.colors["bg"], fg="gray").pack(anchor="w")
        self.txt_section_content = scrolledtext.ScrolledText(right_frame, height=20, bg=self.colors["input_bg"], fg="white", font=("Consolas", 10), borderwidth=1, insertbackground="white")
        self.txt_section_content.pack(fill="both", expand=True, pady=5)
        
        # Zone Prompt IA
        prompt_frame = tk.Frame(right_frame, bg=self.colors["secondary"], pady=10, padx=10, highlightthickness=1, highlightbackground=self.colors["accent"])
        prompt_frame.pack(fill="x", pady=10)
        
        tk.Label(prompt_frame, text="COMMANDE IA (Si aucune sélection = Tout le document)", bg=self.colors["secondary"], fg=self.colors["accent"], font=("Consolas", 10, "bold")).pack(anchor="w")
        
        self.txt_instruction = tk.Entry(prompt_frame, bg=self.colors["input_bg"], fg="white", font=("Segoe UI", 11), relief="flat")
        self.txt_instruction.pack(fill="x", pady=10, ipady=5)
        
        btn_apply = tk.Button(prompt_frame, text="EXÉCUTER MODIFICATION", bg=self.colors["accent"], fg="black", command=self.apply_ai_edit)
        btn_apply.pack(fill="x")
        
        paned.add(right_frame)

    # --- LOGIQUE RETOUCHE AMÉLIORÉE ---
    def load_markdown_for_edit(self):
        f = filedialog.askopenfilename(filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
        if not f: return
        self.edit_file_path = f
        self.lbl_edit_file.config(text=os.path.basename(f).upper())
        
        with open(f, 'r', encoding='utf-8') as file:
            content = file.readlines()
        
        self.doc_sections.clear()
        self.section_list.delete(0, "end")
        
        current_header = "Méta / Intro"
        current_text = []
        
        for line in content:
            if line.startswith("#"):
                if current_text:
                    self.doc_sections[current_header] = "".join(current_text)
                current_header = line.strip()
                current_text = [line]
                self.section_list.insert("end", current_header)
            else:
                current_text.append(line)
        
        if current_text:
            self.doc_sections[current_header] = "".join(current_text)
            if current_header not in self.doc_sections: 
                 self.section_list.insert("end", current_header)

    def on_section_select(self, event):
        sel = self.section_list.curselection()
        if not sel: return
        header = self.section_list.get(sel[0])
        content = self.doc_sections.get(header, "")
        self.txt_section_content.delete("1.0", "end")
        self.txt_section_content.insert("end", content)

    def delete_section(self):
        sel = self.section_list.curselection()
        if not sel: return
        header = self.section_list.get(sel[0])
        
        if messagebox.askyesno("CONFIRMATION", f"Supprimer la section '{header}' ?"):
            del self.doc_sections[header]
            self.section_list.delete(sel[0])
            self.txt_section_content.delete("1.0", "end")

    def apply_ai_edit(self):
        instruction = self.txt_instruction.get().strip()
        if not instruction: return messagebox.showwarning("ERREUR", "Instruction manquante.")
        
        sel = self.section_list.curselection()
        
        # Mode : Section Unique ou Document Entier
        is_full_mode = False
        if sel:
            header = self.section_list.get(sel[0])
            content_to_edit = self.doc_sections.get(header, "")
        else:
            is_full_mode = True
            # Reconstruire tout le document
            content_to_edit = ""
            for i in range(self.section_list.size()):
                key = self.section_list.get(i)
                content_to_edit += self.doc_sections.get(key, "") + "\n"
        
        try:
            engine = LogicEngine(self.api_key.get(), LYRECO_PROXY_URL, self.selected_model.get(), verify_ssl=not self.disable_ssl.get())
            
            self.txt_section_content.delete("1.0", "end")
            msg = "⚡ ANALYSE GLOBALE EN COURS..." if is_full_mode else "⚡ RÉÉCRITURE SECTION..."
            self.txt_section_content.insert("end", msg)
            self.update()
            
            new_content = engine.rewrite_section(content_to_edit, instruction, is_full_doc=is_full_mode)
            
            if new_content:
                if is_full_mode:
                    # Si mode complet, on sauvegarde temporairement dans un fichier et on recharge
                    # Car le découpage section par section est complexe à refaire à la volée sans parser
                    # Pour simplifier ici : on met tout dans une section "Document Réécrit"
                    self.doc_sections.clear()
                    self.section_list.delete(0, "end")
                    self.doc_sections["DOCUMENT RÉÉCRIT"] = new_content
                    self.section_list.insert("end", "DOCUMENT RÉÉCRIT")
                    self.txt_section_content.delete("1.0", "end")
                    self.txt_section_content.insert("end", new_content)
                    messagebox.showinfo("SUCCÈS", "Document entier traité.")
                else:
                    self.doc_sections[header] = new_content
                    self.txt_section_content.delete("1.0", "end")
                    self.txt_section_content.insert("end", new_content)
                    messagebox.showinfo("SUCCÈS", "Section mise à jour.")
            else:
                self.txt_section_content.delete("1.0", "end")
                self.txt_section_content.insert("end", content_to_edit)
                messagebox.showerror("ERREUR", "L'IA n'a rien renvoyé.")
                
        except Exception as e:
            messagebox.showerror("CRASH", str(e))

    def save_edited_doc(self):
        if not self.edit_file_path: return
        full_text = ""
        for i in range(self.section_list.size()):
            key = self.section_list.get(i)
            full_text += self.doc_sections.get(key, "") + "\n\n"
        
        with open(self.edit_file_path, 'w', encoding='utf-8') as f:
            f.write(full_text)
        messagebox.showinfo("SYSTÈME", "Fichier sauvegardé.")

    # --- GENERATION ---
    def start_generation(self):
        self.notebook.select(self.tab_console)
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        p_path, o_path, key = self.project_path.get(), self.output_path.get(), self.api_key.get()
        ssl_off = self.disable_ssl.get()
        model = self.selected_model.get()
        lang_code = LANGUAGES[self.selected_lang.get()]
        want_mermaid = self.enable_mermaid.get()
        
        if not p_path or not o_path: return messagebox.showerror("ERREUR", "Chemins manquants.")
        
        self.log_text.config(state="normal"); self.log_text.delete(1.0, "end"); 
        
        try:
            engine = LogicEngine(key, LYRECO_PROXY_URL, model_name=model, language=lang_code, verify_ssl=not ssl_off)
            self.log(f"SYSTEM INIT >> MODEL: {model}")
            
            project_info = engine.load_project_json(p_path)
            if not project_info: return self.log("CRITICAL ERROR: project.json missing.")
            
            # SCAN
            files_found = []
            for root, dirs, files in os.walk(p_path):
                dirs[:] = [d for d in dirs if d.lower() not in IGNORED_FOLDERS]
                for f in files:
                    if f.endswith(".xaml"): files_found.append({"path": os.path.join(root, f), "type": "XAML"})
                    elif f.endswith(".vbs"): files_found.append({"path": os.path.join(root, f), "type": "VBS"})
            
            self.log(f"SCAN COMPLETE >> {len(files_found)} fichiers.")
            
            # PARSING & IA BATCH (Force Batch = 1)
            processed_wfs = []
            if key:
                self.log("AI ANALYSIS >> STARTING...")
                # Traitement un par un (Batch size = 1)
                for idx, item in enumerate(files_found):
                    d = None
                    if item["type"] == "XAML": d = engine.parse_xaml(item["path"])
                    elif item["type"] == "VBS": d = engine.parse_vbs(item["path"])
                    
                    if d:
                        self.log(f"PROCESSING >> {d['name']}")
                        # Appel IA direct pour ce fichier
                        summ_dict = engine.ask_mistral_batch_summaries([d])
                        summ = summ_dict.get(d['name'], d.get('description', ''))
                        processed_wfs.append({"data": d, "ai_summary": summ})
                        time.sleep(1) # Pause anti-spam
            else:
                self.log("OFFLINE MODE ACTIVATED.")
                # Parsing simple sans IA
                for item in files_found:
                    d = None
                    if item["type"] == "XAML": d = engine.parse_xaml(item["path"])
                    elif item["type"] == "VBS": d = engine.parse_vbs(item["path"])
                    if d: processed_wfs.append({"data": d, "ai_summary": ""})

            # REDACTION
            self.log("GENERATING DOCUMENT...")
            
            main_wf = next((w for w in processed_wfs if "main.xaml" in w['data']['name'].lower()), None)
            project_type = main_wf['data']['workflow_type'] if main_wf else "Sequence"

            ctx = {
                "ProjectName": project_info.get('name'),
                "ProjectDescription": project_info.get('description'),
                "ProjectType": project_type,
                "Dependencies": project_info.get("dependencies", {}),
                "KeyWorkflows": [w['data']['name'] for w in processed_wfs if w['data']['type'] == 'XAML'][:50],
                "AssetsDetected": sorted(list(set([a for w in processed_wfs for a in w['data'].get('assets_detected', [])]))),
                "QueuesDetected": sorted(list(set([a for w in processed_wfs for a in w['data'].get('queues_detected', [])]))),
            }

            final_doc = f"# Spécifications Techniques : {project_info.get('name')}\n"
            final_doc += f"**Date :** {datetime.now().strftime('%d/%m/%Y')}\n\n"

            # Définition des chapitres potentiels
            all_chapters = {
                "resume": {
                    "fr": ("1. Résumé Exécutif", "Synthèse métier globale du processus."),
                    "en": ("1. Executive Summary", "Global business summary.")
                },
                "identity": {
                    "fr": ("2. Fiche d'identité", f"Tableau avec Nom, Version, Type ({project_type})."),
                    "en": ("2. Project Identity", f"Identity table (Name, Version, Type: {project_type}).")
                },
                "architecture": {
                    "fr": ("3. Architecture Globale", "Explique le flux général (Main.xaml). Liste les Invokes."),
                    "en": ("3. Global Architecture", "Explain general flow. Key Invokes.")
                },
                "config": {
                    "fr": ("4. Configuration & Environnement", "Liste Assets, Queues, Config."),
                    "en": ("4. Configuration & Environment", "List Assets, Queues, Config.")
                },
                "errors": {
                    "fr": ("5. Gestion des Erreurs", "Stratégie exceptions (Retry, Business/System)."),
                    "en": ("5. Error Handling", "Exception handling strategy.")
                },
                "deployment": {
                    "fr": ("6. Guide de Déploiement", "Pré-requis techniques."),
                    "en": ("6. Deployment Guide", "Technical prerequisites.")
                }
            }
            
            # Boucle de génération conditionnelle
            if key:
                current_idx = 1
                for key_chap, data in all_chapters.items():
                    # Vérifier si l'utilisateur a coché ce chapitre
                    if self.chapters_to_gen.get(key_chap) and self.chapters_to_gen[key_chap].get():
                        title = data[lang_code][0]
                        instr = data[lang_code][1]
                        self.log(f"WRITING >> {title}...")
                        
                        use_mermaid = (key_chap == "architecture") and want_mermaid
                        content = engine.ask_section_content(title, instr, ctx, use_mermaid)
                        
                        if content and content.strip(): 
                            final_doc += f"## {title}\n{content}\n\n"
                        time.sleep(1)
            
            # Dictionnaire (Optionnel)
            if self.chapters_to_gen["dictionary"].get():
                dic_title = "Dictionnaire des Composants" if lang_code == "fr" else "Component Dictionary"
                final_doc += f"## {dic_title}\n\n"
                final_doc += "| Nom du Fichier | Type | Description / Rôle |\n| :--- | :--- | :--- |\n"
                
                sorted_wfs = sorted(processed_wfs, key=lambda x: (0 if "main" in x['data']['name'].lower() else 1, x['data']['name']))
                for w in sorted_wfs:
                    name = w['data']['name']
                    w_type = w['data']['workflow_type']
                    desc = (w['ai_summary'] or "").replace("\n", " ").replace("|", "-")
                    if not desc: desc = "Composant technique."
                    final_doc += f"| `{name}` | {w_type} | {desc} |\n"

            # Rendu
            self.render_markdown_preview(final_doc)
            self.notebook.select(self.tab_preview)
            
            safe_name = "".join([c for c in project_info.get("name", "Project") if c.isalnum() or c in (' ', '-', '_')])
            final_path = os.path.join(o_path, f"DSD_{safe_name}.md")
            with open(final_path, "w", encoding="utf-8") as f: f.write(final_doc)
            
            self.log("✅ JOB DONE.")
            messagebox.showinfo("SYSTÈME", "Documentation générée.")

        except Exception as e:
            self.log(f"CRITICAL ERROR : {e}")
            import traceback
            traceback.print_exc()

    def render_markdown_preview(self, markdown_text):
        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", "end")
        for line in markdown_text.splitlines():
            tag = "h1" if line.startswith("# ") else "h2" if line.startswith("## ") else None
            text = line.replace("#", "").strip() if tag else line
            self.preview_text.insert("end", text + "\n", tag)
        self.preview_text.config(state="disabled")

    def log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"> {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def browse_project(self):
        d = filedialog.askdirectory()
        if d: self.project_path.set(d)

    def browse_output(self):
        d = filedialog.askdirectory()
        if d: self.output_path.set(d)
        
    def save_manual(self):
        content = self.preview_text.get("1.0", "end")
        f = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("Markdown", "*.md")])
        if f:
            with open(f, "w", encoding="utf-8") as file: file.write(content)
            messagebox.showinfo("SYSTÈME", "Fichier sauvegardé.")

if __name__ == "__main__":
    app = ModernApp()
    app.mainloop()