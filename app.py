import os
import logging
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from mega import Mega
import uuid # Pour générer des noms de fichiers temporaires uniques

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# Récupérer les identifiants Mega depuis les variables d'environnement
# TU DOIS CONFIGURER CES VARIABLES SUR RENDER
MEGA_EMAIL = os.environ.get('MEGA_EMAIL')
MEGA_PASSWORD = os.environ.get('MEGA_PASSWORD')

# Vérification initiale des identifiants
if not MEGA_EMAIL or not MEGA_PASSWORD:
    logging.error("ERREUR CRITIQUE: Les variables d'environnement MEGA_EMAIL et MEGA_PASSWORD doivent être définies.")
    # Dans un cas réel, on pourrait vouloir arrêter l'application ici ou gérer différemment
    # Pour l'instant, on log l'erreur mais on continue pour que l'app puisse démarrer (et échouer plus tard)

# Dossier temporaire pour stocker les fichiers avant l'upload sur Mega
# Render fournit un système de fichiers temporaire
UPLOAD_FOLDER = '/tmp' # Utilisation d'un dossier temporaire standard
if not os.path.exists(UPLOAD_FOLDER):
     try:
         os.makedirs(UPLOAD_FOLDER)
         logging.info(f"Dossier temporaire créé: {UPLOAD_FOLDER}")
     except OSError as e:
         logging.error(f"Impossible de créer le dossier temporaire {UPLOAD_FOLDER}: {e}")
         # Gérer l'erreur si nécessaire, par exemple en arrêtant l'application

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Fonction pour initialiser et logger le client Mega
def get_mega_instance():
    """Initialise et retourne une instance Mega connectée."""
    try:
        logging.info(f"Tentative de connexion à Mega avec l'email: {MEGA_EMAIL[:4]}...") # Ne pas logger l'email complet
        mega = Mega()
        m = mega.login(MEGA_EMAIL, MEGA_PASSWORD)
        logging.info("Connexion à Mega réussie.")
        return m
    except Exception as e:
        logging.error(f"Échec de la connexion à Mega: {e}")
        return None

# Route pour vérifier si le backend fonctionne
@app.route('/')
def index():
    return jsonify({"message": "Le backend de génération de QR Code Image est opérationnel!"})

# Route pour l'upload d'image
@app.route('/upload', methods=['POST'])
def upload_image():
    if not MEGA_EMAIL or not MEGA_PASSWORD:
         logging.error("Tentative d'upload échouée car les identifiants Mega ne sont pas configurés.")
         return jsonify({"error": "Configuration serveur incomplète (identifiants Mega manquants)."}), 500

    if 'file' not in request.files:
        logging.warning("Aucun fichier trouvé dans la requête.")
        return jsonify({"error": "Aucun fichier fourni"}), 400

    file = request.files['file']

    if file.filename == '':
        logging.warning("Nom de fichier vide reçu.")
        return jsonify({"error": "Nom de fichier vide"}), 400

    if file:
        # Utiliser secure_filename pour éviter les problèmes de sécurité avec les noms de fichiers
        original_filename = secure_filename(file.filename)
        # Générer un nom de fichier unique pour éviter les collisions dans /tmp
        temp_filename = f"{uuid.uuid4()}_{original_filename}"
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)

        logging.info(f"Réception du fichier: {original_filename}. Sauvegarde temporaire sous: {temp_filepath}")

        try:
            # 1. Sauvegarder le fichier temporairement sur le serveur Render
            file.save(temp_filepath)
            logging.info(f"Fichier sauvegardé temporairement: {temp_filepath}")

            # 2. Se connecter à Mega
            m = get_mega_instance()
            if m is None:
                 # L'erreur est déjà loggée dans get_mega_instance
                 # Supprimer le fichier temporaire avant de retourner l'erreur
                 if os.path.exists(temp_filepath):
                     os.remove(temp_filepath)
                     logging.info(f"Fichier temporaire supprimé après échec de connexion: {temp_filepath}")
                 return jsonify({"error": "Échec de la connexion au service de stockage"}), 503 # Service Unavailable

            # 3. Téléverser le fichier sur Mega
            logging.info(f"Téléversement du fichier '{temp_filename}' sur Mega...")
            uploaded_file_node = m.upload(temp_filepath)
            logging.info(f"Fichier '{temp_filename}' téléversé sur Mega.")

            # 4. Obtenir le lien public du fichier téléversé
            # Note: m.export() donne un lien qui inclut la clé de déchiffrement
            # Si vous préférez, vous pouvez chercher le fichier et utiliser m.get_link() pour un lien sans clé
            # mais m.export est généralement ce qu'on veut pour un partage public simple.
            public_link = m.export(uploaded_file_node['h']) # 'h' est généralement l'handle du fichier
            logging.info(f"Lien public Mega généré: {public_link}")

            # 5. Retourner le lien à l'application Flutter
            return jsonify({"url": public_link})

        except Exception as e:
            logging.error(f"Erreur lors du traitement du fichier {original_filename}: {e}", exc_info=True) # exc_info=True pour la stack trace
            return jsonify({"error": f"Erreur interne lors du traitement du fichier: {e}"}), 500

        finally:
            # 6. Nettoyer : Supprimer le fichier temporaire DANS TOUS LES CAS (succès ou échec)
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                    logging.info(f"Fichier temporaire supprimé: {temp_filepath}")
                except OSError as e:
                    logging.error(f"Erreur lors de la suppression du fichier temporaire {temp_filepath}: {e}")
    else:
        # Ce cas ne devrait pas arriver si 'file' in request.files est passé, mais par sécurité
        logging.warning("Aucun fichier valide reçu bien que 'file' soit dans request.files.")
        return jsonify({"error": "Fichier invalide ou non reçu"}), 400

# Point d'entrée pour Gunicorn (utilisé par Render)
# Pas besoin de app.run() ici car Gunicorn s'en charge.
# Si tu veux tester localement, tu peux décommenter les lignes suivantes :
# if __name__ == '__main__':
#    # ATTENTION : Ne pas utiliser debug=True en production !
#    # Pour tester localement, tu devras définir les variables d'environnement
#    # export MEGA_EMAIL='ton_email@example.com'
#    # export MEGA_PASSWORD='ton_mot_de_passe'
#    # python app.py
#    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)