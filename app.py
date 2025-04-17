import os
import logging
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from mega import Mega
import uuid # Pour générer des noms de fichiers temporaires uniques
# Pas besoin de 'time' pour cette approche

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

            # ---> Vérification et Export en passant le tuple (handle, node_data) via l'argument 'node' <---
            if file_handle and node_dict:
                logger.info(f"Handle du fichier trouvé: {file_handle}. Données du node extraites.")

                # 5. Obtenir le lien public via m.export(node=...)
                try:
                    # *** Construction du tuple attendu par export en interne ***
                    node_tuple_for_export = (file_handle, node_dict)
                    logger.info(f"Construction du tuple (handle, node_data) pour l'argument 'node' de m.export...")

                    # *** Appel de m.export en utilisant l'argument nommé 'node' ***
                    public_link = m.export(node=node_tuple_for_export)

                    if public_link:
                        logger.info(f"Lien public Mega (via export avec node=tuple) généré avec succès.")
                        # 6. Retourner le lien
                        return jsonify({"url": public_link}), 200
                    else:
                        # Si export retourne None même en passant le node tuple
                        logger.error(f"m.export(node=tuple) a retourné None pour le handle {file_handle}.")
                        return jsonify({"error": "Erreur interne: Impossible de générer le lien public (export node tuple a échoué)."}), 500

                except Exception as export_error:
                    # Gère les erreurs spécifiques à l'exportation
                    logger.error(f"Erreur lors de l'appel à m.export(node=tuple) pour le handle {file_handle}: {export_error}", exc_info=True)
                    error_message = f"Erreur interne lors de la création du lien public ({type(export_error).__name__}). Voir les logs serveur."
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
