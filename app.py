import os
import logging
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from mega import Mega
from mega.errors import ValidationError # Importer si besoin de catcher spécifiquement
import uuid

# Configuration du logging améliorée
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Configuration et Constantes ---
MEGA_EMAIL = os.environ.get('MEGA_EMAIL')
MEGA_PASSWORD = os.environ.get('MEGA_PASSWORD')
UPLOAD_FOLDER = '/tmp'

# --- Vérifications Initiales ---
if not MEGA_EMAIL or not MEGA_PASSWORD:
    logger.critical("ERREUR CRITIQUE: Les variables d'environnement MEGA_EMAIL et MEGA_PASSWORD doivent être définies.")

if not os.path.exists(UPLOAD_FOLDER):
     try:
         os.makedirs(UPLOAD_FOLDER)
         logger.info(f"Dossier temporaire créé: {UPLOAD_FOLDER}")
     except OSError as e:
         logger.error(f"Impossible de créer le dossier temporaire {UPLOAD_FOLDER}: {e}")

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Fonctions Utilitaires ---
def get_mega_instance():
    """Initialise et retourne une instance Mega connectée."""
    if not MEGA_EMAIL or not MEGA_PASSWORD:
        logger.error("Tentative de connexion à Mega échouée : Identifiants non configurés.")
        return None
    try:
        logger.info(f"Tentative de connexion à Mega avec l'email : {MEGA_EMAIL[:4]}...")
        mega = Mega()
        m = mega.login(MEGA_EMAIL, MEGA_PASSWORD)
        logger.info("Connexion à Mega réussie.")
        return m
    except Exception as e:
        logger.error(f"Échec de la connexion à Mega: {e}", exc_info=True)
        return None

# --- Routes Flask ---
@app.route('/')
def index():
    """Route de base pour vérifier si le service est opérationnel."""
    return jsonify({"message": "Le backend de génération de QR Code Image est opérationnel!"})

@app.route('/upload', methods=['POST'])
def upload_image():
    """Route pour recevoir une image, l'uploader sur Mega et retourner le lien."""
    logger.info("Requête reçue sur /upload")

    # 1. Vérification de la requête
    if 'file' not in request.files:
        logger.warning("Aucun fichier trouvé dans la requête (clé 'file' manquante).")
        return jsonify({"error": "Aucun fichier fourni (champ 'file' manquant)"}), 400

    file = request.files['file']

    if file.filename == '':
        logger.warning("Nom de fichier vide reçu.")
        return jsonify({"error": "Nom de fichier vide"}), 400

    if file:
        # 2. Sauvegarde temporaire sécurisée
        original_filename = secure_filename(file.filename)
        temp_filename = f"{uuid.uuid4()}_{original_filename}"
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)

        logger.info(f"Réception du fichier: '{original_filename}'. Sauvegarde temporaire sous: '{temp_filepath}'")

        try:
            file.save(temp_filepath)
            logger.info(f"Fichier sauvegardé temporairement: '{temp_filepath}'")

            # 3. Connexion à Mega
            m = get_mega_instance()
            if m is None:
                 logger.error("Échec de l'obtention de l'instance Mega pour l'upload.")
                 return jsonify({"error": "Échec de la connexion au service de stockage"}), 503

            # 4. Téléversement sur Mega
            logger.info(f"Téléversement du fichier '{temp_filename}' ({os.path.getsize(temp_filepath)} bytes) sur Mega...")
            uploaded_file_node_response = m.upload(temp_filepath)
            logger.info(f"Fichier '{temp_filename}' téléversé sur Mega.")
            logger.info(f"Structure retournée par m.upload: {uploaded_file_node_response}")

            # ---> Accès au handle 'h' et au dictionnaire du node <---
            file_handle = None
            node_dict = None
            try:
                f_list = uploaded_file_node_response.get('f')
                if isinstance(f_list, list) and f_list:
                    first_element = f_list[0]
                    if isinstance(first_element, dict):
                        node_dict = first_element
                        file_handle = node_dict.get('h')

            except (IndexError, TypeError, AttributeError) as e_access:
                 logger.error(f"Erreur lors de l'accès à la structure imbriquée retournée par m.upload(): {e_access}", exc_info=True)
                 logger.error(f"Structure complète reçue: {uploaded_file_node_response}")

            # ---> Vérification et Construction Manuelle du Lien <---
            if file_handle and node_dict:
                logger.info(f"Handle du fichier trouvé: {file_handle}. Données du node extraites.")

                # 5. Construire le lien public manuellement
                try:
                    logger.info("Tentative de construction manuelle du lien d'exportation...")

                    # Extrait la clé complète (ex: 'user:key')
                    full_key = node_dict.get('k')
                    if not full_key or ':' not in full_key:
                        logger.error(f"La clé ('k') est manquante ou a un format inattendu dans node_dict: {node_dict}")
                        raise ValueError("Impossible d'extraire la clé du fichier depuis la réponse d'upload.")

                    # Extrait la partie après les ':'
                    file_key = full_key.split(':', 1)[1]

                    # Construit l'URL
                    public_link = f"https://mega.nz/file/{file_handle}#{file_key}"

                    logger.info(f"Lien public Mega (construit manuellement) généré avec succès.")
                    # 6. Retourner le lien
                    return jsonify({"url": public_link}), 200

                except Exception as link_build_error:
                    # Gère les erreurs pendant la construction du lien
                    logger.error(f"Erreur lors de la construction manuelle du lien pour le handle {file_handle}: {link_build_error}", exc_info=True)
                    error_message = f"Erreur interne lors de la création du lien public ({type(link_build_error).__name__}). Voir les logs serveur."
                    return jsonify({"error": error_message}), 500

            else:
                # Si file_handle ou node_dict n'a pas pu être extrait
                logger.error(f"Le handle ('h') ou les données du node n'ont pas pu être extraits de la structure retournée par m.upload(). Structure: {uploaded_file_node_response}")
                return jsonify({"error": "Erreur interne: Impossible d'extraire les informations du fichier après upload (structure inattendue)."}), 500

        except Exception as e:
            logger.error(f"Erreur globale lors du traitement du fichier '{original_filename}': {e}", exc_info=True)
            return jsonify({"error": "Erreur interne du serveur lors du traitement du fichier."}), 500

        finally:
            # 7. Nettoyage
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                    logger.info(f"Fichier temporaire supprimé: '{temp_filepath}'")
                except OSError as e_remove:
                    logger.error(f"Erreur lors de la suppression du fichier temporaire '{temp_filepath}': {e_remove}")
    else:
        logger.warning("Logique inattendue : 'file' est évalué comme False après vérifications initiales.")
        return jsonify({"error": "Fichier invalide ou non traité"}), 400

# --- Démarrage (pour Gunicorn sur Render) ---
# (Bloc de test local inchangé et commenté)
